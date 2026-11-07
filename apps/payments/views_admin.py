from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import role_required
from apps.bookings.services import BookingError

from . import services
from .models import OwnerLedger, Payment, Refund

T = OwnerLedger.Type


@role_required("SUPERADMIN")
def payments_list(request):
    tab = "refunds" if request.GET.get("tab") == "refunds" else "payments"
    q = request.GET.get("q", "").strip()
    if tab == "payments":
        qs = Payment.objects.select_related("booking", "booking__parking", "booking__driver")
        if q:
            qs = qs.filter(Q(razorpay_order_id__icontains=q) | Q(razorpay_payment_id__icontains=q)
                           | Q(booking__vehicle_number_plate__icontains=q) | Q(booking__booking_code__iexact=q))
    else:
        qs = Refund.objects.select_related("booking", "booking__parking", "booking__driver", "payment")
        if q:
            qs = qs.filter(Q(razorpay_refund_id__icontains=q) | Q(booking__vehicle_number_plate__icontains=q))
    sums = {t: v or Decimal("0") for t, v in OwnerLedger.objects.values_list("entry_type").annotate(s=Sum("amount"))}
    paid = Payment.objects.filter(status__in=["PAID", "PARTIALLY_REFUNDED", "REFUNDED"]).aggregate(s=Sum("amount"))["s"] or 0
    refunded = Refund.objects.filter(status="REFUNDED").aggregate(s=Sum("amount"))["s"] or 0
    return render(request, "superadmin/payments.html", {
        "tab": tab, "q": q, "page": Paginator(qs, 25).get_page(request.GET.get("page")),
        "gross": paid, "refunded": refunded, "platform_earnings": sums.get(T.PLATFORM_FEE, 0) - sums.get(T.FEE_REVERSAL, 0)})


@role_required("SUPERADMIN")
def payouts(request):
    rows = (OwnerLedger.objects.values("owner")
            .annotate(credit=Sum("amount", filter=Q(entry_type=T.CREDIT)),
                      refund=Sum("amount", filter=Q(entry_type=T.REFUND_DEBIT)),
                      paid=Sum("amount", filter=Q(entry_type=T.PAYOUT))))
    users = User.objects.select_related("owner_profile").in_bulk([r["owner"] for r in rows])
    owners = []
    for r in rows:
        credit, refund, paid = (r[k] or Decimal("0") for k in ("credit", "refund", "paid"))
        owners.append({"user": users[r["owner"]], "earned": credit, "refunded": refund, "paid": paid, "balance": credit - refund - paid})
    owners.sort(key=lambda o: o["balance"], reverse=True)
    recent = OwnerLedger.objects.filter(entry_type=T.PAYOUT).select_related("owner", "created_by")[:15]
    return render(request, "superadmin/payouts.html", {
        "owners": owners, "recent": recent, "total_owed": sum((o["balance"] for o in owners), Decimal("0"))})


@role_required("SUPERADMIN")
@require_POST
def record_payout(request):
    owner = User.objects.filter(pk=request.POST.get("owner") or 0, role=User.Role.OWNER).first()
    try:
        if owner is None:
            raise BookingError("Choose an owner.")
        reference = request.POST.get("reference", "").strip()
        if len(reference) < 3:
            raise BookingError("Add a payment reference (for example the UPI or bank transfer id).")
        try:
            amount = Decimal(request.POST.get("amount", ""))
        except Exception:
            raise BookingError("Enter a valid amount.")
        services.record_payout(owner, amount, reference, request.POST.get("note", ""), request.user)
        messages.success(request, f"Payout of ₹{amount} recorded for {owner.email}.")
    except BookingError as exc:
        messages.error(request, str(exc))
    return redirect("sadmin:payouts")
