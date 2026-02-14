"""
Payment Service Layer

This module provides business logic for payment processing, verification, and finalization.
It coordinates between payment providers, database, and subscription service.

All functions are pure business logic - no aiogram imports or Telegram-specific types.

STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
- Payment provider unavailable → PaymentFinalizationError raised (retry later)
- Payment idempotency → preserved (check_payment_idempotency prevents double-processing)
- Payment amount mismatch → PaymentAmountMismatchError raised (NOT retried)
- Payment already processed → PaymentAlreadyProcessedError raised (NOT retried)
- Domain exceptions are NEVER retried → only transient infra errors are retried
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
# Invoice Creation (Crypto)
# ====================================================================================

async def create_invoice(
    telegram_id: int,
    tariff: str,
    period_days: int,
    amount_rubles: float,
    purchase_id: str,
    asset: str = "USDT",
    description: str = ""
) -> Dict[str, Any]:
    """
    Create crypto invoice via CryptoBot API.

    Caller must create pending_purchase first and pass purchase_id.
    Returns pay_url and invoice_id for the payment button.

    Args:
        telegram_id: User Telegram ID
        tariff: Tariff (basic/plus)
        period_days: Subscription period
        amount_rubles: Amount in rubles
        purchase_id: Pending purchase ID (correlation for webhook)
        asset: Crypto asset (USDT/TON/BTC)
        description: Invoice description

    Returns:
        {"invoice_id": int, "pay_url": str, "asset": str, "amount": float}

    Raises:
        PaymentFinalizationError: If CryptoBot not configured or API fails
    """
    try:
        import cryptobot_service
        if not cryptobot_service.is_enabled():
            raise PaymentFinalizationError("CryptoBot not configured")
        result = await cryptobot_service.create_invoice(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            amount_rubles=amount_rubles,
            purchase_id=purchase_id,
            asset=asset,
            description=description
        )
        return {
            "invoice_id": result.get("invoice_id"),
            "pay_url": result.get("pay_url"),
            "asset": result.get("asset", asset),
            "amount": amount_rubles,
        }
    except Exception as e:
        raise PaymentFinalizationError(f"Failed to create invoice: {e}") from e


async def mark_payment_paid(
    purchase_id: str,
    telegram_id: int,
    amount_rubles: float,
    provider: str = "cryptobot",
    invoice_id: Optional[str] = None
) -> PaymentResult:
    """
    Mark payment as paid and activate subscription (idempotent).

    Called from webhook handler after signature verification.
    Uses finalize_subscription_payment internally.

    Args:
        purchase_id: Purchase ID from payload
        telegram_id: User Telegram ID
        amount_rubles: Actual payment amount
        provider: Payment provider name
        invoice_id: Provider invoice ID (for audit)

    Returns:
        PaymentResult with subscription details
    """
    return await finalize_subscription_payment(
        purchase_id=purchase_id,
        telegram_id=telegram_id,
        payment_provider=provider,
        amount_rubles=amount_rubles,
        invoice_id=invoice_id
    )


async def mark_payment_failed(purchase_id: str) -> bool:
    """
    Mark pending purchase as expired (e.g. invoice expired).

    Args:
        purchase_id: Purchase ID to expire

    Returns:
        True if updated, False if not found or already processed
    """
    pool = await database.get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE purchase_id = $1 AND status = 'pending'",
            purchase_id
        )
        return result == "UPDATE 1"


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
    # STEP 4 — PART A: INPUT TRUST BOUNDARIES
    # Validate payload length and format
    if not payload:
        raise InvalidPaymentPayloadError("Payment payload is empty")
    
    # Length check (prevent oversized payloads)
    MAX_PAYLOAD_LENGTH = 256
    if len(payload) > MAX_PAYLOAD_LENGTH:
        raise InvalidPaymentPayloadError(f"Payment payload exceeds maximum length ({MAX_PAYLOAD_LENGTH})")
    
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
    
    STEP 3 — PART C: SIDE-EFFECT SAFETY
    This function provides idempotency boundary for payment finalization.
    - Payment finalization is guarded by this check
    - Executed once per purchase_id (correlation_id)
    - If already processed, returns existing subscription data
    - Logs when side-effect is SKIPPED due to idempotency
    
    Args:
        purchase_id: Purchase ID to check (acts as correlation_id)
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
    provider: str,
    provider_charge_id: str,
    description: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> BalanceTopupResult:
    """
    Finalize balance topup payment with idempotency protection.
    
    This handles the complete balance topup flow:
    - Validates amount and provider_charge_id
    - Checks idempotency (prevents duplicate credits)
    - Finalizes balance topup in database
    - Returns result with payment_id and new balance
    
    Args:
        telegram_id: Telegram ID of the user
        amount_rubles: Amount to topup in rubles
        provider: Payment provider ('telegram' or 'cryptobot')
        provider_charge_id: Unique charge ID from provider (for idempotency)
        description: Optional description for the transaction
        correlation_id: Optional correlation ID for logging
        
    Returns:
        BalanceTopupResult with payment details
        
    Raises:
        PaymentFinalizationError: If finalization fails
        ValueError: If provider_charge_id is missing
    """
    if amount_rubles <= 0:
        raise PaymentFinalizationError(f"Invalid amount for balance topup: {amount_rubles}")
    
    if not provider_charge_id:
        raise PaymentFinalizationError("provider_charge_id is required for idempotency")
    
    if provider not in ("telegram", "cryptobot"):
        raise PaymentFinalizationError(f"Invalid provider: {provider}. Must be 'telegram' or 'cryptobot'")
    
    try:
        result = await database.finalize_balance_topup(
            telegram_id=telegram_id,
            amount_rubles=amount_rubles,
            provider=provider,
            provider_charge_id=provider_charge_id,
            description=description or f"Пополнение баланса через {provider}",
            correlation_id=correlation_id
        )
        
        # Handle idempotent skip (already processed)
        if result.get("reason") == "already_processed":
            # Return success with existing payment info
            return BalanceTopupResult(
                success=True,
                payment_id=result["payment_id"],
                new_balance=result["new_balance"],
                referral_reward=result.get("referral_reward")
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
    
    # STEP 3 — PART C: SIDE-EFFECT SAFETY
    # Check idempotency before executing side-effect (payment finalization)
    # This ensures payment is processed only once per purchase_id
    is_already_processed, existing_subscription = await check_payment_idempotency(purchase_id, telegram_id)
    if is_already_processed:
        # STEP 3 — PART C: SIDE-EFFECT SAFETY
        # Payment already processed - side-effect SKIPPED due to idempotency
        # Log idempotency skip (for observability)
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"[IDEMPOTENCY] Payment finalization skipped: purchase_id={purchase_id}, "
            f"telegram_id={telegram_id}, reason=already_processed"
        )
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
