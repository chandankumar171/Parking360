"""Payments, ledger and refunds. All money movement goes through here and is idempotent."""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.availability import bulk_spots_left, window_problem
from apps.bookings.models import Booking
from apps.bookings.services import BookingError, money
from apps.core.services import audit, notify
from apps.parkings.models import ParkingVehicleType

from . import gateway
from .gateway import GatewayError
from .models import OwnerLedger, Payment, Refund

logger = logging.getLogger(__name__)
S = Booking.Status
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
HOLD_AFTER_ORDER = timedelta(minutes=5)


# ------------------------------------------------------------------ ledger
def owner_balance(owner):
    rows = dict(OwnerLedger.objects.filter(owner=owner).values_list("entry_type").annotate(t=Sum("amount")))
    zero = Decimal("0")
    T = OwnerLedger.Type
    return rows.get(T.CREDIT, zero) - rows.get(T.REFUND_DEBIT, zero) - rows.get(T.PAYOUT, zero)


def ledger_entry(owner, entry_type, amount, *, booking=None, payment=None, refund=None, note="", reference="", by=None):
    """Must be called inside a transaction. Locks the owner row so balance_after stays consistent."""
    User.objects.select_for_update().get(pk=owner.pk)
    T = OwnerLedger.Type
    delta = {T.CREDIT: amount, T.REFUND_DEBIT: -amount, T.PAYOUT: -amount}.get(entry_type, Decimal("0"))
    return OwnerLedger.objects.create(
        owner=owner, booking=booking, payment=payment, refund=refund, entry_type=entry_type, amount=amount,
        balance_after=owner_balance(owner) + delta, note=note[:255], reference=reference[:100], created_by=by)


@transaction.atomic
def record_payout(owner, amount, reference, note, by):
    amount = money(amount)
    if amount <= 0:
        raise BookingError("Enter an amount greater than zero.")
    balance = owner_balance(User.objects.select_for_update().get(pk=owner.pk))
    if amount > balance:
        raise BookingError(f"This owner is only owed ₹{balance}.")
    entry = ledger_entry(owner, OwnerLedger.Type.PAYOUT, amount, note=note, reference=reference, by=by)
    audit(by, "payout.recorded", owner, f"Payout of ₹{amount} to {owner.email}.", reference=reference)
    notify(owner, "Payout recorded", f"₹{amount} was paid out to you. Reference: {reference}", reverse("owner:earnings"))
    return entry


# ------------------------------------------------------------------ creating orders
def _fee_split(booking, amount):
    fee = money(amount * booking.platform_fee_percent / Decimal("100"))
    return fee, money(amount - fee)


def create_booking_order(booking_id, driver):
    now = timezone.now()
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id, driver=driver)
        if booking.status != S.PENDING_PAYMENT or not booking.hold_active:
            raise BookingError("This booking's hold has expired. Please book again.")
        if booking.payment_hold_expires_at < now + HOLD_AFTER_ORDER:  # let a slow payment finish
            booking.payment_hold_expires_at = now + HOLD_AFTER_ORDER
            booking.save(update_fields=["payment_hold_expires_at", "updated_at"])
        amount = booking.base_amount
    order = gateway.create_order(amount, f"bk{booking.pk}", {"booking_id": booking.pk, "kind": "BOOKING"})
    fee, owner_amount = _fee_split(booking, amount)
    return Payment.objects.create(
        booking=booking, kind=Payment.Kind.BOOKING, razorpay_order_id=order["id"], amount=amount,
        platform_fee_amount=fee, owner_amount=owner_amount)


def create_overtime_order(booking_id, driver):
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id, driver=driver)
        if booking.status != S.CHECKED_IN or not booking.checkout_requested_at or booking.overtime_amount <= 0:
            raise BookingError("There is no overtime to pay for this booking.")
        if booking.payments.filter(kind=Payment.Kind.OVERTIME, status=Payment.Status.PAID).exists():
            raise BookingError("Overtime is already paid.")
        amount = booking.overtime_amount
    order = gateway.create_order(amount, f"ot{booking.pk}", {"booking_id": booking.pk, "kind": "OVERTIME"})
    fee, owner_amount = _fee_split(booking, amount)
    return Payment.objects.create(
        booking=booking, kind=Payment.Kind.OVERTIME, razorpay_order_id=order["id"], amount=amount,
        platform_fee_amount=fee, owner_amount=owner_amount)


