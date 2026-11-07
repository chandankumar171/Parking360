from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required

from . import services
from .forms import RejectForm
from .models import Parking

PER_PAGE = 15


def _search(qs, q):
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(city__icontains=q) | Q(area__icontains=q)
            | Q(owner__email__icontains=q) | Q(owner__full_name__icontains=q)
        )
    return qs


def _page(request, qs):
    return Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))


@role_required("SUPERADMIN")
def queue(request):
    q = request.GET.get("q", "").strip()
    qs = _search(Parking.objects.filter(status=Parking.Status.PENDING).select_related("owner", "owner__owner_profile")
                 .prefetch_related("photos").order_by("submitted_at"), q)
    return render(request, "superadmin/queue.html", {"page": _page(request, qs), "q": q})


@role_required("SUPERADMIN")
def parking_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    base = Parking.objects.select_related("owner", "owner__owner_profile").prefetch_related("photos")
    qs = _search(base, q)
    counts = {s: qs.filter(status=s).count() for s, _ in Parking.Status.choices}
    if status in counts:
        qs = qs.filter(status=status)
    tabs = [("", "All", sum(counts.values()))] + [(s, l, counts[s]) for s, l in Parking.Status.choices]
    return render(request, "superadmin/parking_list.html",
                  {"page": _page(request, qs), "q": q, "tabs": tabs, "active_status": status})


@role_required("SUPERADMIN")
def parking_detail(request, pk):
    parking = get_object_or_404(
        Parking.objects.select_related("owner", "owner__owner_profile", "verified_by")
        .prefetch_related("photos", "vehicle_types"), pk=pk)
    return render(request, "superadmin/parking_detail.html",
                  {"parking": parking, "reject_form": RejectForm(), "suspend_form": RejectForm(),
                   "issues": parking.blocking_issues(), "warnings": parking.warnings()})


def _run(request, pk, action, success):
    parking = get_object_or_404(Parking, pk=pk)
    try:
        action(parking)
        messages.success(request, success.format(name=parking.name))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect("sadmin:parking_detail", pk=pk)


@role_required("SUPERADMIN")
@require_POST
def approve(request, pk):
    return _run(request, pk, lambda p: services.approve(p, request.user), "{name} approved and marked Verified.")


@role_required("SUPERADMIN")
@require_POST
def reject(request, pk):
    form = RejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give the owner a reason (at least 5 characters).")
        return redirect("sadmin:parking_detail", pk=pk)
    return _run(request, pk, lambda p: services.reject(p, request.user, form.cleaned_data["reason"]),
                "{name} rejected. The owner has been notified.")


@role_required("SUPERADMIN")
@require_POST
def suspend(request, pk):
    reason = request.POST.get("reason", "")
    return _run(request, pk, lambda p: services.suspend(p, request.user, reason), "{name} suspended.")


@role_required("SUPERADMIN")
@require_POST
def unsuspend(request, pk):
    return _run(request, pk, lambda p: services.unsuspend(p, request.user), "{name} restored.")
