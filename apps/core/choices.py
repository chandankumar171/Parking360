from django.db import models


class VehicleCategory(models.TextChoices):
    TWO_WHEELER = "TWO_WHEELER", "2-wheeler"
    THREE_WHEELER = "THREE_WHEELER", "3-wheeler"
    FOUR_WHEELER = "FOUR_WHEELER", "4-wheeler (car)"
    HEAVY = "HEAVY", "Heavy vehicle"
