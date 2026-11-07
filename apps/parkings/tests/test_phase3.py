from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import DriverVehicle, User
from apps.bookings.models import Booking
from apps.bookings.tests.helpers import S, future, mk_booking, mk_parking, mk_type
from apps.core.models import SiteSettings
from apps.parkings.search import build_queryset

PW = "Tr1cky-Pass!77"


def names(params):
    qs, _ = build_queryset(params)
    return [p.name for p in qs]


class Base(TestCase):
    def setUp(self):
        SiteSettings.load()
        self.owner = User.objects.create_user("o@example.com", PW, full_name="O", role="OWNER", phone="9200000001")
        self.driver = User.objects.create_user("d@example.com", PW, full_name="Dee Driver", role="DRIVER", phone="9200000002")
        self.driver2 = User.objects.create_user("d2@example.com", PW, full_name="Other", role="DRIVER", phone="9200000003")


class SearchTests(Base):
    def setUp(self):
        super().setUp()
        self.a = mk_parking(self.owner, name="Alpha Plaza", area="Saheed Nagar", city="Bhubaneswar", has_cctv=True,
                            is_verified_badge=True, avg_rating=Decimal("4.6"), review_count=10, allow_cancellation=True,
                            latitude=Decimal("20.296100"), longitude=Decimal("85.824500"))
        mk_type(self.a, "FOUR_WHEELER", capacity=2, price="50"); mk_type(self.a, "TWO_WHEELER", capacity=5, price="20")
        self.b = mk_parking(self.owner, name="Beta Yard", area="Old Town", city="Cuttack", is_covered=True,
                            latitude=Decimal("20.462500"), longitude=Decimal("85.883000"))
        mk_type(self.b, "HEAVY", capacity=3, price="200")
        self.hidden = mk_parking(self.owner, name="Hidden Lot", status="PENDING", area="Saheed Nagar")
        mk_type(self.hidden)
        self.empty = mk_parking(self.owner, name="No Types", area="Saheed Nagar")  # approved but nothing to book

    def test_text_search_terms_and_visibility(self):
        self.assertEqual(set(names({})), {"Alpha Plaza", "Beta Yard"})
        self.assertEqual(names({"q": "saheed"}), ["Alpha Plaza"])
        self.assertEqual(names({"q": "saheed bhubaneswar"}), ["Alpha Plaza"])
        self.assertEqual(names({"q": "saheed cuttack"}), [])
        self.assertEqual(set(names({"q": "751007"})), {"Alpha Plaza", "Beta Yard"})  # pincode search
        self.assertNotIn("Hidden Lot", names({"q": "hidden"}))

    def test_vehicle_and_price_apply_to_the_same_type(self):
        self.assertEqual(names({"vehicle": "HEAVY"}), ["Beta Yard"])
        self.assertEqual(names({"vehicle": "FOUR_WHEELER", "max_price": Decimal("30")}), [])  # bike is cheap, car is not
        self.assertEqual(names({"vehicle": "TWO_WHEELER", "max_price": Decimal("30")}), ["Alpha Plaza"])
        self.assertEqual(names({"min_price": Decimal("100")}), ["Beta Yard"])

    def test_feature_filters(self):
        self.assertEqual(names({"amenities": ["has_cctv"]}), ["Alpha Plaza"])
        self.assertEqual(names({"amenities": ["is_covered"]}), ["Beta Yard"])
        self.assertEqual(names({"amenities": ["has_cctv", "is_covered"]}), [])
        self.assertEqual(names({"verified": True}), ["Alpha Plaza"])
        self.assertEqual(names({"cancellable": True}), ["Alpha Plaza"])
        self.assertEqual(names({"min_rating": "4"}), ["Alpha Plaza"])

    def test_sorting(self):
        self.assertEqual(names({"sort": "price"}), ["Alpha Plaza", "Beta Yard"])
        self.assertEqual(names({"sort": "rating"})[0], "Alpha Plaza")
        self.assertEqual(names({"q": "beta"})[0], "Beta Yard")

    def test_distance_filter_and_sort(self):
        near = {"lat": 20.2961, "lng": 85.8245}
        self.assertEqual(names(dict(near, radius="5")), ["Alpha Plaza"])
        self.assertEqual(names(dict(near, radius="50", sort="distance")), ["Alpha Plaza", "Beta Yard"])
        qs, _ = build_queryset(dict(near, radius="50", sort="distance"))
        self.assertLess(qs[0].distance_km, 0.5)
        self.assertTrue(10 < qs[1].distance_km < 30)

    def test_open_now_uses_hours(self):
        self.b.is_24x7, self.b.opening_time, self.b.closing_time = False, time(0, 0), time(0, 1)
        self.b.save()
        self.assertEqual(names({"open_now": True}), ["Alpha Plaza"])
        self.a.is_open = False; self.a.save()
        self.assertEqual(names({"open_now": True}), [])

    def test_window_availability_filter(self):
        vt = self.b.vehicle_types.get()
        for _ in range(3):
            mk_booking(vt, self.driver, future(24), future(26))
        local = timezone.localtime(future(25))
        window = {"date": local.date(), "start": local.time(), "hours": "1"}
        self.assertEqual(names(window), ["Alpha Plaza"])  # Beta is full then
        later = timezone.localtime(future(30))
        self.assertEqual(set(names({"date": later.date(), "start": later.time(), "hours": "1"})), {"Alpha Plaza", "Beta Yard"})

    def test_page_renders_and_shows_full_badge(self):
        vt = self.b.vehicle_types.get()
        for _ in range(3):
            mk_booking(vt, self.driver, timezone.now() - timedelta(minutes=10), timezone.now() + timedelta(hours=1), status=S.CHECKED_IN)
        resp = self.client.get(reverse("parkings:search"), {"q": "cuttack"})
        self.assertContains(resp, "Beta Yard")
        self.assertContains(resp, "Full now")
        self.assertNotContains(resp, "Alpha Plaza")
        self.assertContains(self.client.get(reverse("parkings:search"), {"sort": "bogus", "min_price": "abc"}), "Alpha Plaza")

    def test_suggest_endpoint(self):
        data = self.client.get(reverse("parkings:suggest"), {"q": "sah"}).json()["results"]
        self.assertIn("Saheed Nagar", [r["label"] for r in data])
        self.assertEqual(self.client.get(reverse("parkings:suggest"), {"q": "s"}).json(), {"results": []})
        self.assertNotIn("Hidden Lot", [r["label"] for r in self.client.get(reverse("parkings:suggest"), {"q": "hid"}).json()["results"]])

    def test_landing_lists_verified_parking(self):
        resp = self.client.get("/")
        self.assertContains(resp, "Alpha Plaza")
        self.assertNotContains(resp, "Beta Yard")
        self.assertNotContains(resp, ">Closed<")


