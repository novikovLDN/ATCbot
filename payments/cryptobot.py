"""
Telegram CryptoBot (Crypto Pay API) Integration

Handles invoice creation and status checking via polling (no webhooks).
"""
import os
import json
import logging
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger(__name__)

# Configuration
CRYPTOBOT_API_TOKEN = os.getenv("CRYPTOBOT_API_TOKEN", "")
CRYPTOBOT_API_URL = os.getenv("CRYPTOBOT_API_URL", "https://pay.crypt.bot/api")
CRYPTOBOT_ASSETS = os.getenv("CRYPTOBOT_ASSETS", "USDT,TON,BTC").split(",")

ALLOWED_ASSETS = [asset.strip().upper() for asset in CRYPTOBOT_ASSETS if asset.strip()]


def is_enabled() -> bool:
    """Check if CryptoBot is configured"""
    return bool(CRYPTOBOT_API_TOKEN and ALLOWED_ASSETS)


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
    
    request_body = {
        "amount": round(float(amount_rub), 2),
        "fiat": "RUB",
        "asset": asset.upper(),
        "payload": payload,
        "description": description[:250] if description else "Atlas Secure VPN",
        "allow_comments": False,
        "allow_anonymous": False,
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{CRYPTOBOT_API_URL}/createInvoice",
            headers=_get_auth_headers(),
            json=request_body
        )
    
    if response.status_code != 200:
        logger.error(f"CryptoBot API error: {response.status_code} - {response.text}")
        raise Exception(f"CryptoBot API error: {response.status_code} - {response.text}")
    
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
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{CRYPTOBOT_API_URL}/getInvoices",
            headers=_get_auth_headers(),
            json=request_body
        )
    
    if response.status_code != 200:
        logger.error(f"CryptoBot API error: {response.status_code} - {response.text}")
        raise Exception(f"CryptoBot API error: {response.status_code} - {response.text}")
    
    data = response.json()
    if not data.get("ok"):
        error_msg = data.get("error", {}).get("name", "Unknown error")
        logger.error(f"CryptoBot API error: {error_msg}")
        raise Exception(f"CryptoBot API error: {error_msg}")
    
    items = data.get("result", {}).get("items", [])
    if not items:
        raise Exception(f"Invoice not found: invoice_id={invoice_id}")
    
    invoice = items[0]
    status = invoice.get("status")
    
    logger.info(f"CryptoBot invoice status checked: invoice_id={invoice_id}, status={status}")
    
    return {
        "invoice_id": invoice.get("invoice_id"),
        "status": status,  # "paid", "active", "expired"
        "payload": invoice.get("payload"),
        "paid_at": invoice.get("paid_at"),
        "amount": invoice.get("amount", {}).get("fiat", {}).get("value"),
        "currency": invoice.get("amount", {}).get("fiat", {}).get("currency"),
    }
