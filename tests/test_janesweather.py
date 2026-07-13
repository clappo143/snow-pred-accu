import datetime as dt
import unittest
from unittest.mock import patch

from collectors import janesweather as jw
from resorts import RESORTS

TZ = dt.timezone(dt.timedelta(hours=10))


def hourly(start: dt.datetime, snow_by_hour: dict[dt.datetime, float],
           n_hours: int) -> list[dict]:
    return [
        {"localTime": (start + dt.timedelta(hours=i)).isoformat(),
         "snow": snow_by_hour.get(start + dt.timedelta(hours=i), 0.0)}
        for i in range(n_hours)
    ]


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def collect_with(values: list[dict]) -> dict[dt.date, float]:
    payload = {"data": {"forecast": {"values": values}}}
    with patch.object(jw, "get", return_value=Response(payload)):
        return jw.collect(RESORTS["perisher"])


class JanesWeatherWindowTests(unittest.TestCase):
    def test_overnight_snow_lands_in_the_prior_days_window(self):
        # Snow at 2am on the 12th belongs to the report dated the 12th,
        # which score.py joins to target date the 11th.
        start = dt.datetime(2026, 7, 10, 19, 0, tzinfo=TZ)  # pm-run capture
        dump = {dt.datetime(2026, 7, 12, h, 0, tzinfo=TZ): 2.0 for h in (0, 1, 2)}
        out = collect_with(hourly(start, dump, n_hours=96))
        self.assertEqual(out[dt.date(2026, 7, 11)], 6.0)
        self.assertEqual(out[dt.date(2026, 7, 12)], 0.0)

    def test_pm_run_drops_the_mostly_elapsed_day0_window(self):
        start = dt.datetime(2026, 7, 10, 19, 0, tzinfo=TZ)
        out = collect_with(hourly(start, {}, n_hours=96))
        # Window starting 7am on the 10th began 12h before the capture.
        self.assertNotIn(dt.date(2026, 7, 10), out)
        self.assertIn(dt.date(2026, 7, 11), out)

    def test_am_run_grace_admits_day0(self):
        # ~7:45 capture: hourlies begin 8am, window began 7am — within grace.
        start = dt.datetime(2026, 7, 11, 8, 0, tzinfo=TZ)
        snow = {dt.datetime(2026, 7, 11, 9, 0, tzinfo=TZ): 3.5}
        out = collect_with(hourly(start, snow, n_hours=72))
        self.assertEqual(out[dt.date(2026, 7, 11)], 3.5)

    def test_incomplete_final_window_is_dropped(self):
        start = dt.datetime(2026, 7, 11, 8, 0, tzinfo=TZ)
        out = collect_with(hourly(start, {}, n_hours=72))  # ends 07-14 07:00
        self.assertIn(dt.date(2026, 7, 13), out)   # covered to 7am 07-14
        self.assertNotIn(dt.date(2026, 7, 14), out)

    def test_null_snow_values_count_as_zero(self):
        start = dt.datetime(2026, 7, 11, 8, 0, tzinfo=TZ)
        values = hourly(start, {}, n_hours=48)
        values[5]["snow"] = None
        out = collect_with(values)
        self.assertEqual(out[dt.date(2026, 7, 11)], 0.0)

    def test_no_complete_window_raises(self):
        start = dt.datetime(2026, 7, 11, 12, 0, tzinfo=TZ)  # past grace
        with self.assertRaises(ValueError):
            collect_with(hourly(start, {}, n_hours=10))


if __name__ == "__main__":
    unittest.main()
