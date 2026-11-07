from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OwnerProfile, User
from apps.bookings.models import Booking
from apps.bookings.tests.helpers import S, future, mk_booking, mk_parking, mk_type
from apps.core.models import AuditLog, SiteSettings
from apps.parkings.models import Parking
from apps.payments import services
from apps.payments.tests.test_phase4 import PW, PayBase


class AdminBase(PayBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        OwnerProfile.objects.create(user=self.owner, business_name="City Parks Co")


class AdminAccessTests(AdminBase):
    def test_only_superadmin_can_see_these_pages(self):
        b, _ = self.paid()
        urls = [reverse("sadmin:owners"), reverse("sadmin:drivers"), reverse("sadmin:bookings"), reverse("sadmin:booking_detail", args=[b.pk]),
                reverse("sadmin:user_detail", args=[self.driver.pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302, url)          # anonymous: login
        for user in (self.driver, self.owner):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, f"{user.role} {url}")
            self.assertEqual(self.client.post(reverse("sadmin:user_toggle", args=[self.driver.pk])).status_code, 403)
        self.client.force_login(self.admin)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_sidebar_links_are_real(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:superadmin_home"))
        for name in ("sadmin:owners", "sadmin:drivers", "sadmin:bookings"):
            self.assertContains(page, f'href="{reverse(name)}"')


class OwnersAndDriversTests(AdminBase):
    def test_owner_list_numbers_search_and_filter(self):
        self.paid()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("sadmin:owners"))
        row = next(r for r in resp.context["rows"] if r.pk == self.owner.pk)
        self.assertEqual((row.parkings_live, row.parkings_total, row.bookings_n, row.earned, row.owed),
                         (1, 1, 1, Decimal("100.00"), Decimal("100.00")))
        self.assertContains(resp, "City Parks Co")
        for q in ("city parks", "o@example.com", "9300000001"):   # business, email, mobile (not "Owner": that also matches "Owner2")
            self.assertEqual([r.pk for r in self.client.get(reverse("sadmin:owners"), {"q": q}).context["rows"]], [self.owner.pk], q)
        self.assertEqual(self.client.get(reverse("sadmin:owners"), {"q": "nobody-here"}).context["rows"], [])
        self.assertEqual(self.client.get(reverse("sadmin:owners"), {"state": "disabled"}).context["rows"], [])
        self.assertNotIn(self.driver.pk, [r.pk for r in resp.context["rows"]])   # drivers never appear in the owners list

    def test_driver_list_counts_only_the_drivers_own_cancellations(self):
        mk_booking(self.vt, self.driver, future(-50), future(-48), status=S.COMPLETED)
        mk_booking(self.vt, self.driver, future(-40), future(-38), status=S.NO_SHOW)
        by_driver = mk_booking(self.vt, self.driver, future(-30), future(-28), status=S.CANCELLED)
        by_owner = mk_booking(self.vt, self.driver, future(-20), future(-18), status=S.CANCELLED)
        mk_booking(self.vt, self.driver, future(5), future(7), status=S.PENDING_PAYMENT, hold=future(1))   # never paid: not counted
        Booking.objects.filter(pk=by_driver.pk).update(cancelled_by=self.driver)
        Booking.objects.filter(pk=by_owner.pk).update(cancelled_by=self.owner)
        self.client.force_login(self.admin)
        row = next(r for r in self.client.get(reverse("sadmin:drivers")).context["rows"] if r.pk == self.driver.pk)
        self.assertEqual((row.bookings_n, row.no_shows, row.cancelled), (4, 1, 1))
        self.assertEqual([r.pk for r in self.client.get(reverse("sadmin:drivers"), {"q": "9300000002"}).context["rows"]], [self.driver.pk])

    def test_detail_pages(self):
        b, _ = self.paid()
        self.client.force_login(self.admin)
        owner_page = self.client.get(reverse("sadmin:user_detail", args=[self.owner.pk]))
        self.assertContains(owner_page, "City Parks Co")
        self.assertContains(owner_page, "City Park")        # the parking
        self.assertContains(owner_page, "₹100")             # owed
        driver_page = self.client.get(reverse("sadmin:user_detail", args=[self.driver.pk]))
        self.assertContains(driver_page, "OD02AB1234")
        self.assertContains(driver_page, "Reliability points")
        self.assertEqual(self.client.get(reverse("sadmin:user_detail", args=[self.admin.pk])).status_code, 404)   # superadmins are not managed here
        self.assertEqual(self.client.get(reverse("sadmin:user_detail", args=[999999])).status_code, 404)


class EnableDisableTests(AdminBase):
    def test_disabling_a_driver_blocks_login_and_existing_sessions(self):
        driver_client = Client()
        driver_client.force_login(self.driver)
        self.assertEqual(driver_client.get(reverse("bookings:list")).status_code, 200)
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("sadmin:user_toggle", args=[self.driver.pk]), {"reason": "Abuse reports"})
        self.assertRedirects(resp, reverse("sadmin:user_detail", args=[self.driver.pk]))
        self.driver.refresh_from_db()
        self.assertFalse(self.driver.is_active)
        self.assertEqual(driver_client.get(reverse("bookings:list")).status_code, 302)   # session no longer valid
        self.assertFalse(Client().login(username=self.driver.email, password=PW))
        entry = AuditLog.objects.get(action="user.disabled")
        self.assertEqual((entry.actor, entry.metadata["reason"]), (self.admin, "Abuse reports"))
        self.assertContains(self.client.get(reverse("sadmin:drivers"), {"state": "disabled"}), self.driver.full_name)

        self.client.post(reverse("sadmin:user_toggle", args=[self.driver.pk]))            # enable again
        self.driver.refresh_from_db()
        self.assertTrue(self.driver.is_active)
        self.assertTrue(Client().login(username=self.driver.email, password=PW))
        self.assertTrue(AuditLog.objects.filter(action="user.enabled").exists())

    def test_disabling_an_owner_suspends_only_live_parkings(self):
        draft = mk_parking(self.owner, name="Unfinished", status=Parking.Status.DRAFT)
        self.client.force_login(self.admin)
        self.client.post(reverse("sadmin:user_toggle", args=[self.owner.pk]))
        self.parking.refresh_from_db(); draft.refresh_from_db()
        self.assertEqual((self.parking.status, draft.status), ("SUSPENDED", "DRAFT"))
        self.assertEqual(AuditLog.objects.get(action="user.disabled").metadata["suspended_parkings"], 1)
        self.assertNotContains(self.client.get(reverse("parkings:search")), "City Park")   # gone from search straight away
        self.client.post(reverse("sadmin:user_toggle", args=[self.owner.pk]))             # enabling does NOT silently restore listings
        self.parking.refresh_from_db()
        self.assertEqual(self.parking.status, "SUSPENDED")

    def test_rules(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("sadmin:user_toggle", args=[self.driver.pk])).status_code, 405)   # POST only
        self.assertEqual(self.client.post(reverse("sadmin:user_toggle", args=[self.admin.pk])).status_code, 404)    # never the superadmin
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class AdminBookingsTests(AdminBase):
    def test_list_filters_and_default_hides_unpaid_holds(self):
        paid, _ = self.paid()
        hold = self.hold(start=future(96), plate="OD02AB5678")
        self.client.force_login(self.admin)
        rows = lambda **p: [b.pk for b in self.client.get(reverse("sadmin:bookings"), p).context["page"]]
        self.assertEqual(rows(), [paid.pk])
        self.assertEqual(rows(status="PENDING_PAYMENT"), [hold.pk])
        self.assertEqual(rows(q=paid.booking_code.lower()), [paid.pk])           # code, any case
        self.assertEqual(rows(q="od02 ab 1234"), [paid.pk])                       # plate with spaces
        self.assertEqual(rows(q="d@example.com"), [paid.pk])                      # driver email
        self.assertEqual(rows(q="City Park"), [paid.pk])
        self.assertEqual(rows(q="o@example.com"), [paid.pk])                      # owner email
        self.assertEqual(rows(q="zzz-nothing"), [])
        day = timezone.localtime(paid.start_at).date()
        self.assertEqual(rows(**{"from": day.isoformat(), "to": day.isoformat()}), [paid.pk])
        self.assertEqual(rows(**{"from": (day + timedelta(days=30)).isoformat()}), [])
        self.assertEqual(rows(**{"to": (day - timedelta(days=30)).isoformat()}), [])
        self.assertEqual(self.client.get(reverse("sadmin:bookings"), {"from": "garbage", "status": "bogus"}).status_code, 200)

    def test_detail_shows_the_whole_money_trail(self):
        b, payment = self.paid()
        refund = services.request_refund(b.pk, self.driver, "Plans changed")
        services.approve_refund(refund.pk, self.owner)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("sadmin:booking_detail", args=[b.pk]))
        for needle in (payment.razorpay_order_id, "Refunded", "Plans changed", "Earning", "Refund", "o@example.com", "OD02AB1234"):
            self.assertContains(page, needle)
        self.assertEqual(self.client.get(reverse("sadmin:booking_detail", args=[999999])).status_code, 404)


