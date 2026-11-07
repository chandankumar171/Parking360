"""CSV exports. Streamed, UTF-8 with BOM (opens cleanly in Excel) and safe against spreadsheet formula injection."""
import csv

from django.http import StreamingHttpResponse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.payments.models import OwnerLedger, Payment

from .metrics import PAID, admin_overview, owner_overview


class _Echo:
    def write(self, value):
        return value


def safe(cell):
    """A text cell starting with = + - @ would be run as a formula by Excel/Sheets. Prefix it so it stays text."""
    if isinstance(cell, str) and cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + cell
    return cell


def loc(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""


def stream(filename, header, rows):
    writer = csv.writer(_Echo())

    def generate():
        yield "\ufeff"
        yield writer.writerow(header)
        for row in rows:
            yield writer.writerow([safe(c) for c in row])

    response = StreamingHttpResponse(generate(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


def _name(period, kind):
    return f"parkshare-{kind}-{period.start_date:%Y%m%d}-{period.last_date:%Y%m%d}.csv"


def _booking_rows(qs, with_owner=False):
    for b in qs.select_related("parking", "parking__owner", "driver").iterator():
        row = [b.pk, b.booking_code or "", b.parking.name, b.driver.full_name, b.vehicle_number_plate, b.vehicle_brand_model,
               loc(b.start_at), loc(b.end_at), b.get_status_display(), b.base_amount, b.platform_fee_amount, b.owner_amount,
               b.overtime_amount, b.total_paid]
        yield ([b.parking.owner.email] + row) if with_owner else row


BOOKING_HEADER = ["Booking ID", "Code", "Parking", "Driver", "Plate", "Vehicle", "Start", "End", "Status",
                  "Booking amount", "Platform fee", "Owner share", "Overtime", "Total paid"]


def owner_export(kind, owner, period, parkings):
    pids = [p.pk for p in parkings]
    if kind == "bookings":
        qs = Booking.objects.filter(parking_id__in=pids, status__in=PAID, start_at__gte=period.start, start_at__lt=period.end).order_by("start_at")
        return stream(_name(period, kind), BOOKING_HEADER, _booking_rows(qs))
    if kind == "earnings":
        qs = OwnerLedger.objects.filter(owner=owner, created_at__gte=period.start, created_at__lt=period.end)
        if len(pids) != owner.parkings.count():  # a single parking was chosen: only its entries
            qs = qs.filter(booking__parking_id__in=pids)
        rows = ((loc(e.created_at), e.get_entry_type_display(), e.booking.parking.name if e.booking_id else "", e.booking_id or "",
                 e.amount, e.balance_after, e.reference, e.note) for e in qs.select_related("booking__parking").order_by("created_at").iterator())
        return stream(_name(period, kind), ["Date", "Entry", "Parking", "Booking ID", "Amount", "Balance after", "Reference", "Note"], rows)
    if kind == "series":
        data = owner_overview(owner, period, pids[0] if len(pids) == 1 else None)["charts"]
        labels = data["revenue"]["labels"]
        cols = [data["revenue"]["series"][0]["data"], data["bookings"]["series"][0]["data"],
                data["overtime"]["series"][0]["data"], data["overtime"]["series"][1]["data"], data["occupancy"]["series"][0]["data"]]
        rows = ([labels[i]] + [c[i] for c in cols] for i in range(len(labels)))
        return stream(_name(period, kind), [period.group.capitalize(), "Net earnings", "Bookings", "Booking revenue", "Overtime revenue", "Occupancy %"], rows)
    return None


def admin_export(kind, period):
    if kind == "bookings":
        qs = Booking.objects.filter(status__in=PAID, start_at__gte=period.start, start_at__lt=period.end).order_by("start_at")
        return stream(_name(period, kind), ["Owner"] + BOOKING_HEADER, _booking_rows(qs, with_owner=True))
    if kind == "payments":
        qs = Payment.objects.filter(created_at__gte=period.start, created_at__lt=period.end).select_related("booking__parking").order_by("created_at")
        rows = ((loc(p.created_at), p.booking_id, p.booking.parking.name, p.get_kind_display(), p.amount, p.platform_fee_amount, p.owner_amount,
                 p.get_status_display(), p.razorpay_order_id, p.razorpay_payment_id or "") for p in qs.iterator())
        return stream(_name(period, kind), ["Created", "Booking ID", "Parking", "Type", "Amount", "Platform fee", "Owner share", "Status",
                                            "Razorpay order", "Razorpay payment"], rows)
    if kind == "series":
        charts = admin_overview(period)["charts"]
        labels = charts["revenue"]["labels"]
        cols = [charts["revenue"]["series"][0]["data"], charts["revenue"]["series"][1]["data"], charts["refunds"]["series"][0]["data"],
                charts["signups"]["series"][0]["data"], charts["signups"]["series"][1]["data"]]
        rows = ([labels[i]] + [c[i] for c in cols] for i in range(len(labels)))
        return stream(_name(period, kind), [period.group.capitalize(), "Gross collected", "Platform earnings", "Refunded",
                                            "New owners", "New drivers"], rows)
    return None
