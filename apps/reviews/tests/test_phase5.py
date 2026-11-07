from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OwnerProfile, PointLog, User
from apps.bookings.models import Booking, DriverVerdict
from apps.bookings.services import BookingError
from apps.bookings.tests.helpers import S, future, mk_booking, mk_parking, mk_type
from apps.core.models import Notification, SiteSettings
from apps.parkings.search import build_queryset
from apps.reviews import services
from apps.reviews.models import ParkingReview

PW = "Tr1cky-Pass!77"


class Base(TestCase):
    def setUp(self):
        SiteSettings.load()
        self.owner = User.objects.create_user("o@example.com", PW, full_name="Olive Owner", role="OWNER", phone="9400000001")
        OwnerProfile.objects.create(user=self.owner, business_name="City Parks Co", about="Safe lots since 2020")
        self.other_owner = User.objects.create_user("o2@example.com", PW, full_name="Rival", role="OWNER", phone="9400000002")
        OwnerProfile.objects.create(user=self.other_owner, business_name="Rival Parking")
        self.driver = User.objects.create_user("d@example.com", PW, full_name="Dee Driverson", role="DRIVER", phone="9400000003")
        self.driver2 = User.objects.create_user("d2@example.com", PW, full_name="Eve Other", role="DRIVER", phone="9400000004")
        self.admin = User.objects.create_user("a@example.com", PW, full_name="Admin", role="SUPERADMIN", is_staff=True)
        self.parking = mk_parking(self.owner, name="Alpha Plaza", is_verified_badge=True)
        self.vt = mk_type(self.parking, capacity=5)

    def done(self, driver=None, hours_ago=30, plate=None, parking_vt=None):
        vt = parking_vt or self.vt
        return mk_booking(vt, driver or self.driver, future(-hours_ago), future(-hours_ago + 2), status=S.COMPLETED, plate=plate)


class ReviewServiceTests(Base):
    def test_submit_updates_parking_rating_and_notifies(self):
        services.submit_review(self.done().pk, self.driver, 5, " Great spot ")
        services.submit_review(self.done(driver=self.driver2).pk, self.driver2, 4, "")
        self.parking.refresh_from_db()
        self.assertEqual((self.parking.avg_rating, self.parking.review_count), (Decimal("4.50"), 2))
        self.assertEqual(ParkingReview.objects.get(driver=self.driver).comment, "Great spot")
        self.assertTrue(Notification.objects.filter(user=self.owner, title="New 5-star review").exists())

    def test_only_completed_own_and_once(self):
        pending = mk_booking(self.vt, self.driver, future(30), future(32), status=S.CONFIRMED)
        with self.assertRaisesMessage(BookingError, "completed"):
            services.submit_review(pending.pk, self.driver, 5, "")
        b = self.done()
        with self.assertRaises(Booking.DoesNotExist):
            services.submit_review(b.pk, self.driver2, 5, "")
        services.submit_review(b.pk, self.driver, 5, "")
        with self.assertRaisesMessage(BookingError, "already reviewed"):
            services.submit_review(b.pk, self.driver, 1, "")

    def test_edit_within_seven_days_only_and_recomputes(self):
        r = services.submit_review(self.done().pk, self.driver, 5, "ok")
        services.update_review(r.pk, self.driver, 2, "changed my mind")
        self.parking.refresh_from_db(); self.assertEqual(self.parking.avg_rating, Decimal("2.00"))
        ParkingReview.objects.filter(pk=r.pk).update(created_at=timezone.now() - timedelta(days=8))
        with self.assertRaisesMessage(BookingError, "7 days"):
            services.update_review(r.pk, self.driver, 5, "")
        with self.assertRaises(ParkingReview.DoesNotExist):
            services.update_review(r.pk, self.driver2, 5, "")

    def test_owner_reply_once_and_only_own(self):
        r = services.submit_review(self.done().pk, self.driver, 3, "meh")
        with self.assertRaises(ParkingReview.DoesNotExist):
            services.reply_to_review(r.pk, self.other_owner, "hi")
        services.reply_to_review(r.pk, self.owner, "Sorry about that")
        with self.assertRaisesMessage(BookingError, "already replied"):
            services.reply_to_review(r.pk, self.owner, "again")
        self.assertTrue(Notification.objects.filter(user=self.driver, title__contains="replied").exists())

    def test_distribution(self):
        for i, rating in enumerate((5, 5, 4, 1)):
            d = User.objects.create_user(f"x{i}@example.com", PW, full_name="X", role="DRIVER", phone=f"94000100{i}")
            services.submit_review(self.done(driver=d).pk, d, rating, "")
        dist = dict((s, (n, p)) for s, n, p in services.rating_distribution(ParkingReview.objects.all()))
        self.assertEqual(dist[5], (2, 50)); self.assertEqual(dist[4], (1, 25)); self.assertEqual(dist[3], (0, 0)); self.assertEqual(dist[1], (1, 25))


