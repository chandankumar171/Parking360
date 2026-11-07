from datetime import timedelta

from django.utils import timezone

from apps.bookings.models import Booking
from apps.parkings.models import Parking, ParkingVehicleType

S = Booking.Status


# One fixed reference hour for the whole test run. Recomputing "now rounded to the hour" on every call made tests
# flaky when the run crossed an hour boundary (two bookings meant for the same window landed an hour apart).
# It is the next full hour, so future(0) and future(1) are always still ahead of "now" for the whole run.
_BASE = (timezone.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def future(hours=24, minutes=0):
    return _BASE + timedelta(hours=hours, minutes=minutes)


def mk_parking(owner, **kw):
    data = dict(owner=owner, name="City Park", description="Safe", address_line="1 Main Rd", area="Saheed Nagar",
                city="Bhubaneswar", state="Odisha", pincode="751007", status=Parking.Status.APPROVED,
                google_maps_url="https://www.google.com/maps/@20.29,85.84,17z")
    data.update(kw)
    return Parking.objects.create(**data)


def mk_type(parking, category="FOUR_WHEELER", capacity=2, price="50", **kw):
    data = dict(parking=parking, category=category, capacity=capacity, slot_minutes=60, price_per_slot=price,
                min_slots=1, max_slots=12, overtime_block_minutes=30, overtime_charge=100)
    data.update(kw)
    return ParkingVehicleType.objects.create(**data)


_plate = iter(range(1000, 9999))


def mk_booking(vt, driver, start, end, status=S.CONFIRMED, hold=None, plate=None):
    return Booking.objects.create(
        driver=driver, parking=vt.parking, vehicle_type=vt, start_at=start, end_at=end, slots=1,
        vehicle_category=vt.category, vehicle_number_plate=plate or f"OD02AB{next(_plate)}", vehicle_brand_model="Tata Nexon",
        price_per_slot=50, slot_minutes=60, overtime_block_minutes=30, overtime_charge=100, base_amount=50,
        owner_amount=50, status=status, payment_hold_expires_at=hold)
