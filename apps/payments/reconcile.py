"""Money integrity checks. Run `python manage.py reconcile_payments` after incidents and on a schedule.

Every rule here is something the payment code is *supposed* to guarantee, so any finding is a bug, a manual edit
in the database, or a half-finished operation that needs a human.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.bookings.models import Booking

from .models import OwnerLedger, Payment, Refund

PAID_STATES = (Payment.Status.PAID, Payment.Status.PARTIALLY_REFUNDED, Payment.Status.REFUNDED)
T = OwnerLedger.Type
ZERO = Decimal("0")


def run_reconciliation(now=None):
    """Return a list of {"code", "message", "ref"} findings. An empty list means everything adds up."""
    now = now or timezone.now()
    issues = []

    def add(code, message, ref=""):
        issues.append({"code": code, "message": message, "ref": ref})

    credits = dict(OwnerLedger.objects.filter(entry_type=T.CREDIT, payment__isnull=False)
                   .values_list("payment_id").annotate(t=Sum("amount")))
    debits = dict(OwnerLedger.objects.filter(entry_type=T.REFUND_DEBIT, refund__isnull=False)
                  .values_list("refund_id").annotate(t=Sum("amount")))
    refunded_bookings = set(Refund.objects.filter(status=Refund.Status.REFUNDED).values_list("booking_id", flat=True))

    # 1. every paid payment credited its owner exactly its share
    for p in Payment.objects.filter(status__in=PAID_STATES).select_related("booking"):
        got = credits.get(p.pk)
        if got is None:
            if p.booking.status == Booking.Status.EXPIRED and p.booking_id in refunded_bookings:
                continue  # paid after the hold lapsed and automatically refunded: never confirmed, never credited
            add("MISSING_CREDIT", f"Payment {p.razorpay_order_id} is paid but the owner was never credited.", f"payment:{p.pk}")
        elif got != p.owner_amount:
            add("CREDIT_MISMATCH", f"Payment {p.razorpay_order_id}: owner share is {p.owner_amount} but {got} was credited.", f"payment:{p.pk}")

    # 2. every completed refund reversed what had been credited (and nothing else)
    for r in Refund.objects.filter(status=Refund.Status.REFUNDED):
        credited = r.payment_id in credits
        if credited and r.pk not in debits:
            add("MISSING_REFUND_DEBIT", f"Refund {r.pk} is complete but the owner ledger was not reduced.", f"refund:{r.pk}")
        if not credited and r.pk in debits:
            add("DEBIT_WITHOUT_CREDIT", f"Refund {r.pk} reduced a ledger that was never credited.", f"refund:{r.pk}")

    # 3. balances: stored running balance equals the recomputed one, and nobody is owed a negative amount
    sums = defaultdict(lambda: defaultdict(lambda: ZERO))
    for owner_id, kind, total in OwnerLedger.objects.values_list("owner_id", "entry_type").annotate(t=Sum("amount")):
        sums[owner_id][kind] = total
    latest_ids = [row["last"] for row in OwnerLedger.objects.values("owner_id").annotate(last=Max("id"))]
    stored = dict(OwnerLedger.objects.filter(pk__in=latest_ids).values_list("owner_id", "balance_after"))
    for owner_id, by_type in sums.items():
        balance = by_type[T.CREDIT] - by_type[T.REFUND_DEBIT] - by_type[T.PAYOUT]
        if balance < 0:
            add("NEGATIVE_BALANCE", f"Owner {owner_id} has a negative balance ({balance}).", f"owner:{owner_id}")
        if stored.get(owner_id) != balance:
            add("BALANCE_DRIFT", f"Owner {owner_id}: ledger says {stored.get(owner_id)} but entries add up to {balance}.", f"owner:{owner_id}")

    # 4. bookings and payments agree
    paid_filter = Q(payments__kind=Payment.Kind.BOOKING, payments__status__in=PAID_STATES)
    live_statuses = [Booking.Status.CONFIRMED, Booking.Status.CHECKED_IN, Booking.Status.COMPLETED, Booking.Status.REFUND_REQUESTED]
    for b in Booking.objects.annotate(paid_n=Count("payments", filter=paid_filter)).filter(Q(status__in=live_statuses, paid_n=0) | Q(paid_n__gt=1)):
        code = "DUPLICATE_PAYMENT" if b.paid_n > 1 else "UNPAID_BOOKING"
        add(code, f"Booking #{b.pk} ({b.status}) has {b.paid_n} paid booking payment(s).", f"booking:{b.pk}")
    paid_by_booking = dict(Payment.objects.filter(status__in=PAID_STATES).values_list("booking_id").annotate(t=Sum("amount")))
    for pk, total_paid in Booking.objects.exclude(status__in=[Booking.Status.PENDING_PAYMENT, Booking.Status.EXPIRED]).values_list("pk", "total_paid"):
        expected = paid_by_booking.get(pk, ZERO)
        if total_paid != expected:
            add("TOTAL_PAID_MISMATCH", f"Booking #{pk}: total_paid is {total_paid} but payments add up to {expected}.", f"booking:{pk}")

    # 5. refunds that never finished
    stuck_before = now - timedelta(days=1)
    for r in Refund.objects.filter(status__in=[Refund.Status.APPROVED, Refund.Status.PROCESSING], updated_at__lt=stuck_before):
        add("STUCK_REFUND", f"Refund {r.pk} has been {r.status} for over a day. Check it in the Razorpay dashboard.", f"refund:{r.pk}")
    return issues