class ReviewViewTests(Base):
    def test_driver_writes_then_edits_via_pages(self):
        b = self.done()
        self.client.force_login(self.driver)
        self.assertContains(self.client.get(reverse("bookings:detail", args=[b.pk])), "Rate this parking")
        page = self.client.get(reverse("reviews:write", args=[b.pk]))
        self.assertContains(page, 'name="rating"')
        self.assertContains(self.client.post(reverse("reviews:write", args=[b.pk]), {"comment": "x"}), "Choose a star rating")
        resp = self.client.post(reverse("reviews:write", args=[b.pk]), {"rating": "4", "comment": "Easy entry"})
        self.assertRedirects(resp, reverse("bookings:detail", args=[b.pk]))
        self.assertContains(self.client.get(reverse("bookings:detail", args=[b.pk])), "Edit review")
        self.client.post(reverse("reviews:write", args=[b.pk]), {"rating": "5", "comment": "Even better"})
        self.parking.refresh_from_db(); self.assertEqual((self.parking.avg_rating, self.parking.review_count), (Decimal("5.00"), 1))

    def test_guards(self):
        b = self.done()
        pending = mk_booking(self.vt, self.driver, future(30), future(32), status=S.CONFIRMED)
        self.client.force_login(self.driver2)
        self.assertEqual(self.client.get(reverse("reviews:write", args=[b.pk])).status_code, 404)
        self.client.force_login(self.driver)
        self.assertRedirects(self.client.get(reverse("reviews:write", args=[pending.pk])), reverse("bookings:detail", args=[pending.pk]))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("reviews:write", args=[b.pk])).status_code, 403)

    def test_parking_page_shows_reviews_without_full_names(self):
        services.submit_review(self.done().pk, self.driver, 5, "Spotless and safe")
        resp = self.client.get(self.parking.get_absolute_url())
        self.assertContains(resp, "Spotless and safe")
        self.assertContains(resp, "Dee D.")
        self.assertNotContains(resp, "Driverson")
        self.assertContains(resp, "View owner profile")

    def test_owner_reviews_page_and_reply(self):
        r = services.submit_review(self.done().pk, self.driver, 2, "Tight turns")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("owner:reviews")), "Tight turns")
        self.client.post(reverse("owner:review_reply", args=[r.pk]), {"text": "We will repaint the lines"})
        r.refresh_from_db(); self.assertEqual(r.owner_reply, "We will repaint the lines")
        self.assertContains(self.client.get(self.parking.get_absolute_url()), "We will repaint the lines")
        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.post(reverse("owner:review_reply", args=[r.pk]), {"text": "hijack"}).status_code, 404)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse("owner:reviews")).status_code, 403)

    def test_search_min_rating_follows_reviews(self):
        self.assertEqual([p.name for p in build_queryset({"min_rating": "4"})[0]], [])
        services.submit_review(self.done().pk, self.driver, 5, "")
        self.assertEqual([p.name for p in build_queryset({"min_rating": "4"})[0]], ["Alpha Plaza"])


