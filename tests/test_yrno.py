import datetime as dt
import unittest
from unittest.mock import patch

from collectors import yrno
from resorts import RESORTS

UTC = dt.timezone.utc
COLD, WARM = -3.0, 4.0


def step(t: dt.datetime, hours: int, precip: float, temp: float = COLD) -> dict:
    block = {"details": {"precipitation_amount": precip}}
    return {
        "time": t.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "data": {
            "instant": {"details": {"air_temperature": temp}},
            f"next_{hours}_hours": block,
        },
    }


def series(start: dt.datetime, n_hourly: int, n_sixly: int,
           overrides: dict[dt.datetime, tuple[float, float]] | None = None) -> list[dict]:
    """`n_hourly` 1h steps then `n_sixly` 6h steps from `start` (UTC).
    overrides: step time -> (precip, temp)."""
    overrides = overrides or {}
    out = []
    t = start
    for _ in range(n_hourly):
        p, temp = overrides.get(t, (0.0, COLD))
        out.append(step(t, 1, p, temp))
        t += dt.timedelta(hours=1)
    for _ in range(n_sixly):
        p, temp = overrides.get(t, (0.0, COLD))
        out.append(step(t, 6, p, temp))
        t += dt.timedelta(hours=6)
    return out


class Response:
    def __init__(self, timeseries):
        self._timeseries = timeseries

    def json(self):
        return {"properties": {"timeseries": self._timeseries}}


def collect_with(timeseries: list[dict]) -> dict[dt.date, float]:
    with patch.object(yrno, "get", return_value=Response(timeseries)):
        return yrno.collect(RESORTS["perisher"])


def utc(y, mo, d, h):
    return dt.datetime(y, mo, d, h, tzinfo=UTC)


class YrnoWindowTests(unittest.TestCase):
    # AEST = UTC+10: local 7am = 21:00Z the previous day.

    def test_overnight_snow_lands_in_the_prior_days_window(self):
        # Snow at 2am local on the 12th belongs to the report dated the
        # 12th, which score.py joins to target date the 11th.
        start = utc(2026, 7, 10, 9)  # 7pm local on the 10th (pm run)
        dump = {utc(2026, 7, 11, h): (2.0, COLD) for h in (14, 15, 16)}  # 0-3am local 12th
        out = collect_with(series(start, n_hourly=60, n_sixly=8, overrides=dump))
        self.assertEqual(out[dt.date(2026, 7, 11)], 6.0)
        self.assertEqual(out[dt.date(2026, 7, 12)], 0.0)

    def test_warm_precip_is_not_snow(self):
        start = utc(2026, 7, 10, 9)
        rain = {utc(2026, 7, 11, 14): (5.0, WARM)}
        out = collect_with(series(start, n_hourly=60, n_sixly=8, overrides=rain))
        self.assertEqual(out[dt.date(2026, 7, 11)], 0.0)

    def test_six_hour_block_straddling_7am_splits_uniformly(self):
        # A 6h block 4am-10am local (18:00Z) with 6mm splits 3cm/3cm
        # across the two windows it straddles.
        start = utc(2026, 7, 10, 9)
        dump = {utc(2026, 7, 12, 18): (6.0, COLD)}  # 4am-10am local on the 13th
        out = collect_with(series(start, n_hourly=57, n_sixly=10, overrides=dump))
        self.assertEqual(out[dt.date(2026, 7, 12)], 3.0)
        self.assertEqual(out[dt.date(2026, 7, 13)], 3.0)

    def test_pm_run_drops_the_mostly_elapsed_day0_window(self):
        start = utc(2026, 7, 10, 9)  # 7pm local — 12h past the 7am open
        out = collect_with(series(start, n_hourly=60, n_sixly=8))
        self.assertNotIn(dt.date(2026, 7, 10), out)
        self.assertIn(dt.date(2026, 7, 11), out)

    def test_am_run_grace_admits_day0(self):
        # ~7:45 local capture: series begins 8am local, window began 7am.
        start = utc(2026, 7, 10, 22)  # 8am local on the 11th
        snow = {utc(2026, 7, 10, 23): (3.5, COLD)}  # 9am local
        out = collect_with(series(start, n_hourly=48, n_sixly=4, overrides=snow))
        self.assertEqual(out[dt.date(2026, 7, 11)], 3.5)

    def test_incomplete_final_window_is_dropped(self):
        start = utc(2026, 7, 10, 22)  # 8am local on the 11th
        # coverage ends start + 48h + 4*6h = 8am local on the 14th:
        # the window opening 7am on the 14th is 1h in — must be dropped.
        out = collect_with(series(start, n_hourly=48, n_sixly=4))
        self.assertIn(dt.date(2026, 7, 13), out)
        self.assertNotIn(dt.date(2026, 7, 14), out)

    def test_hourly_step_preferred_over_overlapping_six_hourly(self):
        # Real payloads carry both next_1_hours and next_6_hours on the
        # same step; the covered_until skip must not double-count.
        start = utc(2026, 7, 10, 9)
        ts = series(start, n_hourly=60, n_sixly=8,
                    overrides={utc(2026, 7, 11, 14): (2.0, COLD)})
        for s in ts[:60]:
            s["data"]["next_6_hours"] = {"details": {"precipitation_amount": 99.0}}
        out = collect_with(ts)
        self.assertEqual(out[dt.date(2026, 7, 11)], 2.0)

    def test_no_complete_window_raises(self):
        start = utc(2026, 7, 11, 2)  # noon local — past grace
        with self.assertRaises(ValueError):
            collect_with(series(start, n_hourly=10, n_sixly=0))


if __name__ == "__main__":
    unittest.main()
