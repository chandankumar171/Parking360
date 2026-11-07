import csv
import io
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OwnerProfile, User
from apps.bookings.models import Booking
from apps.bookings.services import create_booking
from apps.bookings.tests.helpers import S, future, mk_booking, mk_parking, mk_type
from apps.core.models import SiteSettings
from apps.payments import services
from apps.payments.models import OwnerLedger, Payment
from apps.payments.tests.test_phase4 import PayBase
from apps.reviews.models import ParkingReview


class DashBase(PayBase):
    def rng(self, extra=10):
        t = timezone.localdate()
        return {"mode": "custom", "start": (t - timedelta(days=1)).isoformat(), "end": (t + timedelta(days=extra)).isoformat()}

    def owner_data(self, **params):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("dashboard:owner_overview"), {**self.rng(), **params})
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def admin_data(self, **params):
        self.client.force_login(self.admin)
        return self.client.get(reverse("dashboard:admin_overview"), {**self.rng(), **params}).json()

    @staticmethod
    def kpis(data):
        return {k["key"]: k["value"] for k in data["kpis"]}

    def extra_booking(self, owner=None, name="Second Lot", driver=None, plate="OD02AB7788", price="80"):
        parking = mk_parking(owner or self.owner, name=name)
        vt = mk_type(parking, capacity=2, price=price)
        b = create_booking(driver or self.driver, vt, future(48), 1, plate, "Tata Nexon")
        return self.pay(b)[0]


class OwnerMetricsTests(DashBase):
    def test_earnings_refunds_rates_and_series(self):
        cfg = SiteSettings.load(); cfg.platform_fee_percent = Decimal("10"); cfg.save()
        b1, _ = self.paid(start=future(48), plate="OD02AB0001")
        self.paid(start=future(72), plate="OD02AB0002")
        d = self.owner_data()
        k = self.kpis(d)
        self.assertEqual((k["earnings"], k["bookings"], k["owed"], k["cancel"], k["noshow"]), (180.0, 2, 180.0, 0.0, 0.0))
        self.assertAlmostEqual(sum(d["charts"]["revenue"]["series"][0]["data"]), 180.0)
        self.assertEqual(sum(d["charts"]["bookings"]["series"][0]["data"]), 2)
        self.assertEqual(len(d["charts"]["revenue"]["labels"]), len(d["charts"]["bookings"]["series"][0]["data"]))

        refund = services.request_refund(b1.pk, self.driver, "x")
        services.approve_refund(refund.pk, self.owner)
        k = self.kpis(self.owner_data())
        self.assertEqual((k["earnings"], k["owed"], k["bookings"], k["cancel"]), (90.0, 90.0, 2, 50.0))
        d = self.owner_data()
        self.assertEqual(dict(zip(d["charts"]["status"]["labels"], d["charts"]["status"]["data"])), {"Confirmed": 1, "Cancelled": 1})
        self.assertEqual(d["charts"]["vehicle"]["labels"], ["4-wheeler (car)"])

    def test_overtime_is_separated_from_booking_revenue(self):
        b, _ = self.paid()
        pay = Payment.objects.create(booking=b, kind="OVERTIME", razorpay_order_id="order_ot_1", amount=Decimal("50"),
                                     status="PAID", owner_amount=Decimal("50"))
        with transaction.atomic():
            services.ledger_entry(self.owner, OwnerLedger.Type.CREDIT, Decimal("50"), booking=b, payment=pay)
        d = self.owner_data()
        series = {s["name"]: sum(s["data"]) for s in d["charts"]["overtime"]["series"]}
        self.assertEqual(series, {"Booking revenue": 100.0, "Overtime revenue": 50.0})
        self.assertEqual(self.kpis(d)["earnings"], 150.0)

    def test_parking_filter_and_no_cross_owner_leak(self):
        self.paid(plate="OD02AB0001")
        second = self.extra_booking(plate="OD02AB7788")
        rival = self.extra_booking(owner=self.other_owner, name="Rival Lot", driver=self.driver2, plate="OD02AB9911", price="60")
        everything = self.owner_data()
        self.assertEqual((self.kpis(everything)["bookings"], self.kpis(everything)["earnings"]), (2, 180.0))
        self.assertEqual(set(everything["charts"]["parking_revenue"]["labels"]), {"City Park", "Second Lot"})
        only_second = self.owner_data(parking=second.parking_id)
        self.assertEqual((self.kpis(only_second)["bookings"], self.kpis(only_second)["earnings"]), (1, 80.0))
        leak_attempt = self.owner_data(parking=rival.parking_id)  # someone else's parking id is ignored, never shown
        self.assertEqual(self.kpis(leak_attempt)["earnings"], 180.0)
        self.assertNotIn("Rival Lot", leak_attempt["charts"]["parking_revenue"]["labels"])
        self.assertEqual(self.owner_data(parking="junk")["kpis"][1]["value"], 2)

    def test_occupancy_and_peak_hours(self):
        day = timezone.localdate() + timedelta(days=3)
        start = timezone.make_aware(datetime.combine(day, time(12, 0)), timezone.get_current_timezone())
        mk_booking(self.vt, self.driver, start, start + timedelta(hours=2), status=S.COMPLETED)
        d = self.owner_data(mode="custom", start=day.isoformat(), end=day.isoformat())
        self.assertEqual(self.kpis(d)["occupancy"], 8.3)  # 2 booked bay-hours / (1 bay x 24 h)
        self.assertEqual(d["charts"]["occupancy"]["series"][0]["data"], [8.3])
        self.assertEqual(d["charts"]["peak_hours"]["data"][12], 1)
        self.assertEqual(d["charts"]["peak_hours"]["labels"][12], "12 PM")
        self.assertEqual(sum(d["charts"]["peak_hours"]["data"]), 1)

    def test_occupancy_counts_stays_crossing_midnight_in_both_days(self):
        day = timezone.localdate() + timedelta(days=3)
        start = timezone.make_aware(datetime.combine(day, time(22, 0)), timezone.get_current_timezone())
        mk_booking(self.vt, self.driver, start, start + timedelta(hours=4), status=S.COMPLETED)
        d = self.owner_data(mode="custom", start=day.isoformat(), end=(day + timedelta(days=1)).isoformat())
        self.assertEqual(d["charts"]["occupancy"]["series"][0]["data"], [8.3, 8.3])  # 2 h on each day

    def test_ratings(self):
        b = mk_booking(self.vt, self.driver, future(-5), future(-3), status=S.COMPLETED)
        ParkingReview.objects.create(booking=b, driver=self.driver, parking=self.parking, owner=self.owner, rating=4)
        d = self.owner_data()
        self.assertEqual(self.kpis(d)["rating"], 4.0)
        data = d["charts"]["rating"]["series"][0]["data"]
        self.assertEqual([v for v in data if v is not None], [4.0])
        self.assertIn(None, data)  # days without reviews are gaps, not zeros

    def test_empty_period_is_safe(self):
        d = self.owner_data(mode="year", year="2024")
        k = self.kpis(d)
        self.assertEqual((k["earnings"], k["bookings"], k["occupancy"], k["cancel"]), (0.0, 0, 0.0, 0.0))
        self.assertEqual(d["charts"]["status"]["data"], [])
        self.assertEqual(len(d["charts"]["revenue"]["labels"]), 12)

    def test_comparison_with_previous_period(self):
        b, _ = self.paid()
        entry = OwnerLedger.objects.create(owner=self.owner, booking=b, entry_type="CREDIT", amount=Decimal("50"), balance_after=Decimal("150"))
        OwnerLedger.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(hours=36))
        today = timezone.localdate()
        d = self.owner_data(mode="custom", start=today.isoformat(), end=(today + timedelta(days=1)).isoformat())
        k = {x["key"]: x for x in d["kpis"]}
        self.assertEqual(k["earnings"]["delta"], 100.0)  # 100 now vs 50 before


