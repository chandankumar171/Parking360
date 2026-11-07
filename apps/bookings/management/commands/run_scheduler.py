"""A tiny scheduler so deployments don't need a separate cron setup: `python manage.py run_scheduler`."""
import logging
import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# (management command, run every N seconds, keyword arguments)
JOBS = [
    ("expire_pending_bookings", 60, {}),
    ("mark_no_shows", 300, {}),
    ("auto_complete_bookings", 900, {}),
    ("reconcile_payments", 6 * 3600, {"warn_only": True}),
]


def due_jobs(last_run, now, jobs=JOBS):
    """Jobs whose interval has elapsed. A job that has never run is always due."""
    return [(name, kwargs) for name, every, kwargs in jobs if now - last_run.get(name, float("-inf")) >= every]


def run_due(last_run, now, jobs=JOBS):
    """Run everything that is due. One failing job never stops the others. Returns the names that succeeded."""
    ran = []
    for name, kwargs in due_jobs(last_run, now, jobs):
        last_run[name] = now
        try:
            call_command(name, verbosity=0, **kwargs)
            ran.append(name)
        except Exception:
            logger.exception("Scheduled job %s failed", name)
    return ran


class Command(BaseCommand):
    help = "Run the periodic jobs (expire holds, no-shows, auto-complete, reconciliation) until stopped."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run every due job once and exit.")
        parser.add_argument("--tick", type=int, default=15, help="Seconds between checks (default 15).")

    def handle(self, *args, **options):
        stop = {"now": False}

        def request_stop(signum, frame):
            stop["now"] = True

        if not options["once"]:  # a one-shot run (cron, tests) keeps the default Ctrl+C behaviour
            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
        last_run = {}
        logger.info("Scheduler started: %s", ", ".join(f"{n} every {e}s" for n, e, _ in JOBS))
        while not stop["now"]:
            if not options["once"]:
                close_old_connections()  # long-lived process: never keep a stale MySQL connection
            ran = run_due(last_run, time.monotonic())
            if ran:
                logger.info("Scheduler ran: %s", ", ".join(ran))
            if options["once"]:
                break
            for _ in range(options["tick"]):
                if stop["now"]:
                    break
                time.sleep(1)
        logger.info("Scheduler stopped.")
