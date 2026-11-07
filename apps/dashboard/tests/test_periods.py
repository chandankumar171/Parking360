from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.dashboard.periods import Buckets, filter_context, parse_period, previous_period

TODAY = date(2026, 10, 15)


class ParsePeriodTests(SimpleTestCase):
    def test_default_is_current_month_by_day(self):
        p = parse_period({}, TODAY)
        self.assertEqual((p.mode, p.start_date, p.end_date, p.group), ("month", date(2026, 10, 1), date(2026, 11, 1), "day"))
        self.assertEqual(p.label, "October 2026")

    def test_year_month_week_custom(self):
        y = parse_period({"mode": "year", "year": "2025"}, TODAY)
        self.assertEqual((y.start_date, y.end_date, y.group, y.label), (date(2025, 1, 1), date(2026, 1, 1), "month", "2025"))
        m = parse_period({"mode": "month", "year": "2026", "month": "2"}, TODAY)
        self.assertEqual((m.start_date, m.end_date), (date(2026, 2, 1), date(2026, 3, 1)))
        w = parse_period({"mode": "week", "date": "2026-10-07"}, TODAY)  # a Wednesday
        self.assertEqual((w.start_date, w.end_date, w.group), (date(2026, 10, 5), date(2026, 10, 12), "day"))
        c = parse_period({"mode": "custom", "start": "2026-10-01", "end": "2026-10-10"}, TODAY)
        self.assertEqual((c.start_date, c.end_date, c.days), (date(2026, 10, 1), date(2026, 10, 11), 10))  # end day included

    def test_invalid_input_falls_back_safely(self):
        p = parse_period({"mode": "zzz", "year": "abc", "month": "99", "group": "hour"}, TODAY)
        self.assertEqual((p.mode, p.year, p.month, p.group), ("month", 2026, 10, "day"))
        self.assertEqual(parse_period({"mode": "custom", "start": "2026-10-10", "end": "2026-10-01"}, TODAY).mode, "month")
        self.assertEqual(parse_period({"mode": "custom", "start": "nope", "end": "2026-10-01"}, TODAY).mode, "month")
        self.assertEqual(parse_period({"year": "1999"}, TODAY).year, 2026)

    def test_custom_range_is_capped_and_group_is_automatic(self):
        long = parse_period({"mode": "custom", "start": "2020-01-01", "end": "2026-01-01"}, TODAY)
        self.assertEqual(long.days, 731)
        self.assertEqual(parse_period({"mode": "custom", "start": "2026-01-01", "end": "2026-04-10"}, TODAY).group, "week")  # 100 days
        self.assertEqual(parse_period({"mode": "custom", "start": "2026-01-01", "end": "2026-01-20"}, TODAY).group, "day")
        self.assertEqual(parse_period({"mode": "year", "year": "2025", "group": "week"}, TODAY).group, "week")  # explicit override
        self.assertEqual(parse_period({"mode": "custom", "start": "2024-01-01", "end": "2025-06-01", "group": "day"}, TODAY).group, "week")  # too fine

    def test_previous_period(self):
        self.assertEqual(previous_period(parse_period({"mode": "month", "year": "2026", "month": "1"}, TODAY)).start_date, date(2025, 12, 1))
        prev_year = previous_period(parse_period({"mode": "year", "year": "2026"}, TODAY))
        self.assertEqual((prev_year.start_date, prev_year.end_date), (date(2025, 1, 1), date(2026, 1, 1)))
        prev_week = previous_period(parse_period({"mode": "week", "date": "2026-10-07"}, TODAY))
        self.assertEqual((prev_week.start_date, prev_week.end_date), (date(2026, 9, 28), date(2026, 10, 5)))
        prev_c = previous_period(parse_period({"mode": "custom", "start": "2026-10-01", "end": "2026-10-10"}, TODAY))
        self.assertEqual((prev_c.start_date, prev_c.end_date), (date(2026, 9, 21), date(2026, 10, 1)))

    def test_filter_context_prefills_controls(self):
        ctx = filter_context({"mode": "year", "year": "2025", "parking": "7", "group": "month"})
        self.assertEqual((ctx["initial"]["mode"], ctx["initial"]["year"], ctx["initial"]["parking"], ctx["initial"]["group"]), ("year", 2025, "7", "month"))
        self.assertEqual(len(ctx["months"]), 12)


class BucketTests(SimpleTestCase):
    def check_cover(self, period):
        b = Buckets(period)
        self.assertEqual(b.items[0].start_date, period.start_date)
        self.assertEqual(b.items[-1].end_date, period.end_date)
        for left, right in zip(b.items, b.items[1:]):
            self.assertEqual(left.end_date, right.start_date)  # no gaps or overlaps
        self.assertEqual(sum(x.days for x in b.items), period.days)
        return b

    def test_day_week_month_buckets_tile_the_period(self):
        day = self.check_cover(parse_period({"mode": "month", "year": "2026", "month": "2"}, TODAY))
        self.assertEqual(len(day), 28)
        week = self.check_cover(parse_period({"mode": "custom", "start": "2026-10-01", "end": "2026-10-20", "group": "week"}, TODAY))
        self.assertEqual([b.start_date.isoformat() for b in week.items], ["2026-10-01", "2026-10-05", "2026-10-12", "2026-10-19"])
        month = self.check_cover(parse_period({"mode": "year", "year": "2025"}, TODAY))
        self.assertEqual((len(month), month.labels[0], month.labels[-1]), (12, "Jan 2025", "Dec 2025"))
        partial = self.check_cover(parse_period({"mode": "custom", "start": "2026-01-15", "end": "2026-03-10", "group": "month"}, TODAY))
        self.assertEqual([b.days for b in partial.items], [17, 28, 10])

    def test_index_for(self):
        from django.utils import timezone
        import datetime as dt

        p = parse_period({"mode": "month", "year": "2026", "month": "10"}, TODAY)
        b = Buckets(p)
        tz = timezone.get_current_timezone()
        at = lambda d, h=12: timezone.make_aware(dt.datetime(2026, 10, d, h), tz)
        self.assertEqual(b.index_for(at(1, 0)), 0)
        self.assertEqual(b.index_for(at(31, 23)), 30)
        self.assertIsNone(b.index_for(timezone.make_aware(dt.datetime(2026, 11, 1, 0, 0), tz)))
        self.assertIsNone(b.index_for(timezone.make_aware(dt.datetime(2026, 9, 30, 23, 59), tz)))
