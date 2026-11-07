from django.apps import AppConfig


class ParkingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.parkings"
    label = "parkings"

    def ready(self):
        from . import signals

        signals.connect()
