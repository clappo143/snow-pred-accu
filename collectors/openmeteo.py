"""Open-Meteo — free model-based snow forecast, no key required.

Unlike the human forecasters, Open-Meteo also *archives its own past
forecast runs*, so this is the one source we can backfill programmatically
(see backfill_openmeteo.py). Going forward it's captured live like the rest.

snowfall_sum is a daily total in cm at the model grid cell; we pin each
resort's elevation (resorts.py) for determinism.
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import get

SOURCE = "openmeteo"
URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&elevation={alt}"
    "&daily=snowfall_sum&forecast_days=11&timezone=Australia%2FSydney"
)


def collect(resort: Resort) -> dict[dt.date, float]:
    url = URL.format(lat=resort.lat, lon=resort.lon, alt=resort.alt)
    daily = get(url).json()["daily"]
    out = {
        dt.date.fromisoformat(t): float(v or 0.0)
        for t, v in zip(daily["time"], daily["snowfall_sum"])
    }
    if not out:
        raise ValueError("empty daily series")
    return out
