"""Static UPI QR payments.

One UPI ID (settings.UPI_ID) receives every payment. The QR encodes a standard
``upi://pay`` link with the order amount and order number pre-filled, so any UPI
app (GPay, PhonePe, Paytm, BHIM...) can pay it. A plain UPI QR does not notify
the server, so the customer submits the UTR and an admin confirms it.
"""
import re
from urllib.parse import quote, urlencode

import segno
from django.conf import settings

UTR_RE = re.compile(r"^\d{12}$")


def is_configured() -> bool:
    return bool(settings.UPI_ID)


def uri_for(amount, note) -> str:
    params = {
        "pa": settings.UPI_ID,
        "pn": settings.UPI_PAYEE_NAME,
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tn": note,
    }
    # Keep "@" literal: some UPI apps fail to parse "%40" in the payee address.
    return "upi://pay?" + urlencode(params, safe="@", quote_via=quote)


def svg_for(uri: str) -> str:
    """Inline <svg> markup of a QR code for ``uri``."""
    return segno.make(uri, error="m").svg_inline(scale=6, border=2)


def payment_uri(order) -> str:
    return uri_for(order.total_amount, f"Order {order.order_number}")


def qr_svg(order) -> str:
    return svg_for(payment_uri(order))


def clean_utr(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
