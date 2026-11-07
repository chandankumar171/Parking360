import io
import shutil
import tempfile
from datetime import time
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification, SiteSettings
from apps.parkings import services
from apps.parkings.maps import extract_lat_lng, is_google_maps_url
from apps.parkings.models import Parking, ParkingPhoto, ParkingVehicleType

PW = "Tr1cky-Pass!77"
TMP_MEDIA = tempfile.mkdtemp()


def png(name="p.png", size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def make_parking(owner, **kw):
    data = dict(owner=owner, name="City Park", description="Safe parking", address_line="1 Main Rd", area="Saheed Nagar",
                city="Bhubaneswar", state="Odisha", pincode="751007", google_maps_url="https://www.google.com/maps/@20.29,85.84,17z")
    data.update(kw)
    return Parking.objects.create(**data)


def make_ready(owner, **kw):
    p = make_parking(owner, **kw)
    ParkingVehicleType.objects.create(parking=p, category="FOUR_WHEELER", capacity=5, price_per_slot=50,
                                      overtime_block_minutes=30, overtime_charge=100)
    ParkingPhoto.objects.create(parking=p, image=png(), is_cover=True)
    return p


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        SiteSettings.load()
        self.owner = User.objects.create_user("o1@example.com", PW, full_name="Owner One", role="OWNER", phone="9000000011")
        self.owner2 = User.objects.create_user("o2@example.com", PW, full_name="Owner Two", role="OWNER", phone="9000000012")
        self.driver = User.objects.create_user("d1@example.com", PW, full_name="Driver", role="DRIVER", phone="9000000013")
        self.admin = User.objects.create_user("adm@example.com", PW, full_name="Admin", role="SUPERADMIN", is_staff=True)


class MapsTests(TestCase):
    def test_extract_coordinates(self):
        self.assertEqual(extract_lat_lng("https://www.google.com/maps/place/X/@20.296059,85.824539,17z"),
                         (Decimal("20.296059"), Decimal("85.824539")))
        self.assertEqual(extract_lat_lng("https://www.google.com/maps?q=20.3,85.8"), (Decimal("20.300000"), Decimal("85.800000")))
        pin = "https://www.google.com/maps/place/X/@20.1,85.1,17z/data=!3d20.296059!4d85.824539"
        self.assertEqual(extract_lat_lng(pin)[0], Decimal("20.296059"))  # exact pin beats viewport
        self.assertIsNone(extract_lat_lng("https://maps.app.goo.gl/abc123"))
        self.assertIsNone(extract_lat_lng("https://www.google.com/maps/@200.1,85.1,17z"))  # out of range

    def test_host_allow_list(self):
        self.assertTrue(is_google_maps_url("https://maps.app.goo.gl/abc"))
        self.assertTrue(is_google_maps_url("https://www.google.co.in/maps/place/x"))
        self.assertFalse(is_google_maps_url("https://evilgoogle.com/maps"))
        self.assertFalse(is_google_maps_url("https://google.com.evil.io/maps"))
        self.assertFalse(is_google_maps_url("javascript:alert(1)"))


class ModelTests(Base):
    def test_overtime_blocks_round_up(self):
        t = ParkingVehicleType(overtime_block_minutes=30, overtime_charge=Decimal("100"), overtime_free_minutes=0)
        self.assertEqual(t.overtime_cost(0), 0)
        self.assertEqual(t.overtime_cost(1), 100)
        self.assertEqual(t.overtime_cost(30), 100)
        self.assertEqual(t.overtime_cost(45), 200)
        per_min = ParkingVehicleType(overtime_block_minutes=1, overtime_charge=Decimal("6"), overtime_free_minutes=10)
        self.assertEqual(per_min.overtime_cost(10), 0)
        self.assertEqual(per_min.overtime_cost(25), 90)  # 15 billable minutes x 6

    def test_open_now_with_overnight_hours(self):
        p = make_parking(self.owner, status="APPROVED", is_24x7=False, opening_time=time(22, 0), closing_time=time(6, 0))
        import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        at = lambda h: datetime.datetime(2026, 1, 5, h, 0, tzinfo=tz)
        self.assertTrue(p.is_open_now(at(23)))
        self.assertTrue(p.is_open_now(at(3)))
        self.assertFalse(p.is_open_now(at(12)))
        p.is_open = False
        self.assertFalse(p.is_open_now(at(23)))

    def test_slug_unique_and_contains_pk(self):
        a, b = make_parking(self.owner), make_parking(self.owner)
        self.assertNotEqual(a.slug, b.slug)
        self.assertTrue(a.slug.endswith(f"-{a.pk}"))


class SubmissionAndVerificationTests(Base):
    def test_submit_requires_vehicle_type_and_photo(self):
        p = make_parking(self.owner)
        ok, issues = services.submit_parking(p, self.owner)
        self.assertFalse(ok)
        self.assertEqual(len(issues), 2)

    def test_verification_required_goes_pending_then_badge_on_approve(self):
        p = make_ready(self.owner)
        ok, _ = services.submit_parking(p, self.owner)
        p.refresh_from_db()
        self.assertTrue(ok)
        self.assertEqual(p.status, "PENDING")
        self.assertFalse(p.is_verified_badge)
        self.assertTrue(Notification.objects.filter(user=self.admin).exists())
        services.approve(p, self.admin)
        p.refresh_from_db()
        self.assertEqual((p.status, p.is_verified_badge, p.verified_by), ("APPROVED", True, self.admin))

    def test_verification_not_required_goes_live_without_badge(self):
        cfg = SiteSettings.load(); cfg.verification_required = False; cfg.save()
        p = make_ready(self.owner)
        services.submit_parking(p, self.owner)
        p.refresh_from_db()
        self.assertEqual((p.status, p.is_verified_badge), ("APPROVED", False))

    def test_switching_verification_off_auto_approves_pending(self):
        p1, p2 = make_ready(self.owner), make_ready(self.owner2, name="Other")
        services.submit_parking(p1, self.owner); services.submit_parking(p2, self.owner2)
        self.client.force_login(self.admin)
        data = {"platform_fee_percent": "0", "default_grace_minutes": 15, "default_cancel_window_minutes": 60,
                "support_email": "", "points_start": 100, "points_satisfied": 5, "points_unsatisfied": -10,
                "points_no_show": -15, "points_late_cancel": -5, "points_no_checkout_verify": -20}  # verification_required omitted = off
        resp = self.client.post(reverse("core:platform_settings"), data)
        self.assertRedirects(resp, reverse("core:platform_settings"))
        for p in (p1, p2):
            p.refresh_from_db()
            self.assertEqual((p.status, p.is_verified_badge), ("APPROVED", False))
        self.assertEqual(AuditLog.objects.get(action="parking.auto_approved").metadata["count"], 2)

    def test_reject_then_resubmit(self):
        p = make_ready(self.owner)
        services.submit_parking(p, self.owner)
        services.reject(p, self.admin, "Photos are blurry")
        p.refresh_from_db()
        self.assertEqual((p.status, p.rejection_reason), ("REJECTED", "Photos are blurry"))
        self.assertTrue(services.submit_parking(p, self.owner)[0])

    def test_edit_of_live_parking_reverifies_only_when_configured(self):
        p = make_ready(self.owner, status="APPROVED", is_verified_badge=True)
        self.assertFalse(services.mark_edited(p, self.owner))
        cfg = SiteSettings.load(); cfg.reverify_on_edit = True; cfg.save()
        self.assertTrue(services.mark_edited(p, self.owner))
        p.refresh_from_db()
        self.assertEqual((p.status, p.is_verified_badge), ("PENDING", False))

    def test_suspend_and_restore(self):
        p = make_ready(self.owner, status="APPROVED")
        services.suspend(p, self.admin, "Complaints")
        p.refresh_from_db(); self.assertEqual(p.status, "SUSPENDED")
        services.unsuspend(p, self.admin)
        p.refresh_from_db(); self.assertEqual(p.status, "APPROVED")


class PhotoTests(Base):
    def test_limit_of_five_cover_and_delete_rules(self):
        p = make_parking(self.owner)
        added, errors = services.add_photos(p, [png(f"{i}.png") for i in range(7)])
        self.assertEqual(added, 5)
        self.assertTrue(errors)
        self.assertEqual(p.photos.filter(is_cover=True).count(), 1)
        cover = p.photos.get(is_cover=True)
        other = p.photos.exclude(pk=cover.pk).first()
        services.set_cover(other)
        self.assertEqual(p.photos.filter(is_cover=True).get(), other)
        services.delete_photo(other)  # deleting the cover promotes another photo
        self.assertEqual(p.photos.filter(is_cover=True).count(), 1)

    def test_rejects_non_images(self):
        p = make_parking(self.owner)
        fake = SimpleUploadedFile("x.png", b"not really an image", content_type="image/png")
        added, errors = services.add_photos(p, [fake])
        self.assertEqual(added, 0)
        self.assertTrue(errors)

    def test_cannot_delete_last_photo_of_listed_parking(self):
        from django.core.exceptions import ValidationError
        p = make_ready(self.owner, status="APPROVED")
        with self.assertRaises(ValidationError):
            services.delete_photo(p.photos.get())


class OwnerViewTests(Base):
    def basics(self, **extra):
        d = {"name": "Airport Lot", "description": "Near terminal", "address_line": "NH16", "area": "Airport", "city": "Bhubaneswar",
             "state": "Odisha", "pincode": "751020", "google_maps_url": "https://www.google.com/maps/@20.2444,85.8178,17z",
             "latitude": "", "longitude": "", "contact_phone": "98765 43210"}
        d.update(extra)
        return d

    def test_create_draft_parses_coordinates(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("owner:parking_create"), self.basics())
        p = Parking.objects.get(owner=self.owner)
        self.assertRedirects(resp, reverse("owner:parking_edit", args=[p.pk, "vehicles"]))
        self.assertEqual((p.status, p.latitude, p.longitude), ("DRAFT", Decimal("20.244400"), Decimal("85.817800")))
        self.assertEqual(p.contact_phone, "9876543210")

    def test_non_google_link_rejected(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("owner:parking_create"), self.basics(google_maps_url="https://example.com/x"))
        self.assertContains(resp, "Paste a link from Google Maps")
        self.assertFalse(Parking.objects.exists())

    def test_vehicle_formset_saves_and_validates(self):
        p = make_parking(self.owner)
        self.client.force_login(self.owner)
        row = {"vt-TOTAL_FORMS": 1, "vt-INITIAL_FORMS": 0, "vt-MIN_NUM_FORMS": 1, "vt-MAX_NUM_FORMS": 6,
               "vt-0-category": "TWO_WHEELER", "vt-0-label": "", "vt-0-capacity": 20, "vt-0-slot_minutes": 60,
               "vt-0-price_per_slot": "20", "vt-0-min_slots": 1, "vt-0-max_slots": 12, "vt-0-overtime_free_minutes": 0,
               "vt-0-overtime_block_minutes": 1, "vt-0-overtime_charge": "6", "vt-0-is_active": "on"}
        url = reverse("owner:parking_edit", args=[p.pk, "vehicles"])
        bad = dict(row, **{"vt-0-min_slots": 10, "vt-0-max_slots": 2})
        self.assertContains(self.client.post(url, bad), "Maximum slots must be at least the minimum")
        self.assertEqual(p.vehicle_types.count(), 0)
        self.assertEqual(self.client.post(url, row).status_code, 302)
        self.assertEqual(p.vehicle_types.get().overtime_cost(3), 18)

    def test_owner_cannot_touch_other_owners_parking(self):
        p = make_ready(self.owner2)
        self.client.force_login(self.owner)
        for step in ("basics", "vehicles", "review"):
            self.assertEqual(self.client.get(reverse("owner:parking_edit", args=[p.pk, step])).status_code, 404)
        self.assertEqual(self.client.post(reverse("owner:parking_delete", args=[p.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("owner:parking_submit", args=[p.pk])).status_code, 404)
        self.assertTrue(Parking.objects.filter(pk=p.pk).exists())

    def test_full_flow_submit_and_toggle_and_delete(self):
        p = make_ready(self.owner)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("owner:parking_edit", args=[p.pk, "review"])), "Submit for verification")
        self.client.post(reverse("owner:parking_submit", args=[p.pk]))
        p.refresh_from_db(); self.assertEqual(p.status, "PENDING")
        self.client.post(reverse("owner:parking_toggle_open", args=[p.pk]))
        p.refresh_from_db(); self.assertFalse(p.is_open)
        self.client.post(reverse("owner:parking_delete", args=[p.pk]))
        self.assertFalse(Parking.objects.filter(pk=p.pk).exists())

    def test_suspended_parking_cannot_be_edited(self):
        p = make_ready(self.owner, status="SUSPENDED")
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get(reverse("owner:parking_edit", args=[p.pk, "basics"])), reverse("owner:parking_list"))

    def test_amenities_require_hours_unless_24x7(self):
        p = make_parking(self.owner)
        self.client.force_login(self.owner)
        url = reverse("owner:parking_edit", args=[p.pk, "amenities"])
        base = {"grace_minutes": 15, "cancel_window_minutes": 60, "cancellation_charge_percent": "0", "is_open": "on"}
        self.assertContains(self.client.post(url, base), "Set both opening and closing time")
        ok = self.client.post(url, dict(base, is_24x7="on", has_cctv="on"))
        self.assertEqual(ok.status_code, 302)
        p.refresh_from_db(); self.assertTrue(p.has_cctv)


