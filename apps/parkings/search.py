"""Driver search: text, filters, sorting, distance and time-window availability."""
from datetime import datetime, timedelta

from django import forms
from django.db.models import Case, Exists, F, FloatField, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import ACos, Cast, Cos, Greatest, Least, Radians, Sin
from django.utils import timezone

from apps.core.choices import VehicleCategory

from .models import AMENITIES, Parking, ParkingVehicleType

SORTS = [("relevance", "Best match"), ("price", "Price: low to high"), ("rating", "Top rated"),
         ("distance", "Nearest"), ("newest", "Newest")]
HOURS = [("1", "1 hour"), ("2", "2 hours"), ("3", "3 hours"), ("4", "4 hours"), ("6", "6 hours"),
         ("8", "8 hours"), ("12", "12 hours"), ("24", "24 hours")]
RADII = [("2", "2 km"), ("5", "5 km"), ("10", "10 km"), ("25", "25 km"), ("50", "50 km")]
MAX_WINDOW_CANDIDATES = 500


class SearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=120)
    vehicle = forms.ChoiceField(required=False, choices=[("", "Any vehicle")] + list(VehicleCategory.choices))
    min_price = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2)
    max_price = forms.DecimalField(required=False, min_value=0, max_digits=8, decimal_places=2)
    open_now = forms.BooleanField(required=False)
    is_24x7 = forms.BooleanField(required=False)
    cancellable = forms.BooleanField(required=False)
    verified = forms.BooleanField(required=False)
    amenities = forms.MultipleChoiceField(required=False, choices=[(f, l) for f, l, _i in AMENITIES])
    min_rating = forms.ChoiceField(required=False, choices=[("", "Any"), ("3", "3+"), ("4", "4+"), ("4.5", "4.5+")])
    date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    start = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    hours = forms.ChoiceField(required=False, choices=HOURS)
    lat = forms.FloatField(required=False, min_value=-90, max_value=90)
    lng = forms.FloatField(required=False, min_value=-180, max_value=180)
    radius = forms.ChoiceField(required=False, choices=RADII)
    sort = forms.ChoiceField(required=False, choices=SORTS)


def _haversine_km(lat, lng):
    lat_f = Cast("latitude", FloatField())
    lng_f = Cast("longitude", FloatField())
    v_lat = Value(float(lat), output_field=FloatField())
    v_lng = Value(float(lng), output_field=FloatField())
    cos_angle = Cos(Radians(v_lat)) * Cos(Radians(lat_f)) * Cos(Radians(lng_f) - Radians(v_lng)) \
        + Sin(Radians(v_lat)) * Sin(Radians(lat_f))
    clamped = Least(Value(1.0, output_field=FloatField()), Greatest(Value(-1.0, output_field=FloatField()), cos_angle))
    return Value(6371.0, output_field=FloatField()) * ACos(clamped)


def _open_now_q(now):
    t = timezone.localtime(now).time()
    return Q(is_open=True) & (
        Q(is_24x7=True) | Q(opening_time__isnull=True) | Q(closing_time__isnull=True)
        | (Q(opening_time__lt=F("closing_time")) & Q(opening_time__lte=t) & Q(closing_time__gt=t))
        | (Q(opening_time__gte=F("closing_time")) & (Q(opening_time__lte=t) | Q(closing_time__gt=t)))
    )


def search_window(p):
    """(start, end) when the driver chose a date + start time, else None."""
    if p.get("date") and p.get("start"):
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(p["date"], p["start"]), tz)
        return start, start + timedelta(hours=int(p.get("hours") or 2))
    return None


def type_filters(p):
    f = {"is_active": True}
    if p.get("vehicle"):
        f["category"] = p["vehicle"]
    if p.get("min_price") is not None:
        f["price_per_slot__gte"] = p["min_price"]
    if p.get("max_price") is not None:
        f["price_per_slot__lte"] = p["max_price"]
    return f


