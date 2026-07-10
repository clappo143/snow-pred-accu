"""BOM — undocumented JSON API used by the BOM app/site.

Daily forecast gives rain min/max (mm) and temps. When the day is cold
enough that precipitation falls as snow (max <= SNOW_TMAX_C), we take the
midpoint of the rain range at ~1mm water -> 1cm snow.
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import TZ, get

SOURCE = "bom"
URL = "https://api.weather.bom.gov.au/v1/locations/{geohash}/forecasts/daily"
SNOW_TMAX_C = 2.0


def collect(resort: Resort) -> dict[dt.date, float]:
    data = get(URL.format(geohash=resort.bom_geohash)).json()
    out: dict[dt.date, float] = {}
    for day in data["data"]:
        date = (
            dt.datetime.fromisoformat(day["date"].replace("Z", "+00:00"))
            .astimezone(TZ)
            .date()
        )
        rain = day.get("rain") or {}
        lo = rain.get("amount", {}).get("min") if "amount" in rain else rain.get("min")
        hi = rain.get("amount", {}).get("max") if "amount" in rain else rain.get("max")
        lo = lo or 0
        hi = hi if hi is not None else lo
        tmax = day.get("temp_max")
        snow = (lo + hi) / 2 if (tmax is not None and tmax <= SNOW_TMAX_C) else 0.0
        out[date] = float(snow)
    return out