# ------------------------------------------------------------------ settling payments (browser callback + webhook)
@transaction.atomic
def settle_payment(order_id, razorpay_payment_id, signature="", payload=None, amount_paise=None):
    """Mark an order paid. Safe to call any number of times, from the browser and from the webhook."""
    payment = Payment.objects.select_for_update().filter(razorpay_order_id=order_id).first()
    if payment is None:
        return None
    if amount_paise is not None and int(amount_paise) != gateway.to_paise(payment.amount):
        logger.error("Amount mismatch on %s: got %s paise, expected %s paise", order_id, amount_paise, gateway.to_paise(payment.amount))
        return None
    if payment.status in (Payment.Status.PAID, Payment.Status.PARTIALLY_REFUNDED, Payment.Status.REFUNDED):
        return payment
    payment.status = Payment.Status.PAID
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = signature or ""
    payment.paid_at = timezone.now()
    payment.failure_reason = ""
    if payload:
        payment.raw_payload = payload
    payment.save()
    if payment.kind == Payment.Kind.BOOKING:
        _confirm_booking(payment)
    else:
        _credit_overtime(payment)
    return payment


def _new_code():
    for _ in range(50):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not Booking.objects.filter(booking_code=code).exists():
            return code
    raise RuntimeError("Could not generate a unique booking code")


def _credit(payment, booking, note):
    owner = booking.parking.owner
    ledger_entry(owner, OwnerLedger.Type.CREDIT, payment.owner_amount, booking=booking, payment=payment, note=note)
    if payment.platform_fee_amount > 0:
        ledger_entry(owner, OwnerLedger.Type.PLATFORM_FEE, payment.platform_fee_amount, booking=booking,
                     payment=payment, note=note)


def _confirm_booking(payment):
    booking = Booking.objects.select_for_update().select_related("parking__owner", "driver").get(pk=payment.booking_id)
    if booking.status in (S.CONFIRMED, S.CHECKED_IN, S.COMPLETED, S.REFUND_REQUESTED):
        return  # already confirmed by the other path
    can_confirm = booking.status == S.PENDING_PAYMENT
    if booking.status == S.EXPIRED:  # paid after the hold lapsed: only confirm if the bay is still free
        vt = ParkingVehicleType.objects.select_for_update().get(pk=booking.vehicle_type_id)
        can_confirm = (window_problem(booking.parking, booking.start_at, booking.end_at) is None
                       and bulk_spots_left([vt], booking.start_at, booking.end_at)[vt.pk] >= 1)
    if not can_confirm:
        refund = Refund.objects.create(
            booking=booking, payment=payment, amount=payment.amount, status=Refund.Status.APPROVED,
            initiated_by_owner=True, reason="The bay was no longer available when your payment arrived.")
        notify(booking.driver, "Payment refunded", "The bay was taken before your payment arrived. You will be refunded in full.",
               reverse("bookings:detail", args=[booking.pk]))
        transaction.on_commit(lambda: process_refund(refund.pk))
        return
    for _ in range(5):  # retry on the (very unlikely) code collision
        try:
            with transaction.atomic():
                booking.booking_code = _new_code()
                booking.status = S.CONFIRMED
                booking.total_paid = payment.amount
                booking.payment_hold_expires_at = None
                booking.save(update_fields=["booking_code", "status", "total_paid", "payment_hold_expires_at", "updated_at"])
            break
        except IntegrityError:
            continue
    _credit(payment, booking, f"Booking #{booking.pk}")
    notify(booking.driver, "Booking confirmed", f"Your code is {booking.booking_code}. Show it at the gate.",
           reverse("bookings:detail", args=[booking.pk]))
    notify(booking.parking.owner, "New booking", f"{booking.vehicle_number_plate} · {booking.start_at:%d %b, %I:%M %p}",
           reverse("owner:booking_detail", args=[booking.pk]))


def _credit_overtime(payment):
    booking = Booking.objects.select_for_update().select_related("parking__owner", "driver").get(pk=payment.booking_id)
    booking.total_paid += payment.amount
    booking.save(update_fields=["total_paid", "updated_at"])
    _credit(payment, booking, f"Overtime, booking #{booking.pk}")
    notify(booking.parking.owner, "Overtime paid", f"₹{payment.amount} received for {booking.vehicle_number_plate}.",
           reverse("owner:booking_detail", args=[booking.pk]))


