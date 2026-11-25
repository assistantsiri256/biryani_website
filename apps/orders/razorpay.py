"""Razorpay integration (Orders API + Checkout + webhooks) without extra dependencies.

Security:
  * Checkout callback: HMAC-SHA256(key_secret, "<razorpay_order_id>|<razorpay_payment_id>")
  * Webhook:           HMAC-SHA256(webhook_secret, raw request body)
Both are compared in constant time. Keys come only from environment variables.
"""

import base64
import hashlib
import hmac
import json
import urllib.request

from django.conf import settings

API_BASE = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def to_paise(amount) -> int:
    return int((amount * 100).quantize(1))


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    token = base64.b64encode(f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:  # network / 4xx / 5xx
        raise RazorpayError(f"Razorpay request failed: {e}") from e


def create_order(amount, receipt: str, notes: dict) -> dict:
    """Create a Razorpay order for ``amount`` rupees and return the API response."""
    return _request("POST", "/orders", {"amount": to_paise(amount), "currency": "INR", "receipt": receipt[:40], "notes": notes})


def create_gateway_order(order) -> str:
    """Create (once) the Razorpay order for our order and return its id."""
    if order.gateway_order_id:
        return order.gateway_order_id
    data = _request("POST", "/orders", {
        "amount": to_paise(order.total_amount),
        "currency": "INR",
        "receipt": order.order_number,
        "notes": {"order_number": order.order_number},
    })
    order.gateway_order_id = data["id"]
    order.save(update_fields=["gateway_order_id"])
    return order.gateway_order_id


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_payment_signature(gateway_order_id: str, payment_id: str, signature: str) -> bool:
    if not (settings.RAZORPAY_KEY_SECRET and gateway_order_id and payment_id and signature):
        return False
    expected = _hmac(settings.RAZORPAY_KEY_SECRET, f"{gateway_order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    if not (settings.RAZORPAY_WEBHOOK_SECRET and signature):
        return False
    return hmac.compare_digest(_hmac(settings.RAZORPAY_WEBHOOK_SECRET, body), signature)
