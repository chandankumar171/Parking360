from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.services import BookingError
from apps.core.services import notify
from apps.parkings.models import Parking

from .models import ParkingReview


def recompute_parking_rating(parking_id):
    """Keep Parking.avg_rating / review_count in step so search and cards never need to aggregate."""
    agg = ParkingReview.objects.filter(parking_id=parking_id).aggregate(avg=Avg("rating"), n=Count("id"))
    avg = Decimal(str(agg["avg"] or 0)).quantize(Decimal("0.01"))
    Parking.objects.filter(pk=parking_id).update(avg_rating=avg, review_count=agg["n"])


def rating_distribution(queryset):
    """[(5, count, percent), ... (1, count, percent)] for the bars next to the average."""
    counts = {row["rating"]: row["n"] for row in queryset.values("rating").annotate(n=Count("id"))}
    total = sum(counts.values())
    return [(star, counts.get(star, 0), round(100 * counts.get(star, 0) / total) if total else 0)
            for star in (5, 4, 3, 2, 1)]


@transaction.atomic
def submit_review(booking_id, driver, rating, comment):
    booking = Booking.objects.select_for_update().select_related("parking__owner").get(pk=booking_id, driver=driver)
    if booking.status != Booking.Status.COMPLETED:
        raise BookingError("You can review a parking once your stay is completed.")
    if ParkingReview.objects.filter(booking=booking).exists():
        raise BookingError("You have already reviewed this stay.")
    review = ParkingReview.objects.create(
        booking=booking, driver=driver, parking=booking.parking, owner=booking.parking.owner,
        rating=rating, comment=(comment or "").strip())
    recompute_parking_rating(booking.parking_id)
    notify(booking.parking.owner, f"New {rating}-star review", f"{booking.parking.name}: {review.comment[:120]}",
           reverse("owner:reviews"))
    return review


@transaction.atomic
def update_review(review_id, driver, rating, comment):
    review = ParkingReview.objects.select_for_update().get(pk=review_id, driver=driver)
    if not review.editable:
        raise BookingError("Reviews can be edited for 7 days after you post them.")
    review.rating, review.comment = rating, (comment or "").strip()
    review.save(update_fields=["rating", "comment", "updated_at"])
    recompute_parking_rating(review.parking_id)
    return review


@transaction.atomic
def reply_to_review(review_id, owner, text):
    review = ParkingReview.objects.select_for_update().select_related("parking").get(pk=review_id, owner=owner)
    if review.owner_reply:
        raise BookingError("You have already replied to this review.")
    text = (text or "").strip()
    if len(text) < 2:
        raise BookingError("Write a short reply.")
    review.owner_reply, review.replied_at = text[:600], timezone.now()
    review.save(update_fields=["owner_reply", "replied_at", "updated_at"])
    notify(review.driver, "The owner replied to your review", f"{review.parking.name}: {text[:120]}",
           reverse("parkings:detail", args=[review.parking.slug]))
    return review
