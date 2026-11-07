import hashlib
import hmac
import itertools
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.bookings.services import BookingError, create_booking
from apps.bookings.tests.helpers import S, future, mk_booking, mk_parking, mk_type
from apps.core.models import Notification, SiteSettings
from apps.payments import gateway, services
from apps.payments.gateway import GatewayError
from apps.payments.models import OwnerLedger, Payment, Refund

PW = "Tr1cky-Pass!77"
KEY_SECRET, WH_SECRET = "key_secret_test", "whsec_test"
_ids = itertools.count(1)


def sign(order_id, payment_id):
    return hmac.new(KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


@override_settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET=KEY_SECRET, RAZORPAY_WEBHOOK_SECRET=WH_SECRET)
class PayBase(TestCase):
    def setUp(self):
        SiteSettings.load()
        self.owner = User.objects.create_user("o@example.com", PW, full_name="Owner", role="OWNER", phone="9300000001")
        self.other_owner = User.objects.create_user("o2@example.com", PW, full_name="Owner2", role="OWNER", phone="9300000004")
        self.driver = User.objects.create_user("d@example.com", PW, full_name="Driver", role="DRIVER", phone="9300000002")
        self.driver2 = User.objects.create_user("d2@example.com", PW, full_name="Driver2", role="DRIVER", phone="9300000003")
        self.admin = User.objects.create_user("a@example.com", PW, full_name="Admin", role="SUPERADMIN", is_staff=True)
        self.parking = mk_parking(self.owner, allow_cancellation=True, cancel_window_minutes=60)
        self.vt = mk_type(self.parking, capacity=1, price="100")
        self.refund_calls = []
        patchers = [
            mock.patch.object(gateway, "create_order", side_effect=lambda amount, receipt, notes=None: {"id": f"order_{next(_ids)}"}),
            mock.patch.object(gateway, "refund_payment", side_effect=self._refund),
        ]
        self.refund_result = {"status": "processed"}
        for p in patchers:
            p.start(); self.addCleanup(p.stop)

    def _refund(self, payment_id, amount, receipt, notes=None):
        self.refund_calls.append((payment_id, Decimal(amount)))
        if isinstance(self.refund_result, Exception):
            raise self.refund_result
        return {"id": f"rfnd_{next(_ids)}", **self.refund_result}

    def hold(self, start=None, plate="OD02AB1234", driver=None):
        return create_booking(driver or self.driver, self.vt, start or future(48), 1, plate, "Tata Nexon")

    def pay(self, booking):
        payment = services.create_booking_order(booking.pk, booking.driver)
        services.settle_payment(payment.razorpay_order_id, f"pay_{next(_ids)}", "sig")
        booking.refresh_from_db()
        return booking, Payment.objects.get(pk=payment.pk)

    def paid(self, **kw):
        return self.pay(self.hold(**kw))

    def balance(self):
        return services.owner_balance(self.owner)