@transaction.atomic
def mark_payment_failed(order_id, reason=""):
    payment = Payment.objects.select_for_update().filter(razorpay_order_id=order_id).first()
    if payment and payment.status == Payment.Status.CREATED:
        payment.status = Payment.Status.FAILED
        payment.failure_reason = reason[:255]
        payment.save(update_fields=["status", "failure_reason"])
    return payment


# ------------------------------------------------------------------ refunds
def _booking_payment(booking):
    return booking.payments.filter(kind=Payment.Kind.BOOKING, status=Payment.Status.PAID).first()


def _active_refund(booking):
    return booking.refunds.filter(status__in=Refund.ACTIVE).first()


@transaction.atomic
def request_refund(booking_id, driver, reason=""):
    booking = Booking.objects.select_for_update().select_related("parking__owner").get(pk=booking_id, driver=driver)
    if booking.status != S.CONFIRMED:
        raise BookingError("Only confirmed bookings can be cancelled.")
    if not booking.allow_cancellation:
        raise BookingError("This parking does not allow cancellation.")
    if timezone.now() > booking.cancel_deadline:
        raise BookingError("The cancellation window for this booking has closed.")
    payment = _booking_payment(booking)
    if payment is None:
        raise BookingError("No payment found for this booking.")
    charge = money(payment.amount * booking.cancel_charge_percent / Decimal("100"))
    refund = Refund.objects.create(booking=booking, payment=payment, requested_by=driver, amount=payment.amount - charge,
                                   cancellation_charge=charge, reason=reason[:255])
    booking.status = S.REFUND_REQUESTED
    booking.save(update_fields=["status", "updated_at"])
    notify(booking.parking.owner, "Refund requested", f"{booking.vehicle_number_plate}: ₹{refund.amount} to refund.",
           reverse("owner:refunds"))
    return refund


@transaction.atomic
def reject_refund(refund_id, owner, reason):
    refund = Refund.objects.select_for_update().select_related("booking__parking", "booking__driver").get(
        pk=refund_id, booking__parking__owner=owner)
    if refund.status != Refund.Status.REQUESTED:
        raise BookingError("This request has already been handled.")
    refund.status = Refund.Status.REJECTED
    refund.rejection_reason = reason[:255]
    refund.save()
    booking = Booking.objects.select_for_update().get(pk=refund.booking_id)
    if booking.status == S.REFUND_REQUESTED:
        booking.status = S.CONFIRMED
        booking.save(update_fields=["status", "updated_at"])
    notify(booking.driver, "Refund request declined", reason, reverse("bookings:detail", args=[booking.pk]))
    return refund


def approve_refund(refund_id, owner):
    """Owner approves a driver's request (or retries a failed refund) and the Razorpay refund is issued."""
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund_id, booking__parking__owner=owner)
        if refund.status not in (Refund.Status.REQUESTED, Refund.Status.FAILED):
            raise BookingError("This refund can't be approved in its current state.")
        refund.status = Refund.Status.APPROVED
        refund.save(update_fields=["status", "updated_at"])
    return process_refund(refund_id)


def owner_cancel_booking(booking_id, owner, reason):
    """Owner cancels a paid booking: full refund, no penalty for the driver."""
    with transaction.atomic():
        booking = Booking.objects.select_for_update().select_related("driver").get(pk=booking_id, parking__owner=owner)
        if booking.status not in (S.CONFIRMED, S.REFUND_REQUESTED):
            raise BookingError("Only confirmed bookings that have not started can be cancelled.")
        payment = _booking_payment(booking)
        if payment is None:
            raise BookingError("No payment found for this booking.")
        refund = _active_refund(booking)
        if refund and refund.status in (Refund.Status.PROCESSING,):
            raise BookingError("A refund is already being processed.")
        if refund is None:
            refund = Refund(booking=booking, payment=payment)
        refund.amount, refund.cancellation_charge = payment.amount, Decimal("0")
        refund.status, refund.initiated_by_owner, refund.requested_by = Refund.Status.APPROVED, True, owner
        refund.reason = reason[:255]
        refund.save()
        booking.status = S.REFUND_REQUESTED  # keeps the bay held until Razorpay confirms
        booking.save(update_fields=["status", "updated_at"])
        notify(booking.driver, "Booking cancelled by the owner", f"{reason} Your refund is on its way.",
               reverse("bookings:detail", args=[booking.pk]))
    return process_refund(refund.pk)


