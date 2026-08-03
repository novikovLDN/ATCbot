"""
Subscription Service Layer

This module provides business logic for subscription purchases, renewals, and payments.
It acts as a thin wrapper around database operations, providing a clean interface
for handlers while keeping business logic separate from Telegram-specific code.

All functions are pure business logic - no aiogram imports or Telegram-specific types.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass
import database
# Доменные исключения слоя БД: сервис их не «переводит», а пропускает —
# см. комментарий в finalize_purchase.
from database.subscriptions import (
    PaymentAlreadyProcessed,
    PaymentAmountMismatch,
    PurchaseInvalidStatus,
    PurchaseLocked,
)
import config

logger = logging.getLogger(__name__)


# ====================================================================================
# Domain Exceptions (re-export from exceptions module)
# ====================================================================================

from app.services.subscriptions.exceptions import (
    SubscriptionServiceError,
    InvalidTariffError,
    PriceCalculationError,
    PurchaseCreationError,
    PaymentFinalizationError,
)

# ====================================================================================
# Price Calculation
# ====================================================================================

async def calculate_price(
    telegram_id: int,
    tariff: str,
    period_days: int,
    promo_code: Optional[str] = None,
    country: Optional[str] = None,
    base_price_override_rubles: Optional[int] = None
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
        # Combo tariffs pass an explicit base price (config.COMBO_TARIFFS),
        # so they skip validation against config.TARIFFS.
        original_config_price_kopecks: Optional[int] = None
        pricing_reason: Optional[str] = None
        pricing_percent: int = 0
        if base_price_override_rubles is None:
            # Validate tariff exists
            if tariff not in config.TARIFFS:
                raise InvalidTariffError(f"Invalid tariff: {tariff}")

            # Validate period exists for tariff
            if period_days not in config.TARIFFS[tariff]:
                raise InvalidTariffError(f"Invalid period_days: {period_days} for tariff {tariff}")

            # Admin-managed pricing (migration 069): применяем override
            # + global discount ПЕРЕД юзер-скидками (promo/vip/personal).
            # Combo/business с country идут по другому пути (get_biz_price)
            # — их не трогаем.
            if country is None:
                try:
                    from app.services import pricing as _pricing
                    _ep = await _pricing.get_effective_price(tariff, period_days)
                    if _ep is not None:
                        # Оригинал из config — для strikethrough в UI.
                        original_config_price_kopecks = int(config.TARIFFS[tariff][period_days]["price"] * 100)
                        # Если override или global-discount изменили цену,
                        # передаём в БД как base_price_override → юзер-скидки
                        # применятся поверх нашей.
                        if _ep.effective != int(config.TARIFFS[tariff][period_days]["price"]):
                            base_price_override_rubles = _ep.effective
                            pricing_reason = _ep.discount_reason
                            pricing_percent = _ep.discount_percent
                except Exception as _e:
                    logger.warning("pricing helper failed (fallback config): %s", _e)

        # Delegate to database layer
        result = await database.calculate_final_price(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            promo_code=promo_code,
            country=country,
            base_price_override_rubles=base_price_override_rubles
        )

        # Дополнительные поля для UI-рендера strikethrough.
        # Backward-compatible: старые вызовы читающие только base/final
        # продолжают работать.
        if original_config_price_kopecks is not None:
            result["original_config_price_kopecks"] = original_config_price_kopecks
            result["pricing_discount_reason"] = pricing_reason
            result["pricing_discount_percent"] = pricing_percent

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

async def create_subscription_purchase(
    telegram_id: int,
    tariff: str,
    period_days: int,
    price_kopecks: int,
    promo_code: Optional[str] = None,
    country: Optional[str] = None,
    is_combo: bool = False,
) -> str:
    """
    Create a pending subscription purchase record.
    Subscription purchases require tariff and period_days. Never use for balance top-up.
    
    Args:
        telegram_id: Telegram ID of the user
        tariff: Tariff type ("basic" or "plus")
        period_days: Subscription period in days (must be > 0)
        price_kopecks: Price in kopecks
        promo_code: Optional promo code used
        
    Returns:
        purchase_id: Unique purchase identifier
        
    Raises:
        InvalidTariffError: If tariff or period is invalid
        PurchaseCreationError: If purchase creation fails
    """
    try:
        if period_days <= 0:
            raise InvalidTariffError(f"Invalid period_days: {period_days} (subscription requires period_days > 0)")
        if tariff not in config.TARIFFS:
            raise InvalidTariffError(f"Invalid tariff: {tariff}")
        if period_days not in config.TARIFFS[tariff]:
            raise InvalidTariffError(f"Invalid period_days: {period_days} for tariff {tariff}")
        if price_kopecks <= 0:
            raise PurchaseCreationError(f"Invalid price: {price_kopecks} kopecks")

        # SECURITY: Block new subscription purchases when VPN is disabled
        # Balance top-ups and gifts are still allowed
        if not config.VPN_ENABLED:
            logger.warning(
                f"PURCHASE_BLOCKED_VPN_DISABLED user={telegram_id} tariff={tariff} period_days={period_days}"
            )
            raise PurchaseCreationError(
                "VPN service is temporarily unavailable. Please try again later."
            )

        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            promo_code=promo_code,
            country=country,
            is_combo=is_combo,
        )

        logger.info(
            f"SUBSCRIPTION_PURCHASE_CREATED purchase_id={purchase_id} telegram_id={telegram_id} "
            f"tariff={tariff} period_days={period_days} price={price_kopecks} kopecks"
        )
        return purchase_id

    except (InvalidTariffError, PurchaseCreationError):
        raise
    except Exception as e:
        logger.error(f"PURCHASE_CREATION_FAILED user={telegram_id} tariff={tariff} period_days={period_days} error={e}")
        raise PurchaseCreationError(f"Purchase creation failed: {e}") from e


async def create_balance_topup_purchase(
    telegram_id: int,
    amount_kopecks: int,
    currency: str = "RUB"
) -> str:
    """
    Create a pending balance top-up purchase. No tariff, no period_days.
    Separate from subscription logic. Invoice creation is caller's responsibility.
    
    Args:
        telegram_id: Telegram ID of the user
        amount_kopecks: Amount in kopecks
        currency: Currency code (default RUB)
        
    Returns:
        purchase_id: Unique purchase identifier
        
    Raises:
        PurchaseCreationError: If purchase creation fails
    """
    try:
        if amount_kopecks <= 0:
            raise PurchaseCreationError(f"Invalid amount: {amount_kopecks} kopecks")

        purchase_id = await database.create_pending_balance_topup_purchase(
            telegram_id=telegram_id,
            amount_kopecks=amount_kopecks
        )

        logger.info(
            f"BALANCE_TOPUP_PURCHASE_CREATED purchase_id={purchase_id} telegram_id={telegram_id} "
            f"amount={amount_kopecks} kopecks currency={currency}"
        )
        return purchase_id

    except PurchaseCreationError:
        raise
    except Exception as e:
        logger.error(f"PURCHASE_CREATION_FAILED user={telegram_id} purchase_type=balance_topup amount={amount_kopecks} error={e}")
        raise PurchaseCreationError(f"Balance top-up purchase creation failed: {e}") from e


# Backward compatibility alias


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
        payment_provider: Payment provider ("telegram_payment", "platega", etc.)
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
        
    except (
        PaymentAlreadyProcessed,
        PaymentAmountMismatch,
        PurchaseInvalidStatus,
        PurchaseLocked,
    ):
        # Доменные исключения слоя БД пробрасываем как есть.
        #
        # Каждое означает конкретную вещь, на которую вызывающий отвечает
        # по-своему: «платёж уже обработан» — вернуть провайдеру
        # already_processed и не повторять; «сумма не сошлась» — отказать и
        # позвать админа; «покупка заперта» — подождать и повторить.
        #
        # Если завернуть их в общий PaymentFinalizationError, все три
        # превращаются в «что-то пошло не так»: повторный вебхук получит
        # ошибку вместо идемпотентного ответа, а расхождение суммы потеряет
        # причину.
        raise
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
    DB returns naive UTC; use database._from_db_utc for boundary normalization.
    Domain layer must receive aware UTC only.
    
    Args:
        expires_at: Expiration date in various formats
        
    Returns:
        datetime object (timezone-aware UTC) or None
    """
    if expires_at is None:
        return None
    
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            return database._from_db_utc(expires_at)
        if expires_at.tzinfo != timezone.utc:
            return expires_at.astimezone(timezone.utc)
        return expires_at
    
    if isinstance(expires_at, str):
        # fromisoformat возвращает naive, если в строке нет смещения.
        # Домен работает только с aware UTC, поэтому нормализуем результат:
        # без нормализации сравнение с now падало с TypeError.
        try:
            return _ensure_utc(datetime.fromisoformat(expires_at.replace('Z', '+00:00')))
        except Exception as e:
            logger.debug("Date parse (Z format) failed: %s", e)
            try:
                return _ensure_utc(datetime.fromisoformat(expires_at))
            except Exception as e2:
                logger.debug("Date parse (plain) failed: %s", e2)
                return None
    
    return None


def _ensure_utc(moment: Optional[datetime]) -> Optional[datetime]:
    """Привести момент времени к aware UTC.

    Домен работает только с aware-датами, но `now` приходит извне и может
    оказаться naive — например из кода, который ещё не перевели на
    datetime.now(timezone.utc). Naive трактуем как UTC: так же, как
    database._from_db_utc трактует значения из БД.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


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
        subscription: Subscription dictionary from database (or None, or legacy int)
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        True if subscription is active, False otherwise
    """
    if not subscription:
        return False
    
    # Legacy fallback: если subscription это int (старый формат)
    if isinstance(subscription, int):
        return bool(subscription)
    
    # Если subscription не dict, возвращаем False
    if not isinstance(subscription, dict):
        logger.warning(f"is_subscription_active: unexpected subscription type: {type(subscription)}")
        return False
    
    now = _ensure_utc(now) or datetime.now(timezone.utc)

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
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        SubscriptionStatus with all status information
    """
    now = _ensure_utc(now) or datetime.now(timezone.utc)

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


# ====================================================================================
# Renewal Logic
# ====================================================================================
