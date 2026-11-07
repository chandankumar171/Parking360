from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required
from apps.payments import services as pay
from apps.payments.models import OwnerLedger, Refund

from . import lifecycle
from .forms import ReasonForm, VerdictForm
from .models import Booking
from .services import BookingError

S = Booking.Status


def _own(request, pk):
    return get_object_or_404(
        Booking.objects.select_related("parking", "driver", "vehicle_type"), pk=pk, parking__owner=request.user)


def _back(pk):
    return redirect("owner:booking_detail", pk=pk)


@role_required("OWNER")
def bookings_list(request):
    qs = Booking.objects.filter(parking__owner=request.user).select_related("parking", "driver")
    status = request.GET.get("status", "")
    parking = request.GET.get("parking", "")
    q = request.GET.get("q", "").strip()
    if status in S.values:
        qs = qs.filter(status=status)
    if parking.isdigit():
        qs = qs.filter(parking_id=int(parking))
    if q:
        qs = qs.filter(Q(booking_code__iexact=q) | Q(vehicle_number_plate__icontains=q.replace(" ", "")) | Q(driver__full_name__icontains=q))
    qs = qs.exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED]) if not status else qs
    page = Paginator(qs.order_by("-start_at"), 20).get_page(request.GET.get("page"))
    return render(request, "owner/bookings_list.html", {
        "page": page, "q": q, "status": status, "parking": parking, "statuses": S.choices,
        "my_parkings": request.user.parkings.order_by("name")})


@role_required("OWNER")
def booking_detail(request, pk):
    b = _own(request, pk)
    now = timezone.now()
    ctx = {"b": b, "refunds": b.refunds.all(), "verdict": getattr(b, "verdict", None), "reason_form": ReasonForm(),
           "verdict_form": VerdictForm(), "driver": b.driver, "now": now}
    if b.status == S.CONFIRMED:
        opens, closes = lifecycle.checkin_window(b)
        ctx.update(window_open=opens <= now <= closes, opens=opens, closes=closes)
    if b.status == S.CHECKED_IN:
        paid = lifecycle.overtime_paid(b)
        if b.checkout_requested_at:
            extra, amount = b.overtime_minutes, b.overtime_amount
        else:
            extra, amount = lifecycle.compute_overtime(b, now)
        ctx.update(extra=extra, ot_amount=amount, ot_paid=paid, requested=bool(b.checkout_requested_at),
                   can_finish=bool(b.checkout_requested_at) and (amount == 0 or paid))
    ctx["pending_refund"] = b.refunds.filter(status=Refund.Status.REQUESTED).first()
    return render(request, "owner/booking_detail.html", ctx)


@role_required("OWNER")
def booking_status(request, pk):
    b = _own(request, pk)
    return JsonResponse({"status": b.status, "overtime_paid": lifecycle.overtime_paid(b)})


@role_required("OWNER")
def verify_code(request):
    if request.method == "POST":
        booking = lifecycle.find_by_code(request.user, request.POST.get("code", ""))
        if booking is None:
            messages.error(request, "No booking with that code at your parkings. Check the code and try again.")
        else:
            return _back(booking.pk)
    return render(request, "owner/verify.html")


def _act(request, pk, fn, success):
    try:
        fn()
        messages.success(request, success)
    except BookingError as exc:
        messages.error(request, str(exc))
    except Booking.DoesNotExist:
        messages.error(request, "Booking not found.")
    return _back(pk)


@role_required("OWNER")
@require_POST
def check_in(request, pk):
    _own(request, pk)
    return _act(request, pk, lambda: lifecycle.check_in(pk, request.user), "Checked in. The vehicle is now parked.")


@role_required("OWNER")
@require_POST
def checkout_start(request, pk):
    _own(request, pk)
    return _act(request, pk, lambda: lifecycle.request_checkout(pk, request.user), "Checkout started.")


@role_required("OWNER")
@require_POST
def checkout_complete(request, pk):
    _own(request, pk)
    form = VerdictForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose satisfied or not satisfied for this driver.")
        return _back(pk)
    return _act(request, pk, lambda: lifecycle.complete_checkout(
        pk, request.user, form.cleaned_data["satisfied"], form.cleaned_data["comment"]), "Checkout complete. Thank you.")


@role_required("OWNER")
@require_POST
def cancel_booking(request, pk):
    _own(request, pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give the driver a short reason (at least 3 characters).")
        return _back(pk)
    return _act(request, pk, lambda: pay.owner_cancel_booking(pk, request.user, form.cleaned_data["reason"]),
                "Booking cancelled. The refund was sent to Razorpay.")


# ------------------------------------------------------------------ refunds
REFUND_TABS = [
    ("pending", "Needs your decision", [Refund.Status.REQUESTED]),
    ("progress", "In progress", [Refund.Status.APPROVED, Refund.Status.PROCESSING]),
    ("failed", "Failed", [Refund.Status.FAILED]),
    ("done", "Completed", [Refund.Status.REFUNDED, Refund.Status.REJECTED]),
]


@role_required("OWNER")
def refunds_list(request):
    tab = request.GET.get("tab", "pending")
    tab = tab if tab in {t[0] for t in REFUND_TABS} else "pending"
    base = Refund.objects.filter(booking__parking__owner=request.user).select_related("booking", "booking__parking", "booking__driver")
    counts = {key: base.filter(status__in=statuses).count() for key, _l, statuses in REFUND_TABS}
    statuses = next(s for k, _l, s in REFUND_TABS if k == tab)
    page = Paginator(base.filter(status__in=statuses), 20).get_page(request.GET.get("page"))
    return render(request, "owner/refunds.html", {
        "page": page, "tab": tab, "tabs": [(k, l, counts[k]) for k, l, _s in REFUND_TABS], "reason_form": ReasonForm()})


def _refund_action(request, pk, fn, success):
    get_object_or_404(Refund, pk=pk, booking__parking__owner=request.user)
    try:
        refund = fn()
        if getattr(refund, "status", "") == Refund.Status.FAILED:
            messages.error(request, f"Razorpay could not process the refund: {refund.error_message}")
        else:
            messages.success(request, success)
    except BookingError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "owner:refunds")


@role_required("OWNER")
@require_POST
def refund_approve(request, pk):
    return _refund_action(request, pk, lambda: pay.approve_refund(pk, request.user), "Refund approved and sent to Razorpay.")


@role_required("OWNER")
@require_POST
def refund_reject(request, pk):
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give the driver a reason for declining.")
        return redirect(request.POST.get("next") or "owner:refunds")
    return _refund_action(request, pk, lambda: pay.reject_refund(pk, request.user, form.cleaned_data["reason"]), "Request declined.")


# ------------------------------------------------------------------ earnings
@role_required("OWNER")
def earnings(request):
    T = OwnerLedger.Type
    entries = OwnerLedger.objects.filter(owner=request.user)
    sums = {t: v or Decimal("0") for t, v in entries.values_list("entry_type").annotate(s=Sum("amount"))}
    page = Paginator(entries.select_related("booking"), 25).get_page(request.GET.get("page"))
    return render(request, "owner/earnings.html", {
        "balance": pay.owner_balance(request.user), "earned": sums.get(T.CREDIT, 0), "refunded": sums.get(T.REFUND_DEBIT, 0),
        "paid_out": sums.get(T.PAYOUT, 0), "fees": sums.get(T.PLATFORM_FEE, 0) - sums.get(T.FEE_REVERSAL, 0), "page": page})
