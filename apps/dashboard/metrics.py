"""Everything the dashboards show. Plain functions that return JSON-ready dicts.

Money for owners is read from the ledger (what the owner actually earned, after the platform fee and refunds).
Time series are bucketed in Python on local dates, so nothing depends on MySQL timezone tables.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.parkings.models import Parking
from apps.payments.models import OwnerLedger, Payment, Refund
from apps.payments.services import owner_balance
from apps.reviews.models import ParkingReview

from .periods import Buckets, previous_period

S = Booking.Status
T = OwnerLedger.Type
PAID = [S.CONFIRMED, S.CHECKED_IN, S.COMPLETED, S.CANCELLED, S.NO_SHOW, S.REFUND_REQUESTED]
OCCUPYING = [S.CONFIRMED, S.CHECKED_IN, S.COMPLETED]
ZERO = Decimal("0")
CATEGORY_LABELS = dict(Booking._meta.get_field("vehicle_category").choices)
STATUS_LABELS = dict(S.choices)


def _r(x):
    return round(float(x or 0), 2)


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def sum_by_bucket(buckets, rows):
    out = [0.0] * len(buckets)
    for dt, value in rows:
        i = buckets.index_for(dt)
        if i is not None:
            out[i] += float(value or 0)
    return [round(v, 2) for v in out]


def count_by_bucket(buckets, datetimes):
    return [int(v) for v in sum_by_bucket(buckets, ((dt, 1) for dt in datetimes))]


def top_n(mapping, n=8):
    items = sorted(((k, round(float(v), 2)) for k, v in mapping.items()), key=lambda kv: kv[1], reverse=True)[:n]
    return {"labels": [k for k, _ in items], "data": [v for _, v in items]}


def open_hours_per_day(parking):
    o, c = parking.opening_time, parking.closing_time
    if parking.is_24x7 or not (o and c) or o == c:
        return 24.0
    mins = ((c.hour * 60 + c.minute) - (o.hour * 60 + o.minute)) % (24 * 60)
    return mins / 60


def bay_hours_per_day(parkings):
    """Total bay-hours the parkings can sell per day (capacity x opening hours)."""
    total = 0.0
    for p in parkings:
        cap = sum(t.capacity for t in p.vehicle_types.all() if t.is_active)
        total += cap * open_hours_per_day(p)
    return total


def occupancy(buckets, intervals, per_day):
    """(overall %, [% per bucket]) from booked bay-hours against sellable bay-hours."""
    n = len(buckets)
    booked = [0.0] * n
    for s, e in intervals:
        s, e = max(s, buckets.period.start), min(e, buckets.period.end)
        if s >= e:
            continue
        i = buckets.index_at(s)
        while i < n and buckets.items[i].start < e:
            b = buckets.items[i]
            booked[i] += max(0.0, (min(e, b.end) - max(s, b.start)).total_seconds() / 3600)
            i += 1
    series = [min(100.0, _pct(booked[i], per_day * buckets.items[i].days)) for i in range(n)]
    return min(100.0, _pct(sum(booked), per_day * buckets.period.days)), series


def _meta(period):
    return {"label": period.label, "group": period.group, "mode": period.mode,
            "start": period.start_date.isoformat(), "end": period.last_date.isoformat()}


def _delta(now, before):
    if not before:
        return None if not now else 100.0
    return round(100 * (now - before) / before, 1)


# ------------------------------------------------------------------ owner
def owner_parkings(owner, parking_id=None):
    qs = Parking.objects.filter(owner=owner).prefetch_related("vehicle_types")
    if parking_id:
        picked = qs.filter(pk=parking_id)
        if picked.exists():  # someone else's id (or junk) quietly falls back to "all my parkings"
            return list(picked)
    return list(qs)


def _owner_totals(owner, pids, period):
    """Net earnings and booking count for a period (also used for the previous-period comparison)."""
    led = OwnerLedger.objects.filter(owner=owner, booking__parking_id__in=pids,
                                     created_at__gte=period.start, created_at__lt=period.end)
    sums = {t: v or ZERO for t, v in led.values_list("entry_type").annotate(s=Sum("amount"))}
    bookings = Booking.objects.filter(parking_id__in=pids, status__in=PAID, start_at__gte=period.start, start_at__lt=period.end).count()
    return sums.get(T.CREDIT, ZERO) - sums.get(T.REFUND_DEBIT, ZERO), bookings


def owner_overview(owner, period, parking_id=None):
    parkings = owner_parkings(owner, parking_id)
    pids = [p.pk for p in parkings]
    buckets = Buckets(period)
    window = dict(created_at__gte=period.start, created_at__lt=period.end)

    ledger = OwnerLedger.objects.filter(owner=owner, booking__parking_id__in=pids, **window)
    credits = list(ledger.filter(entry_type=T.CREDIT).values_list("created_at", "amount", "payment__kind", "booking__parking__name"))
    refunds = list(ledger.filter(entry_type=T.REFUND_DEBIT).values_list("created_at", "amount", "booking__parking__name"))

    net_series = [round(c - r, 2) for c, r in zip(sum_by_bucket(buckets, ((d, a) for d, a, _k, _p in credits)),
                                                  sum_by_bucket(buckets, ((d, a) for d, a, _p in refunds)))]
    base_series = sum_by_bucket(buckets, ((d, a) for d, a, k, _p in credits if k != Payment.Kind.OVERTIME))
    overtime_series = sum_by_bucket(buckets, ((d, a) for d, a, k, _p in credits if k == Payment.Kind.OVERTIME))
    by_parking = defaultdict(Decimal)
    for _d, a, _k, name in credits:
        by_parking[name] += a
    for _d, a, name in refunds:
        by_parking[name] -= a

    rows = list(Booking.objects.filter(parking_id__in=pids, status__in=PAID, start_at__gte=period.start, start_at__lt=period.end)
                .values_list("start_at", "status", "vehicle_category"))
    status_counts, vehicle_counts, peak = defaultdict(int), defaultdict(int), [0] * 24
    for start, status, category in rows:
        status_counts[status] += 1
        vehicle_counts[category] += 1
        if status in OCCUPYING:
            peak[timezone.localtime(start).hour] += 1
    total = len(rows)

    intervals = list(Booking.objects.filter(parking_id__in=pids, status__in=OCCUPYING, start_at__lt=period.end, end_at__gt=period.start)
                     .values_list("start_at", "end_at"))
    occ_overall, occ_series = occupancy(buckets, intervals, bay_hours_per_day(parkings))

    reviews = list(ParkingReview.objects.filter(parking_id__in=pids, **window).values_list("created_at", "rating"))
    sums, counts = [0.0] * len(buckets), [0] * len(buckets)
    for dt, rating in reviews:
        i = buckets.index_for(dt)
        if i is not None:
            sums[i] += rating
            counts[i] += 1
    rating_series = [round(s / c, 2) if c else None for s, c in zip(sums, counts)]
    rating_all = ParkingReview.objects.filter(parking_id__in=pids).aggregate(avg=Avg("rating"), n=Count("id"))

    net = sum(net_series)
    prev_net, prev_bookings = _owner_totals(owner, pids, previous_period(period))
    live_pids = {"parking_id__in": pids}
    kpis = [
        {"key": "earnings", "label": "Net earnings", "value": _r(net), "format": "money", "delta": _delta(net, float(prev_net)),
         "hint": "After platform fee and refunds"},
        {"key": "bookings", "label": "Bookings", "value": total, "format": "int", "delta": _delta(total, prev_bookings)},
        {"key": "active", "label": "Parked right now", "value": Booking.objects.filter(status=S.CHECKED_IN, **live_pids).count(), "format": "int"},
        {"key": "occupancy", "label": "Occupancy", "value": occ_overall, "format": "pct", "hint": "Booked bay-hours / available"},
        {"key": "rating", "label": "Average rating", "value": _r(rating_all["avg"]) if rating_all["n"] else None, "format": "rating",
         "hint": f"{rating_all['n']} review{'s' if rating_all['n'] != 1 else ''}"},
        {"key": "refunds", "label": "Refund requests", "value": Refund.objects.filter(status=Refund.Status.REQUESTED, booking__parking_id__in=pids).count(),
         "format": "int", "hint": "Waiting for you"},
        {"key": "owed", "label": "Owed to you", "value": _r(owner_balance(owner)), "format": "money", "hint": "Not yet paid out"},
        {"key": "cancel", "label": "Cancellation rate", "value": _pct(status_counts.get(S.CANCELLED, 0), total), "format": "pct"},
        {"key": "noshow", "label": "No-show rate", "value": _pct(status_counts.get(S.NO_SHOW, 0), total), "format": "pct"},
    ]
    return {
        "meta": _meta(period),
        "kpis": kpis,
        "charts": {
            "revenue": {"labels": buckets.labels, "series": [{"name": "Net earnings", "data": net_series}]},
            "bookings": {"labels": buckets.labels, "series": [{"name": "Bookings", "data": count_by_bucket(buckets, (r[0] for r in rows))}]},
            "overtime": {"labels": buckets.labels, "series": [{"name": "Booking revenue", "data": base_series}, {"name": "Overtime revenue", "data": overtime_series}]},
            "status": {"labels": [STATUS_LABELS[k] for k in status_counts], "data": list(status_counts.values())},
            "vehicle": {"labels": [CATEGORY_LABELS.get(k, k) for k in vehicle_counts], "data": list(vehicle_counts.values())},
            "parking_revenue": top_n(by_parking, 8),
            "peak_hours": {"labels": [f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}" for h in range(24)], "data": peak},
            "occupancy": {"labels": buckets.labels, "series": [{"name": "Occupancy %", "data": occ_series}]},
            "rating": {"labels": buckets.labels, "series": [{"name": "Average rating", "data": rating_series}]},
        },
    }


def owner_live(owner):
    from django.urls import reverse

    now = timezone.now()
    today = timezone.localdate()
    base = Booking.objects.filter(parking__owner=owner).select_related("parking", "driver")

    def row(b, **extra):
        return {"id": b.pk, "plate": b.vehicle_number_plate, "driver": b.driver.full_name, "parking": b.parking.name,
                "url": reverse("owner:booking_detail", args=[b.pk]), **extra}

    from .periods import _aware
    from datetime import timedelta

    day_start, day_end = _aware(today), _aware(today + timedelta(days=1))
    upcoming = base.filter(status=S.CONFIRMED, start_at__gte=day_start, start_at__lt=day_end).order_by("start_at")[:10]
    over = base.filter(status=S.CHECKED_IN, end_at__lt=now).order_by("end_at")[:10]
    refunds = Refund.objects.filter(booking__parking__owner=owner, status=Refund.Status.REQUESTED).select_related(
        "booking__parking", "booking__driver").order_by("created_at")[:10]
    recent = base.filter(status__in=PAID).order_by("-created_at")[:8]
    return {
        "upcoming": [row(b, time=timezone.localtime(b.start_at).strftime("%I:%M %p")) for b in upcoming],
        "overstaying": [row(b, minutes=int((now - b.end_at).total_seconds() // 60)) for b in over],
        "refunds": [row(r.booking, amount=_r(r.amount), url=reverse("owner:refunds")) for r in refunds],
        "recent": [row(b, status=b.get_status_display(), amount=_r(b.base_amount)) for b in recent],
    }


# ------------------------------------------------------------------ superadmin
def admin_overview(period):
    buckets = Buckets(period)
    window = dict(start_at__gte=period.start, start_at__lt=period.end)

    paid_rows = list(Payment.objects.filter(status__in=[Payment.Status.PAID, Payment.Status.PARTIALLY_REFUNDED, Payment.Status.REFUNDED],
                                            paid_at__gte=period.start, paid_at__lt=period.end).values_list("paid_at", "amount"))
    fee_rows = list(OwnerLedger.objects.filter(entry_type__in=[T.PLATFORM_FEE, T.FEE_REVERSAL], created_at__gte=period.start,
                                               created_at__lt=period.end).values_list("created_at", "amount", "entry_type"))
    refund_rows = list(Refund.objects.filter(status=Refund.Status.REFUNDED, processed_at__gte=period.start, processed_at__lt=period.end)
                       .values_list("processed_at", "amount"))
    signups = list(User.objects.filter(role__in=[User.Role.OWNER, User.Role.DRIVER], date_joined__gte=period.start,
                                       date_joined__lt=period.end).values_list("date_joined", "role"))
    bookings = list(Booking.objects.filter(status__in=PAID, **window).values_list("status", "parking__city"))
    ledger = list(OwnerLedger.objects.filter(entry_type__in=[T.CREDIT, T.REFUND_DEBIT], created_at__gte=period.start, created_at__lt=period.end)
                  .values_list("entry_type", "amount", "booking__parking__name", "owner_id"))

    gross = sum(a for _d, a in paid_rows)
    fees = [(d, a if k == T.PLATFORM_FEE else -a) for d, a, k in fee_rows]
    status_counts, cities = defaultdict(int), defaultdict(int)
    for status, city in bookings:
        status_counts[status] += 1
        cities[city] += 1
    by_parking, by_owner = defaultdict(Decimal), defaultdict(Decimal)
    for kind, amount, parking_name, owner_id in ledger:
        signed = amount if kind == T.CREDIT else -amount
        by_parking[parking_name] += signed
        by_owner[owner_id] += signed
    names = {u.pk: (getattr(getattr(u, "owner_profile", None), "business_name", "") or u.full_name)
             for u in User.objects.filter(pk__in=by_owner).select_related("owner_profile")}
    owner_totals = {names.get(k, f"Owner {k}"): v for k, v in by_owner.items()}

    owed = OwnerLedger.objects.values_list("entry_type").annotate(s=Sum("amount"))
    owed = {t: v or ZERO for t, v in owed}
    roles = {r["role"]: r["n"] for r in User.objects.values("role").annotate(n=Count("id"))}
    pk_status = {r["status"]: r["n"] for r in Parking.objects.values("status").annotate(n=Count("id"))}
    total = len(bookings)
    platform = sum(a for _d, a in fees)

    kpis = [
        {"key": "gross", "label": "Gross collected", "value": _r(gross), "format": "money"},
        {"key": "platform", "label": "Platform earnings", "value": _r(platform), "format": "money", "hint": "Fees after reversals"},
        {"key": "bookings", "label": "Bookings", "value": total, "format": "int"},
        {"key": "refunds", "label": "Refunds issued", "value": _r(sum(a for _d, a in refund_rows)), "format": "money"},
        {"key": "owed", "label": "Owed to owners", "value": _r(owed.get(T.CREDIT, ZERO) - owed.get(T.REFUND_DEBIT, ZERO) - owed.get(T.PAYOUT, ZERO)),
         "format": "money", "hint": "All time, not yet paid out"},
        {"key": "cancel", "label": "Cancellation rate", "value": _pct(status_counts.get(S.CANCELLED, 0), total), "format": "pct"},
        {"key": "users", "label": "Owners / drivers", "value": f"{roles.get('OWNER', 0)} / {roles.get('DRIVER', 0)}", "format": "text", "hint": "All time"},
        {"key": "live", "label": "Live parkings", "value": pk_status.get("APPROVED", 0), "format": "int", "hint": f"{pk_status.get('PENDING', 0)} awaiting verification"},
    ]
    owners_series = sum_by_bucket(buckets, ((d, 1) for d, r in signups if r == User.Role.OWNER))
    drivers_series = sum_by_bucket(buckets, ((d, 1) for d, r in signups if r == User.Role.DRIVER))
    return {
        "meta": _meta(period),
        "kpis": kpis,
        "charts": {
            "revenue": {"labels": buckets.labels, "series": [{"name": "Gross collected", "data": sum_by_bucket(buckets, paid_rows)},
                                                              {"name": "Platform earnings", "data": sum_by_bucket(buckets, fees)}]},
            "signups": {"labels": buckets.labels, "series": [{"name": "Owners", "data": owners_series}, {"name": "Drivers", "data": drivers_series}]},
            "refunds": {"labels": buckets.labels, "series": [{"name": "Refunded", "data": sum_by_bucket(buckets, refund_rows)}]},
            "status": {"labels": [STATUS_LABELS[k] for k in status_counts], "data": list(status_counts.values())},
            "cities": {"labels": [c for c, _ in sorted(cities.items(), key=lambda kv: -kv[1])[:8]],
                       "data": [n for _, n in sorted(cities.items(), key=lambda kv: -kv[1])[:8]]},
            "top_parkings": top_n(by_parking, 8),
            "top_owners": top_n(owner_totals, 8),
        },
    }