def process_refund(refund_id):
    """Call the Razorpay Refund API exactly once per attempt. Network call happens outside any DB lock."""
    with transaction.atomic():
        refund = Refund.objects.select_for_update().select_related("payment").get(pk=refund_id)
        if refund.status not in (Refund.Status.APPROVED, Refund.Status.FAILED):
            return refund
        refund.status = Refund.Status.PROCESSING
        refund.retry_count += 1
        refund.error_message = ""
        refund.save(update_fields=["status", "retry_count", "error_message", "updated_at"])
        payment_id, amount, attempt = refund.payment.razorpay_payment_id, refund.amount, refund.retry_count
    try:
        resp = gateway.refund_payment(payment_id, amount, f"rf{refund_id}-{attempt}",
                                      {"refund_id": refund_id, "booking_id": refund.booking_id})
    except GatewayError as exc:
        with transaction.atomic():
            refund = Refund.objects.select_for_update().select_related("booking__parking__owner").get(pk=refund_id)
            if refund.status == Refund.Status.PROCESSING:
                refund.status, refund.error_message = Refund.Status.FAILED, str(exc)[:255]
                refund.save(update_fields=["status", "error_message", "updated_at"])
                notify(refund.booking.parking.owner, "Refund failed", str(exc)[:200], reverse("owner:refunds"))
        return refund
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(pk=refund_id)
        if not refund.razorpay_refund_id:
            refund.razorpay_refund_id = resp.get("id")
            refund.save(update_fields=["razorpay_refund_id", "updated_at"])
        if resp.get("status") == "processed":
            finalize_refund(refund_id)
    return Refund.objects.get(pk=refund_id)


@transaction.atomic
def finalize_refund(refund_id):
    """Razorpay confirmed the money went back: cancel the booking and reverse the ledger. Idempotent."""
    refund = Refund.objects.select_for_update().select_related("payment", "booking__parking__owner", "booking__driver").get(pk=refund_id)
    if refund.status == Refund.Status.REFUNDED:
        return refund
    payment, booking = refund.payment, Booking.objects.select_for_update().get(pk=refund.booking_id)
    now = timezone.now()
    refund.status, refund.processed_at, refund.error_message = Refund.Status.REFUNDED, now, ""
    refund.save()
    payment.status = Payment.Status.REFUNDED if refund.amount >= payment.amount else Payment.Status.PARTIALLY_REFUNDED
    payment.save(update_fields=["status"])
    if booking.status in (S.CONFIRMED, S.REFUND_REQUESTED):
        booking.status, booking.cancelled_at = S.CANCELLED, now
        booking.cancelled_by = refund.requested_by
        booking.cancel_reason = refund.reason[:255]
        booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])

    notify(refund.booking.driver, "Refund processed", f"₹{refund.amount} is on its way back to your original payment method.",
           reverse("bookings:detail", args=[booking.pk]))
    owner = refund.booking.parking.owner
    # Only reverse what was actually credited: a payment auto-refunded because the booking never confirmed has no credit.
    if OwnerLedger.objects.filter(payment=payment, entry_type=OwnerLedger.Type.CREDIT).exists():
        fee_back = money(refund.amount * payment.platform_fee_amount / payment.amount) if payment.amount else Decimal("0")
        ledger_entry(owner, OwnerLedger.Type.REFUND_DEBIT, refund.amount - fee_back, booking=booking, payment=payment,
                     refund=refund, note=f"Refund, booking #{booking.pk}")
        if fee_back > 0:
            ledger_entry(owner, OwnerLedger.Type.FEE_REVERSAL, fee_back, booking=booking, payment=payment, refund=refund,
                         note=f"Fee reversed, booking #{booking.pk}")
        notify(owner, "Refund completed", f"{booking.vehicle_number_plate}: ₹{refund.amount} refunded.", reverse("owner:refunds"))
    return refund


@transaction.atomic
def mark_refund_failed(refund_id, message=""):
    refund = Refund.objects.select_for_update().get(pk=refund_id)
    if refund.status in (Refund.Status.PROCESSING, Refund.Status.APPROVED):
        refund.status, refund.error_message = Refund.Status.FAILED, message[:255]
        refund.save(update_fields=["status", "error_message", "updated_at"])
    return refund
