"""Availability engine.

Availability is computed per parking, per vehicle type and per time window (half-open [start, end)).
A window is bookable only if the *peak* number of simultaneous bookings inside it is below the type's capacity.
"""
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.parkings.models import Parking

from .models import Booking

S = Booking.Status
HOLDING_STATUSES = (S.CONFIRMED, S.REFUND_REQUESTED, S.CHECKED_IN)
MAX_ADVANCE_DAYS = 60
# A car that is still checked in after its paid time keeps its bay. We don't know when it will leave, so we assume
# it stays for at least this much longer. Bookings starting later than that are unaffected; the owner's checkout
# (or the auto-complete job) releases the bay for good.
OVERSTAY_BUFFER = timedelta(minutes=60)


def blocking_filter(now):
    """Bookings that occupy a bay: paid/active ones and unexpired payment holds."""
    return Q(status__in=HOLDING_STATUSES) | Q(status=S.PENDING_PAYMENT, payment_hold_expires_at__gt=now)


def overlaps_window_filter(start, end):
    """DB-side prefilter. CHECKED_IN rows are kept even if end_at passed, because overstaying cars still hold a bay."""
    return Q(start_at__lt=end) & (Q(end_at__gt=start) | Q(status=S.CHECKED_IN))


def effective_end(status, end_at, now):
    return max(end_at, now + OVERSTAY_BUFFER) if status == S.CHECKED_IN else end_at


def peak_concurrency(intervals, start, end):
    """Maximum number of intervals overlapping at any instant inside [start, end)."""
    events = []
    for s, e in intervals:
        s, e = max(s, start), min(e, end)
        if s < e:
            events.append((s, 1))
            events.append((e, -1))
    events.sort(key=lambda ev: (ev[0], ev[1]))  # at equal instants, ends (-1) are processed before starts (+1)
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def bulk_spots_left(vehicle_types, start, end, now=None):
    """{vehicle_type_id: free bays for the whole window}. One query for any number of types."""
    now = now or timezone.now()
    types = list(vehicle_types)
    if not types:
        return {}
    rows = (Booking.objects.filter(vehicle_type_id__in=[t.pk for t in types])
            .filter(blocking_filter(now)).filter(overlaps_window_filter(start, end))
            .values_list("vehicle_type_id", "status", "start_at", "end_at"))
    grouped = defaultdict(list)
    for type_id, status, s, e in rows:
        grouped[type_id].append((s, effective_end(status, e, now)))
    return {t.pk: max(0, t.capacity - peak_concurrency(grouped[t.pk], start, end)) for t in types}


def spots_left(vehicle_type, start, end, now=None):
    return bulk_spots_left([vehicle_type], start, end, now)[vehicle_type.pk]


def is_window_available(vehicle_type, start, end, now=None):
    return spots_left(vehicle_type, start, end, now) >= 1


def within_opening_hours(parking, start, end):
    o, c = parking.opening_time, parking.closing_time
    if parking.is_24x7 or not (o and c) or o == c:
        return True
    tz = timezone.get_current_timezone()
    s = timezone.localtime(start, tz)

    def at(day, t):
        return timezone.make_aware(datetime.combine(day, t), tz)

    if o < c:
        return at(s.date(), o) <= start and end <= at(s.date(), c)
    # overnight opening (for example 22:00 to 06:00): the whole stay must fit inside one session
    if s.time() >= o:
        session = s.date()
    elif s.time() < c:
        session = s.date() - timedelta(days=1)
    else:
        return False
    return at(session, o) <= start and end <= at(session + timedelta(days=1), c)


def window_problem(parking, start, end, now=None):
    """Parking-level reason a window can't be booked, or None."""
    now = now or timezone.now()
    if parking.status != Parking.Status.APPROVED:
        return "This parking is not available for booking."
    if not parking.is_open:
        return "This parking is closed for new bookings."
    if end <= start:
        return "End time must be after the start time."
    if start < now - timedelta(minutes=1):
        return "The start time is in the past."
    if start > now + timedelta(days=MAX_ADVANCE_DAYS):
        return f"You can book up to {MAX_ADVANCE_DAYS} days ahead."
    if not within_opening_hours(parking, start, end):
        return f"Your stay must fit inside the opening hours ({parking.opening_hours_text})."
    return None
