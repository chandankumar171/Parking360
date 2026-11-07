import threading
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.availability import (bulk_spots_left, is_window_available, peak_concurrency, spots_left,
                                        window_problem)
from apps.bookings.models import Booking
from apps.bookings.services import BookingError, create_booking, expire_stale_holds, release_hold
from apps.core.models import SiteSettings

from .helpers import S, future, mk_booking, mk_parking, mk_type

PW = "Tr1cky-Pass!77"


class Base(TestCase):
    def setUp(self):
        SiteSettings.load()
        self.owner = User.objects.create_user("o@example.com", PW, full_name="O", role="OWNER", phone="9100000001")
        self.driver = User.objects.create_user("d@example.com", PW, full_name="D", role="DRIVER", phone="9100000002")
        self.parking = mk_parking(self.owner)
        self.vt = mk_type(self.parking, capacity=2)


class PeakConcurrencyTests(TestCase):
    def test_sweep_not_simple_count(self):
        t0 = future(24)
        h = lambda n: t0 + timedelta(hours=n)
        # A: 0-2, B: 2-4 never overlap; a naive "count overlaps" would say 2
        self.assertEqual(peak_concurrency([(h(0), h(2)), (h(2), h(4))], h(0), h(4)), 1)
        self.assertEqual(peak_concurrency([(h(0), h(3)), (h(2), h(4))], h(0), h(4)), 2)
        self.assertEqual(peak_concurrency([(h(0), h(3)), (h(1), h(4)), (h(2), h(5))], h(0), h(5)), 3)

    def test_clipped_to_window_and_half_open(self):
        t0 = future(24)
        h = lambda n: t0 + timedelta(hours=n)
        self.assertEqual(peak_concurrency([(h(0), h(2))], h(2), h(4)), 0)  # ends exactly when window starts
        self.assertEqual(peak_concurrency([(h(-5), h(1))], h(0), h(2)), 1)


class SpotsTests(Base):
    def test_capacity_boundaries(self):
        s, e = future(24), future(26)
        self.assertEqual(spots_left(self.vt, s, e), 2)
        mk_booking(self.vt, self.driver, s, e)
        self.assertEqual(spots_left(self.vt, s, e), 1)
        mk_booking(self.vt, self.driver, s, e)
        self.assertEqual(spots_left(self.vt, s, e), 0)
        self.assertFalse(is_window_available(self.vt, s, e))
        self.assertTrue(is_window_available(self.vt, e, future(27)))  # back-to-back is fine

    def test_non_simultaneous_bookings_leave_room(self):
        mk_booking(self.vt, self.driver, future(24), future(26))
        mk_booking(self.vt, self.driver, future(26), future(28))
        mk_booking(self.vt, self.driver, future(24), future(25))
        self.assertEqual(spots_left(self.vt, future(24), future(28)), 0)  # 2 cars overlap during hour 0-1
        self.assertEqual(spots_left(self.vt, future(25), future(28)), 1)

    def test_only_blocking_statuses_count(self):
        s, e = future(24), future(26)
        now = timezone.now()
        for status in (S.CANCELLED, S.NO_SHOW, S.COMPLETED, S.EXPIRED):
            mk_booking(self.vt, self.driver, s, e, status=status)
        mk_booking(self.vt, self.driver, s, e, status=S.PENDING_PAYMENT, hold=now - timedelta(minutes=1))  # lapsed hold
        self.assertEqual(spots_left(self.vt, s, e), 2)
        mk_booking(self.vt, self.driver, s, e, status=S.PENDING_PAYMENT, hold=now + timedelta(minutes=5))
        mk_booking(self.vt, self.driver, s, e, status=S.REFUND_REQUESTED)  # still holds the bay until decided
        self.assertEqual(spots_left(self.vt, s, e), 0)

    def test_overstaying_vehicle_keeps_its_bay(self):
        now = timezone.now()
        mk_booking(self.vt, self.driver, now - timedelta(hours=3), now - timedelta(hours=1), status=S.CHECKED_IN)
        self.assertEqual(spots_left(self.vt, now, now + timedelta(minutes=30), now), 1)  # still parked
        self.assertEqual(spots_left(self.vt, now + timedelta(minutes=45), now + timedelta(hours=2), now), 1)  # inside buffer
        self.assertEqual(spots_left(self.vt, now + timedelta(hours=2), now + timedelta(hours=3), now), 2)

    def test_types_are_independent_and_bulk_matches_single(self):
        bike = mk_type(self.parking, category="TWO_WHEELER", capacity=5, price="20")
        s, e = future(24), future(26)
        mk_booking(self.vt, self.driver, s, e)
        res = bulk_spots_left([self.vt, bike], s, e)
        self.assertEqual(res, {self.vt.pk: 1, bike.pk: 5})


