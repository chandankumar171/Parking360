"""Dashboard time filters: year / month / week / custom range, plus the buckets charts are drawn in.

All boundaries are local (Asia/Kolkata) calendar days; `end` is exclusive.
"""
import calendar
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone

MODES = ("year", "month", "week", "custom")
GROUPS = ("day", "week", "month")
MAX_CUSTOM_DAYS = 731
FIRST_YEAR = 2024


def _aware(d):
    return timezone.make_aware(datetime.combine(d, time.min), timezone.get_current_timezone())


def _int(value, lo, hi, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def _date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _first_of_next_month(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


@dataclass(frozen=True)
class Period:
    mode: str
    start_date: date
    end_date: date  # exclusive
    group: str
    label: str
    year: int
    month: int
    week_date: date | None = None

    @property
    def start(self):
        return _aware(self.start_date)

    @property
    def end(self):
        return _aware(self.end_date)

    @property
    def days(self):
        return (self.end_date - self.start_date).days

    @property
    def last_date(self):
        return self.end_date - timedelta(days=1)


def auto_group(days):
    return "day" if days <= 62 else "week" if days <= 190 else "month"


def _make(mode, sd, ed, label, group_param, year, month, week_date=None):
    days = (ed - sd).days
    group = group_param if group_param in GROUPS else auto_group(days)
    if group == "day" and days > 400:  # keep charts readable
        group = "week"
    return Period(mode, sd, ed, group, label, year, month, week_date)


def parse_period(params, today=None):
    """Turn query-string params into a validated Period. Anything invalid falls back to 'this month'."""
    today = today or timezone.localdate()
    mode = params.get("mode") if params.get("mode") in MODES else "month"
    year = _int(params.get("year"), FIRST_YEAR, today.year + 1, today.year)
    month = _int(params.get("month"), 1, 12, today.month)
    group = params.get("group")
    if mode == "year":
        return _make(mode, date(year, 1, 1), date(year + 1, 1, 1), str(year), group, year, month)
    if mode == "week":
        d = _date(params.get("date")) or today
        sd = d - timedelta(days=d.weekday())
        return _make(mode, sd, sd + timedelta(days=7), f"Week of {sd:%d %b %Y}", group, sd.year, sd.month, d)
    if mode == "custom":
        s, e = _date(params.get("start")), _date(params.get("end"))
        if s and e and e >= s:
            if (e - s).days + 1 > MAX_CUSTOM_DAYS:
                e = s + timedelta(days=MAX_CUSTOM_DAYS - 1)
            return _make(mode, s, e + timedelta(days=1), f"{s:%d %b %Y} to {e:%d %b %Y}", group, s.year, s.month)
        mode = "month"
    first = date(year, month, 1)
    return _make("month", first, _first_of_next_month(first), f"{calendar.month_name[month]} {year}", group, year, month)


def previous_period(p):
    """The comparable period just before this one (previous month/year/week, else the same number of days)."""
    if p.mode == "year":
        return _make("year", date(p.year - 1, 1, 1), date(p.year, 1, 1), str(p.year - 1), p.group, p.year - 1, p.month)
    if p.mode == "month":
        last = p.start_date - timedelta(days=1)
        first = date(last.year, last.month, 1)
        return _make("month", first, p.start_date, f"{calendar.month_name[first.month]} {first.year}", p.group, first.year, first.month)
    n = p.days
    return _make(p.mode, p.start_date - timedelta(days=n), p.start_date, "Previous period", p.group, p.year, p.month)


@dataclass(frozen=True)
class Bucket:
    start_date: date
    end_date: date
    label: str
    start: datetime
    end: datetime

    @property
    def days(self):
        return (self.end_date - self.start_date).days


class Buckets:
    """The x-axis of a chart: consecutive day/week/month slices covering the period exactly."""

    def __init__(self, period):
        self.period = period
        self.items = []
        cur, end = period.start_date, period.end_date
        while cur < end:
            if period.group == "day":
                nxt, label = cur + timedelta(days=1), f"{cur:%d %b}"
            elif period.group == "week":
                nxt, label = cur + timedelta(days=7 - cur.weekday()), f"{cur:%d %b}"
            else:
                nxt, label = _first_of_next_month(cur), f"{cur:%b %Y}"
            nxt = min(nxt, end)
            self.items.append(Bucket(cur, nxt, label, _aware(cur), _aware(nxt)))
            cur = nxt
        self._starts = [b.start_date for b in self.items]
        self._start_dts = [b.start for b in self.items]

    def __len__(self):
        return len(self.items)

    @property
    def labels(self):
        return [b.label for b in self.items]

    def index_for(self, dt):
        """Bucket index for an aware datetime, or None if it falls outside the period."""
        d = timezone.localtime(dt).date()
        if not self.period.start_date <= d < self.period.end_date:
            return None
        return bisect_right(self._starts, d) - 1

    def index_at(self, dt):
        """Like index_for but works on exact instants (used for interval overlap)."""
        return max(0, bisect_right(self._start_dts, dt) - 1)


def filter_context(params):
    """Initial values + option lists for the filter bar, so a reloaded page keeps its filters."""
    p = parse_period(params)
    today = timezone.localdate()
    return {
        "initial": {
            "mode": p.mode, "year": p.year, "month": p.month,
            "date": (p.week_date or today).isoformat(),
            "start": params.get("start") or p.start_date.isoformat(),
            "end": params.get("end") or p.last_date.isoformat(),
            "group": params.get("group") if params.get("group") in GROUPS else "",
            "parking": params.get("parking", ""),
        },
        "groups": [("day", "Day"), ("week", "Week"), ("month", "Month")],
        "modes": [("year", "Year"), ("month", "Month"), ("week", "Week"), ("custom", "Custom")],
        "years": list(range(today.year, FIRST_YEAR - 1, -1)),
        "months": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "period_label": p.label,
    }