class PaymentFlowTests(PayBase):
    def test_order_verify_confirm_and_idempotent(self):
        b = self.hold()
        self.client.force_login(self.driver)
        data = self.client.post(reverse("payments:booking_order", args=[b.pk])).json()
        self.assertTrue(data["ok"])
        self.assertEqual((data["amount"], data["key"], data["currency"]), (10000, "rzp_test_abc", "INR"))
        payment = Payment.objects.get(razorpay_order_id=data["order_id"])
        self.assertEqual((payment.status, payment.amount), ("CREATED", Decimal("100.00")))

        body = {"razorpay_order_id": data["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": sign(data["order_id"], "pay_1")}
        resp = self.client.post(reverse("payments:verify"), json.dumps(body), content_type="application/json")
        self.assertEqual(resp.json(), {"ok": True, "redirect": reverse("bookings:detail", args=[b.pk])})
        b.refresh_from_db()
        self.assertEqual((b.status, b.total_paid), ("CONFIRMED", Decimal("100.00")))
        self.assertRegex(b.booking_code, r"^[A-HJ-NP-Z2-9]{6}$")
        self.assertEqual(self.balance(), Decimal("100.00"))
        self.assertTrue(Notification.objects.filter(user=self.owner, title="New booking").exists())

        self.client.post(reverse("payments:verify"), json.dumps(body), content_type="application/json")  # replay
        self.assertEqual(OwnerLedger.objects.filter(entry_type="CREDIT").count(), 1)
        self.assertEqual(self.client.get(reverse("bookings:detail", args=[b.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("bookings:detail", args=[b.pk])), b.booking_code)

    def test_bad_signature_does_not_confirm(self):
        b = self.hold()
        payment = services.create_booking_order(b.pk, self.driver)
        self.client.force_login(self.driver)
        resp = self.client.post(reverse("payments:verify"), json.dumps(
            {"razorpay_order_id": payment.razorpay_order_id, "razorpay_payment_id": "pay_1", "razorpay_signature": "forged"}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        b.refresh_from_db(); self.assertEqual(b.status, "PENDING_PAYMENT")
        self.assertFalse(OwnerLedger.objects.exists())

    def test_other_driver_cannot_pay_or_verify(self):
        b = self.hold()
        self.client.force_login(self.driver2)
        self.assertEqual(self.client.post(reverse("payments:booking_order", args=[b.pk])).status_code, 404)
        payment = services.create_booking_order(b.pk, self.driver)
        body = {"razorpay_order_id": payment.razorpay_order_id, "razorpay_payment_id": "pay_9", "razorpay_signature": sign(payment.razorpay_order_id, "pay_9")}
        self.assertEqual(self.client.post(reverse("payments:verify"), json.dumps(body), content_type="application/json").status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("payments:booking_order", args=[b.pk])).status_code, 403)

    def test_expired_hold_cannot_start_payment(self):
        b = self.hold()
        Booking.objects.filter(pk=b.pk).update(payment_hold_expires_at=timezone.now() - timedelta(seconds=5))
        with self.assertRaises(BookingError):
            services.create_booking_order(b.pk, self.driver)

    def test_gateway_failure_is_reported(self):
        b = self.hold()
        gateway.create_order.side_effect = GatewayError("Could not start")
        self.client.force_login(self.driver)
        resp = self.client.post(reverse("payments:booking_order", args=[b.pk]))
        self.assertEqual((resp.status_code, resp.json()["ok"]), (400, False))

    def test_platform_fee_split_in_ledger(self):
        cfg = SiteSettings.load(); cfg.platform_fee_percent = Decimal("10"); cfg.save()
        b, p = self.paid()
        self.assertEqual((p.platform_fee_amount, p.owner_amount), (Decimal("10.00"), Decimal("90.00")))
        self.assertEqual(self.balance(), Decimal("90.00"))
        self.assertEqual(OwnerLedger.objects.get(entry_type="PLATFORM_FEE").amount, Decimal("10.00"))

    def test_paid_after_hold_expired_confirms_if_bay_free_else_refunds(self):
        b = self.hold()
        payment = services.create_booking_order(b.pk, self.driver)
        Booking.objects.filter(pk=b.pk).update(status="EXPIRED", payment_hold_expires_at=None)
        services.settle_payment(payment.razorpay_order_id, "pay_late", "sig")
        b.refresh_from_db(); self.assertEqual(b.status, "CONFIRMED")  # bay was still free

        b2 = self.hold(start=future(72), plate="OD02AB5555")
        payment2 = services.create_booking_order(b2.pk, self.driver)
        Booking.objects.filter(pk=b2.pk).update(status="EXPIRED", payment_hold_expires_at=None)
        mk_booking(self.vt, self.driver2, b2.start_at, b2.end_at)  # someone else took the bay meanwhile
        with self.captureOnCommitCallbacks(execute=True):
            services.settle_payment(payment2.razorpay_order_id, "pay_late2", "sig")
        b2.refresh_from_db()
        self.assertEqual(b2.status, "EXPIRED")
        refund = Refund.objects.get(booking=b2)
        self.assertEqual((refund.status, refund.amount), ("REFUNDED", Decimal("100.00")))
        self.assertEqual(self.refund_calls, [("pay_late2", Decimal("100.00"))])
        self.assertEqual(self.balance(), Decimal("100.00"))  # only the first booking's earning; no phantom debit


def _event(event, entity_key, entity, event_id="evt_1"):
    body = json.dumps({"event": event, "payload": {entity_key: {"entity": entity}}}).encode()
    sig = hmac.new(WH_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"HTTP_X_RAZORPAY_SIGNATURE": sig, "HTTP_X_RAZORPAY_EVENT_ID": event_id}


class WebhookTests(PayBase):
    def post(self, body, **headers):
        return self.client.post(reverse("payments:webhook"), body, content_type="application/json", **headers)

    def test_signature_required(self):
        body, headers = _event("payment.captured", "payment", {"id": "pay_1", "order_id": "x", "amount": 1})
        self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.post(body, HTTP_X_RAZORPAY_SIGNATURE="bad").status_code, 400)
        self.assertEqual(self.post(body, **headers).status_code, 200)  # unknown order is acknowledged and ignored

    def test_captured_confirms_once_even_with_browser_callback_and_replays(self):
        b = self.hold()
        payment = services.create_booking_order(b.pk, self.driver)
        body, headers = _event("payment.captured", "payment", {"id": "pay_w1", "order_id": payment.razorpay_order_id, "amount": 10000})
        self.assertEqual(self.post(body, **headers).status_code, 200)
        self.assertEqual(self.post(body, **headers).status_code, 200)  # Razorpay retry, same event id
        services.settle_payment(payment.razorpay_order_id, "pay_w1", "sig")  # browser callback arrives late
        b.refresh_from_db(); self.assertEqual(b.status, "CONFIRMED")
        self.assertEqual(OwnerLedger.objects.filter(entry_type="CREDIT").count(), 1)

    def test_wrong_amount_is_ignored(self):
        b = self.hold()
        payment = services.create_booking_order(b.pk, self.driver)
        body, headers = _event("payment.captured", "payment", {"id": "pay_w2", "order_id": payment.razorpay_order_id, "amount": 100})
        self.post(body, **headers)
        b.refresh_from_db(); self.assertEqual(b.status, "PENDING_PAYMENT")

    def test_payment_failed_marks_payment(self):
        b = self.hold()
        payment = services.create_booking_order(b.pk, self.driver)
        body, headers = _event("payment.failed", "payment", {"id": "pay_f", "order_id": payment.razorpay_order_id, "error_description": "Card declined"})
        self.post(body, **headers)
        payment.refresh_from_db(); self.assertEqual((payment.status, payment.failure_reason), ("FAILED", "Card declined"))

    def test_refund_processed_webhook_finalizes_pending_refund(self):
        self.refund_result = {"status": "pending"}
        b, p = self.paid()
        refund = services.request_refund(b.pk, self.driver, "Plans changed")
        services.approve_refund(refund.pk, self.owner)
        refund.refresh_from_db(); self.assertEqual(refund.status, "PROCESSING")
        body, headers = _event("refund.processed", "refund", {"id": refund.razorpay_refund_id, "notes": {"refund_id": refund.pk}}, "evt_r1")
        self.post(body, **headers)
        refund.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((refund.status, b.status), ("REFUNDED", "CANCELLED"))
        self.assertEqual(self.balance(), Decimal("0.00"))


class RefundTests(PayBase):
    def test_driver_request_owner_approve_full_cycle(self):
        b, p = self.paid()
        refund = services.request_refund(b.pk, self.driver, "Plans changed")
        b.refresh_from_db(); self.assertEqual((b.status, refund.status), ("REFUND_REQUESTED", "REQUESTED"))
        services.approve_refund(refund.pk, self.owner)
        refund.refresh_from_db(); b.refresh_from_db(); p.refresh_from_db()
        self.assertEqual((refund.status, b.status, p.status), ("REFUNDED", "CANCELLED", "REFUNDED"))
        self.assertEqual(self.refund_calls, [(p.razorpay_payment_id, Decimal("100.00"))])
        self.assertEqual(self.balance(), Decimal("0.00"))
        services.finalize_refund(refund.pk)  # idempotent
        self.assertEqual(OwnerLedger.objects.filter(entry_type="REFUND_DEBIT").count(), 1)
        self.assertEqual(Booking.objects.filter(pk=b.pk).first().status, "CANCELLED")
        from apps.bookings.availability import spots_left
        self.assertEqual(spots_left(self.vt, b.start_at, b.end_at), 1)  # bay freed

    def test_cancellation_charge_and_fee_reversal(self):
        cfg = SiteSettings.load(); cfg.platform_fee_percent = Decimal("10"); cfg.save()
        self.parking.cancellation_charge_percent = Decimal("20"); self.parking.save()
        b, p = self.paid()
        refund = services.request_refund(b.pk, self.driver)
        self.assertEqual((refund.amount, refund.cancellation_charge), (Decimal("80.00"), Decimal("20.00")))
        services.approve_refund(refund.pk, self.owner)
        p.refresh_from_db()
        self.assertEqual(p.status, "PARTIALLY_REFUNDED")
        self.assertEqual(self.balance(), Decimal("18.00"))  # kept 20 = 18 owner + 2 platform

    def test_rules_enforced(self):
        b, _ = self.paid()
        self.parking.allow_cancellation = False; self.parking.save()
        b2 = self.hold(start=future(96), plate="OD02AB0002")
        self.pay(b2)
        b2.refresh_from_db()
        Booking.objects.filter(pk=b2.pk).update(allow_cancellation=False)
        with self.assertRaisesMessage(BookingError, "does not allow"):
            services.request_refund(b2.pk, self.driver)
        Booking.objects.filter(pk=b.pk).update(start_at=timezone.now() + timedelta(minutes=30), end_at=timezone.now() + timedelta(hours=2))
        with self.assertRaisesMessage(BookingError, "window"):
            services.request_refund(b.pk, self.driver)
        with self.assertRaises(Booking.DoesNotExist):
            services.request_refund(b.pk, self.driver2)

    def test_owner_rejects_and_booking_returns_to_confirmed(self):
        b, _ = self.paid()
        refund = services.request_refund(b.pk, self.driver, "x")
        with self.assertRaises(Refund.DoesNotExist):
            services.reject_refund(refund.pk, self.other_owner, "no")
        services.reject_refund(refund.pk, self.owner, "Too late")
        refund.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((refund.status, b.status, refund.rejection_reason), ("REJECTED", "CONFIRMED", "Too late"))
        self.assertEqual(self.refund_calls, [])

    def test_gateway_failure_then_retry(self):
        b, p = self.paid()
        refund = services.request_refund(b.pk, self.driver)
        self.refund_result = GatewayError("Insufficient balance")
        services.approve_refund(refund.pk, self.owner)
        refund.refresh_from_db(); self.assertEqual((refund.status, refund.retry_count), ("FAILED", 1))
        self.assertIn("Insufficient", refund.error_message)
        self.assertEqual(self.balance(), Decimal("100.00"))
        self.refund_result = {"status": "processed"}
        services.approve_refund(refund.pk, self.owner)
        refund.refresh_from_db(); self.assertEqual((refund.status, refund.retry_count), ("REFUNDED", 2))

    def test_owner_cancel_gives_full_refund_no_penalty(self):
        self.parking.cancellation_charge_percent = Decimal("50"); self.parking.save()
        b, p = self.paid()
        before = User.objects.get(pk=self.driver.pk).points
        services.owner_cancel_booking(b.pk, self.owner, "Maintenance work")
        b.refresh_from_db(); self.assertEqual(b.status, "CANCELLED")
        self.assertEqual(Refund.objects.get().amount, Decimal("100.00"))
        self.assertEqual(User.objects.get(pk=self.driver.pk).points, before)
        with self.assertRaises(Booking.DoesNotExist):
            services.owner_cancel_booking(self.paid(start=future(120), plate="OD02AB0009")[0].pk, self.other_owner, "x")

    def test_refund_views_permissions(self):
        b, _ = self.paid()
        refund = services.request_refund(b.pk, self.driver, "x")
        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.post(reverse("owner:refund_approve", args=[refund.pk])).status_code, 404)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.post(reverse("owner:refund_approve", args=[refund.pk])).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("owner:refunds")), "OD02AB1234")
        self.client.post(reverse("owner:refund_approve", args=[refund.pk]))
        refund.refresh_from_db(); self.assertEqual(refund.status, "REFUNDED")


