from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from apps.accounts.models import DriverVehicle, PointLog, User
from apps.accounts.permissions import role_required
from apps.bookings.models import Booking, DriverVerdict
from apps.bookings.services import BookingError
from apps.parkings.models import Parking
from apps.parkings.search import attach_card_data

from . import services
from .forms import ReplyForm, ReviewForm
from .models import ParkingReview

S = Booking.Status


# ------------------------------------------------------------------ driver writes a review
@role_required("DRIVER")
@ratelimit(key="user", rate="30/h", method="POST", block=True)
def write_review(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("parking"), pk=pk, driver=request.user)
    review = getattr(booking, "review", None)
    if booking.status != S.COMPLETED:
        messages.error(request, "You can review a parking once your stay is completed.")
        return redirect("bookings:detail", pk=pk)
    if review and not review.editable:
        messages.error(request, "Reviews can be edited for 7 days after you post them.")
        return redirect("bookings:detail", pk=pk)
    initial = {"rating": review.rating, "comment": review.comment} if review else None
    form = ReviewForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            if review:
                services.update_review(review.pk, request.user, form.cleaned_data["rating"], form.cleaned_data["comment"])
                messages.success(request, "Review updated.")
            else:
                services.submit_review(pk, request.user, form.cleaned_data["rating"], form.cleaned_data["comment"])
                messages.success(request, "Thanks! Your review is now visible on the parking page.")
            return redirect("bookings:detail", pk=pk)
        except BookingError as exc:
            messages.error(request, str(exc))
    return render(request, "reviews/write.html", {"booking": booking, "form": form, "review": review})


# ------------------------------------------------------------------ owner: read and reply
@role_required("OWNER")
def owner_reviews(request):
    qs = ParkingReview.objects.filter(owner=request.user).select_related("parking", "driver", "booking")
    parking = request.GET.get("parking", "")
    pending = request.GET.get("pending") == "1"
    summary = qs.aggregate(avg=Avg("rating"), n=Count("id"), waiting=Count("id", filter=Q(owner_reply="")))
    if parking.isdigit():
        qs = qs.filter(parking_id=int(parking))
    if pending:
        qs = qs.filter(owner_reply="")
    page = Paginator(qs, 15).get_page(request.GET.get("page"))
    return render(request, "reviews/owner_reviews.html", {
        "page": page, "summary": summary, "parking": parking, "pending": pending,
        "my_parkings": request.user.parkings.order_by("name"), "reply_form": ReplyForm()})


@role_required("OWNER")
def reply(request, pk):
    review = get_object_or_404(ParkingReview, pk=pk, owner=request.user)
    if request.method == "POST":
        form = ReplyForm(request.POST)
        if form.is_valid():
            try:
                services.reply_to_review(review.pk, request.user, form.cleaned_data["text"])
                messages.success(request, "Reply posted.")
            except BookingError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Write a short reply.")
    return redirect("owner:reviews")


# ------------------------------------------------------------------ public owner profile
def owner_profile(request, pk):
    owner = get_object_or_404(User.objects.select_related("owner_profile"), pk=pk, role=User.Role.OWNER)
    profile = getattr(owner, "owner_profile", None)
    if (profile is None or not profile.is_public) and request.user != owner:
        raise Http404
    live = Parking.objects.filter(owner=owner, status=Parking.Status.APPROVED).prefetch_related("photos", "vehicle_types")
    parkings = attach_card_data(list(live.order_by("-is_verified_badge", "-avg_rating", "name")))
    reviews_qs = ParkingReview.objects.filter(owner=owner).select_related("driver", "parking")
    summary = reviews_qs.aggregate(avg=Avg("rating"), n=Count("id"))
    completed = Booking.objects.filter(parking__owner=owner, status=S.COMPLETED).count()
    return render(request, "reviews/owner_profile.html", {
        "owner": owner, "profile": profile, "parkings": parkings, "summary": summary, "completed": completed,
        "verified_count": sum(1 for p in parkings if p.is_verified_badge),
        "distribution": services.rating_distribution(reviews_qs),
        "page": Paginator(reviews_qs, 10).get_page(request.GET.get("page"))})


# ------------------------------------------------------------------ owner views a driver who booked with them
@role_required("OWNER")
def driver_profile(request, pk):
    driver = get_object_or_404(User, pk=pk, role=User.Role.DRIVER)
    mine = Booking.objects.filter(driver=driver, parking__owner=request.user)
    if not mine.exists():
        raise Http404  # owners only see drivers who have booked with them
    paid = Booking.objects.filter(driver=driver).exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED])
    stats = paid.aggregate(
        total=Count("id"), completed=Count("id", filter=Q(status=S.COMPLETED)),
        cancelled=Count("id", filter=Q(status=S.CANCELLED, cancelled_by=driver)),
        no_shows=Count("id", filter=Q(status=S.NO_SHOW)))
    verdicts = DriverVerdict.objects.filter(driver=driver).select_related("owner__owner_profile", "booking__parking")
    v = verdicts.aggregate(total=Count("id"), good=Count("id", filter=Q(satisfied=True)))
    vehicles = (mine.exclude(status__in=[S.PENDING_PAYMENT, S.EXPIRED])
                .values("vehicle_number_plate", "vehicle_brand_model", "vehicle_category")
                .annotate(n=Count("id")).order_by("-n"))
    return render(request, "reviews/driver_profile.html", {
        "driver": driver, "stats": stats, "verdicts": verdicts[:20], "v": v,
        "satisfied_pct": round(100 * v["good"] / v["total"]) if v["total"] else None,
        "vehicles": vehicles, "recent": mine.select_related("parking").order_by("-start_at")[:8],
        "point_logs": PointLog.objects.filter(user=driver)[:10], "since": driver.date_joined})
