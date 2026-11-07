from django.conf import settings
from django.core.cache import cache

from apps.accounts.utils import home_url_for

SUPPORT_EMAIL_KEY = "site:support_email"

# Each item: (label, icon, url name or None when the module ships in a later phase, extra view names that also highlight it)
NAV = {
    "OWNER": [
        ("Dashboard", "bi-speedometer2", "core:owner_home", ()),
        ("My parkings", "bi-p-square", "owner:parking_list", ("owner:parking_edit", "owner:parking_edit_default")),
        ("Add parking", "bi-plus-square", "owner:parking_create", ()),
        ("Bookings", "bi-calendar2-check", "owner:bookings", ("owner:booking_detail",)),
        ("Verify code", "bi-qr-code-scan", "owner:verify", ()),
        ("Refund requests", "bi-arrow-counterclockwise", "owner:refunds", ()),
        ("Reviews", "bi-star", "owner:reviews", ()),
        ("Earnings", "bi-wallet2", "owner:earnings", ()),
        ("Profile", "bi-person-circle", "accounts:profile", ()),
    ],
    "SUPERADMIN": [
        ("Dashboard", "bi-speedometer2", "core:superadmin_home", ()),
        ("Verification queue", "bi-patch-check", "sadmin:queue", ()),
        ("Parkings", "bi-p-square", "sadmin:parking_list", ("sadmin:parking_detail",)),
        ("Owners", "bi-building", "sadmin:owners", ("sadmin:user_detail",)),
        ("Drivers", "bi-people", "sadmin:drivers", ()),
        ("Bookings", "bi-calendar2-check", "sadmin:bookings", ("sadmin:booking_detail",)),
        ("Payments & refunds", "bi-credit-card", "sadmin:payments", ()),
        ("Owner payouts", "bi-cash-stack", "sadmin:payouts", ()),
        ("Platform settings", "bi-sliders", "core:platform_settings", ()),
        ("Audit log", "bi-journal-text", "core:audit_log", ()),
        ("Profile", "bi-person-circle", "accounts:profile", ()),
    ],
    "DRIVER": [
        ("Find parking", "bi-search", "parkings:search", ("parkings:detail",)),
        ("My bookings", "bi-calendar2-check", "bookings:list", ("bookings:detail",)),
        ("My vehicles", "bi-car-front", "accounts:vehicles", ()),
        ("Profile", "bi-person-circle", "accounts:profile", ()),
    ],
}


def _support_email():
    """Shown in the footer and legal pages. Cached; cleared whenever the platform settings are saved."""
    from apps.core.models import SiteSettings

    value = cache.get(SUPPORT_EMAIL_KEY)
    if value is None:
        value = SiteSettings.load().support_email or ""
        cache.set(SUPPORT_EMAIL_KEY, value, 300)
    return value


def site(request):
    ctx = {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_URL": settings.SITE_URL,
        "layout": "layouts/panel.html",
        "SUPPORT_EMAIL": _support_email(),
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        match = getattr(request, "resolver_match", None)
        current = match.view_name if match else None
        ctx["home_url"] = home_url_for(user)
        ctx["layout"] = "layouts/driver.html" if user.role == "DRIVER" else "layouts/panel.html"
        pending = 0
        if user.role == "SUPERADMIN":
            from apps.parkings.models import Parking

            pending = Parking.objects.filter(status=Parking.Status.PENDING).count()
        ctx["nav_items"] = [
            {
                "label": label, "icon": icon, "url_name": name,
                "active": name is not None and (name == current or current in extra),
                "badge": pending if name == "sadmin:queue" and pending else 0,
            }
            for label, icon, name, extra in NAV.get(user.role, [])
        ]
    return ctx
