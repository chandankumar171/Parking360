from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import OwnerProfile, User
from apps.accounts.services import ensure_superadmin
from apps.core.validators import normalize_phone, normalize_plate


class SuperadminBootstrapTests(TestCase):
    @override_settings(SUPERADMIN_EMAIL="Root@Example.com", SUPERADMIN_PASSWORD="Str0ng-Pass!x9")
    def test_created_then_idempotent_then_password_rotates(self):
        user, action = ensure_superadmin()
        self.assertEqual(action, "created")
        self.assertEqual(user.email, "root@example.com")
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertEqual(user.role, User.Role.SUPERADMIN)
        self.assertEqual(ensure_superadmin()[1], "unchanged")
        self.assertEqual(User.objects.filter(email__iexact="root@example.com").count(), 1)

        with override_settings(SUPERADMIN_PASSWORD="An0ther-Pass!y8"):
            user2, action2 = ensure_superadmin()
        self.assertEqual(action2, "updated")
        self.assertTrue(User.objects.get(pk=user.pk).check_password("An0ther-Pass!y8"))

    @override_settings(SUPERADMIN_EMAIL="", SUPERADMIN_PASSWORD="")
    def test_skipped_when_not_configured(self):
        self.assertIsNone(ensure_superadmin())

    @override_settings(SUPERADMIN_EMAIL="not-an-email", SUPERADMIN_PASSWORD="Str0ng-Pass!x9")
    def test_invalid_email_is_rejected(self):
        self.assertIsNone(ensure_superadmin())


class SignupLoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def _payload(self, **extra):
        data = {
            "role": "DRIVER", "full_name": "Asha Rout", "email": "asha@example.com",
            "phone": "+91 98765 43210", "business_name": "", "password1": "Tr1cky-Pass!77",
            "password2": "Tr1cky-Pass!77", "terms": "on",
        }
        data.update(extra)
        return data

    def test_driver_signup_logs_in_and_redirects_to_driver_home(self):
        resp = self.client.post(reverse("accounts:signup"), self._payload())
        self.assertRedirects(resp, reverse("parkings:search"))
        user = User.objects.get(email="asha@example.com")
        self.assertEqual(user.phone, "9876543210")
        self.assertEqual(user.role, User.Role.DRIVER)
        self.assertEqual(user.points, 100)

    def test_owner_signup_creates_profile_and_needs_business_name(self):
        bad = self.client.post(reverse("accounts:signup"), self._payload(role="OWNER"))
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(User.objects.filter(email="asha@example.com").exists())
        ok = self.client.post(
            reverse("accounts:signup"), self._payload(role="OWNER", business_name="City Park")
        )
        self.assertRedirects(ok, reverse("core:owner_home"))
        self.assertEqual(OwnerProfile.objects.get(user__email="asha@example.com").business_name, "City Park")

    def test_superadmin_role_cannot_be_chosen_at_signup(self):
        resp = self.client.post(reverse("accounts:signup"), self._payload(role="SUPERADMIN"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="asha@example.com").exists())

    def test_duplicate_email_and_phone_rejected(self):
        self.client.post(reverse("accounts:signup"), self._payload())
        self.client.logout()
        resp = self.client.post(reverse("accounts:signup"), self._payload(email="ASHA@example.com"))
        self.assertContains(resp, "already exists")
        resp = self.client.post(reverse("accounts:signup"), self._payload(email="new@example.com"))
        self.assertContains(resp, "already registered")

    def test_login_is_case_insensitive_and_role_redirect(self):
        User.objects.create_user("own@example.com", "Tr1cky-Pass!77", full_name="O", role="OWNER", phone="9000000001")
        resp = self.client.post(
            reverse("accounts:login"), {"username": "OWN@Example.com", "password": "Tr1cky-Pass!77"}
        )
        self.assertRedirects(resp, reverse("core:owner_home"))

    def test_wrong_password_shows_generic_error(self):
        User.objects.create_user("d@example.com", "Tr1cky-Pass!77", full_name="D", phone="9000000002")
        resp = self.client.post(reverse("accounts:login"), {"username": "d@example.com", "password": "nope"})
        self.assertContains(resp, "Incorrect email or password")


class RoleAccessTests(TestCase):
    def setUp(self):
        self.driver = User.objects.create_user("d@example.com", "Tr1cky-Pass!77", full_name="D", role="DRIVER", phone="9000000003")
        self.owner = User.objects.create_user("o@example.com", "Tr1cky-Pass!77", full_name="O", role="OWNER", phone="9000000004")
        self.admin = User.objects.create_user("a@example.com", "Tr1cky-Pass!77", full_name="A", role="SUPERADMIN", is_staff=True)

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(reverse("core:owner_home"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp["Location"])

    def test_each_role_only_reaches_its_own_home(self):
        cases = {
            self.driver: ("parkings:search", ["core:owner_home", "core:superadmin_home"]),
            self.owner: ("core:owner_home", ["core:driver_home", "core:superadmin_home"]),
            self.admin: ("core:superadmin_home", ["core:owner_home", "core:driver_home"]),
        }
        for user, (allowed, denied) in cases.items():
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse(allowed)).status_code, 200, allowed)
            for name in denied:
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, f"{user.role} -> {name}")

    def test_vehicle_crud_is_private_to_driver(self):
        self.client.force_login(self.driver)
        resp = self.client.post(
            reverse("accounts:vehicles"),
            {"category": "FOUR_WHEELER", "plate_number": "od 02 ab 1234", "brand": "Tata", "model": "Nexon", "color": "White"},
        )
        self.assertRedirects(resp, reverse("accounts:vehicles"))
        vehicle = self.driver.vehicles.get()
        self.assertEqual(vehicle.plate_number, "OD02AB1234")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("accounts:vehicles")).status_code, 403)
        self.assertEqual(self.client.post(reverse("accounts:vehicle_delete", args=[vehicle.pk])).status_code, 403)


class PublicPageTests(TestCase):
    def test_landing_health_robots_sitemap(self):
        self.assertContains(self.client.get("/"), "Reserve parking before you reach the gate")
        self.assertEqual(self.client.get("/healthz/").json(), {"status": "ok"})
        self.assertContains(self.client.get("/robots.txt"), "Sitemap:")
        self.assertEqual(self.client.get("/sitemap.xml").status_code, 200)


class ValidatorTests(TestCase):
    def test_normalizers(self):
        self.assertEqual(normalize_phone("+91 98765-43210"), "9876543210")
        self.assertEqual(normalize_phone("09876543210"), "9876543210")
        self.assertEqual(normalize_plate("od-02 ab 1234"), "OD02AB1234")
