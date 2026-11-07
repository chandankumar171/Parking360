from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

EDIT_WINDOW = timedelta(days=7)


class ParkingReview(models.Model):
    """A driver's rating of a completed stay. One per booking."""

    booking = models.OneToOneField("bookings.Booking", on_delete=models.PROTECT, related_name="review")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviews_written")
    parking = models.ForeignKey("parkings.Parking", on_delete=models.PROTECT, related_name="reviews")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviews_received")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.CharField(max_length=1000, blank=True)
    owner_reply = models.CharField(max_length=600, blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["parking", "-created_at"], name="review_parking_idx"),
            models.Index(fields=["owner", "-created_at"], name="review_owner_idx"),
        ]
        constraints = [models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name="review_rating_1_5")]

    def __str__(self):
        return f"{self.rating}★ for {self.parking_id} by {self.driver_id}"

    @property
    def editable(self):
        return timezone.now() <= self.created_at + EDIT_WINDOW

    @property
    def edit_deadline(self):
        return self.created_at + EDIT_WINDOW
