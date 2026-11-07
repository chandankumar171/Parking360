import json
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PointLog, User
from apps.bookings import lifecycle
from apps.bookings.models import Booking, DriverVerdict
from apps.bookings.services import BookingError
from apps.bookings.tests.helpers import S, mk_booking, mk_type
from apps.payments import services
from apps.payments.models import OwnerLedger, Payment
from apps.payments.tests.test_phase4 import PayBase, sign


class GateBase(PayBase):
    def parked(self, minutes_late=-30, code="K7M2QX", plate="OD02AB4321"):
        """Checked-in booking whose paid time ended `minutes_late` ago (negative: still inside the paid time)."""
        end = timezone.now() - timedelta(minutes=minutes_late)
        b = mk_booking(self.vt, self.driver, end - timedelta(hours=2), end, status=S.CHECKED_IN, plate=plate)
        Booking.objects.filter(pk=b.pk).update(booking_code=code, checked_in_at=b.start_at, total_paid=Decimal("50"))
        return Booking.objects.get(pk=b.pk)

    def confirmed(self, start_offset_min, code="Q3W4ER"):
        now = timezone.now()
        b = mk_booking(self.vt, self.driver, now + timedelta(minutes=start_offset_min), now + timedelta(minutes=start_offset_min + 60))
        Booking.objects.filter(pk=b.pk).update(booking_code=code)
        return Booking.objects.get(pk=b.pk)


class OvertimeMathTests(GateBase):
    def test_blocks_round_up_and_free_minutes(self):
        b = self.parked()
        at = lambda m: b.end_at + timedelta(minutes=m)
        self.assertEqual(lifecycle.compute_overtime(b, at(0)), (0, Decimal("0.00")))
        self.assertEqual(lifecycle.compute_overtime(b, at(1)), (1, Decimal("100.00")))
        self.assertEqual(lifecycle.compute_overtime(b, at(30)), (30, Decimal("100.00")))
        self.assertEqual(lifecycle.compute_overtime(b, at(45)), (45, Decimal("200.00")))
        b.overtime_block_minutes, b.overtime_charge, b.overtime_free_minutes = 1, Decimal("6"), 10
        self.assertEqual(lifecycle.compute_overtime(b, at(10)), (10, Decimal("0.00")))
        self.assertEqual(lifecycle.compute_overtime(b, at(25)), (25, Decimal("90.00")))


class CheckInTests(GateBase):
    def test_window_rules(self):
        early, ok, late = self.confirmed(60, "AAAAAA"), self.confirmed(-5, "BBBBBB"), self.confirmed(-30, "CCCCCC")
        with self.assertRaisesMessage(BookingError, "Too early"):
            lifecycle.check_in(early.pk, self.owner)
        self.assertEqual(lifecycle.check_in(ok.pk, self.owner).status, "CHECKED_IN")
        with self.assertRaisesMessage(BookingError, "already checked in"):
            lifecycle.check_in(ok.pk, self.owner)
        with self.assertRaisesMessage(BookingError, "grace period"):
            lifecycle.check_in(late.pk, self.owner)
        with self.assertRaises(Booking.DoesNotExist):
            lifecycle.check_in(early.pk, self.other_owner)

    def test_code_lookup_normalises_and_scopes_to_owner(self):
        b = self.confirmed(0, "K7M2QX")
        self.assertEqual(lifecycle.find_by_code(self.owner, " k7m-2qx "), b)
        self.assertIsNone(lifecycle.find_by_code(self.other_owner, "K7M2QX"))
        self.assertIsNone(lifecycle.find_by_code(self.owner, "K7M"))

    def test_verify_page(self):
        b = self.confirmed(0, "K7M2QX")
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(reverse("owner:verify"), {"code": "k7m2qx"}), reverse("owner:booking_detail", args=[b.pk]))
        self.assertContains(self.client.post(reverse("owner:verify"), {"code": "ZZZZZZ"}), "No booking with that code")
        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.get(reverse("owner:booking_detail", args=[b.pk])).status_code, 404)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse("owner:verify")).status_code, 403)


