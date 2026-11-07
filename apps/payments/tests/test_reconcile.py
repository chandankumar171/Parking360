from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.tests.helpers import future
from apps.payments import services
from apps.payments.models import OwnerLedger, Payment, Refund
from apps.payments.reconcile import run_reconciliation
from apps.payments.tests.test_phase4 import PayBase


def codes():
    return {i["code"] for i in run_reconciliation()}


class ReconcileTests(PayBase):
    def test_clean_through_pay_refund_and_payout(self):
        self.assertEqual(run_reconciliation(), [])
        b, _ = self.paid()
        b2, _ = self.paid(start=future(72), plate="OD02AB0002")
        self.assertEqual(run_reconciliation(), [])
        refund = services.request_refund(b.pk, self.driver, "x")
        services.approve_refund(refund.pk, self.owner)
        services.record_payout(self.owner, Decimal("40"), "UPI-1", "", self.admin)
        self.assertEqual(run_reconciliation(), [])

    def test_auto_refunded_late_payment_is_not_a_finding(self):
        from apps.bookings.tests.helpers import mk_booking

        b = self.hold()
        payment = services.create_booking_order(b.pk, self.driver)
        Booking.objects.filter(pk=b.pk).update(status="EXPIRED", payment_hold_expires_at=None)
        mk_booking(self.vt, self.driver2, b.start_at, b.end_at)
        with self.captureOnCommitCallbacks(execute=True):
            services.settle_payment(payment.razorpay_order_id, "pay_late", "sig")
        self.assertEqual(Refund.objects.get(booking=b).status, "REFUNDED")
        self.assertNotIn("MISSING_CREDIT", codes())
        self.assertNotIn("DEBIT_WITHOUT_CREDIT", codes())

    def test_detects_missing_and_wrong_credit(self):
        b, p = self.paid()
        entry = OwnerLedger.objects.get(payment=p, entry_type="CREDIT")
        OwnerLedger.objects.filter(pk=entry.pk).update(amount=Decimal("90"))
        self.assertIn("CREDIT_MISMATCH", codes())
        entry.delete()
        found = codes()
        self.assertIn("MISSING_CREDIT", found)

    def test_detects_balance_drift_and_negative_balance(self):
        self.paid()
        OwnerLedger.objects.create(owner=self.owner, entry_type="PAYOUT", amount=Decimal("500"), balance_after=Decimal("-400"))
        self.assertIn("NEGATIVE_BALANCE", codes())
        OwnerLedger.objects.filter(entry_type="PAYOUT").update(balance_after=Decimal("123"))
        self.assertIn("BALANCE_DRIFT", codes())

    def test_detects_booking_payment_disagreements(self):
        b, p = self.paid()
        Booking.objects.filter(pk=b.pk).update(total_paid=Decimal("1"))
        self.assertIn("TOTAL_PAID_MISMATCH", codes())
        Booking.objects.filter(pk=b.pk).update(total_paid=Decimal("100"))
        Payment.objects.create(booking=b, kind="BOOKING", razorpay_order_id="order_dup", razorpay_payment_id="pay_dup", amount=Decimal("100"),
                               status="PAID", owner_amount=Decimal("100"))
        found = codes()
        self.assertIn("DUPLICATE_PAYMENT", found)
        self.assertIn("MISSING_CREDIT", found)

    def test_unpaid_confirmed_booking(self):
        b, p = self.paid()
        Payment.objects.filter(pk=p.pk).update(status="FAILED")
        self.assertIn("UNPAID_BOOKING", codes())

    def test_stuck_refund(self):
        self.refund_result = {"status": "pending"}
        b, _ = self.paid()
        refund = services.request_refund(b.pk, self.driver, "x")
        services.approve_refund(refund.pk, self.owner)
        self.assertNotIn("STUCK_REFUND", codes())
        Refund.objects.filter(pk=refund.pk).update(updated_at=timezone.now() - timedelta(days=2))
        self.assertIn("STUCK_REFUND", codes())

    def test_command_exit_behaviour(self):
        call_command("reconcile_payments", verbosity=0)          # clean: no error
        b, p = self.paid()
        OwnerLedger.objects.filter(payment=p).delete()
        with self.assertRaises(CommandError):
            call_command("reconcile_payments", verbosity=0)
        call_command("reconcile_payments", warn_only=True, verbosity=0)   # scheduler mode never raises
