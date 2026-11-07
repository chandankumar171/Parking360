import hmac

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from django.views.generic import TemplateView, View

from apps.accounts.permissions import DriverRequiredMixin, OwnerRequiredMixin, SuperadminRequiredMixin
from apps.accounts.utils import home_url_for
from apps.core.models import SiteSettings


LANDING_CACHE_KEY = "landing:v1"


class LandingView(TemplateView):
    template_name = "landing.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Count

        from apps.parkings.models import Parking

        ctx = super().get_context_data(**kwargs)
        data = cache.get(LANDING_CACHE_KEY)
        if data is None:  # two cheap queries, cached for two minutes and cleared whenever a parking changes
            live = Parking.objects.filter(status=Parking.Status.APPROVED)
            data = {
                "featured_ids": list(live.filter(is_verified_badge=True).order_by("-avg_rating", "-created_at")
                                     .values_list("pk", flat=True)[:6]),
                "cities": list(live.values("city").annotate(n=Count("id")).order_by("-n", "city")[:8]),
            }
            cache.set(LANDING_CACHE_KEY, data, 120)
        position = {pk: i for i, pk in enumerate(data["featured_ids"])}
        ctx["featured"] = sorted(
            Parking.objects.filter(pk__in=position, status=Parking.Status.APPROVED).prefetch_related("photos", "vehicle_types"),
            key=lambda p: position[p.pk])
        from apps.parkings.search import attach_card_data

        attach_card_data(ctx["featured"])
        ctx["cities"] = data["cities"]
        return ctx


class LegalView(TemplateView):
    """Terms and Privacy Policy (draft text in templates/legal/; have it reviewed before launch)."""


class OwnerHomeView(OwnerRequiredMixin, TemplateView):
    template_name = "owner/home.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Count

        from apps.dashboard.periods import filter_context

        ctx = super().get_context_data(**kwargs)
        ctx.update(filter_context(self.request.GET))
        ctx["my_parkings"] = list(self.request.user.parkings.order_by("name"))
        ctx["exports"] = [(reverse("dashboard:owner_export", args=[k]), label) for k, label in
                          (("bookings", "Bookings"), ("earnings", "Earnings ledger"), ("series", "Chart data"))]
        rows = self.request.user.parkings.values("status").annotate(n=Count("id"))
        counts = {r["status"]: r["n"] for r in rows}
        ctx.update(
            total_parkings=sum(counts.values()), live=counts.get("APPROVED", 0),
            pending=counts.get("PENDING", 0), drafts=counts.get("DRAFT", 0) + counts.get("REJECTED", 0),
        )
        return ctx


class DriverHomeView(DriverRequiredMixin, View):
    """Kept for old bookmarks; drivers land on the search page."""

    def get(self, request):
        return redirect("parkings:search")


class SuperadminHomeView(SuperadminRequiredMixin, TemplateView):
    template_name = "superadmin/home.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Count

        from apps.accounts.models import User
        from apps.parkings.models import Parking

        from apps.dashboard.periods import filter_context

        ctx = super().get_context_data(**kwargs)
        ctx.update(filter_context(self.request.GET))
        ctx["exports"] = [(reverse("dashboard:admin_export", args=[k]), label) for k, label in
                          (("bookings", "Bookings"), ("payments", "Payments"), ("series", "Chart data"))]
        roles = {r["role"]: r["n"] for r in User.objects.values("role").annotate(n=Count("id"))}
        pk = {r["status"]: r["n"] for r in Parking.objects.values("status").annotate(n=Count("id"))}
        ctx.update(owners=roles.get("OWNER", 0), drivers=roles.get("DRIVER", 0), live=pk.get("APPROVED", 0),
                   pending=pk.get("PENDING", 0), suspended=pk.get("SUSPENDED", 0),
                   verification_required=SiteSettings.load().verification_required)
        return ctx


@csrf_exempt
@require_http_methods(["GET", "POST"])
def run_jobs(request):
    """Run the periodic jobs on request. For hosts with no background worker: a free cron service calls this every few minutes.

    Protected by JOBS_TOKEN (header X-Jobs-Token or ?token=). Without a configured token the URL does not exist.
    """
    token = settings.JOBS_TOKEN
    supplied = request.headers.get("X-Jobs-Token") or request.GET.get("token", "")
    if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
        raise Http404
    ran = []
    for name in ("expire_pending_bookings", "mark_no_shows", "auto_complete_bookings"):
        call_command(name, verbosity=0)
        ran.append(name)
    if request.GET.get("reconcile") == "1":
        call_command("reconcile_payments", warn_only=True, verbosity=0)
        ran.append("reconcile_payments")
    return JsonResponse({"status": "ok", "ran": ran})


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /accounts/",
        "Disallow: /jobs/",
        "Disallow: /owner/",
        "Disallow: /driver/",
        "Disallow: /superadmin/",
        f"Disallow: /{settings.ADMIN_URL}",
        f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def error_400(request, exception=None):
    return HttpResponseBadRequest(render_to_string("400.html"))


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return HttpResponseServerError(render_to_string("500.html"))


# ------------------------------------------------------------------ superadmin: settings + audit log
from django.contrib import messages  # noqa: E402
from django.core.paginator import Paginator  # noqa: E402

from apps.accounts.permissions import role_required  # noqa: E402

from .forms import PlatformSettingsForm  # noqa: E402
from .models import AuditLog, Notification, SiteSettings  # noqa: E402
from .services import audit  # noqa: E402


@role_required("SUPERADMIN")
def platform_settings(request):
    cfg = SiteSettings.load()
    was_required = cfg.verification_required
    form = PlatformSettingsForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        changed = {f: form.cleaned_data[f] for f in form.changed_data}
        form.save()
        audit(request.user, "settings.updated", cfg, "Platform settings updated.",
              changed={k: str(v) for k, v in changed.items()})
        msg = "Settings saved."
        if was_required and not cfg.verification_required:
            from apps.parkings.services import auto_approve_pending

            n = auto_approve_pending(request.user)
            msg += f" Verification is now off, so {n} pending parking{'s were' if n != 1 else ' was'} approved automatically."
        messages.success(request, msg)
        return redirect("core:platform_settings")
    return render(request, "superadmin/settings.html", {"form": form})


@role_required("SUPERADMIN")
def audit_log(request):
    q = request.GET.get("q", "").strip()
    qs = AuditLog.objects.select_related("actor")
    if q:
        qs = qs.filter(action__icontains=q) | qs.filter(message__icontains=q)
    return render(request, "superadmin/audit.html", {"page": Paginator(qs, 30).get_page(request.GET.get("page")), "q": q})


# ------------------------------------------------------------------ notifications (any signed-in user)
from django.contrib.auth.decorators import login_required  # noqa: E402
from django.shortcuts import get_object_or_404  # noqa: E402
from django.utils.http import url_has_allowed_host_and_scheme  # noqa: E402


@login_required
def notifications(request):
    page = Paginator(Notification.objects.filter(user=request.user), 20).get_page(request.GET.get("page"))
    return render(request, "notifications.html", {"page": page})


@login_required
def notification_go(request, pk):
    """Mark one notification read and continue to the page it points at."""
    note = get_object_or_404(Notification, pk=pk, user=request.user)
    if not note.is_read:
        note.is_read = True
        note.save(update_fields=["is_read"])
    link = note.link
    safe = link.startswith("/") and not link.startswith("//") and url_has_allowed_host_and_scheme(
        link, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return redirect(link if safe else "core:notifications")


@login_required
def notifications_read_all(request):
    if request.method == "POST":
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect("core:notifications")
