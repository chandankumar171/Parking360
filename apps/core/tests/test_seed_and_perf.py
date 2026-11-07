import shutil
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.availability import peak_concurrency
from apps.bookings.models import Booking
from apps.bookings.tests.helpers import S, future, mk_booking, mk_parking, mk_type
from apps.core.management.commands.seed_demo_data import DOMAIN, PASSWORD
from apps.core.models import Notification, SiteSettings
from apps.parkings.models import Parking, ParkingPhoto, ParkingVehicleType
from apps.parkings.tests.test_phase2 import png
from apps.payments.models import OwnerLedger, Payment, Refund
from apps.payments.reconcile import run_reconciliation
from apps.reviews.models import ParkingReview

SEED = dict(force=True, no_photos=True, months=1, per_parking=6, verbosity=0)
PW = "Tr1cky-Pass!77"


class SeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cache.clear()
        SiteSettings.load()

    def test_seed_builds_consistent_realistic_data(self):
        call_command("seed_demo_data", **SEED)
        demo = User.objects.filter(email__endswith="@" + DOMAIN)
        self.assertEqual(demo.filter(role="OWNER").count(), 3)
        self.assertEqual(demo.filter(role="DRIVER").count(), 14)
        statuses = {s: Parking.objects.filter(owner__in=demo, status=s).count() for s in ("APPROVED", "PENDING", "REJECTED")}
        self.assertEqual(statuses, {"APPROVED": 6, "PENDING": 1, "REJECTED": 1})
        self.assertGreaterEqual(Parking.objects.filter(owner__in=demo, is_verified_badge=True).count(), 3)
        self.assertGreater(Booking.objects.filter(driver__in=demo).count(), 20)
        self.assertTrue(Payment.objects.filter(razorpay_order_id__startswith="order_demo_", status__in=["PAID", "REFUNDED"]).exists())
        self.assertTrue(Refund.objects.filter(booking__driver__in=demo, status="REFUNDED").exists() or Refund.objects.filter(status="REQUESTED").exists())
        self.assertTrue(OwnerLedger.objects.filter(entry_type="PAYOUT").exists())
        self.assertGreater(ParkingReview.objects.count(), 0)
        # the money all adds up, exactly as it would with live data
        self.assertEqual(run_reconciliation(), [])
        # no parking was ever booked beyond its bays
        for vt in ParkingVehicleType.objects.filter(parking__owner__in=demo):
            spans = list(Booking.objects.filter(vehicle_type=vt).exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED, S.CANCELLED])
                         .values_list("start_at", "end_at"))
            if spans:
                self.assertLessEqual(peak_concurrency(spans, min(s for s, _ in spans), max(e for _, e in spans)), vt.capacity, vt)
        # each parking's stored rating matches its reviews
        for p in Parking.objects.filter(owner__in=demo, status="APPROVED", review_count__gt=0):
            self.assertEqual(p.review_count, ParkingReview.objects.filter(parking=p).count())
        # most alerts start read, a few unread
        self.assertTrue(Notification.objects.filter(user__in=demo, is_read=False).exists())

    def test_demo_accounts_can_log_in_and_see_data(self):
        call_command("seed_demo_data", **SEED)
        self.assertTrue(self.client.login(username=f"owner1@{DOMAIN}", password=PASSWORD))
        self.assertEqual(self.client.get(reverse("core:owner_home")).status_code, 200)
        today = timezone.localdate()
        api = self.client.get(reverse("dashboard:owner_overview"), {"mode": "custom", "start": (today - timedelta(days=40)).isoformat(), "end": today.isoformat()}).json()
        kpis = {k["key"]: k["value"] for k in api["kpis"]}
        self.assertGreater(kpis["bookings"], 0)
        self.assertGreater(kpis["earnings"], 0)
        self.client.logout()
        self.assertTrue(self.client.login(username=f"driver1@{DOMAIN}", password=PASSWORD))
        self.assertContains(self.client.get(reverse("parkings:search")), "Saheed Nagar Smart Parking")
        self.assertEqual(self.client.get(reverse("bookings:list")).status_code, 200)

    def test_admin_dashboard_on_seeded_data(self):
        call_command("seed_demo_data", **SEED)
        # the seeder reuses your real superadmin when one exists (created from .env), otherwise it makes a demo one
        admin = User.objects.filter(role=User.Role.SUPERADMIN).first()
        self.assertIsNotNone(admin)
        self.client.force_login(admin)
        today = timezone.localdate()
        data = self.client.get(reverse("dashboard:admin_overview"), {"mode": "custom", "start": (today - timedelta(days=40)).isoformat(), "end": today.isoformat()}).json()
        kpis = {k["key"]: k["value"] for k in data["kpis"]}
        self.assertGreater(kpis["gross"], 0)
        self.assertGreater(kpis["platform"], 0)   # the seeder sets a 5% platform fee
        self.assertEqual(self.client.get(reverse("sadmin:queue")).status_code, 200)
        self.assertContains(self.client.get(reverse("sadmin:queue")), "Puri Temple View Lot")  # the pending listing

    def test_guards_reset_and_clear(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo_data", no_photos=True, verbosity=0)   # DEBUG is off in tests: refuses without --force
        call_command("seed_demo_data", **SEED)
        with self.assertRaises(CommandError):
            call_command("seed_demo_data", **SEED)                         # already there
        call_command("seed_demo_data", reset=True, **SEED)                 # rebuilds cleanly
        self.assertEqual(User.objects.filter(email__endswith="@" + DOMAIN, role="OWNER").count(), 3)
        self.assertEqual(run_reconciliation(), [])
        call_command("seed_demo_data", clear=True, **SEED)
        self.assertFalse(User.objects.filter(email__endswith="@" + DOMAIN).exists())
        self.assertFalse(Parking.objects.filter(name="Saheed Nagar Smart Parking").exists())
        self.assertFalse(Booking.objects.exists())

    def test_seed_is_repeatable_for_a_given_seed(self):
        call_command("seed_demo_data", seed=11, **SEED)
        first = Booking.objects.count()
        users = User.objects.filter(email__endswith="@" + DOMAIN).count()
        call_command("seed_demo_data", seed=11, reset=True, **SEED)
        self.assertEqual(User.objects.filter(email__endswith="@" + DOMAIN).count(), users)
        # times are relative to "now", so allow a booking or two of drift between runs
        self.assertAlmostEqual(Booking.objects.count(), first, delta=3)


TMP_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class QueryCountTests(TestCase):
    """The number of queries a page runs must not grow with the amount of data (no N+1)."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        SiteSettings.load()
        self.owner = User.objects.create_user("q-owner@example.com", PW, full_name="Owner", role="OWNER", phone="9600000001")
        self.driver = User.objects.create_user("q-driver@example.com", PW, full_name="Driver", role="DRIVER", phone="9600000002")
        self.admin = User.objects.create_user("q-admin@example.com", PW, full_name="Admin", role="SUPERADMIN", is_staff=True)
        self.n = 0

    # ---- data builders
    def add_parking(self, status="APPROVED", verified=True):
        self.n += 1
        p = mk_parking(self.owner, name=f"Lot {self.n}", status=status, is_verified_badge=verified, submitted_at=timezone.now())
        car, bike = mk_type(p, "FOUR_WHEELER", capacity=3), mk_type(p, "TWO_WHEELER", capacity=5, price="20")
        ParkingPhoto.objects.create(parking=p, image=png(f"p{self.n}.png"), is_cover=True)
        mk_booking(car, self.driver, future(-30 - self.n), future(-28 - self.n), status=S.COMPLETED)
        mk_booking(bike, self.driver, future(5 + self.n * 3), future(7 + self.n * 3), status=S.CONFIRMED)
        return p

    def add_review(self, parking):
        b = mk_booking(parking.vehicle_types.first(), self.driver, future(-100 - self.n), future(-98 - self.n), status=S.COMPLETED)
        self.n += 1
        ParkingReview.objects.create(booking=b, driver=self.driver, parking=parking, owner=self.owner, rating=4, comment="ok", owner_reply="thanks")

    def add_notification(self, user):
        Notification.objects.create(user=user, title="Hello", message="World", link="/search/")

    # ---- measuring
    def count(self, url, **params):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url, params)
        self.assertEqual(resp.status_code, 200, url)
        return len(ctx)

    def assert_flat(self, url, grow, **params):
        before = self.count(url, **params)
        for _ in range(6):
            grow()
        after = self.count(url, **params)
        self.assertLessEqual(after, before + 2, f"{url}: {before} queries with little data, {after} with more (N+1?)")

    def seed(self, n=3):
        for _ in range(n):
            self.add_parking()

    # ---- pages
    def test_public_pages(self):
        self.seed()
        self.assert_flat(reverse("parkings:search"), self.add_parking)
        self.assert_flat(reverse("core:landing"), self.add_parking)
        self.assert_flat(reverse("parkings:search"), self.add_parking, q="Lot", sort="price")

    def test_parking_page_and_owner_profile(self):
        p = self.add_parking()
        for _ in range(2):
            self.add_review(p)
        self.assert_flat(p.get_absolute_url(), lambda: self.add_review(p))
        from apps.accounts.models import OwnerProfile
        OwnerProfile.objects.create(user=self.owner, business_name="Co")
        self.assert_flat(reverse("reviews:owner_profile", args=[self.owner.pk]), lambda: (self.add_parking(), self.add_review(p)))

    def test_owner_pages(self):
        self.seed()
        self.client.force_login(self.owner)
        self.assert_flat(reverse("owner:parking_list"), self.add_parking)
        self.assert_flat(reverse("owner:bookings"), self.add_parking)
        p = self.add_parking()
        self.assert_flat(reverse("owner:reviews"), lambda: self.add_review(p))
        self.assert_flat(reverse("owner:earnings"), self.add_parking)

    def test_dashboard_endpoints(self):
        self.seed()
        today = timezone.localdate()
        window = {"mode": "custom", "start": (today - timedelta(days=60)).isoformat(), "end": (today + timedelta(days=60)).isoformat()}
        self.client.force_login(self.owner)
        self.assert_flat(reverse("dashboard:owner_overview"), self.add_parking, **window)
        self.assert_flat(reverse("dashboard:owner_live"), self.add_parking)
        self.client.force_login(self.admin)
        self.assert_flat(reverse("dashboard:admin_overview"), self.add_parking, **window)

    def test_superadmin_lists(self):
        for _ in range(3):
            self.add_parking(status="PENDING", verified=False)
        self.client.force_login(self.admin)
        self.assert_flat(reverse("sadmin:queue"), lambda: self.add_parking(status="PENDING", verified=False))
        self.assert_flat(reverse("sadmin:parking_list"), self.add_parking)
        self.assert_flat(reverse("sadmin:payments"), self.add_parking)

    def test_driver_pages(self):
        self.seed()
        self.client.force_login(self.driver)
        self.assert_flat(reverse("bookings:list"), self.add_parking)
        self.assert_flat(reverse("bookings:list"), self.add_parking, tab="past")
        for _ in range(3):
            self.add_notification(self.driver)
        self.assert_flat(reverse("core:notifications"), lambda: self.add_notification(self.driver))
