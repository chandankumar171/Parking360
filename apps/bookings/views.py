from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from apps.accounts.permissions import role_required

from .models import Booking
from apps.payments import gateway, services as pay_services

from .forms import ReasonForm
from .lifecycle import overtime_paid
from .services import BookingError, expire_stale_holds, release_hold

S = Booking.Status
TABS = [
    ("upcoming", "Active and upcoming"),
    ("past", "Past"),
    ("cancelled", "Cancelled"),
]


@role_required("DRIVER")
def my_bookings(request):
    expire_stale_holds()
    tab = request.GET.get("tab", "upcoming")
    tab = tab if tab in dict(TABS) else "upcoming"
    qs = Booking.objects.filter(driver=request.user).select_related("parking", "vehicle_type")
    now = timezone.now()
    if tab == "upcoming":
        qs = qs.filter(Q(status__in=[S.CONFIRMED, S.CHECKED_IN, S.REFUND_REQUESTED])
                       | Q(status=S.PENDING_PAYMENT, payment_hold_expires_at__gt=now)).order_by("start_at")
    elif tab == "past":
        qs = qs.filter(status__in=[S.COMPLETED, S.NO_SHOW])
    else:
        qs = qs.filter(status__in=[S.CANCELLED, S.EXPIRED])
    return render(request, "bookings/list.html", {"bookings": qs[:100], "tab": tab, "tabs": TABS})


@role_required("DRIVER")
def booking_detail(request, pk):
    expire_stale_holds()
    booking = get_object_or_404(Booking.objects.select_related("parking", "vehicle_type"), pk=pk, driver=request.user)
    return render(request, "bookings/detail.html", {
        "booking": booking, "refunds": booking.refunds.all(), "razorpay_ready": gateway.configured(),
        "overtime_due": (booking.status == S.CHECKED_IN and booking.checkout_requested_at is not None
                         and booking.overtime_amount > 0 and not overtime_paid(booking)),
        "verdict": getattr(booking, "verdict", None),
    })


@role_required("DRIVER")
@require_POST
def release(request, pk):
    get_object_or_404(Booking, pk=pk, driver=request.user)
    try:
        release_hold(pk, request.user)
        messages.success(request, "Booking released. The bay is free again.")
    except BookingError as exc:
        messages.error(request, str(exc))
    return redirect("bookings:list")


@role_required("DRIVER")
@require_POST
@ratelimit(key="user", rate="20/h", method="POST", block=True)
def request_refund(request, pk):
    get_object_or_404(Booking, pk=pk, driver=request.user)
    form = ReasonForm(request.POST)
    try:
        pay_services.request_refund(pk, request.user, form.cleaned_data["reason"] if form.is_valid() else "")
        messages.success(request, "Cancellation requested. The owner will review it and you'll be notified.")
    except BookingError as exc:
        messages.error(request, str(exc))
    return redirect("bookings:detail", pk=pk)
