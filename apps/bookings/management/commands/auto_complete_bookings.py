from django.core.management.base import BaseCommand

from apps.bookings.lifecycle import auto_complete


class Command(BaseCommand):
    help = "Close stays the owner never checked out (AUTO_COMPLETE_HOURS after the end time). Run every 15 minutes."

    def handle(self, *args, **options):
        self.stdout.write(f"Auto-completed {auto_complete()} booking(s).")