class SuperadminViewTests(Base):
    def test_role_gates(self):
        for user in (self.owner, self.driver):
            self.client.force_login(user)
            for name in ("sadmin:queue", "sadmin:parking_list", "core:platform_settings", "core:audit_log"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.client.force_login(self.admin)
        for name in ("sadmin:queue", "sadmin:parking_list", "core:platform_settings", "core:audit_log"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.client.force_login(self.driver)
        self.assertEqual(self.client.get(reverse("owner:parking_list")).status_code, 403)

    def test_approve_and_reject_via_http(self):
        p = make_ready(self.owner)
        services.submit_parking(p, self.owner)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("sadmin:queue")), "City Park")
        self.client.post(reverse("sadmin:reject", args=[p.pk]), {"reason": "no"})  # too short
        p.refresh_from_db(); self.assertEqual(p.status, "PENDING")
        self.client.post(reverse("sadmin:approve", args=[p.pk]))
        p.refresh_from_db(); self.assertTrue(p.is_verified_badge)
        self.assertContains(self.client.get(reverse("sadmin:parking_detail", args=[p.pk])), "Verified")

    def test_penalty_values_must_not_be_positive(self):
        self.client.force_login(self.admin)
        data = {"verification_required": "on", "platform_fee_percent": "5", "default_grace_minutes": 15,
                "default_cancel_window_minutes": 60, "support_email": "", "points_start": 100, "points_satisfied": 5,
                "points_unsatisfied": 10, "points_no_show": -15, "points_late_cancel": -5, "points_no_checkout_verify": -20}
        resp = self.client.post(reverse("core:platform_settings"), data)
        self.assertContains(resp, "Penalties must be zero or negative")