class DetailAndBookingTests(Base):
    def setUp(self):
        super().setUp()
        self.p = mk_parking(self.owner, name="Detail Lot")
        self.vt = mk_type(self.p, "FOUR_WHEELER", capacity=1, price="50")
        self.url = reverse("parkings:detail", args=[self.p.slug])
        local = timezone.localtime(future(24))
        self.form = {"vehicle_type": self.vt.pk, "date": local.date().isoformat(), "time": local.strftime("%H:%M"), "slots": 2,
                     "vehicle_number_plate": "od 02 ab 1234", "vehicle_brand_model": "Tata Nexon", "terms": "on"}

    def test_visibility(self):
        self.assertContains(self.client.get(self.url), "Detail Lot")
        self.p.status = "PENDING"; self.p.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_booking_requires_driver_login(self):
        resp = self.client.post(self.url, self.form)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp["Location"])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url, self.form).status_code, 403)
        self.assertEqual(Booking.objects.count(), 0)

    def test_driver_books_and_sees_hold(self):
        self.client.force_login(self.driver)
        resp = self.client.post(self.url, self.form)
        b = Booking.objects.get()
        self.assertRedirects(resp, reverse("bookings:detail", args=[b.pk]))
        self.assertEqual((b.vehicle_number_plate, b.base_amount, b.driver), ("OD02AB1234", Decimal("100.00"), self.driver))
        page = self.client.get(reverse("bookings:detail", args=[b.pk]))
        self.assertContains(page, "₹100")
        self.assertContains(page, "Release hold")

    def test_validation_errors_are_shown(self):
        self.client.force_login(self.driver)
        bad = dict(self.form, vehicle_number_plate="123", slots=50)
        resp = self.client.post(self.url, bad)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "valid vehicle number")
        self.assertEqual(Booking.objects.count(), 0)

    def test_second_driver_sees_full_message(self):
        self.client.force_login(self.driver)
        self.client.post(self.url, self.form)
        self.client.force_login(self.driver2)
        resp = self.client.post(self.url, dict(self.form, vehicle_number_plate="OD02AB9999"))
        self.assertContains(resp, "fully booked")
        self.assertEqual(Booking.objects.count(), 1)

    def test_quote_endpoint(self):
        q = {"vehicle_type": self.vt.pk, "date": self.form["date"], "time": self.form["time"], "slots": 2}
        data = self.client.get(reverse("parkings:quote", args=[self.p.pk]), q).json()
        self.assertEqual((data["ok"], data["spots_left"], Decimal(data["price"]), data["duration_minutes"]), (True, 1, Decimal("100"), 120))
        mk_booking(self.vt, self.driver, future(24), future(27))
        data = self.client.get(reverse("parkings:quote", args=[self.p.pk]), q).json()
        self.assertFalse(data["ok"]); self.assertIn("booked", data["message"])
        bad = self.client.get(reverse("parkings:quote", args=[self.p.pk]), dict(q, slots=99)).json()
        self.assertFalse(bad["ok"])

    def test_my_bookings_privacy_and_release(self):
        b = mk_booking(self.vt, self.driver, future(24), future(26), status=S.PENDING_PAYMENT,
                       hold=timezone.now() + timedelta(minutes=5))
        self.client.force_login(self.driver2)
        self.assertEqual(self.client.get(reverse("bookings:detail", args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("bookings:release", args=[b.pk])).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("bookings:list")).status_code, 403)
        self.client.force_login(self.driver)
        self.assertContains(self.client.get(reverse("bookings:list")), "Detail Lot")
        self.client.post(reverse("bookings:release", args=[b.pk]))
        b.refresh_from_db(); self.assertEqual(b.status, "EXPIRED")
        self.assertNotContains(self.client.get(reverse("bookings:list")), "Detail Lot")

    def test_saved_vehicles_offered_to_driver(self):
        DriverVehicle.objects.create(user=self.driver, category="FOUR_WHEELER", plate_number="OD05Z1111", brand="Hyundai", model="i20")
        self.client.force_login(self.driver)
        self.assertContains(self.client.get(self.url), "OD05Z1111")

    def test_owner_cannot_delete_parking_with_bookings_or_vehicle_type(self):
        from apps.parkings import services
        from django.core.exceptions import ValidationError
        mk_booking(self.vt, self.driver, future(24), future(26), status=S.COMPLETED)
        with self.assertRaises(ValidationError):
            services.delete_parking(self.p)
        self.assertTrue(type(self.p).objects.filter(pk=self.p.pk).exists())
