"""Jane's Weather — public forecast-edge API behind their graph page.

`model=ml` is their blended machine-learning forecast (the headline JW
product). No auth needed. The API snaps lat/lng to the nearest
precalculated "snow location"; for all five resorts our coordinates snap
to the same points janesweather.com/snow-forecast itself uses, which JW
labels as upper-mountain (Perisher 1800 m, Thredbo 1850 m, Hotham 1850 m,
Falls Creek 1750 m, Buller 1700 m) — verified 2026-07-13 by querying the
API with JW's own published coordinates and getting identical snapped
points and totals.

Windowing (changed 2026-07-13): the response's daily summary
(dailySnowTotal) is a local *calendar-day* total — midnight to midnight,
confirmed against its own hourly values. Resort snow reports run 7am to
7am, so calendar-day totals are skewed 7 hours against the ground truth;
the 2026-07-11/12 storm fell almost entirely between 11pm and 7am and the
calendar series was scored a day off (huge under- then over-errors that
were an alignment artifact, not bad forecasts). We therefore aggregate
the hourly `snow` series into 7am → 7am windows: the value stored for
target date D covers 7am D to 7am D+1 local, which is exactly the window
of the resort report dated D+1 that score.py joins it to.

Rows collected before 2026-07-13 18:00 AEST were calendar-day totals and
live on under source name 'janesweather_cal' (non-ensemble, undisplayed),
so the canonical 'janesweather' series has one consistent meaning.

Hourlies extend ~9.3 days out (the daily summary reaches 11), so this
series has ~9 scoreable target days per snapshot instead of 11. A window
is only emitted when the hourlies cover it to the final hour; a window
already underway may start up to START_GRACE after 7am, which admits the
~7:45 am-run capture of the day-0 window without storing badly truncated
totals (an evening capture of the mostly-elapsed day-0 window is dropped
— it was hindsight, and score.py excluded it anyway).
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import get

SOURCE = "janesweather"
URL = "https://janesweather.com/api/v1/forecast-edge?lat={lat}&lng={lon}&model=ml"

WINDOW_START_HOUR = 7  # resort reports cover 24h to ~7am local
START_GRACE = dt.timedelta(hours=2)


def _windows(hours: list[tuple[dt.datetime, float]]) -> dict[dt.date, float]:
    """Sum (time, snow_cm) hourlies into 7am→7am windows keyed by the
    window's start date. Only windows the series actually covers are
    emitted: through the final hour, and from no later than 7am +
    START_GRACE."""
    first, last = hours[0][0], hours[-1][0]
    tz = first.tzinfo
    out: dict[dt.date, float] = {}
    day = first.date() - dt.timedelta(days=1)
    while day <= last.date():
        start = dt.datetime.combine(day, dt.time(WINDOW_START_HOUR), tzinfo=tz)
        end = start + dt.timedelta(days=1)
        if first <= start + START_GRACE and last >= end - dt.timedelta(hours=1):
            total = sum(cm for t, cm in hours if start <= t < end)
            out[day] = round(total, 2)
        day += dt.timedelta(days=1)
    return out


def collect(resort: Resort) -> dict[dt.date, float]:
    url = URL.format(lat=resort.lat, lon=resort.lon)
    values = get(url).json()["data"]["forecast"]["values"]
    hours = [
        (dt.datetime.fromisoformat(v["localTime"]), float(v.get("snow") or 0.0))
        for v in values
    ]
    hours.sort(key=lambda h: h[0])
    out = _windows(hours) if hours else {}
    if not out:
        raise ValueError("no complete 7am-window in hourly series")
    return out
