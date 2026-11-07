"""Google Maps URL helpers. We never fetch URLs (no SSRF); we only read coordinates written in the link."""
import re
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse

ALLOWED_HOSTS = ("google.com", "google.co.in", "goo.gl", "g.co")
_NUM = r"(-?\d{1,3}\.\d+)"
_PATTERNS = [re.compile(rf"!3d{_NUM}!4d{_NUM}"), re.compile(rf"@{_NUM},{_NUM}")]
_PAIR = re.compile(rf"^\s*{_NUM}\s*,\s*{_NUM}\s*$")


def is_google_maps_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _valid(lat, lng):
    return -90 <= lat <= 90 and -180 <= lng <= 180


def extract_lat_lng(url):
    """Return (Decimal lat, Decimal lng) found in a Google Maps URL, or None (e.g. short links)."""
    url = unquote(url or "")
    for pattern in _PATTERNS:  # exact place pin (!3d!4d) wins over viewport centre (@)
        m = pattern.search(url)
        if m and _valid(float(m.group(1)), float(m.group(2))):
            return Decimal(m.group(1)).quantize(Decimal("0.000001")), Decimal(m.group(2)).quantize(Decimal("0.000001"))
    query = parse_qs(urlparse(url).query)
    for key in ("q", "query", "ll", "center", "destination"):
        for value in query.get(key, []):
            m = _PAIR.match(value)
            if m and _valid(float(m.group(1)), float(m.group(2))):
                return Decimal(m.group(1)).quantize(Decimal("0.000001")), Decimal(m.group(2)).quantize(Decimal("0.000001"))
    return None
