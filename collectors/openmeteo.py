"""Open-Meteo — free model-based snow forecast, no key required.

Unlike the human forecasters, Open-Meteo also *archives its own past
forecast runs*, so this is the one source we can backfill programmatically
(see backfill_openmeteo.py). Going forward it's captured live like the rest.

Windowing (changed 2026-07-13, same fix as Jane's Weather): the old
`daily=snowfall_sum` is a calendar-day total (each hourly value covers the
*preceding* hour and the daily aggregate groups stamps 00:00–23:00, so it
physically spans 11pm→11pm local — even more skewed than midnight→midnight
against the 7am→7am resort report). We now fetch `hourly=snowfall` and sum
it into 7am→7am windows via common.windows_7am: the value stored for
target date D covers 7am D to 7am D+1 local, exactly the window of the
resort report dated D+1 that score.py joins it to.

Rows collected (and backfilled) before the change were calendar-day totals
and live on under source name 'openmeteo_cal' (non-ensemble, undisplayed);
the canonical 'openmeteo' series restarts clean, with its season history
re-derived in the new windowing from the historical-forecast archive
(backfill_openmeteo.py).

forecast_days=12 keeps 11 scoreable windows: the hourly series runs
midnight today → 11pm day+11, which fully covers windows day 0 … day+10.

snowfall is in cm at the model grid cell; we pin each resort's elevation
(resorts.py) for determinism.
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import TZ, get, windows_7am

SOURCE = "openmeteo"
URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&elevation={alt}"
    "&hourly=snowfall&forecast_days=12&timezone=Australia%2FSydney"
)


def hours_from_hourly(hourly: dict) -> list[tuple[dt.datetime, float]]:
    """(hour_start, cm) pairs from an Open-Meteo `hourly` block. Stamps are
    naive local time and each value covers the *preceding* hour, so the
    value stamped 08:00 is re-keyed to hour_start 07:00."""
    return [
        (dt.datetime.fromisoformat(t).replace(tzinfo=TZ) - dt.timedelta(hours=1),
         float(v or 0.0))
        for t, v in zip(hourly["time"], hourly["snowfall"])
    ]


def collect(resort: Resort) -> dict[dt.date, float]:
    url = URL.format(lat=resort.lat, lon=resort.lon, alt=resort.alt)
    hours = hours_from_hourly(get(url).json()["hourly"])
    out = windows_7am(hours) if hours else {}
    if not out:
        raise ValueError("no complete 7am-window in hourly series")
    return out
