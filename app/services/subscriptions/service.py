"""
Subscription Service Layer

This module provides business logic for subscription purchases, renewals, and payments.
It acts as a thin wrapper around database operations, providing a clean interface
for handlers while keeping business logic separate from Telegram-specific code.

All functions are pure business logic - no aiogram imports or Telegram-specific types.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass
import database
import config

logger = logging.getLogger(__name__)


# ====================================================================================
# Domain Exceptions
# ====================================================================================

class SubscriptionServiceError(Exception):
    """Base exception for subscription service errors"""
    pass


class InvalidTariffError(SubscriptionServiceError):
    """Raised when tariff or period is invalid"""
    pass


class PriceCalculationError(SubscriptionServiceError):
    """Raised when price calculation fails"""
    pass


class PurchaseCreationError(SubscriptionServiceError):
    """Raised when purchase creation fails"""
    pass


class PaymentFinalizationError(SubscriptionServiceError):
    """Raised when payment finalization fails"""
    pass


# ====================================================================================
# Price Calculation
# ====================================================================================

async def calculate_price(
    telegram_id: int,
    tariff: str,
    period_days: int,
    promo_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    Calculate final price for a subscription with all discounts applied.
    
    This is a wrapper around database.calculate_final_price() that provides
    domain-specific error handling.
    
    Args:
        telegram_id: Telegram ID of the user
        tariff: Tariff type ("basic" or "plus")
        period_days: Subscription period in days (30, 90, 180, 365)
        promo_code: Optional promo code
        
    Returns:
        {
            "base_price_kopecks": int,
            "discount_amount_kopecks": int,
            "final_price_kopecks": int,
            "discount_percent": int,
            "discount_type": str,  # "promo", "vip", "personal", None
            "promo_code": Optional[str],
            "is_valid": bool
        }
        
    Raises:
        InvalidTariffError: If tariff or period is invalid
        PriceCalculationError: If price calculation fails
    """
    try:
        # Validate tariff exists
        if tariff not in config.TARIFFS:
            raise InvalidTariffError(f"Invalid tariff: {tariff}")
        
        # Validate period exists for tariff
        if period_days not in config.TARIFFS[tariff]:
            raise InvalidTariffError(f"Invalid period_days: {period_days} for tariff {tariff}")
        
        # Delegate to database layer
        result = await database.calculate_final_price(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            promo_code=promo_code
        )
        
        return result
        
    except ValueError as e:
        # database.calculate_final_price raises ValueError for invalid inputs
        raise InvalidTariffError(str(e)) from e
    except Exception as e:
        logger.error(f"Price calculation failed: user={telegram_id}, tariff={tariff}, period={period_days}, error={e}")
        raise PriceCalculationError(f"Price calculation failed: {e}") from e


# ====================================================================================
# Purchase Creation
# ====================================================================================

async def create_purchase(
    telegram_id: int,
    tariff: str,
    period_days: int,
    price_kopecks: int,
    promo_code: Optional[str] = None
) -> str:
    """
    Create a pending purchase record.
    
    This creates a pending purchase that will be finalized when payment is received.
    The purchase_id is returned and should be used in payment flows.
    
    Args:
        telegram_id: Telegram ID of the user
        tariff: Tariff type ("basic" or "plus")
        period_days: Subscription period in days
        price_kopecks: Price in kopecks
        promo_code: Optional promo code used
        
    Returns:
        purchase_id: Unique purchase identifier
        
    Raises:
        InvalidTariffError: If tariff or period is invalid
        PurchaseCreationError: If purchase creation fails
    """
    try:
        # Validate tariff exists
        if tariff not in config.TARIFFS:
            raise InvalidTariffError(f"Invalid tariff: {tariff}")
        
        # Validate period exists for tariff
        if period_days not in config.TARIFFS[tariff]:
            raise InvalidTariffError(f"Invalid period_days: {period_days} for tariff {tariff}")
        
        # Validate price is positive
        if price_kopecks <= 0:
            raise PurchaseCreationError(f"Invalid price: {price_kopecks} kopecks")
        
        # Delegate to database layer
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            promo_code=promo_code
        )
        
        logger.info(
            f"Purchase created: purchase_id={purchase_id}, user={telegram_id}, "
            f"tariff={tariff}, period={period_days}, price={price_kopecks} kopecks"
        )
        
        return purchase_id
        
    except Exception as e:
        logger.error(f"Purchase creation failed: user={telegram_id}, tariff={tariff}, period={period_days}, error={e}")
        raise PurchaseCreationError(f"Purchase creation failed: {e}") from e


