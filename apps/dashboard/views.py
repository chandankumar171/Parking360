from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache

from apps.accounts.permissions import role_required

from . import exports, metrics
from .periods import parse_period


def _parking_id(request):
    value = request.GET.get("parking", "")
    return int(value) if value.isdigit() else None


@role_required("OWNER")
@never_cache
def owner_overview(request):
    period = parse_period(request.GET)
    return JsonResponse(metrics.owner_overview(request.user, period, _parking_id(request)))


@role_required("OWNER")
@never_cache
def owner_live(request):
    return JsonResponse(metrics.owner_live(request.user))


@role_required("OWNER")
@never_cache
def owner_export(request, kind):
    period = parse_period(request.GET)
    response = exports.owner_export(kind, request.user, period, metrics.owner_parkings(request.user, _parking_id(request)))
    if response is None:
        raise Http404
    return response


@role_required("SUPERADMIN")
@never_cache
def admin_overview(request):
    return JsonResponse(metrics.admin_overview(parse_period(request.GET)))


@role_required("SUPERADMIN")
@never_cache
def admin_export(request, kind):
    response = exports.admin_export(kind, parse_period(request.GET))
    if response is None:
        raise Http404
    return response
