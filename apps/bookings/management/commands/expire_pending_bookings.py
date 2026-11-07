from django.core.management.base import BaseCommand

from apps.bookings.services import expire_stale_holds


class Command(BaseCommand):
    help = "Mark unpaid bookings whose 10-minute hold has lapsed as EXPIRED (run every few minutes via cron)."

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_stale_holds()} unpaid booking(s).")
