from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required

from . import services
from .forms import AmenitiesRulesForm, ParkingBasicsForm, VehicleTypeFormSet
from .models import MAX_PHOTOS, Parking, ParkingPhoto

STEPS = [
    ("basics", "Basics", "bi-geo-alt"),
    ("vehicles", "Vehicles & prices", "bi-car-front"),
    ("amenities", "Amenities & rules", "bi-shield-check"),
    ("photos", "Photos", "bi-images"),
    ("review", "Review & submit", "bi-check2-circle"),
]
STEP_KEYS = [s[0] for s in STEPS]


def _owned(request, pk):
    return get_object_or_404(Parking, pk=pk, owner=request.user)


def _ctx(parking, step, **extra):
    idx = STEP_KEYS.index(step)
    ctx = {
        "parking": parking,
        "step": step,
        "steps": [
            {"key": k, "label": l, "icon": i, "number": n + 1, "current": k == step, "done": n < idx}
            for n, (k, l, i) in enumerate(STEPS)
        ],
        "next_step": STEP_KEYS[idx + 1] if idx + 1 < len(STEP_KEYS) else None,
        "prev_step": STEP_KEYS[idx - 1] if idx > 0 else None,
    }
    ctx.update(extra)
    return ctx


def _after_save(request, parking, step):
    nxt = STEP_KEYS[STEP_KEYS.index(step) + 1] if step != STEP_KEYS[-1] else None
    if request.POST.get("action") == "continue" and nxt:
        return redirect("owner:parking_edit", pk=parking.pk, step=nxt)
    return redirect("owner:parking_edit", pk=parking.pk, step=step)


@role_required("OWNER")
def parking_list(request):
    status = request.GET.get("status", "")
    qs = (Parking.objects.filter(owner=request.user)
          .prefetch_related("photos", "vehicle_types"))
    counts = {s: 0 for s, _ in Parking.Status.choices}
    for s in qs.values_list("status", flat=True):
        counts[s] += 1
    if status in counts:
        qs = qs.filter(status=status)
    tabs = [("", "All", sum(counts.values()))] + [(s, l, counts[s]) for s, l in Parking.Status.choices]
    return render(request, "owner/parking_list.html", {"parkings": qs, "tabs": tabs, "active_status": status})


@role_required("OWNER")
def parking_create(request):
    form = ParkingBasicsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        parking = form.save(commit=False)
        parking.owner = request.user
        parking.status = Parking.Status.DRAFT
        parking.save()
        messages.success(request, "Draft created. Now add your vehicle types and prices.")
        return redirect("owner:parking_edit", pk=parking.pk, step="vehicles")
    return render(request, "owner/step_basics.html", {"form": form, "parking": None, "creating": True,
                                                        "steps": _ctx(Parking(), "basics")["steps"], "step": "basics"})


@role_required("OWNER")
def parking_edit(request, pk, step="basics"):
    parking = _owned(request, pk)
    if step not in STEP_KEYS:
        raise Http404
    if not parking.can_edit:
        messages.error(request, "This parking is suspended. Contact support to have it restored.")
        return redirect("owner:parking_list")
    return {
        "basics": _step_basics, "vehicles": _step_vehicles, "amenities": _step_amenities,
        "photos": _step_photos, "review": _step_review,
    }[step](request, parking)


def _step_basics(request, parking):
    form = ParkingBasicsForm(request.POST or None, instance=parking)
    if request.method == "POST" and form.is_valid():
        changed = form.has_changed()
        parking = form.save()
        if changed and services.mark_edited(parking, request.user):
            messages.warning(request, "Saved. Because this parking was live, it is back in review until approved.")
        else:
            messages.success(request, "Basics saved.")
        if parking.latitude is None:
            messages.warning(request, "We could not read coordinates from your link. Add latitude and longitude for 'near me' search.")
        return _after_save(request, parking, "basics")
    return render(request, "owner/step_basics.html", _ctx(parking, "basics", form=form))