class WindowProblemTests(Base):
    def test_closed_past_and_far_future(self):
        self.assertIsNone(window_problem(self.parking, future(24), future(26)))
        self.assertIn("past", window_problem(self.parking, future(-5), future(-3)))
        self.assertIn("60 days", window_problem(self.parking, future(24 * 70), future(24 * 70 + 2)))
        self.parking.is_open = False
        self.assertIn("closed", window_problem(self.parking, future(24), future(26)))
        self.parking.is_open, self.parking.status = True, "PENDING"
        self.assertIn("not available", window_problem(self.parking, future(24), future(26)))

    def test_opening_hours_daytime_and_overnight(self):
        tz = timezone.get_current_timezone()
        day = (timezone.localdate() + timedelta(days=3))
        at = lambda d, h, m=0: timezone.make_aware(datetime.combine(d, time(h, m)), tz)
        self.parking.is_24x7, self.parking.opening_time, self.parking.closing_time = False, time(8), time(20)
        self.assertIsNone(window_problem(self.parking, at(day, 9), at(day, 12)))
        self.assertTrue(window_problem(self.parking, at(day, 18), at(day, 21)))  # runs past closing
        self.assertTrue(window_problem(self.parking, at(day, 7), at(day, 9)))    # starts before opening
        self.parking.opening_time, self.parking.closing_time = time(22), time(6)
        self.assertIsNone(window_problem(self.parking, at(day, 23), at(day + timedelta(days=1), 3)))
        self.assertIsNone(window_problem(self.parking, at(day, 1), at(day, 4)))
        self.assertTrue(window_problem(self.parking, at(day, 10), at(day, 12)))
        self.assertTrue(window_problem(self.parking, at(day, 23), at(day + timedelta(days=1), 8)))


class CreateBookingTests(Base):
    def book(self, start=None, slots=1, plate="OD02AB1234", vt=None, driver=None):
        return create_booking(driver or self.driver, vt or self.vt, start or future(24), slots, plate, "Tata Nexon")

    def test_snapshot_prices_hold_and_fee_rounding(self):
        cfg = SiteSettings.load(); cfg.platform_fee_percent = Decimal("7.5"); cfg.save()
        b = self.book(slots=3)
        self.assertEqual((b.status, b.base_amount, b.platform_fee_amount, b.owner_amount),
                         ("PENDING_PAYMENT", Decimal("150.00"), Decimal("11.25"), Decimal("138.75")))
        self.assertEqual(b.end_at - b.start_at, timedelta(hours=3))
        self.assertTrue(b.hold_active)
        self.assertIsNone(b.booking_code)
        self.vt.price_per_slot = 999; self.vt.save()  # later owner edits never change the booking
        b.refresh_from_db()
        self.assertEqual((b.price_per_slot, b.base_amount), (Decimal("50.00"), Decimal("150.00")))

    def test_zero_fee_default(self):
        b = self.book()
        self.assertEqual((b.platform_fee_amount, b.owner_amount), (Decimal("0.00"), Decimal("50.00")))

    def test_slot_limits(self):
        self.vt.max_slots = 4; self.vt.save()
        with self.assertRaisesMessage(BookingError, "between 1 and 4"):
            self.book(slots=5)

    def test_fully_booked_then_released_hold_frees_bay(self):
        a = self.book(plate="OD02AB0001", driver=self.driver)
        other = User.objects.create_user("d2@example.com", PW, full_name="D2", role="DRIVER", phone="9100000003")
        self.book(plate="OD02AB0002", driver=other)
        third = User.objects.create_user("d3@example.com", PW, full_name="D3", role="DRIVER", phone="9100000004")
        with self.assertRaisesMessage(BookingError, "fully booked"):
            self.book(plate="OD02AB0003", driver=third)
        release_hold(a.pk, self.driver)
        self.assertEqual(self.book(plate="OD02AB0003", driver=third).status, "PENDING_PAYMENT")

    def test_same_vehicle_cannot_double_book(self):
        self.book(plate="OD02AB7777")
        other = User.objects.create_user("d2@example.com", PW, full_name="D2", role="DRIVER", phone="9100000003")
        with self.assertRaisesMessage(BookingError, "already has a booking"):
            self.book(plate="OD02AB7777", driver=other)

    def test_limit_on_unpaid_holds(self):
        for i in range(3):
            self.book(start=future(24 + i * 3), plate=f"OD02AB10{i}0")
        with self.assertRaisesMessage(BookingError, "unpaid bookings"):
            self.book(start=future(40), plate="OD02AB9999")

    def test_expire_stale_holds(self):
        b = self.book()
        Booking.objects.filter(pk=b.pk).update(payment_hold_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(expire_stale_holds(), 1)
        b.refresh_from_db(); self.assertEqual(b.status, "EXPIRED")

    def test_closed_parking_rejected(self):
        self.parking.is_open = False; self.parking.save()
        with self.assertRaisesMessage(BookingError, "closed"):
            self.book()


class ConcurrencyTests(TransactionTestCase):
    def test_last_bay_cannot_be_double_booked(self):
        SiteSettings.load()
        owner = User.objects.create_user("o@example.com", PW, full_name="O", role="OWNER", phone="9100000001")
        parking = mk_parking(owner)
        vt = mk_type(parking, capacity=1)
        drivers = [User.objects.create_user(f"d{i}@example.com", PW, full_name=f"D{i}", role="DRIVER", phone=f"91000001{i:02d}")
                   for i in range(6)]
        start = future(24)
        results, barrier = [], threading.Barrier(len(drivers))

        def worker(i):
            try:
                barrier.wait()
                create_booking(drivers[i], vt, start, 1, f"OD02AB{2000 + i}", "Tata Nexon")
                results.append("ok")
            except BookingError:
                results.append("full")
            except Exception as exc:  # deadlock retries etc. must not hide a double booking
                results.append(f"err:{exc.__class__.__name__}")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(drivers))]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(Booking.objects.filter(vehicle_type=vt).count(), 1, results)
        self.assertEqual(results.count("ok"), 1, results)