def build_queryset(p, now=None):
    from apps.bookings.availability import bulk_spots_left, window_problem

    now = now or timezone.now()
    qs = Parking.objects.filter(status=Parking.Status.APPROVED).prefetch_related("photos", "vehicle_types")

    terms = (p.get("q") or "").split()[:6]
    for term in terms:
        qs = qs.filter(Q(name__icontains=term) | Q(area__icontains=term) | Q(city__icontains=term)
                       | Q(address_line__icontains=term) | Q(pincode__icontains=term) | Q(state__icontains=term))

    type_qs = ParkingVehicleType.objects.filter(parking=OuterRef("pk"), **type_filters(p))
    qs = qs.filter(Exists(type_qs)).annotate(
        min_price=Subquery(type_qs.order_by("price_per_slot").values("price_per_slot")[:1]))

    for field in p.get("amenities") or []:
        if field in {f for f, _l, _i in AMENITIES}:
            qs = qs.filter(**{field: True})
    if p.get("is_24x7"):
        qs = qs.filter(is_24x7=True)
    if p.get("cancellable"):
        qs = qs.filter(allow_cancellation=True)
    if p.get("verified"):
        qs = qs.filter(is_verified_badge=True)
    if p.get("min_rating"):
        qs = qs.filter(review_count__gt=0, avg_rating__gte=p["min_rating"])
    if p.get("open_now"):
        qs = qs.filter(_open_now_q(now))

    has_coords = p.get("lat") is not None and p.get("lng") is not None
    if has_coords:
        radius = float(p.get("radius") or 10)
        qs = qs.filter(latitude__isnull=False, longitude__isnull=False).annotate(
            distance_km=_haversine_km(p["lat"], p["lng"])).filter(distance_km__lte=radius)

    window = search_window(p)
    if window:
        start, end = window
        ids = list(qs.values_list("pk", flat=True)[:MAX_WINDOW_CANDIDATES])
        types = list(ParkingVehicleType.objects.filter(parking_id__in=ids, **type_filters(p)).select_related("parking"))
        spots = bulk_spots_left(types, start, end, now)
        ok = {t.parking_id for t in types if spots[t.pk] >= 1 and window_problem(t.parking, start, end, now) is None}
        qs = qs.filter(pk__in=ok)

    sort = p.get("sort") or "relevance"
    if sort == "distance" and not has_coords:
        sort = "relevance"
    if sort == "price":
        qs = qs.order_by("min_price", "-avg_rating", "id")
    elif sort == "rating":
        qs = qs.order_by("-avg_rating", "-review_count", "id")
    elif sort == "distance":
        qs = qs.order_by("distance_km", "id")
    elif sort == "newest":
        qs = qs.order_by("-created_at", "id")
    else:
        if terms:
            q0 = terms[0]
            qs = qs.annotate(rel=Case(
                When(name__icontains=q0, then=0), When(area__icontains=q0, then=1), When(city__icontains=q0, then=2),
                default=3, output_field=IntegerField()))
            qs = qs.order_by("rel", "-is_verified_badge", "-avg_rating", "-created_at", "id")
        else:
            qs = qs.order_by("-is_verified_badge", "-avg_rating", "-created_at", "id")
    return qs, window


def attach_card_data(parkings, window=None, now=None, vehicle=None):
    """Adds p.card_types (each with free bays) and p.full_now / p.open_now for display."""
    from apps.bookings.availability import bulk_spots_left

    now = now or timezone.now()
    start, end = window or (now, now + timedelta(minutes=1))
    all_types = [t for p in parkings for t in p.vehicle_types.all() if t.is_active]
    spots = bulk_spots_left(all_types, start, end, now)
    for p in parkings:
        rows = [{"type": t, "left": spots[t.pk], "match": not vehicle or t.category == vehicle}
                for t in p.vehicle_types.all() if t.is_active]
        relevant = [r for r in rows if r["match"]] or rows
        p.card_types = rows
        p.full_now = bool(relevant) and all(r["left"] == 0 for r in relevant)
        p.open_now = p.is_open_now(now)
        p.window_label = bool(window)
    return parkings
