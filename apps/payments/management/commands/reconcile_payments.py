import logging

from django.core.management.base import BaseCommand, CommandError

from apps.payments.reconcile import run_reconciliation
from apps.payments.models import OwnerLedger, Payment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check that payments, refunds, bookings and the owner ledger agree. Exits with an error if they do not."

    def add_arguments(self, parser):
        parser.add_argument("--warn-only", action="store_true", help="Log findings but exit successfully (used by the scheduler).")

    def handle(self, *args, **options):
        issues = run_reconciliation()
        if not issues:
            self.stdout.write(self.style.SUCCESS(
                f"OK: {Payment.objects.count()} payments and {OwnerLedger.objects.count()} ledger entries reconcile."))
            return
        for i in issues:
            logger.warning("RECONCILE %s %s: %s", i["code"], i["ref"], i["message"])
            self.stdout.write(self.style.ERROR(f"[{i['code']}] {i['message']}"))
        if options["warn_only"]:
            self.stdout.write(self.style.WARNING(f"{len(issues)} issue(s) found."))
            return
        raise CommandError(f"{len(issues)} reconciliation issue(s) found.")