class AdminMetricsTests(DashBase):
    def test_platform_numbers(self):
        cfg = SiteSettings.load(); cfg.platform_fee_percent = Decimal("10"); cfg.save()
        b, _ = self.paid()
        d = self.admin_data()
        k = self.kpis(d)
        self.assertEqual((k["gross"], k["platform"], k["bookings"], k["owed"]), (100.0, 10.0, 1, 90.0))
        self.assertEqual(d["charts"]["cities"]["labels"], ["Bhubaneswar"])
        self.assertEqual(d["charts"]["top_parkings"]["labels"], ["City Park"])
        self.assertEqual(d["charts"]["top_parkings"]["data"], [90.0])
        self.assertEqual(d["charts"]["top_owners"]["data"], [90.0])
        signups = {s["name"]: sum(s["data"]) for s in d["charts"]["signups"]["series"]}
        self.assertEqual(signups, {"Owners": 2.0, "Drivers": 2.0})

        refund = services.request_refund(b.pk, self.driver, "x")
        services.approve_refund(refund.pk, self.owner)
        k = self.kpis(self.admin_data())
        self.assertEqual((k["refunds"], k["platform"], k["owed"], k["cancel"]), (100.0, 0.0, 0.0, 100.0))  # fee reversed with the refund
        self.assertEqual(sum(self.admin_data()["charts"]["refunds"]["series"][0]["data"]), 100.0)

    def test_owner_names_use_business_name(self):
        OwnerProfile.objects.create(user=self.owner, business_name="City Parks Co")
        self.paid()
        self.assertEqual(self.admin_data()["charts"]["top_owners"]["labels"], ["City Parks Co"])


