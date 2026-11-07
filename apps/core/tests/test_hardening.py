from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.bookings.management.commands import run_scheduler as sched
from apps.bookings.tests.helpers import mk_parking
from apps.core.middleware import build_csp
from apps.core.views import LANDING_CACHE_KEY
from apps.core.models import SiteSettings

PW = "Tr1cky-Pass!77"


class SecurityHeaderTests(TestCase):
    def test_csp_is_report_only_by_default_and_allows_what_the_app_needs(self):
        resp = self.client.get("/")
        csp = resp["Content-Security-Policy-Report-Only"]
        self.assertNotIn("Content-Security-Policy", [h for h in resp.headers if h.lower() == "content-security-policy"])
        for needle in ("default-src 'self'", "https://checkout.razorpay.com", "https://cdn.jsdelivr.net", "https://res.cloudinary.com",
                       "https://fonts.googleapis.com", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'"):
            self.assertIn(needle, csp)
        self.assertIn("camera=(self)", resp["Permissions-Policy"])
        self.assertIn("geolocation=(self)", resp["Permissions-Policy"])

    def test_modes(self):
        with override_settings(CSP_MODE="enforce"):
            resp = self.client.get("/")
            self.assertIn("script-src", resp["Content-Security-Policy"])
            self.assertFalse(resp.has_header("Content-Security-Policy-Report-Only"))
        with override_settings(CSP_MODE="off"):
            resp = self.client.get("/")
            self.assertFalse(resp.has_header("Content-Security-Policy"))
            self.assertFalse(resp.has_header("Content-Security-Policy-Report-Only"))
            self.assertTrue(resp.has_header("Permissions-Policy"))
        with override_settings(CSP_REPORT_URI="https://example.report/csp"):
            self.assertIn("report-uri https://example.report/csp", build_csp())

    def test_admin_is_exempt_and_url_is_configurable(self):
        resp = self.client.get("/" + settings.ADMIN_URL + "login/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.has_header("Content-Security-Policy-Report-Only"))
        self.assertContains(self.client.get("/robots.txt"), f"Disallow: /{settings.ADMIN_URL}")
        self.assertTrue(settings.ADMIN_URL.endswith("/") and not settings.ADMIN_URL.startswith("/"))

    def test_error_pages_still_carry_headers(self):
        resp = self.client.get("/definitely-not-here/")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.has_header("Permissions-Policy"))


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteSettings.load()
        self.driver = User.objects.create_user("rl@example.com", PW, full_name="Rate Limited", role="DRIVER", phone="9500000001")

    @override_settings(RATELIMIT_ENABLE=True)
    def test_payment_endpoint_is_limited_per_user(self):
        self.client.force_login(self.driver)
        url = reverse("payments:booking_order", args=[999999])
        codes = [self.client.post(url).status_code for _ in range(61)]
        self.assertEqual(set(codes[:60]), {404})   # unknown booking, but each attempt still counts
        self.assertEqual(codes[60], 403)

    @override_settings(RATELIMIT_ENABLE=True)
    def test_limit_is_per_user_not_global(self):
        other = User.objects.create_user("rl2@example.com", PW, full_name="Other", role="DRIVER", phone="9500000002")
        self.client.force_login(self.driver)
        for _ in range(61):
            self.client.post(reverse("payments:booking_order", args=[999999]))
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("payments:booking_order", args=[999999])).status_code, 404)


class LandingCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("lc@example.com", PW, full_name="Owner", role="OWNER", phone="9500000003")

    def test_cached_but_refreshed_when_a_parking_changes(self):
        p = mk_parking(self.owner, name="Alpha Plaza", is_verified_badge=True)
        self.assertIsNone(cache.get(LANDING_CACHE_KEY))
        self.assertContains(self.client.get("/"), "Alpha Plaza")
        self.assertIsNotNone(cache.get(LANDING_CACHE_KEY))   # now served from the cache
        p.name = "Renamed Plaza"
        p.save()                                         # post_save clears the cache
        self.assertIsNone(cache.get(LANDING_CACHE_KEY))
        resp = self.client.get("/")
        self.assertContains(resp, "Renamed Plaza")
        self.assertNotContains(resp, "Alpha Plaza")

    def test_unapproved_parking_disappears_after_suspension(self):
        p = mk_parking(self.owner, name="Beta Yard", is_verified_badge=True)
        self.assertContains(self.client.get("/"), "Beta Yard")
        p.status = "SUSPENDED"
        p.save()
        self.assertNotContains(self.client.get("/"), "Beta Yard")


class SchedulerTests(TestCase):
    JOBS = [("fast", 60, {}), ("slow", 900, {}), ("flag", 100, {"warn_only": True})]

    def test_due_jobs(self):
        self.assertEqual([n for n, _ in sched.due_jobs({}, 0, self.JOBS)], ["fast", "slow", "flag"])  # never run: all due
        last = {"fast": 100, "slow": 100, "flag": 100}
        self.assertEqual(sched.due_jobs(last, 150, self.JOBS), [])
        self.assertEqual([n for n, _ in sched.due_jobs(last, 165, self.JOBS)], ["fast"])
        self.assertEqual([n for n, _ in sched.due_jobs(last, 205, self.JOBS)], ["fast", "flag"])

    def test_run_due_records_runs_and_isolates_failures(self):
        calls = []

        def fake(name, **kwargs):
            calls.append((name, kwargs))
            if name == "fast":
                raise RuntimeError("boom")

        last = {}
        with mock.patch.object(sched, "call_command", side_effect=fake):
            ran = sched.run_due(last, 1000, self.JOBS)
            self.assertEqual(ran, ["slow", "flag"])                     # "fast" failed, the others still ran
            self.assertEqual([c[0] for c in calls], ["fast", "slow", "flag"])
            self.assertEqual(calls[2][1], {"verbosity": 0, "warn_only": True})
            self.assertEqual(sched.run_due(last, 1001, self.JOBS), [])  # nothing due a second later (failed jobs are not retried at once)

    def test_real_one_shot_run_executes_every_job(self):
        call_command("run_scheduler", once=True)   # runs expire / no-show / auto-complete / reconcile against the test database

    def test_configured_jobs_exist(self):
        from django.core.management import get_commands

        known = get_commands()
        for name, _every, _kw in sched.JOBS:
            self.assertIn(name, known)
