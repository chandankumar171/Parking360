"""Superadmin: browse owners and drivers, inspect their history, enable or disable accounts."""
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required
from apps.bookings.models import Booking, DriverVerdict
from apps.core.services import audit
from apps.parkings import services as parking_services
from apps.parkings.models import Parking
from apps.payments.models import OwnerLedger
from apps.payments.services import owner_balance
from apps.reviews.models import ParkingReview

from .models import PointLog, User

S = Booking.Status
PAID = [S.CONFIRMED, S.CHECKED_IN, S.COMPLETED, S.CANCELLED, S.NO_SHOW, S.REFUND_REQUESTED]
ACTIVE = [S.CONFIRMED, S.CHECKED_IN, S.REFUND_REQUESTED]
PER_PAGE = 20


def _filtered(request, role):
    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "")
    qs = User.objects.filter(role=role)
    if state == "active":
        qs = qs.filter(is_active=True)
    elif state == "disabled":
        qs = qs.filter(is_active=False)
    if q:
        cond = Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        if role == User.Role.OWNER:
            cond |= Q(owner_profile__business_name__icontains=q)
        qs = qs.filter(cond)
    return qs, q, state


@role_required("SUPERADMIN")
def owners(request):
    qs, q, state = _filtered(request, User.Role.OWNER)
    qs = qs.select_related("owner_profile").annotate(
        parkings_total=Count("parkings", distinct=True),
        parkings_live=Count("parkings", filter=Q(parkings__status=Parking.Status.APPROVED), distinct=True))
    page = Paginator(qs.order_by("-date_joined"), PER_PAGE).get_page(request.GET.get("page"))
    rows = list(page.object_list)
    ids = [u.pk for u in rows]
    bookings = dict(Booking.objects.filter(parking__owner_id__in=ids, status__in=PAID).values_list("parking__owner_id").annotate(n=Count("id")))
    sums = {}
    for owner_id, kind, total in OwnerLedger.objects.filter(owner_id__in=ids).values_list("owner_id", "entry_type").annotate(t=Sum("amount")):
        sums.setdefault(owner_id, {})[kind] = total
    zero = Decimal("0")
    for u in rows:
        s = sums.get(u.pk, {})
        u.bookings_n = bookings.get(u.pk, 0)
        u.earned = s.get("CREDIT", zero) - s.get("REFUND_DEBIT", zero)
        u.owed = s.get("CREDIT", zero) - s.get("REFUND_DEBIT", zero) - s.get("PAYOUT", zero)
    return render(request, "superadmin/users_list.html", {"kind": "owners", "page": page, "rows": rows, "q": q, "state": state})


@role_required("SUPERADMIN")
def drivers(request):
    qs, q, state = _filtered(request, User.Role.DRIVER)
    qs = qs.annotate(
        bookings_n=Count("bookings", filter=Q(bookings__status__in=PAID), distinct=True),
        no_shows=Count("bookings", filter=Q(bookings__status=S.NO_SHOW), distinct=True),
        cancelled=Count("bookings", filter=Q(bookings__status=S.CANCELLED, bookings__cancelled_by_id=F("id")), distinct=True))
    page = Paginator(qs.order_by("-date_joined"), PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "superadmin/users_list.html", {"kind": "drivers", "page": page, "rows": list(page.object_list), "q": q, "state": state})


@role_required("SUPERADMIN")
def user_detail(request, pk):
    user = get_object_or_404(User.objects.select_related("owner_profile"), pk=pk, role__in=[User.Role.OWNER, User.Role.DRIVER])
    ctx = {"u": user}
    if user.role == User.Role.OWNER:
        parkings = list(Parking.objects.filter(owner=user).order_by("-created_at"))
        mine = Booking.objects.filter(parking__owner=user)
        sums = {k: v or Decimal("0") for k, v in OwnerLedger.objects.filter(owner=user).values_list("entry_type").annotate(t=Sum("amount"))}
        rating = ParkingReview.objects.filter(owner=user).aggregate(total=Sum("rating"), n=Count("id"))
        ctx.update(
            parkings=parkings, active_bookings=mine.filter(status__in=ACTIVE).count(),
            recent=mine.select_related("parking", "driver").exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED]).order_by("-start_at")[:10],
            earned=sums.get("CREDIT", Decimal("0")) - sums.get("REFUND_DEBIT", Decimal("0")), paid_out=sums.get("PAYOUT", Decimal("0")),
            balance=owner_balance(user),
            rating_avg=round(rating["total"] / rating["n"], 1) if rating["n"] else None, rating_n=rating["n"])
    else:
        mine = Booking.objects.filter(driver=user)
        ctx.update(
            vehicles=user.vehicles.all(), active_bookings=mine.filter(status__in=ACTIVE).count(),
            recent=mine.select_related("parking").exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED]).order_by("-start_at")[:10],
            stats=mine.aggregate(total=Count("id", filter=Q(status__in=PAID)), completed=Count("id", filter=Q(status=S.COMPLETED)),
                                 no_shows=Count("id", filter=Q(status=S.NO_SHOW)),
                                 cancelled=Count("id", filter=Q(status=S.CANCELLED, cancelled_by=user))),
            point_logs=PointLog.objects.filter(user=user)[:15],
            verdicts=DriverVerdict.objects.filter(driver=user).select_related("owner__owner_profile", "booking__parking")[:10])
    return render(request, "superadmin/user_detail.html", ctx)


@role_required("SUPERADMIN")
@require_POST
def user_toggle(request, pk):
    """Disable (or re-enable) an owner or driver account. Disabled users cannot log in and their sessions stop working."""
    user = get_object_or_404(User, pk=pk, role__in=[User.Role.OWNER, User.Role.DRIVER])
    reason = request.POST.get("reason", "").strip()[:200]
    with transaction.atomic():
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
            suspended = 0
            if user.role == User.Role.OWNER:  # their listings disappear from search straight away
                for parking in Parking.objects.filter(owner=user, status=Parking.Status.APPROVED):
                    parking_services.suspend(parking, request.user, "The owner account was disabled.")
                    suspended += 1
            audit(request.user, "user.disabled", user, f"{user.email} disabled.", reason=reason, suspended_parkings=suspended)
            messages.success(request, f"{user.full_name} was disabled." + (f" {suspended} live parking(s) were suspended." if suspended else ""))
        else:
            user.is_active = True
            user.save(update_fields=["is_active"])
            audit(request.user, "user.enabled", user, f"{user.email} enabled.", reason=reason)
            note = " Their suspended parkings stay suspended until you restore them from the Parkings page." if user.role == User.Role.OWNER else ""
            messages.success(request, f"{user.full_name} can log in again." + note)
    return redirect("sadmin:user_detail", pk=pk)