class CheckoutTests(GateBase):
    def test_no_overtime_flow_awards_points(self):
        b = self.parked(minutes_late=-30)
        with self.assertRaisesMessage(BookingError, "Start the checkout"):
            lifecycle.complete_checkout(b.pk, self.owner, True, "")
        lifecycle.request_checkout(b.pk, self.owner)
        done = lifecycle.complete_checkout(b.pk, self.owner, True, "")
        self.assertEqual(done.status, "COMPLETED")
        self.assertTrue(DriverVerdict.objects.get(booking=b).satisfied)
        self.assertEqual(User.objects.get(pk=self.driver.pk).points, 105)
        self.assertEqual(PointLog.objects.get(user=self.driver).reason, "SATISFIED")

    def test_unsatisfied_needs_comment(self):
        b = self.parked()
        lifecycle.request_checkout(b.pk, self.owner)
        with self.assertRaisesMessage(BookingError, "what went wrong"):
            lifecycle.complete_checkout(b.pk, self.owner, False, "")
        lifecycle.complete_checkout(b.pk, self.owner, False, "Blocked another bay")
        self.assertEqual(User.objects.get(pk=self.driver.pk).points, 90)

    def test_points_never_below_zero(self):
        User.objects.filter(pk=self.driver.pk).update(points=4)
        b = self.parked()
        lifecycle.request_checkout(b.pk, self.owner)
        lifecycle.complete_checkout(b.pk, self.owner, False, "bad")
        self.assertEqual(User.objects.get(pk=self.driver.pk).points, 0)

    def test_overtime_must_be_paid_through_razorpay_before_completion(self):
        b = self.parked(minutes_late=45)
        self.client.force_login(self.owner)
        self.client.post(reverse("owner:checkout_start", args=[b.pk]))
        b.refresh_from_db()
        self.assertEqual((b.overtime_amount, b.checkout_requested_at is not None), (Decimal("200.00"), True))
        with self.assertRaisesMessage(BookingError, "not paid"):
            lifecycle.complete_checkout(b.pk, self.owner, True, "")
        self.assertFalse(self.client.get(reverse("owner:booking_status", args=[b.pk])).json()["overtime_paid"])

        self.client.force_login(self.driver)
        self.assertContains(self.client.get(reverse("bookings:detail", args=[b.pk])), "Pay ₹200")
        data = self.client.post(reverse("payments:overtime_order", args=[b.pk])).json()
        self.assertEqual(data["amount"], 20000)
        body = {"razorpay_order_id": data["order_id"], "razorpay_payment_id": "pay_ot", "razorpay_signature": sign(data["order_id"], "pay_ot")}
        self.assertTrue(self.client.post(reverse("payments:verify"), json.dumps(body), content_type="application/json").json()["ok"])
        self.assertEqual(Payment.objects.get(kind="OVERTIME").status, "PAID")
        self.assertEqual(self.balance(), Decimal("200.00"))  # overtime credited to the owner
        with self.assertRaises(BookingError):  # nothing more to pay
            services.create_overtime_order(b.pk, self.driver)

        self.client.force_login(self.owner)
        self.assertTrue(self.client.get(reverse("owner:booking_status", args=[b.pk])).json()["overtime_paid"])
        self.client.post(reverse("owner:checkout_complete", args=[b.pk]), {"satisfied": "yes", "comment": ""})
        b.refresh_from_db(); self.assertEqual((b.status, b.total_paid), ("COMPLETED", Decimal("250.00")))

    def test_overtime_amount_is_frozen_at_checkout(self):
        b = self.parked(minutes_late=10)
        lifecycle.request_checkout(b.pk, self.owner)
        b.refresh_from_db()
        first = b.overtime_amount
        shift = timedelta(hours=3)   # move the whole stay earlier, so "now" is even later than the paid time
        Booking.objects.filter(pk=b.pk).update(start_at=b.start_at - shift, end_at=b.end_at - shift)
        lifecycle.request_checkout(b.pk, self.owner)
        b.refresh_from_db(); self.assertEqual(b.overtime_amount, first)

    def test_driver_cannot_pay_overtime_for_someone_elses_booking(self):
        b = self.parked(minutes_late=45)
        lifecycle.request_checkout(b.pk, self.owner)
        with self.assertRaises(Booking.DoesNotExist):
            services.create_overtime_order(b.pk, self.driver2)


class BackgroundJobTests(GateBase):
    def test_no_shows(self):
        solo = mk_type(self.parking, "TWO_WHEELER", capacity=1)  # its own bay pool, so we can check the bay is freed
        now = timezone.now()
        late = mk_booking(solo, self.driver, now - timedelta(minutes=30), now + timedelta(minutes=30))
        Booking.objects.filter(pk=late.pk).update(booking_code="LATE11")
        within = self.confirmed(-5, "WITH22")
        parked = self.parked(code="PARK33", plate="OD02AB7000")
        self.assertEqual(lifecycle.mark_no_shows(), 1)
        for b in (late, within, parked):
            b.refresh_from_db()
        self.assertEqual((late.status, within.status, parked.status), ("NO_SHOW", "CONFIRMED", "CHECKED_IN"))
        self.assertEqual(User.objects.get(pk=self.driver.pk).points, 85)
        self.assertEqual(lifecycle.mark_no_shows(), 0)  # idempotent
        from apps.bookings.availability import spots_left
        self.assertEqual(spots_left(solo, late.start_at, late.end_at), 1)  # bay freed, though not refunded

    @override_settings(AUTO_COMPLETE_HOURS=6)
    def test_auto_complete_unverified_checkout(self):
        stale = self.parked(minutes_late=7 * 60, code="STAL44")
        fresh = self.parked(minutes_late=60, code="FRSH55", plate="OD02AB7001")
        self.assertEqual(lifecycle.auto_complete(), 1)
        stale.refresh_from_db(); fresh.refresh_from_db()
        self.assertEqual((stale.status, fresh.status), ("COMPLETED", "CHECKED_IN"))
        self.assertEqual(User.objects.get(pk=self.driver.pk).points, 80)

    def test_management_commands_run(self):
        from django.core.management import call_command
        for name in ("mark_no_shows", "auto_complete_bookings", "expire_pending_bookings"):
            call_command(name, verbosity=0)
