import datetime as dt
import unittest
from unittest.mock import patch

import backfill_openmeteo
from collectors import openmeteo as om
from resorts import RESORTS


def hourly(start: dt.datetime, snow_by_stamp: dict[dt.datetime, float],
           n_hours: int) -> dict:
    """Open-Meteo `hourly` block: naive local stamps, each value covering
    the preceding hour."""
    stamps = [start + dt.timedelta(hours=i) for i in range(n_hours)]
    return {
        "time": [t.isoformat() for t in stamps],
        "snowfall": [snow_by_stamp.get(t, 0.0) for t in stamps],
    }


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def collect_with(hourly_block: dict) -> dict[dt.date, float]:
    with patch.object(om, "get", return_value=Response({"hourly": hourly_block})):
        return om.collect(RESORTS["perisher"])


def full_series(day0: dt.date, snow: dict[dt.datetime, float]) -> dict:
    """What the live API returns: midnight day0 → 11pm day0+11 (12 days)."""
    return hourly(dt.datetime.combine(day0, dt.time(0)), snow, n_hours=12 * 24)


class OpenMeteoWindowTests(unittest.TestCase):
    def test_overnight_snow_lands_in_the_prior_days_window(self):
        # Values stamped 1-3am on the 14th cover hours after midnight, i.e.
        # the report dated the 14th, which score.py joins to target the 13th.
        day0 = dt.date(2026, 7, 13)
        dump = {dt.datetime(2026, 7, 14, h, 0): 2.0 for h in (1, 2, 3)}
        out = collect_with(full_series(day0, dump))
        self.assertEqual(out[dt.date(2026, 7, 13)], 6.0)
        self.assertEqual(out[dt.date(2026, 7, 14)], 0.0)

    def test_preceding_hour_boundary(self):
        # Stamp 07:00 covers 6-7am (prior window); 08:00 covers 7-8am (next).
        day0 = dt.date(2026, 7, 13)
        out = collect_with(full_series(day0, {dt.datetime(2026, 7, 14, 7, 0): 1.0}))
        self.assertEqual(out[dt.date(2026, 7, 13)], 1.0)
        out = collect_with(full_series(day0, {dt.datetime(2026, 7, 14, 8, 0): 1.0}))
        self.assertEqual(out[dt.date(2026, 7, 14)], 1.0)

    def test_twelve_forecast_days_yield_eleven_full_windows(self):
        day0 = dt.date(2026, 7, 13)
        out = collect_with(full_series(day0, {}))
        self.assertEqual(sorted(out), [day0 + dt.timedelta(days=i)
                                       for i in range(11)])

    def test_null_snow_values_count_as_zero(self):
        block = full_series(dt.date(2026, 7, 13), {})
        block["snowfall"][30] = None  # stamp 06:00 on the 14th → window of the 13th
        out = collect_with(block)
        self.assertEqual(out[dt.date(2026, 7, 13)], 0.0)

    def test_no_complete_window_raises(self):
        start = dt.datetime(2026, 7, 13, 0, 0)
        with self.assertRaises(ValueError):
            collect_with(hourly(start, {}, n_hours=10))


class BackfillWindowTests(unittest.TestCase):
    def fetch_with(self, hourly_block, start, end):
        payload = {"hourly": hourly_block}
        with patch.object(backfill_openmeteo, "get",
                          return_value=Response(payload)):
            return backfill_openmeteo.fetch(RESORTS["perisher"], start, end)

    def test_windows_clipped_to_requested_range(self):
        # Archive response for start..end+1 → exactly start..end windows,
        # with overnight snow credited to the prior day's window.
        start, end = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        block = hourly(dt.datetime(2026, 6, 1, 0, 0),
                       {dt.datetime(2026, 6, 3, 2, 0): 4.0}, n_hours=4 * 24)
        out = self.fetch_with(block, start, end)
        self.assertEqual(sorted(out), [dt.date(2026, 6, d) for d in (1, 2, 3)])
        self.assertEqual(out[dt.date(2026, 6, 2)], 4.0)

    def test_trailing_nulls_drop_the_uncovered_window(self):
        # Hours the archive hasn't got yet come back null: the final
        # window must be dropped, not stored truncated-as-zero.
        start, end = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        block = hourly(dt.datetime(2026, 6, 1, 0, 0), {}, n_hours=4 * 24)
        block["snowfall"][-20:] = [None] * 20
        out = self.fetch_with(block, start, end)
        self.assertNotIn(dt.date(2026, 6, 3), out)
        self.assertIn(dt.date(2026, 6, 2), out)


if __name__ == "__main__":
    unittest.main()
