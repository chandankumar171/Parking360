from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.core.utils import image_url, indian_number

register = template.Library()


@register.filter
def thumb(fieldfile, size="600x400"):
    """{{ photo.image|thumb:"600x400" }}"""
    w, _, h = str(size).partition("x")
    return image_url(fieldfile, int(w) if w.isdigit() else None, int(h) if h.isdigit() else None)


@register.filter
def rupees(value):
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    whole, frac = divmod(abs(d), 1)
    text = indian_number(whole)
    if frac:
        text += f".{int(frac * 100):02d}"
    return f"{'-' if d < 0 else ''}₹{text}"


@register.filter
def duration(minutes):
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return ""
    h, r = divmod(m, 60)
    if h and r:
        return f"{h} h {r} min"
    return f"{h} h" if h else f"{r} min"


@register.filter
def star_icons(value):
    """4.3 -> ['bi-star-fill'] * 4 + ['bi-star']"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0
    icons = []
    for i in range(1, 6):
        if v >= i - 0.25:
            icons.append("bi-star-fill")
        elif v >= i - 0.75:
            icons.append("bi-star-half")
        else:
            icons.append("bi-star")
    return icons


@register.simple_tag(takes_context=True)
def url_with(context, **kwargs):
    """Current query string with some keys replaced: {% url_with page=3 %}"""
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        query[key] = value
    return "?" + query.urlencode()


@register.simple_tag
def qr_code(text):
    from apps.core.utils import qr_svg

    return mark_safe(f'<div class="qr" role="img" aria-label="QR code for booking code {escape(text)}">{qr_svg(text)}</div>') if text else ""


@register.inclusion_tag("partials/_bell.html", takes_context=True)
def notification_bell(context):
    from apps.core.models import Notification

    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"show": False}
    qs = Notification.objects.filter(user=user)
    # inclusion tags get a fresh context, so pass the CSRF token through explicitly for the "mark all read" form
    return {"show": True, "unread": qs.filter(is_read=False).count(), "items": list(qs[:6]), "request": request,
            "csrf_token": context.get("csrf_token")}


@register.filter
def person_name(user):
    """'Asha Rout' -> 'Asha R.' so reviews don't expose full names."""
    parts = (getattr(user, "full_name", "") or "").split()
    if not parts:
        return "Driver"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."
