from django.core.management.base import BaseCommand, CommandError

from apps.accounts.services import ensure_superadmin


class Command(BaseCommand):
    help = "Create or update the superadmin from SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD."

    def handle(self, *args, **options):
        result = ensure_superadmin()
        if result is None:
            raise CommandError("Set SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD (valid email) in the environment.")
        user, action = result
        self.stdout.write(self.style.SUCCESS(f"Superadmin {user.email}: {action}"))
