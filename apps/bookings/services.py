import logging
import secrets  # noqa: F401  (booking codes are generated on payment in Phase 4)
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.parkings.models import ParkingVehicleType

from .availability import blocking_filter, bulk_spots_left, overlaps_window_filter, window_problem
from .models import Booking

logger = logging.getLogger(__name__)
S = Booking.Status
HOLD_MINUTES = 10
MAX_OPEN_HOLDS = 3


class BookingError(Exception):
    """A user-facing reason the booking could not be made."""


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def split_amount(base, fee_percent):
    fee = money(base * fee_percent / Decimal("100"))
    return fee, money(base - fee)


def expire_stale_holds(now=None):
    now = now or timezone.now()
    return Booking.objects.filter(status=S.PENDING_PAYMENT, payment_hold_expires_at__lte=now).update(
        status=S.EXPIRED, updated_at=now
    )


def create_booking(driver, vehicle_type, start_at, slots, plate, brand_model):
    """Create a PENDING_PAYMENT booking that holds a bay for HOLD_MINUTES.

    The vehicle-type row is locked (SELECT ... FOR UPDATE) so two drivers can never take the last bay.
    Prices are always recomputed here; nothing the browser sends is trusted.
    """
    now = timezone.now()
    with transaction.atomic():
        vt = ParkingVehicleType.objects.select_for_update().select_related("parking").get(pk=vehicle_type.pk)
        parking = vt.parking
        if not vt.is_active:
            raise BookingError("This vehicle type is not accepted right now.")
        if not vt.min_slots <= slots <= vt.max_slots:
            raise BookingError(f"Choose between {vt.min_slots} and {vt.max_slots} slots.")
        end_at = start_at + timedelta(minutes=vt.slot_minutes * slots)

        problem = window_problem(parking, start_at, end_at, now)
        if problem:
            raise BookingError(problem)

        open_holds = Booking.objects.filter(
            driver=driver, status=S.PENDING_PAYMENT, payment_hold_expires_at__gt=now).count()
        if open_holds >= MAX_OPEN_HOLDS:
            raise BookingError("You have several unpaid bookings. Pay for or release them before making another.")

        same_vehicle = (Booking.objects.filter(vehicle_number_plate=plate)
                        .filter(blocking_filter(now)).filter(overlaps_window_filter(start_at, end_at)))
        if same_vehicle.exists():
            raise BookingError("This vehicle already has a booking that overlaps this time.")

        if bulk_spots_left([vt], start_at, end_at, now)[vt.pk] < 1:
            raise BookingError("Sorry, this parking is fully booked for your time. Try another time or vehicle type.")

        fee_percent = SiteSettings.load().platform_fee_percent
        base = money(vt.total_price(slots))
        fee, owner_amount = split_amount(base, fee_percent)
        return Booking.objects.create(
            driver=driver, parking=parking, vehicle_type=vt, start_at=start_at, end_at=end_at, slots=slots,
            vehicle_category=vt.category, vehicle_number_plate=plate, vehicle_brand_model=brand_model.strip(),
            price_per_slot=vt.price_per_slot, slot_minutes=vt.slot_minutes,
            overtime_block_minutes=vt.overtime_block_minutes, overtime_charge=vt.overtime_charge,
            overtime_free_minutes=vt.overtime_free_minutes, grace_minutes=parking.grace_minutes,
            allow_cancellation=parking.allow_cancellation, cancel_window_minutes=parking.cancel_window_minutes,
            cancel_charge_percent=parking.cancellation_charge_percent if parking.allow_cancellation else Decimal("0"),
            base_amount=base, platform_fee_percent=fee_percent, platform_fee_amount=fee, owner_amount=owner_amount,
            status=S.PENDING_PAYMENT, payment_hold_expires_at=now + timedelta(minutes=HOLD_MINUTES),
        )


@transaction.atomic
def release_hold(booking_id, driver):
    booking = Booking.objects.select_for_update().get(pk=booking_id, driver=driver)
    if booking.status != S.PENDING_PAYMENT:
        raise BookingError("Only unpaid bookings can be released.")
    booking.status = S.EXPIRED
    booking.save(update_fields=["status", "updated_at"])
    return booking
