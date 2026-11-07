"""Superadmin: every booking on the platform, with its payments, refunds and ledger trail."""
from datetime import date, datetime, time, timedelta

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.accounts.permissions import role_required
from apps.payments.models import OwnerLedger

from .models import Booking

S = Booking.Status


def _day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@role_required("SUPERADMIN")
def bookings_list(request):
    qs = Booking.objects.select_related("parking", "parking__owner", "driver")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    start, end = _day(request.GET.get("from")), _day(request.GET.get("to"))
    if q:
        qs = qs.filter(Q(booking_code__iexact=q) | Q(vehicle_number_plate__icontains=q.replace(" ", "").upper())
                       | Q(driver__full_name__icontains=q) | Q(driver__email__icontains=q)
                       | Q(parking__name__icontains=q) | Q(parking__owner__email__icontains=q))
    if status in S.values:
        qs = qs.filter(status=status)
    else:
        status = ""
        qs = qs.exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED])  # unpaid holds are noise unless asked for
    tz = timezone.get_current_timezone()
    if start:
        qs = qs.filter(start_at__gte=timezone.make_aware(datetime.combine(start, time.min), tz))
    if end:
        qs = qs.filter(start_at__lt=timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz))
    page = Paginator(qs.order_by("-start_at", "-id"), 25).get_page(request.GET.get("page"))
    return render(request, "superadmin/bookings.html", {
        "page": page, "q": q, "status": status, "statuses": S.choices,
        "date_from": start.isoformat() if start else "", "date_to": end.isoformat() if end else ""})


@role_required("SUPERADMIN")
def booking_detail(request, pk):
    b = get_object_or_404(Booking.objects.select_related("parking", "parking__owner", "driver", "vehicle_type"), pk=pk)
    return render(request, "superadmin/booking_detail.html", {
        "b": b, "payments": b.payments.all().order_by("id"), "refunds": b.refunds.all().order_by("id"),
        "ledger": OwnerLedger.objects.filter(booking=b).order_by("id"),
        "verdict": getattr(b, "verdict", None), "review": getattr(b, "review", None)})