class LedgerAndPayoutTests(PayBase):
    def test_payout_reduces_balance_and_is_validated(self):
        self.paid()
        services.record_payout(self.owner, Decimal("40"), "UPI-123", "", self.admin)
        self.assertEqual(self.balance(), Decimal("60.00"))
        with self.assertRaisesMessage(BookingError, "only owed"):
            services.record_payout(self.owner, Decimal("61"), "UPI-124", "", self.admin)
        with self.assertRaises(BookingError):
            services.record_payout(self.owner, Decimal("0"), "UPI-125", "", self.admin)
        last = OwnerLedger.objects.filter(entry_type="PAYOUT").get()
        self.assertEqual((last.balance_after, last.reference), (Decimal("60.00"), "UPI-123"))

    def test_pages_and_permissions(self):
        self.paid()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("owner:earnings")), "₹100")
        self.assertEqual(self.client.get(reverse("sadmin:payouts")).status_code, 403)
        self.assertEqual(self.client.post(reverse("sadmin:record_payout"), {}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("sadmin:payouts")), "owed")
        self.client.post(reverse("sadmin:record_payout"), {"owner": self.owner.pk, "amount": "30", "reference": "UPI-777"})
        self.assertEqual(self.balance(), Decimal("70.00"))
        self.assertEqual(self.client.get(reverse("sadmin:payments")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sadmin:payments"), {"tab": "refunds"}).status_code, 200)

    def test_signature_helpers(self):
        self.assertTrue(gateway.verify_checkout_signature("o1", "p1", sign("o1", "p1")))
        self.assertFalse(gateway.verify_checkout_signature("o1", "p1", sign("o1", "p2")))
        self.assertFalse(gateway.verify_checkout_signature("", "p1", ""))
        self.assertEqual(gateway.to_paise(Decimal("12.34")), 1234)
