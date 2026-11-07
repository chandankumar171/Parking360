"""Gate flow: check-in, checkout with overtime, completion, no-shows and auto-completion."""
import math
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PointLog
from apps.accounts.points import apply_points
from apps.core.services import notify

from .models import Booking, DriverVerdict
from .services import BookingError, money

S = Booking.Status
CHECKIN_EARLY = timedelta(minutes=15)


def normalize_code(raw):
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())[:6]


def find_by_code(owner, raw_code):
    code = normalize_code(raw_code)
    if len(code) != 6:
        return None
    return Booking.objects.select_related("parking", "driver", "vehicle_type").filter(
        booking_code=code, parking__owner=owner).first()


def compute_overtime(booking, at):
    """(minutes late, charge). Uses the rule snapshotted on the booking, in whole blocks after the free minutes."""
    extra = max(0, math.ceil((at - booking.end_at).total_seconds() / 60))
    billable = max(0, extra - booking.overtime_free_minutes)
    blocks = math.ceil(billable / booking.overtime_block_minutes) if billable else 0
    return extra, money(booking.overtime_charge * blocks)


def overtime_paid(booking):
    return booking.payments.filter(kind="OVERTIME", status="PAID").exists()


def checkin_window(booking):
    return booking.start_at - CHECKIN_EARLY, booking.start_at + timedelta(minutes=booking.grace_minutes)


@transaction.atomic
def check_in(booking_id, owner):
    booking = Booking.objects.select_for_update().select_related("driver").get(pk=booking_id, parking__owner=owner)
    if booking.status == S.CHECKED_IN:
        raise BookingError("This vehicle is already checked in.")
    if booking.status != S.CONFIRMED:
        raise BookingError(f"This booking is {booking.get_status_display().lower()} and can't be checked in.")
    opens, closes = checkin_window(booking)
    now = timezone.now()
    if now < opens:
        raise BookingError(f"Too early. Check-in opens at {timezone.localtime(opens):%d %b, %I:%M %p}.")
    if now > closes:
        raise BookingError("The check-in grace period has passed, so this booking is a no-show.")
    booking.status, booking.checked_in_at = S.CHECKED_IN, now
    booking.save(update_fields=["status", "checked_in_at", "updated_at"])
    notify(booking.driver, "Checked in", f"{booking.parking.name}: your vehicle is parked.", reverse("bookings:detail", args=[booking.pk]))
    return booking


@transaction.atomic
def request_checkout(booking_id, owner):
    """Freeze the overtime at this moment. Zero overtime means the owner can finish straight away."""
    booking = Booking.objects.select_for_update().select_related("driver").get(pk=booking_id, parking__owner=owner)
    if booking.status != S.CHECKED_IN:
        raise BookingError("Only parked vehicles can be checked out.")
    if booking.checkout_requested_at:
        return booking
    now = timezone.now()
    booking.overtime_minutes, booking.overtime_amount = compute_overtime(booking, now)
    booking.checkout_requested_at = now
    booking.save(update_fields=["overtime_minutes", "overtime_amount", "checkout_requested_at", "updated_at"])
    if booking.overtime_amount > 0:
        notify(booking.driver, "Pay overtime to leave", f"₹{booking.overtime_amount} for {booking.overtime_minutes} extra minutes.",
               reverse("bookings:detail", args=[booking.pk]))
    return booking


@transaction.atomic
def complete_checkout(booking_id, owner, satisfied, comment):
    booking = Booking.objects.select_for_update().select_related("driver", "parking").get(pk=booking_id, parking__owner=owner)
    if booking.status != S.CHECKED_IN:
        raise BookingError("This booking is not checked in.")
    if not booking.checkout_requested_at:
        raise BookingError("Start the checkout first.")
    if booking.overtime_amount > 0 and not overtime_paid(booking):
        raise BookingError("The driver has not paid the overtime yet.")
    comment = (comment or "").strip()
    if not satisfied and len(comment) < 3:
        raise BookingError("Please say what went wrong, so the driver's record is fair.")
    now = timezone.now()
    booking.status, booking.checked_out_at = S.COMPLETED, now
    booking.save(update_fields=["status", "checked_out_at", "updated_at"])
    DriverVerdict.objects.create(booking=booking, owner=owner, driver=booking.driver, satisfied=satisfied, comment=comment[:500])
    apply_points(booking.driver, PointLog.Reason.SATISFIED if satisfied else PointLog.Reason.UNSATISFIED,
                 note=f"Booking #{booking.pk}")
    notify(booking.driver, "Checkout complete", f"Thanks for parking at {booking.parking.name}. You can now rate your stay.",
           reverse("bookings:detail", args=[booking.pk]))
    return booking


def mark_no_shows(now=None):
    """CONFIRMED bookings not checked in by start + grace become NO_SHOW (no refund, points penalty)."""
    now = now or timezone.now()
    count = 0
    candidates = Booking.objects.filter(status=S.CONFIRMED, start_at__lt=now).select_related("driver", "parking")
    for b in candidates:
        if now <= b.start_at + timedelta(minutes=b.grace_minutes):
            continue
        with transaction.atomic():
            locked = Booking.objects.select_for_update().get(pk=b.pk)
            if locked.status != S.CONFIRMED:
                continue
            locked.status = S.NO_SHOW
            locked.save(update_fields=["status", "updated_at"])
            apply_points(b.driver, PointLog.Reason.NO_SHOW, note=f"Booking #{b.pk}")
            notify(b.driver, "Marked as no-show", f"You did not arrive at {b.parking.name} in time. No refund applies.",
                   reverse("bookings:detail", args=[b.pk]))
            count += 1
    return count


def auto_complete(now=None, hours=None):
    """Owner never closed the stay: complete it after `hours` past the end time and penalise the missing verification."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=hours if hours is not None else settings.AUTO_COMPLETE_HOURS)
    count = 0
    for b in Booking.objects.filter(status=S.CHECKED_IN, end_at__lt=cutoff).select_related("driver", "parking"):
        with transaction.atomic():
            locked = Booking.objects.select_for_update().get(pk=b.pk)
            if locked.status != S.CHECKED_IN:
                continue
            locked.status, locked.checked_out_at = S.COMPLETED, now
            locked.save(update_fields=["status", "checked_out_at", "updated_at"])
            apply_points(b.driver, PointLog.Reason.NO_CHECKOUT_VERIFY, note=f"Booking #{b.pk}, checkout never verified")
            notify(b.parking.owner, "Stay closed automatically", f"{b.vehicle_number_plate} was never checked out.",
                   reverse("owner:booking_detail", args=[b.pk]))
            count += 1
    return count