class AccessTests(DashBase):
    def test_roles_and_bad_input(self):
        owner_urls = [reverse("dashboard:owner_overview"), reverse("dashboard:owner_live"), reverse("dashboard:owner_export", args=["bookings"])]
        admin_urls = [reverse("dashboard:admin_overview"), reverse("dashboard:admin_export", args=["bookings"])]
        for url in owner_urls + admin_urls:
            self.assertEqual(self.client.get(url).status_code, 302, url)  # anonymous -> login
        self.client.force_login(self.driver)
        for url in owner_urls + admin_urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.force_login(self.owner)
        for url in admin_urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.force_login(self.admin)
        for url in owner_urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.force_login(self.owner)
        junk = {"mode": "zzz", "year": "abc", "month": "13", "start": "x", "group": "hour", "parking": "-5"}
        self.assertEqual(self.client.get(reverse("dashboard:owner_overview"), junk).status_code, 200)

    def test_pages_render_with_filters_prefilled(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("core:owner_home"), {"mode": "year", "year": "2025", "parking": str(self.parking.pk)})
        self.assertContains(page, 'id="ch-revenue"')
        self.assertContains(page, "chart.umd.min.js")
        self.assertContains(page, 'value="2025" selected')
        self.assertContains(page, f'value="{self.parking.pk}" selected')
        self.assertContains(page, "Export CSV")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:superadmin_home"))
        self.assertContains(page, 'id="ch-top_owners"')
        self.assertNotContains(page, 'id="f-parking"')


class LiveTests(DashBase):
    def live(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("dashboard:owner_live")).json()

    def test_lists(self):
        now = timezone.now()
        mk_booking(self.vt, self.driver, now, now + timedelta(hours=1), status=S.CONFIRMED, plate="OD02AB1000")
        mk_booking(self.vt, self.driver2, now - timedelta(hours=3), now - timedelta(minutes=30), status=S.CHECKED_IN, plate="OD02AB2000")
        b, _ = self.paid(start=future(48), plate="OD02AB3000")
        services.request_refund(b.pk, self.driver, "x")
        data = self.live(self.owner)
        self.assertEqual([r["plate"] for r in data["upcoming"]], ["OD02AB1000"])
        self.assertEqual([(r["plate"], r["minutes"] >= 30) for r in data["overstaying"]], [("OD02AB2000", True)])
        self.assertEqual([(r["plate"], r["amount"]) for r in data["refunds"]], [("OD02AB3000", 100.0)])
        self.assertGreaterEqual(len(data["recent"]), 3)
        self.assertEqual(self.live(self.other_owner), {"upcoming": [], "overstaying": [], "refunds": [], "recent": []})


class ExportTests(DashBase):
    def rows(self, resp):
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])
        text = b"".join(resp.streaming_content).decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def get(self, user, name, kind, **params):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=[kind]), {**self.rng(), **params})

    def test_owner_exports(self):
        evil = User.objects.create_user("evil@example.com", "Tr1cky-Pass!77", full_name="=HYPERLINK(\"http://x\")", role="DRIVER", phone="9300000077")
        b, _ = self.pay(self.hold(driver=evil, plate="OD02AB4444"))
        self.extra_booking(owner=self.other_owner, name="Rival Lot", driver=self.driver2, plate="OD02AB9911")
        rows = self.rows(self.get(self.owner, "dashboard:owner_export", "bookings"))
        self.assertEqual(rows[0][:3], ["Booking ID", "Code", "Parking"])
        self.assertEqual(len(rows), 2)  # header + only this owner's booking
        self.assertTrue(rows[1][3].startswith("'="), rows[1][3])  # spreadsheet formula neutralised
        self.assertEqual(rows[1][4], "OD02AB4444")
        leak = self.rows(self.get(self.owner, "dashboard:owner_export", "bookings", parking=self.extra_booking(owner=self.other_owner, name="R2", driver=self.driver2, plate="OD02AB9922").parking_id))
        self.assertEqual(len(leak), 2)  # foreign parking id ignored: still just my one booking

        ledger = self.rows(self.get(self.owner, "dashboard:owner_export", "earnings"))
        self.assertEqual(ledger[1][1], "Earning")
        self.assertEqual(ledger[1][4], "100.00")
        series = self.rows(self.get(self.owner, "dashboard:owner_export", "series"))
        self.assertEqual(series[0][1:4], ["Net earnings", "Bookings", "Booking revenue"])
        self.assertEqual(len(series) - 1, 12)  # one row per day in the 12-day range
        self.assertAlmostEqual(sum(float(r[1]) for r in series[1:]), 100.0)

    def test_unknown_kind_and_admin_exports(self):
        self.paid()
        self.assertEqual(self.get(self.owner, "dashboard:owner_export", "secrets").status_code, 404)
        self.assertEqual(self.get(self.admin, "dashboard:admin_export", "owners").status_code, 404)
        bookings = self.rows(self.get(self.admin, "dashboard:admin_export", "bookings"))
        self.assertEqual((bookings[0][0], bookings[1][0]), ("Owner", "o@example.com"))
        payments = self.rows(self.get(self.admin, "dashboard:admin_export", "payments"))
        self.assertEqual((payments[0][3], payments[1][3], payments[1][7]), ("Type", "Booking", "Paid"))
        series = self.rows(self.get(self.admin, "dashboard:admin_export", "series"))
        self.assertEqual(series[0][1:3], ["Gross collected", "Platform earnings"])
        self.assertAlmostEqual(sum(float(r[1]) for r in series[1:]), 100.0)
