"""Open-Meteo — free model-based snow forecast, no key required.

Unlike the human forecasters, Open-Meteo also *archives its own past
forecast runs*, so this is the one source we can backfill programmatically
(see backfill_openmeteo.py). Going forward it's captured live like the rest.

snowfall_sum is a daily total in cm at the model grid cell, whose elevation
(~1722m) matches Perisher closely; we pin elevation=1720 for determinism.
"""
from __future__ import annotations

import datetime as dt

from .common import LAT, LON, get

SOURCE = "openmeteo"
ELEV = 1720
URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}&elevation={ELEV}"
    "&daily=snowfall_sum&forecast_days=11&timezone=Australia%2FSydney"
)


def collect() -> dict[dt.date, float]:
    daily = get(URL).json()["daily"]
    out = {
        dt.date.fromisoformat(t): float(v or 0.0)
        for t, v in zip(daily["time"], daily["snowfall_sum"])
    }
    if not out:
        raise ValueError("empty daily series")
    return out
