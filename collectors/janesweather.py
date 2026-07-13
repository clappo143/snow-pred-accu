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
series has ~9 scoreable target days per snapshot instead of 11. Window
completeness/grace rules live in common.windows_7am (shared with the
Open-Meteo and YR.no collectors, re-windowed the same way 2026-07-13).
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import get, windows_7am

SOURCE = "janesweather"
URL = "https://janesweather.com/api/v1/forecast-edge?lat={lat}&lng={lon}&model=ml"


def collect(resort: Resort) -> dict[dt.date, float]:
    url = URL.format(lat=resort.lat, lon=resort.lon)
    values = get(url).json()["data"]["forecast"]["values"]
    hours = [
        (dt.datetime.fromisoformat(v["localTime"]), float(v.get("snow") or 0.0))
        for v in values
    ]
    hours.sort(key=lambda h: h[0])
    out = windows_7am(hours) if hours else {}
    if not out:
        raise ValueError("no complete 7am-window in hourly series")
    return out
