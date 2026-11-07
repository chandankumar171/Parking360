"""Thin wrapper around Razorpay. Everything that talks to the network lives here so tests can mock it."""
import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    """A user-presentable payment gateway failure."""


def configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def to_paise(amount):
    return int((Decimal(amount) * 100).quantize(Decimal("1")))


def _client():
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def create_order(amount, receipt, notes=None):
    if not configured():
        raise GatewayError("Online payments are not configured yet. Please contact support.")
    try:
        return _client().order.create({
            "amount": to_paise(amount), "currency": "INR", "receipt": str(receipt)[:40],
            "payment_capture": 1, "notes": notes or {},
        })
    except Exception as exc:
        logger.exception("Razorpay order creation failed")
        raise GatewayError("We could not start the payment. Please try again in a moment.") from exc


def refund_payment(payment_id, amount, receipt, notes=None):
    if not configured():
        raise GatewayError("Online payments are not configured.")
    try:
        return _client().payment.refund(payment_id, {
            "amount": to_paise(amount), "speed": "normal", "receipt": str(receipt)[:40], "notes": notes or {},
        })
    except Exception as exc:
        logger.exception("Razorpay refund failed for %s", payment_id)
        message = getattr(exc, "args", [""])[0] if getattr(exc, "args", None) else str(exc)
        raise GatewayError(f"Razorpay could not process the refund: {str(message)[:150]}") from exc


def _hmac(secret, message):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(order_id, payment_id, signature):
    """Signature Razorpay Checkout returns to the browser: HMAC_SHA256(order_id|payment_id, key_secret)."""
    if not (settings.RAZORPAY_KEY_SECRET and order_id and payment_id and signature):
        return False
    expected = _hmac(settings.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body, signature):
    if not (settings.RAZORPAY_WEBHOOK_SECRET and signature):
        return False
    return hmac.compare_digest(_hmac(settings.RAZORPAY_WEBHOOK_SECRET, body), signature)
