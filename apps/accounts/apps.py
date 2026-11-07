from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from .signals import ensure_superadmin_after_migrate

        post_migrate.connect(ensure_superadmin_after_migrate, sender=self)
