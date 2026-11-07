from django.core.management.base import BaseCommand

from apps.bookings.lifecycle import mark_no_shows


class Command(BaseCommand):
    help = "Mark confirmed bookings that were never checked in (past start + grace) as NO_SHOW. Run every 5 minutes."

    def handle(self, *args, **options):
        self.stdout.write(f"Marked {mark_no_shows()} booking(s) as no-show.")