class OwnerProfileTests(Base):
    def test_public_profile(self):
        services.submit_review(self.done().pk, self.driver, 4, "Good")
        resp = self.client.get(reverse("reviews:owner_profile", args=[self.owner.pk]))
        self.assertContains(resp, "City Parks Co")
        self.assertContains(resp, "Alpha Plaza")
        self.assertContains(resp, "Safe lots since 2020")
        self.assertEqual((resp.context["completed"], resp.context["summary"]["n"], resp.context["verified_count"]), (1, 1, 1))

    def test_private_and_non_owner_profiles(self):
        OwnerProfile.objects.filter(user=self.owner).update(is_public=False)
        self.assertEqual(self.client.get(reverse("reviews:owner_profile", args=[self.owner.pk])).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("reviews:owner_profile", args=[self.owner.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("reviews:owner_profile", args=[self.driver.pk])).status_code, 404)


class DriverProfileTests(Base):
    def setUp(self):
        super().setUp()
        self.other_parking = mk_parking(self.other_owner, name="Other Lot")
        self.other_vt = mk_type(self.other_parking, capacity=5)
        b1 = self.done(plate="OD02AB1111")
        DriverVerdict.objects.create(booking=b1, owner=self.owner, driver=self.driver, satisfied=True, comment="Smooth")
        mk_booking(self.vt, self.driver, future(-10), future(-8), status=S.NO_SHOW, plate="OD02AB1111")
        c1 = mk_booking(self.vt, self.driver, future(-20), future(-18), status=S.CANCELLED, plate="OD02AB1111")
        c2 = mk_booking(self.vt, self.driver, future(-40), future(-38), status=S.CANCELLED, plate="OD02AB1111")
        Booking.objects.filter(pk=c1.pk).update(cancelled_by=self.driver)
        Booking.objects.filter(pk=c2.pk).update(cancelled_by=self.owner)  # owner-initiated: must not count against the driver
        mk_booking(self.vt, self.driver, future(5), future(7), status=S.PENDING_PAYMENT, hold=future(1), plate="OD02AB1111")
        b6 = mk_booking(self.other_vt, self.driver, future(-50), future(-48), status=S.COMPLETED, plate="OD02ZZ9999")
        DriverVerdict.objects.create(booking=b6, owner=self.other_owner, driver=self.driver, satisfied=False, comment="Blocked the exit")
        PointLog.objects.create(user=self.driver, delta=-15, balance_after=85, reason="NO_SHOW", note="Booking #9")

    def test_stats_verdicts_and_privacy(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("owner:driver_profile", args=[self.driver.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(dict(resp.context["stats"]), {"total": 5, "completed": 2, "cancelled": 1, "no_shows": 1})
        self.assertEqual((resp.context["v"]["total"], resp.context["v"]["good"], resp.context["satisfied_pct"]), (2, 1, 50))
        self.assertContains(resp, "Smooth")
        self.assertContains(resp, "Blocked the exit")
        self.assertContains(resp, "Rival Parking")  # the commenting owner's business name
        self.assertContains(resp, "OD02AB1111")
        self.assertNotContains(resp, "OD02ZZ9999")  # plates from other owners' bookings stay private
        self.assertContains(resp, "No-show")

    def test_only_owners_who_booked_with_driver(self):
        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.get(reverse("owner:driver_profile", args=[self.driver.pk])).status_code, 200)  # booked at Other Lot
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("owner:driver_profile", args=[self.driver2.pk])).status_code, 404)  # never booked
        self.assertEqual(self.client.get(reverse("owner:driver_profile", args=[self.other_owner.pk])).status_code, 404)  # not a driver
        self.client.force_login(self.driver2)
        self.assertEqual(self.client.get(reverse("owner:driver_profile", args=[self.driver.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("owner:driver_profile", args=[self.driver.pk])).status_code, 302)

    def test_driver_sees_own_points_history(self):
        self.client.force_login(self.driver)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertContains(resp, "Your points")
        self.assertContains(resp, "No-show")
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("accounts:profile")), "Your points")


class NotificationTests(Base):
    def note(self, user, link="/bookings/", read=False):
        return Notification.objects.create(user=user, title="Hello", message="World", link=link, is_read=read)

    def test_bell_count_go_and_read_all(self):
        n1, n2 = self.note(self.driver), self.note(self.driver, "/search/")
        self.client.force_login(self.driver)
        page = self.client.get(reverse("parkings:search"))
        self.assertContains(page, "2 unread")
        self.assertContains(page, "Mark all read")
        self.assertContains(page, "csrfmiddlewaretoken")
        self.assertRedirects(self.client.get(reverse("core:notification_go", args=[n1.pk])), "/bookings/", fetch_redirect_response=False)
        n1.refresh_from_db(); self.assertTrue(n1.is_read)
        self.client.post(reverse("core:notifications_read_all"))
        n2.refresh_from_db(); self.assertTrue(n2.is_read)
        self.assertContains(self.client.get(reverse("core:notifications")), "Hello")
        self.assertNotContains(self.client.get(reverse("parkings:search")), "unread")

    def test_unsafe_links_and_other_users(self):
        for bad in ("https://evil.example/x", "//evil.example/x", "/\\evil.example"):
            n = self.note(self.driver, bad)
            self.client.force_login(self.driver)
            resp = self.client.get(reverse("core:notification_go", args=[n.pk]))
            self.assertRedirects(resp, reverse("core:notifications"), fetch_redirect_response=False, msg_prefix=bad)
        other = self.note(self.driver2)
        self.assertEqual(self.client.get(reverse("core:notification_go", args=[other.pk])).status_code, 404)
        resp = self.client.post(reverse("core:notifications_read_all"), {"next": "https://evil.example"})
        self.assertRedirects(resp, reverse("core:notifications"), fetch_redirect_response=False)
        other.refresh_from_db(); self.assertFalse(other.is_read)

    def test_requires_login_and_works_in_owner_panel(self):
        self.assertEqual(self.client.get(reverse("core:notifications")).status_code, 302)
        self.note(self.owner, "/owner/reviews/")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("core:owner_home")), "1 unread")
        self.assertContains(self.client.get(reverse("core:notifications")), "Hello")