class LegalAndSupportTests(AdminBase):
    def test_pages_exist_and_are_linked(self):
        for name, heading in (("core:terms", "Terms of Use"), ("core:privacy", "Privacy Policy")):
            resp = self.client.get(reverse(name))
            self.assertContains(resp, heading)
            self.assertContains(resp, "Razorpay")
        self.assertContains(self.client.get(reverse("accounts:signup")), f'href="{reverse("core:terms")}"')
        self.assertContains(self.client.get(reverse("accounts:signup")), f'href="{reverse("core:privacy")}"')
        landing = self.client.get("/")
        self.assertContains(landing, reverse("core:terms"))
        self.assertContains(landing, reverse("core:privacy"))
        self.assertContains(self.client.get("/sitemap.xml"), "/terms/")
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse("core:terms")).status_code, 200)    # also fine when signed in

    def test_support_email_appears_once_configured_and_updates_immediately(self):
        self.assertNotContains(self.client.get("/"), "mailto:")
        cfg = SiteSettings.load()
        cfg.support_email = "help@parkshare.example"
        cfg.save()                                       # clears the cached value
        self.assertContains(self.client.get("/"), "mailto:help@parkshare.example")
        self.assertContains(self.client.get(reverse("core:privacy")), "help@parkshare.example")
        self.assertContains(self.client.get(reverse("parkings:search")), "help@parkshare.example")   # public layout footer