def _step_vehicles(request, parking):
    formset = VehicleTypeFormSet(request.POST or None, instance=parking, prefix="vt")
    if request.method == "POST" and formset.is_valid():
        changed = formset.has_changed()
        formset.save()
        if changed and services.mark_edited(parking, request.user):
            messages.warning(request, "Saved. Because this parking was live, it is back in review until approved.")
        else:
            messages.success(request, "Vehicle types saved.")
        return _after_save(request, parking, "vehicles")
    return render(request, "owner/step_vehicles.html", _ctx(parking, "vehicles", formset=formset))


def _step_amenities(request, parking):
    form = AmenitiesRulesForm(request.POST or None, instance=parking)
    if request.method == "POST" and form.is_valid():
        changed = form.has_changed()
        parking = form.save()
        if changed and services.mark_edited(parking, request.user):
            messages.warning(request, "Saved. Because this parking was live, it is back in review until approved.")
        else:
            messages.success(request, "Amenities and rules saved.")
        return _after_save(request, parking, "amenities")
    return render(request, "owner/step_amenities.html", _ctx(parking, "amenities", form=form))


def _step_photos(request, parking):
    if request.method == "POST":
        added, errors = services.add_photos(parking, request.FILES.getlist("photos"))
        for e in errors:
            messages.error(request, e)
        if added:
            messages.success(request, f"{added} photo{'s' if added != 1 else ''} uploaded.")
            services.mark_edited(parking, request.user)
        return _after_save(request, parking, "photos")
    photos = list(parking.photos.all())
    return render(request, "owner/step_photos.html",
                  _ctx(parking, "photos", photos=photos, max_photos=MAX_PHOTOS, remaining=MAX_PHOTOS - len(photos)))


def _step_review(request, parking):
    parking = Parking.objects.prefetch_related("photos", "vehicle_types").get(pk=parking.pk)
    from apps.core.models import SiteSettings

    return render(request, "owner/step_review.html", _ctx(
        parking, "review", issues=parking.blocking_issues(), warnings=parking.warnings(),
        verification_required=SiteSettings.load().verification_required,
    ))


@role_required("OWNER")
@require_POST
def parking_submit(request, pk):
    parking = _owned(request, pk)
    ok, problems = services.submit_parking(parking, request.user)
    if ok:
        parking.refresh_from_db()
        if parking.status == Parking.Status.PENDING:
            messages.success(request, "Submitted. Drivers will see it once the ParkShare team approves it.")
        else:
            messages.success(request, "Your parking is live and visible to drivers.")
        return redirect("owner:parking_list")
    for p in problems:
        messages.error(request, p)
    return redirect("owner:parking_edit", pk=pk, step="review")


@role_required("OWNER")
@require_POST
def parking_toggle_open(request, pk):
    parking = _owned(request, pk)
    if not parking.can_edit:
        messages.error(request, "Suspended parkings cannot be changed.")
    else:
        parking.is_open = not parking.is_open
        parking.save(update_fields=["is_open", "updated_at"])
        messages.success(request, f"{parking.name} is now {'open for bookings' if parking.is_open else 'closed for new bookings'}.")
    return redirect(request.POST.get("next") or "owner:parking_list")


@role_required("OWNER")
@require_POST
def parking_delete(request, pk):
    parking = _owned(request, pk)
    try:
        services.delete_parking(parking)
        messages.success(request, "Parking deleted.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("owner:parking_list")


@role_required("OWNER")
@require_POST
def photo_cover(request, pk, photo_pk):
    photo = get_object_or_404(ParkingPhoto, pk=photo_pk, parking__pk=pk, parking__owner=request.user)
    services.set_cover(photo)
    messages.success(request, "Cover photo updated.")
    return redirect("owner:parking_edit", pk=pk, step="photos")


@role_required("OWNER")
@require_POST
def photo_delete(request, pk, photo_pk):
    photo = get_object_or_404(ParkingPhoto, pk=photo_pk, parking__pk=pk, parking__owner=request.user)
    try:
        services.delete_photo(photo)
        messages.success(request, "Photo deleted.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("owner:parking_edit", pk=pk, step="photos")
