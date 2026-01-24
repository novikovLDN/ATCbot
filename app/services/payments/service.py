"""
Payment Service Layer

This module provides business logic for payment processing, verification, and finalization.
It coordinates between payment providers, database, and subscription service.

All functions are pure business logic - no aiogram imports or Telegram-specific types.
"""

from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
import database
from app.services.subscriptions import service as subscription_service
from app.services.payments.exceptions import (
    PaymentServiceError,
    InvalidPaymentPayloadError,
    PaymentAmountMismatchError,
    PaymentAlreadyProcessedError,
    PaymentFinalizationError,
)


# ====================================================================================
# Result Types
# ====================================================================================

@dataclass
class PaymentResult:
    """Result of subscription payment finalization"""
    success: bool
    payment_id: int
    expires_at: Any  # datetime
    vpn_key: Optional[str]
    is_renewal: bool
    activation_status: Optional[str]  # "active" or "pending"
    referral_reward: Optional[Dict[str, Any]]


@dataclass
class BalanceTopupResult:
    """Result of balance topup payment finalization"""
    success: bool
    payment_id: int
    new_balance: float
    referral_reward: Optional[Dict[str, Any]]


@dataclass
class PaymentPayloadInfo:
    """Parsed payment payload information"""
    payload_type: str  # "balance_topup", "purchase", "renew", "legacy"
    purchase_id: Optional[str]
    telegram_id: Optional[int]
    tariff: Optional[str]
    amount: Optional[float]
    promo_code: Optional[str]


# ====================================================================================
# Payment Payload Verification
# ====================================================================================

async def verify_payment_payload(
    payload: str,
    telegram_id: int
) -> PaymentPayloadInfo:
    """
    Verify and parse payment payload.
    
    Args:
        payload: Payment payload string from Telegram
        telegram_id: Telegram ID of the user making payment
        
    Returns:
        PaymentPayloadInfo with parsed payload data
        
    Raises:
        InvalidPaymentPayloadError: If payload format is invalid
    """
    if not payload:
        raise InvalidPaymentPayloadError("Payment payload is empty")
    
    # Balance topup format: "balance_topup_{telegram_id}_{amount}"
    if payload.startswith("balance_topup_"):
        parts = payload.split("_")
        if len(parts) < 4:
            raise InvalidPaymentPayloadError(f"Invalid balance topup payload format: {payload}")
        
        try:
            payload_user_id = int(parts[2])
            amount = int(parts[3])
            
            if payload_user_id != telegram_id:
                raise InvalidPaymentPayloadError(
                    f"Payload user_id mismatch: payload_user_id={payload_user_id}, telegram_id={telegram_id}"
                )
            
            return PaymentPayloadInfo(
                payload_type="balance_topup",
                purchase_id=None,
                telegram_id=payload_user_id,
                tariff=None,
                amount=float(amount),
                promo_code=None
            )
        except (ValueError, IndexError) as e:
            raise InvalidPaymentPayloadError(f"Error parsing balance topup payload: {e}")
    
    # New format: "purchase:{purchase_id}"
    if payload.startswith("purchase:"):
        purchase_id = payload.split(":", 1)[1]
        if not purchase_id:
            raise InvalidPaymentPayloadError("Purchase ID is empty in payload")
        
        # Get pending purchase to extract details
        pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
        if not pending_purchase:
            raise InvalidPaymentPayloadError(f"Pending purchase not found: purchase_id={purchase_id}")
        
        return PaymentPayloadInfo(
            payload_type="purchase",
            purchase_id=purchase_id,
            telegram_id=telegram_id,
            tariff=pending_purchase.get("tariff"),
            amount=pending_purchase.get("price_kopecks", 0) / 100.0,
            promo_code=pending_purchase.get("promo_code")
        )
    
    # Legacy formats (for backward compatibility)
    if payload.startswith("renew:"):
        parts = payload.split(":")
        if len(parts) < 3:
            raise InvalidPaymentPayloadError(f"Invalid renewal payload format: {payload}")
        
        try:
            payload_user_id = int(parts[1])
            tariff_key = parts[2]
            
            if payload_user_id != telegram_id:
                raise InvalidPaymentPayloadError(
                    f"Payload user_id mismatch: payload_user_id={payload_user_id}, telegram_id={telegram_id}"
                )
            
            return PaymentPayloadInfo(
                payload_type="renew",
                purchase_id=None,
                telegram_id=payload_user_id,
                tariff=tariff_key,
                amount=None,
                promo_code=None
            )
        except (ValueError, IndexError) as e:
            raise InvalidPaymentPayloadError(f"Error parsing renewal payload: {e}")
    
    if payload.startswith("purchase:promo:"):
        parts = payload.split(":")
        if len(parts) < 5:
            raise InvalidPaymentPayloadError(f"Invalid promo purchase payload format: {payload}")
        
        try:
            promo_code_used = parts[2]
            payload_user_id = int(parts[3])
            tariff_key = parts[4]
            
            if payload_user_id != telegram_id:
                raise InvalidPaymentPayloadError(
                    f"Payload user_id mismatch: payload_user_id={payload_user_id}, telegram_id={telegram_id}"
                )
            
            return PaymentPayloadInfo(
                payload_type="purchase",
                purchase_id=None,
                telegram_id=payload_user_id,
                tariff=tariff_key,
                amount=None,
                promo_code=promo_code_used
            )
        except (ValueError, IndexError) as e:
            raise InvalidPaymentPayloadError(f"Error parsing promo purchase payload: {e}")
    
    # Legacy format: "{telegram_id}_{tariff}"
    parts = payload.split("_")
    if len(parts) < 2:
        raise InvalidPaymentPayloadError(f"Invalid payload format: {payload}")
    
    try:
        payload_user_id = int(parts[0])
        tariff_key = parts[1]
        
        if payload_user_id != telegram_id:
            raise InvalidPaymentPayloadError(
                f"Payload user_id mismatch: payload_user_id={payload_user_id}, telegram_id={telegram_id}"
            )
        
        return PaymentPayloadInfo(
            payload_type="legacy",
            purchase_id=None,
            telegram_id=payload_user_id,
            tariff=tariff_key,
            amount=None,
            promo_code=None
        )
    except (ValueError, IndexError) as e:
        raise InvalidPaymentPayloadError(f"Error parsing legacy payload: {e}")


