"""Jane's Weather — public forecast-edge API behind their graph page.

`model=ml` is their blended machine-learning forecast (the headline JW
product). The daily summary carries dailySnowTotal in cm; no auth needed.
"""
from __future__ import annotations

import datetime as dt

from .common import LAT, LON, get

SOURCE = "janesweather"
URL = f"https://janesweather.com/api/v1/forecast-edge?lat={LAT}&lng={LON}&model=ml"


def collect() -> dict[dt.date, float]:
    data = get(URL).json()["data"]["forecast"]["summary"]
    out: dict[dt.date, float] = {}
    for day in data:
        total = day.get("dailySnowTotal")
        if total is None:
            continue  # beyond this model run's coverage
        date = dt.date.fromisoformat(day["valid"][:10])
        out[date] = round(float(total), 2)
    if not out:
        raise ValueError("empty summary")
    return out