# ====================================================================================
# Payment Finalization
# ====================================================================================

async def finalize_purchase(
    purchase_id: str,
    payment_provider: str,
    amount_rubles: float,
    invoice_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Finalize a purchase after successful payment.
    
    This activates the subscription, creates payment records, and processes
    referral rewards. All operations are atomic within a database transaction.
    
    Args:
        purchase_id: Purchase ID from pending_purchases
        payment_provider: Payment provider ("telegram_payment" or "cryptobot")
        amount_rubles: Amount paid in rubles
        invoice_id: Optional invoice ID from payment provider
        
    Returns:
        {
            "success": bool,
            "payment_id": int,
            "expires_at": datetime,
            "vpn_key": Optional[str],
            "is_renewal": bool,
            "activation_status": Optional[str],  # "active" or "pending"
            "is_balance_topup": Optional[bool]
        }
        
    Raises:
        PaymentFinalizationError: If finalization fails
    """
    try:
        # Delegate to database layer
        result = await database.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=payment_provider,
            amount_rubles=amount_rubles,
            invoice_id=invoice_id
        )
        
        if not result or not result.get("success"):
            raise PaymentFinalizationError(f"finalize_purchase returned failure: {result}")
        
        logger.info(
            f"Purchase finalized: purchase_id={purchase_id}, "
            f"payment_id={result.get('payment_id')}, provider={payment_provider}"
        )
        
        return result
        
    except ValueError as e:
        # database.finalize_purchase raises ValueError for invalid inputs
        raise PaymentFinalizationError(f"Invalid purchase: {e}") from e
    except Exception as e:
        logger.error(f"Payment finalization failed: purchase_id={purchase_id}, error={e}")
        raise PaymentFinalizationError(f"Payment finalization failed: {e}") from e


# ====================================================================================
# Subscription Status and Expiry Logic
# ====================================================================================

@dataclass
class SubscriptionStatus:
    """Subscription status information"""
    is_active: bool
    has_subscription: bool
    expires_at: Optional[datetime]
    activation_status: Optional[str]
    is_expired: bool


def parse_expires_at(expires_at: Any) -> Optional[datetime]:
    """
    Parse expires_at from various formats (datetime, string, None).
    
    Args:
        expires_at: Expiration date in various formats
        
    Returns:
        datetime object or None
    """
    if expires_at is None:
        return None
    
    if isinstance(expires_at, datetime):
        return expires_at
    
    if isinstance(expires_at, str):
        try:
            return datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        except:
            try:
                return datetime.fromisoformat(expires_at)
            except:
                return None
    
    return None


def is_subscription_active(
    subscription: Optional[Dict[str, Any]],
    now: Optional[datetime] = None
) -> bool:
    """
    Check if subscription is active.
    
    Subscription is active if:
    - subscription exists
    - status == 'active'
    - expires_at > now
    - uuid is not None (has VPN access)
    
    Args:
        subscription: Subscription dictionary from database
        now: Current time (defaults to datetime.now())
        
    Returns:
        True if subscription is active, False otherwise
    """
    if not subscription:
        return False
    
    if now is None:
        now = datetime.now()
    
    status = subscription.get("status")
    if status != "active":
        return False
    
    expires_at = parse_expires_at(subscription.get("expires_at"))
    if not expires_at:
        return False
    
    if expires_at <= now:
        return False
    
    # Check if UUID exists (has VPN access)
    uuid = subscription.get("uuid")
    if uuid is None:
        return False
    
    return True


def get_subscription_status(
    subscription: Optional[Dict[str, Any]],
    now: Optional[datetime] = None
) -> SubscriptionStatus:
    """
    Get comprehensive subscription status information.
    
    Args:
        subscription: Subscription dictionary from database
        now: Current time (defaults to datetime.now())
        
    Returns:
        SubscriptionStatus with all status information
    """
    if now is None:
        now = datetime.now()
    
    if not subscription:
        return SubscriptionStatus(
            is_active=False,
            has_subscription=False,
            expires_at=None,
            activation_status=None,
            is_expired=False
        )
    
    expires_at = parse_expires_at(subscription.get("expires_at"))
    activation_status = subscription.get("activation_status", "active")
    is_active = is_subscription_active(subscription, now)
    is_expired = expires_at is not None and expires_at <= now
    
    return SubscriptionStatus(
        is_active=is_active,
        has_subscription=True,
        expires_at=expires_at,
        activation_status=activation_status,
        is_expired=is_expired
    )


async def check_and_disable_expired_subscription(telegram_id: int) -> bool:
    """
    Check and disable expired subscription if needed.
    
    This is a wrapper around database.check_and_disable_expired_subscription
    to keep subscription-related logic in the subscription service.
    
    Args:
        telegram_id: Telegram ID of the user
        
    Returns:
        True if subscription was disabled, False otherwise
    """
    return await database.check_and_disable_expired_subscription(telegram_id)


# ====================================================================================
# Renewal Logic
# ====================================================================================

async def calculate_renewal_price(
    telegram_id: int,
    tariff: str,
    period_days: int = 30
) -> Dict[str, Any]:
    """
    Calculate price for subscription renewal.
    
    Renewals use the same pricing logic as new purchases, but typically
    default to 30 days. VIP and personal discounts apply.
    
    Args:
        telegram_id: Telegram ID of the user
        tariff: Tariff type ("basic" or "plus")
        period_days: Subscription period in days (default: 30)
        
    Returns:
        Same format as calculate_price()
        
    Raises:
        InvalidTariffError: If tariff or period is invalid
        PriceCalculationError: If price calculation fails
    """
    # Renewals don't use promo codes - only VIP and personal discounts
    return await calculate_price(
        telegram_id=telegram_id,
        tariff=tariff,
        period_days=period_days,
        promo_code=None
    )


async def renew_subscription(
    telegram_id: int,
    tariff: str,
    period_days: int = 30,
    payment_provider: str = "telegram_payment",
    amount_rubles: float = None,
    invoice_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renew an existing subscription.
    
    This creates a purchase and finalizes it immediately. Used for
    auto-renewals and manual renewals.
    
    Args:
        telegram_id: Telegram ID of the user
        tariff: Tariff type ("basic" or "plus")
        period_days: Subscription period in days (default: 30)
        payment_provider: Payment provider
        amount_rubles: Amount paid (if None, will be calculated)
        invoice_id: Optional invoice ID
        
    Returns:
        Same format as finalize_purchase()
        
    Raises:
        InvalidTariffError: If tariff or period is invalid
        PaymentFinalizationError: If renewal fails
    """
    try:
        # Calculate price if not provided
        if amount_rubles is None:
            price_info = await calculate_renewal_price(
                telegram_id=telegram_id,
                tariff=tariff,
                period_days=period_days
            )
            amount_rubles = price_info["final_price_kopecks"] / 100.0
        
        # Create purchase
        purchase_id = await create_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=int(amount_rubles * 100),
            promo_code=None  # Renewals don't use promo codes
        )
        
        # Finalize immediately
        result = await finalize_purchase(
            purchase_id=purchase_id,
            payment_provider=payment_provider,
            amount_rubles=amount_rubles,
            invoice_id=invoice_id
        )
        
        logger.info(
            f"Subscription renewed: user={telegram_id}, tariff={tariff}, "
            f"period={period_days}, purchase_id={purchase_id}"
        )
        
        return result
        
    except (InvalidTariffError, PurchaseCreationError, PaymentFinalizationError):
        # Re-raise domain exceptions
        raise
    except Exception as e:
        logger.error(f"Renewal failed: user={telegram_id}, tariff={tariff}, period={period_days}, error={e}")
        raise PaymentFinalizationError(f"Renewal failed: {e}") from e
