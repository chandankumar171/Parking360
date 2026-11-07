from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse


class JobsEndpointTests(TestCase):
    url = "/jobs/run/"

    @override_settings(JOBS_TOKEN="")
    def test_does_not_exist_without_a_token(self):
        self.assertEqual(self.client.get(self.url, {"token": "anything"}).status_code, 404)

    @override_settings(JOBS_TOKEN="s3cret-token")
    def test_wrong_or_missing_token_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.url, {"token": "nope"}).status_code, 404)
        self.assertEqual(self.client.post(self.url, HTTP_X_JOBS_TOKEN="nope").status_code, 404)

    @override_settings(JOBS_TOKEN="s3cret-token")
    def test_correct_token_runs_the_jobs(self):
        with mock.patch("apps.core.views.call_command") as run:
            resp = self.client.get(self.url, {"token": "s3cret-token"})
        self.assertEqual(resp.json(), {"status": "ok", "ran": ["expire_pending_bookings", "mark_no_shows", "auto_complete_bookings"]})
        self.assertEqual([c.args[0] for c in run.call_args_list], ["expire_pending_bookings", "mark_no_shows", "auto_complete_bookings"])
        with mock.patch("apps.core.views.call_command") as run:
            resp = self.client.post(self.url + "?reconcile=1", HTTP_X_JOBS_TOKEN="s3cret-token")   # header works too
        self.assertEqual(resp.json()["ran"][-1], "reconcile_payments")
        self.assertEqual(reverse("core:run_jobs"), self.url)

    @override_settings(JOBS_TOKEN="s3cret-token")
    def test_really_runs_against_the_database(self):
        self.assertEqual(self.client.get(self.url, {"token": "s3cret-token"}).status_code, 200)
