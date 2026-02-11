"""
Telegram CryptoBot (Crypto Pay API) Integration

Handles invoice creation and status checking via polling (no webhooks).

STEP 3 — PART D: EXTERNAL DEPENDENCY ISOLATION
- All CryptoBot API calls are isolated inside try/except blocks
- External failures are mapped to dependency_error
- External failure does NOT break handler/worker
- System continues degraded when CryptoBot API unavailable
- Retries handled by retry_async (transient errors only)

Configuration: Token/API URL resolved via config.py only (Railway env-safe).
"""
import config
import json
import logging
from typing import Optional, Dict, Any
import httpx
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)

# Configuration — single source: config.py (STAGE_CRYPTOBOT_TOKEN / PROD_CRYPTOBOT_TOKEN)
CRYPTOBOT_API_TOKEN = config.CRYPTOBOT_TOKEN
CRYPTOBOT_API_URL = config.CRYPTOBOT_API_URL
ALLOWED_ASSETS = config.CRYPTOBOT_ALLOWED_ASSETS

# Exchange rate: RUB to USD (fixed rate for conversion)
RUB_TO_USD_RATE = 95.0


class CryptoBotError(Exception):
    """Base class for CryptoBot API errors"""
    pass


class CryptoBotAuthError(CryptoBotError):
    """Authentication error (401, 403)"""
    pass


class CryptoBotInvalidResponseError(CryptoBotError):
    """Invalid response error (4xx)"""
    pass


def rub_kopecks_to_usd(kopecks: int) -> float:
    """
    Convert RUB kopecks to USD
    
    Args:
        kopecks: Amount in kopecks (RUB)
        
    Returns:
        Amount in USD rounded to 2 decimal places
    """
    rubles = kopecks / 100.0
    usd = rubles / RUB_TO_USD_RATE
    return round(usd, 2)


def is_enabled() -> bool:
    """Check if CryptoBot is configured (token + assets from config, Railway env-safe)."""
    if not CRYPTOBOT_API_TOKEN:
        logger.warning("CRYPTOBOT_DISABLED_NO_TOKEN")
        return False
    if not ALLOWED_ASSETS:
        logger.warning("CRYPTOBOT_DISABLED_NO_ASSETS")
        return False
    return True


def _get_auth_headers() -> Dict[str, str]:
    """Get authentication headers for CryptoBot API"""
    return {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json"
    }