# ====================================================================================
# Payment Amount Validation
# ====================================================================================

async def validate_payment_amount(
    actual_amount_rubles: float,
    expected_amount_rubles: float,
    tolerance: float = 1.0
) -> bool:
    """
    Validate that payment amount matches expected amount.
    
    Args:
        actual_amount_rubles: Actual payment amount in rubles
        expected_amount_rubles: Expected payment amount in rubles
        tolerance: Allowed difference in rubles (default: 1.0)
        
    Returns:
        True if amounts match within tolerance
        
    Raises:
        PaymentAmountMismatchError: If amounts don't match
    """
    amount_diff = abs(actual_amount_rubles - expected_amount_rubles)
    
    if amount_diff > tolerance:
        raise PaymentAmountMismatchError(
            f"Payment amount mismatch: expected={expected_amount_rubles:.2f} RUB, "
            f"actual={actual_amount_rubles:.2f} RUB, diff={amount_diff:.2f} RUB"
        )
    
    return True


# ====================================================================================
# Payment Idempotency Checks
# ====================================================================================

async def check_payment_idempotency(
    purchase_id: str,
    telegram_id: int
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if payment has already been processed (idempotency check).
    
    Args:
        purchase_id: Purchase ID to check
        telegram_id: Telegram ID of the user
        
    Returns:
        Tuple of (is_already_processed, existing_subscription_data)
        - is_already_processed: True if payment already processed
        - existing_subscription_data: Subscription data if already processed, None otherwise
    """
    # Get pending purchase
    pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
    if not pending_purchase:
        return False, None
    
    # Check if already paid
    pending_purchase_status = pending_purchase.get("status")
    if pending_purchase_status == "paid":
        # Check if payment exists and is approved
        pool = await database.get_pool()
        if pool:
            async with pool.acquire() as conn:
                payment_row = await conn.fetchrow(
                    "SELECT id, status FROM payments WHERE purchase_id = $1 ORDER BY id DESC LIMIT 1",
                    purchase_id
                )
                
                if payment_row and payment_row.get("status") == "approved":
                    # Get existing subscription
                    existing_subscription = await database.get_subscription(telegram_id)
                    return True, existing_subscription
    
    return False, None


# ====================================================================================
# Balance Topup Finalization
# ====================================================================================

async def finalize_balance_topup_payment(
    telegram_id: int,
    amount_rubles: float,
    description: Optional[str] = None
) -> BalanceTopupResult:
    """
    Finalize balance topup payment.
    
    This handles the complete balance topup flow:
    - Validates amount
    - Finalizes balance topup in database
    - Returns result with payment_id and new balance
    
    Args:
        telegram_id: Telegram ID of the user
        amount_rubles: Amount to topup in rubles
        description: Optional description for the transaction
        
    Returns:
        BalanceTopupResult with payment details
        
    Raises:
        PaymentFinalizationError: If finalization fails
    """
    if amount_rubles <= 0:
        raise PaymentFinalizationError(f"Invalid amount for balance topup: {amount_rubles}")
    
    try:
        result = await database.finalize_balance_topup(
            telegram_id=telegram_id,
            amount_rubles=amount_rubles,
            description=description or "Пополнение баланса через Telegram Payments"
        )
        
        if not result or not result.get("success"):
            raise PaymentFinalizationError(f"finalize_balance_topup returned failure: {result}")
        
        return BalanceTopupResult(
            success=True,
            payment_id=result["payment_id"],
            new_balance=result["new_balance"],
            referral_reward=result.get("referral_reward")
        )
        
    except ValueError as e:
        raise PaymentFinalizationError(f"Invalid balance topup: {e}") from e
    except Exception as e:
        raise PaymentFinalizationError(f"Balance topup finalization failed: {e}") from e


# ====================================================================================
# Subscription Payment Finalization
# ====================================================================================

async def finalize_subscription_payment(
    purchase_id: str,
    telegram_id: int,
    payment_provider: str,
    amount_rubles: float,
    invoice_id: Optional[str] = None
) -> PaymentResult:
    """
    Finalize subscription payment.
    
    This coordinates the complete subscription payment flow:
    - Validates pending purchase
    - Validates payment amount
    - Checks idempotency
    - Finalizes purchase through subscription service
    - Returns result with subscription details
    
    Args:
        purchase_id: Purchase ID from pending purchase
        telegram_id: Telegram ID of the user
        payment_provider: Payment provider name
        amount_rubles: Actual payment amount in rubles
        invoice_id: Optional invoice ID from payment provider
        
    Returns:
        PaymentResult with subscription details
        
    Raises:
        InvalidPaymentPayloadError: If pending purchase is invalid
        PaymentAmountMismatchError: If payment amount doesn't match
        PaymentFinalizationError: If finalization fails (including when payment already processed but subscription invalid)
    """
    # Validate pending purchase
    pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
    if not pending_purchase:
        raise InvalidPaymentPayloadError(
            f"Pending purchase not found or expired: purchase_id={purchase_id}, user={telegram_id}"
        )
    
    # Validate payment amount
    expected_amount_rubles = pending_purchase["price_kopecks"] / 100.0
    await validate_payment_amount(amount_rubles, expected_amount_rubles)
    
    # Check idempotency
    is_already_processed, existing_subscription = await check_payment_idempotency(purchase_id, telegram_id)
    if is_already_processed:
        # Payment already processed - return existing subscription data
        if existing_subscription and existing_subscription.get("status") == "active":
            expires_at = existing_subscription.get("expires_at")
            vpn_key = existing_subscription.get("vpn_key")
            
            # Generate key from UUID if missing
            if not vpn_key:
                uuid = existing_subscription.get("uuid")
                if uuid:
                    import vpn_utils
                    vpn_key = vpn_utils.generate_vless_url(uuid)
            
            if expires_at and vpn_key:
                # Get payment_id from existing payment
                pool = await database.get_pool()
                payment_id = None
                if pool:
                    async with pool.acquire() as conn:
                        payment_row = await conn.fetchrow(
                            "SELECT id FROM payments WHERE purchase_id = $1 ORDER BY id DESC LIMIT 1",
                            purchase_id
                        )
                        if payment_row:
                            payment_id = payment_row["id"]
                
                return PaymentResult(
                    success=True,
                    payment_id=payment_id or 0,
                    expires_at=expires_at,
                    vpn_key=vpn_key,
                    is_renewal=existing_subscription.get("is_renewal", False),
                    activation_status=existing_subscription.get("activation_status"),
                    referral_reward=None
                )
        
        # Payment processed but subscription not found or inactive - this is an error
        raise PaymentFinalizationError(
            f"Payment already processed but subscription not found or inactive: purchase_id={purchase_id}, user={telegram_id}"
        )
    
    # Finalize purchase through subscription service
    try:
        result = await subscription_service.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=payment_provider,
            amount_rubles=amount_rubles,
            invoice_id=invoice_id
        )
        
        if not result or not result.get("success"):
            raise PaymentFinalizationError(f"finalize_purchase returned invalid result: {result}")
        
        # Extract referral reward if present
        referral_reward = result.get("referral_reward")
        
        return PaymentResult(
            success=True,
            payment_id=result["payment_id"],
            expires_at=result["expires_at"],
            vpn_key=result.get("vpn_key"),
            is_renewal=result["is_renewal"],
            activation_status=result.get("activation_status"),
            referral_reward=referral_reward
        )
        
    except subscription_service.PaymentFinalizationError as e:
        raise PaymentFinalizationError(f"Subscription payment finalization failed: {e}") from e
    except Exception as e:
        raise PaymentFinalizationError(f"Payment finalization failed: {e}") from e
