from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from apps.bookings.availability import bulk_spots_left
from apps.bookings.forms import BookingForm, QuoteForm
from apps.bookings.services import BookingError, create_booking
from apps.reviews.services import rating_distribution

from .models import AMENITIES, Parking
from .search import HOURS, RADII, SORTS, SearchForm, attach_card_data, build_queryset

PER_PAGE = 12


def _next_quarter(now):
    local = timezone.localtime(now) + timedelta(minutes=15)
    return local.replace(minute=(local.minute // 15) * 15, second=0, microsecond=0)


def search(request):
    form = SearchForm(request.GET)
    form.is_valid()
    params = getattr(form, "cleaned_data", {}) or {}
    qs, window = build_queryset(params)
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))
    parkings = attach_card_data(list(page.object_list), window, vehicle=params.get("vehicle") or None)
    active = [k for k in ("vehicle", "min_price", "max_price", "open_now", "is_24x7", "cancellable", "verified",
                          "min_rating", "date", "lat") if params.get(k) not in (None, "", False)]
    active_count = len(active) + len(params.get("amenities") or [])
    return render(request, "parkings/search.html", {
        "form": form, "params": params, "page": page, "parkings": parkings, "window": window,
        "active_count": active_count, "has_geo": params.get("lat") is not None and params.get("lng") is not None, "sorts": SORTS, "hours": HOURS, "radii": RADII,
        "amenity_options": [(f, l, i) for f, l, i in AMENITIES],
        "selected_amenities": params.get("amenities") or [],
        "today": timezone.localdate().isoformat(),
    })


@ratelimit(key="user_or_ip", rate="40/h", method="POST", block=True)
def detail(request, slug):
    parking = get_object_or_404(
        Parking.objects.select_related("owner", "owner__owner_profile").prefetch_related("photos", "vehicle_types"),
        slug=slug, status=Parking.Status.APPROVED)
    types = [t for t in parking.vehicle_types.all() if t.is_active]
    now = timezone.now()
    spots = bulk_spots_left(types, now, now + timedelta(minutes=1), now)
    type_rows = [{"type": t, "left": spots[t.pk]} for t in types]

    user = request.user
    can_book = user.is_authenticated and user.role == "DRIVER"
    saved_vehicles = list(user.vehicles.all()) if can_book else []

    if request.method == "POST":
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_book:
            raise PermissionDenied
        form = BookingForm(request.POST, parking=parking)
        if form.is_valid():
            try:
                booking = create_booking(
                    user, form.cleaned_data["vehicle_type"], form.start_at, form.cleaned_data["slots"],
                    form.cleaned_data["vehicle_number_plate"], form.cleaned_data["vehicle_brand_model"])
                messages.success(request, "Bay held for 10 minutes. Review your booking below.")
                return redirect("bookings:detail", pk=booking.pk)
            except BookingError as exc:
                form.add_error(None, str(exc))
    else:
        first = _next_quarter(now)
        form = BookingForm(parking=parking, initial={"date": first.date(), "time": first.time(), "slots": 1})

    return render(request, "parkings/detail.html", {
        "parking": parking, "photos": list(parking.photos.all()), "type_rows": type_rows, "form": form,
        "reviews": list(parking.reviews.select_related("driver")[:10]), "distribution": rating_distribution(parking.reviews.all()),
        "can_book": can_book, "saved_vehicles": saved_vehicles, "open_now": parking.is_open_now(now),
        "today": timezone.localdate().isoformat(),
        "max_day": (timezone.localdate() + timedelta(days=60)).isoformat(),
    })


@require_GET
def quote(request, pk):
    """Live availability + price for the booking box. Public, read-only."""
    parking = get_object_or_404(Parking, pk=pk, status=Parking.Status.APPROVED)
    form = QuoteForm(request.GET, parking=parking)
    if not form.is_valid():
        errors = form.non_field_errors() or [m for errs in form.errors.values() for m in errs]
        return JsonResponse({"ok": False, "message": str(errors[0]) if errors else "Check your selection."})
    vt = form.cleaned_data["vehicle_type"]
    slots = form.cleaned_data["slots"]
    left = bulk_spots_left([vt], form.start_at, form.end_at)[vt.pk]
    local = timezone.localtime
    same_day = local(form.start_at).date() == local(form.end_at).date()
    end_fmt = "%I:%M %p" if same_day else "%a %d %b, %I:%M %p"
    return JsonResponse({
        "ok": left >= 1,
        "spots_left": left,
        "message": "" if left >= 1 else "Fully booked for this time. Try another time or vehicle type.",
        "start": local(form.start_at).strftime("%a %d %b, %I:%M %p"),
        "end": local(form.end_at).strftime(end_fmt),
        "duration_minutes": vt.slot_minutes * slots,
        "price": str(vt.total_price(slots)),
    })


@require_GET
def suggest(request):
    q = request.GET.get("q", "").strip()[:60]
    if len(q) < 2:
        return JsonResponse({"results": []})
    base = Parking.objects.filter(status=Parking.Status.APPROVED)
    seen, results = set(), []
    for field, kind in (("city", "City"), ("area", "Area")):
        for value in (base.filter(**{f"{field}__icontains": q}).order_by().values_list(field, flat=True).distinct()[:5]):
            if value.lower() not in seen:
                seen.add(value.lower())
                results.append({"label": value, "kind": kind})
    for name in base.filter(name__icontains=q).order_by("name").values_list("name", flat=True)[:4]:
        if name.lower() not in seen:
            seen.add(name.lower())
            results.append({"label": name, "kind": "Parking"})
    return JsonResponse({"results": results[:8]})