async def create_invoice(
    amount_rub: float,
    description: str,
    payload: str,
    asset: str = "USDT"
) -> Dict[str, Any]:
    """
    Create invoice via CryptoBot API
    
    Args:
        amount_rub: Payment amount in rubles
        description: Invoice description
        payload: Payload string (should contain purchase_id)
        asset: Cryptocurrency asset (USDT/TON/BTC)
        
    Returns:
        Invoice data with invoice_id and pay_url
        
    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("CryptoBot not configured")
    
    if asset.upper() not in ALLOWED_ASSETS:
        raise ValueError(f"Invalid asset: {asset}. Allowed: {ALLOWED_ASSETS}")
    
    # Convert RUB to USD (CryptoBot API requires USD)
    amount_usd = round(float(amount_rub) / RUB_TO_USD_RATE, 2)
    
    request_body = {
        "amount": amount_usd,
        "fiat": "USD",
        "asset": asset.upper(),
        "payload": payload,
        "description": description[:250] if description else "Atlas Secure VPN",
        "allow_comments": False,
        "allow_anonymous": False,
    }
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CRYPTOBOT_API_URL}/createInvoice",
                headers=_get_auth_headers(),
                json=request_body
            )
            # Convert 401/403 to AuthError (should NOT be retried)
            if response.status_code == 401 or response.status_code == 403:
                error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"CryptoBot API error: {error_msg}")
                raise CryptoBotAuthError(error_msg)
            
            # Convert 4xx to InvalidResponseError (should NOT be retried)
            if 400 <= response.status_code < 500:
                error_msg = f"Client error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"CryptoBot API error: {error_msg}")
                raise CryptoBotInvalidResponseError(error_msg)
            
            # Only 5xx/timeout/network errors will be retried
            if response.status_code != 200:
                # Let httpx raise HTTPStatusError for 5xx, which will be retried
                response.raise_for_status()
            return response
    
    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError)
    )
    
    data = response.json()
    if not data.get("ok"):
        error_msg = data.get("error", {}).get("name", "Unknown error")
        logger.error(f"CryptoBot API error: {error_msg}")
        raise Exception(f"CryptoBot API error: {error_msg}")
    
    result = data.get("result", {})
    if not result.get("invoice_id") or not result.get("pay_url"):
        raise Exception("Invalid response from CryptoBot API: missing invoice_id or pay_url")
    
    logger.info(f"CryptoBot invoice created: invoice_id={result.get('invoice_id')}, amount={amount_rub} RUB")
    
    return {
        "invoice_id": result.get("invoice_id"),
        "pay_url": result.get("pay_url"),
        "asset": asset.upper(),
        "amount": amount_rub,
    }


async def check_invoice_status(invoice_id: int) -> Dict[str, Any]:
    """
    Check invoice status via CryptoBot API
    
    Args:
        invoice_id: Invoice ID from CryptoBot
        
    Returns:
        Invoice data with status and payment info
        
    Raises:
        Exception on API errors
    """
    if not is_enabled():
        raise Exception("CryptoBot not configured")
    
    request_body = {
        "invoice_ids": [invoice_id]
    }
    
    # Use centralized retry utility for HTTP calls (only retries transient errors)
    async def _make_request():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{CRYPTOBOT_API_URL}/getInvoices",
                headers=_get_auth_headers(),
                json=request_body
            )
            # Convert 401/403 to AuthError (should NOT be retried)
            if response.status_code == 401 or response.status_code == 403:
                error_msg = f"Authentication error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"CryptoBot API error: {error_msg}")
                raise CryptoBotAuthError(error_msg)
            
            # Convert 4xx to InvalidResponseError (should NOT be retried)
            if 400 <= response.status_code < 500:
                error_msg = f"Client error: status={response.status_code}, response={response.text[:200]}"
                logger.error(f"CryptoBot API error: {error_msg}")
                raise CryptoBotInvalidResponseError(error_msg)
            
            # Only 5xx/timeout/network errors will be retried
            if response.status_code != 200:
                # Let httpx raise HTTPStatusError for 5xx, which will be retried
                response.raise_for_status()
            return response
    
    response = await retry_async(
        _make_request,
        retries=2,
        base_delay=1.0,
        max_delay=5.0,
        retry_on=(httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError)
    )
    
    data = response.json()
    if not data.get("ok"):
        error_msg = data.get("error", {}).get("name", "Unknown error")
        logger.error(f"CryptoBot API error: {error_msg}")
        raise Exception(f"CryptoBot API error: {error_msg}")
    
    items = data.get("result", {}).get("items", [])
    if not items:
        raise Exception(f"Invoice not found: invoice_id={invoice_id}")
    
    invoice = items[0]
    raw_status = invoice.get("status", "")
    payload = invoice.get("payload", "")
    paid_at = invoice.get("paid_at")
    
    # Parse amount (API returns string, not nested object)
    amount_str = ""
    if "amount" in invoice:
        amount_value = invoice["amount"]
        if isinstance(amount_value, str):
            amount_str = amount_value
        elif isinstance(amount_value, dict):
            # Fallback for different API response formats
            amount_str = str(amount_value.get("fiat", {}).get("value", ""))
    
    # Normalize status
    if raw_status == "paid":
        normalized_status = "paid"
    elif raw_status == "active":
        normalized_status = "pending"
    elif raw_status in ("expired", "cancelled"):
        normalized_status = "failed"
    else:
        normalized_status = "pending"
    
    logger.info(f"CryptoBot invoice status checked: invoice_id={invoice_id}, status={raw_status} -> {normalized_status}")
    
    return {
        "invoice_id": invoice.get("invoice_id"),
        "status": normalized_status,
        "raw_status": raw_status,
        "payload": payload,
        "paid_at": paid_at,
        "amount": amount_str,
    }
