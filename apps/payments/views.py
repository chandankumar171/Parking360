import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from apps.accounts.permissions import role_required
from apps.bookings.models import Booking
from apps.bookings.services import BookingError

from . import gateway, services
from .gateway import GatewayError
from .models import Payment, Refund, WebhookEvent

logger = logging.getLogger(__name__)


def _checkout_payload(request, payment):
    b = payment.booking
    user = request.user
    return {
        "ok": True, "key": settings.RAZORPAY_KEY_ID, "order_id": payment.razorpay_order_id,
        "amount": gateway.to_paise(payment.amount), "currency": "INR", "name": "ParkShare",
        "description": f"{b.parking.name} · {'Overtime' if payment.kind == Payment.Kind.OVERTIME else 'Parking booking'}",
        "prefill": {"name": user.full_name, "email": user.email, "contact": user.phone or ""},
        "verify_url": reverse("payments:verify"), "next_url": reverse("bookings:detail", args=[b.pk]),
    }


def _create(request, pk, creator):
    try:
        payment = creator(pk, request.user)
    except Booking.DoesNotExist:
        return JsonResponse({"ok": False, "message": "Booking not found."}, status=404)
    except (BookingError, GatewayError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)
    return JsonResponse(_checkout_payload(request, payment))


@role_required("DRIVER")
@require_POST
@ratelimit(key="user", rate="60/h", method="POST", block=True)
def booking_order(request, pk):
    return _create(request, pk, services.create_booking_order)


@role_required("DRIVER")
@require_POST
@ratelimit(key="user", rate="60/h", method="POST", block=True)
def overtime_order(request, pk):
    return _create(request, pk, services.create_overtime_order)


@role_required("DRIVER")
@require_POST
@ratelimit(key="user", rate="120/h", method="POST", block=True)
def verify(request):
    """Browser callback after Razorpay Checkout. The signature is checked on the server before anything is confirmed."""
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = request.POST
    if not hasattr(data, "get"):
        data = {}
    order_id = data.get("razorpay_order_id", "")
    payment_id = data.get("razorpay_payment_id", "")
    signature = data.get("razorpay_signature", "")
    payment = Payment.objects.select_related("booking").filter(razorpay_order_id=order_id, booking__driver=request.user).first()
    if payment is None:
        return JsonResponse({"ok": False, "message": "Payment not found."}, status=404)
    if not gateway.verify_checkout_signature(order_id, payment_id, signature):
        logger.warning("Bad checkout signature for order %s", order_id)
        return JsonResponse({"ok": False, "message": "We could not verify this payment. If money was deducted it will be refunded."}, status=400)
    services.settle_payment(order_id, payment_id, signature)
    return JsonResponse({"ok": True, "redirect": reverse("bookings:detail", args=[payment.booking_id])})


def _handle_event(data):
    event = data.get("event", "")
    payload = data.get("payload", {})
    pay = (payload.get("payment") or {}).get("entity", {})
    if event in ("payment.captured", "order.paid"):
        order_id = pay.get("order_id") or ((payload.get("order") or {}).get("entity") or {}).get("id")
        if order_id and pay.get("id"):
            services.settle_payment(order_id, pay["id"], payload=data, amount_paise=pay.get("amount"))
    elif event == "payment.failed":
        if pay.get("order_id"):
            services.mark_payment_failed(pay["order_id"], pay.get("error_description") or "Payment failed")
    elif event in ("refund.processed", "refund.failed"):
        entity = (payload.get("refund") or {}).get("entity", {})
        notes = entity.get("notes") or {}
        refund = Refund.objects.filter(razorpay_refund_id=entity.get("id")).first() if entity.get("id") else None
        if refund is None and isinstance(notes, dict) and notes.get("refund_id"):
            refund = Refund.objects.filter(pk=notes["refund_id"]).first()
        if refund is None:
            return
        if entity.get("id") and not refund.razorpay_refund_id:
            Refund.objects.filter(pk=refund.pk, razorpay_refund_id__isnull=True).update(razorpay_refund_id=entity["id"])
        if event == "refund.processed":
            services.finalize_refund(refund.pk)
        else:
            services.mark_refund_failed(refund.pk, "Razorpay reported the refund as failed.")


@csrf_exempt
@require_POST
def webhook(request):
    if not gateway.verify_webhook_signature(request.body, request.headers.get("X-Razorpay-Signature", "")):
        return HttpResponse(status=400)
    try:
        data = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)
    event_id = request.headers.get("X-Razorpay-Event-Id", "")
    try:
        with transaction.atomic():  # the dedupe row rolls back with the work if handling fails, so Razorpay's retry runs again
            if event_id:
                WebhookEvent.objects.create(event_id=event_id, event=data.get("event", "")[:60])
            _handle_event(data)
    except IntegrityError:
        pass  # already processed
    return HttpResponse(status=200)
