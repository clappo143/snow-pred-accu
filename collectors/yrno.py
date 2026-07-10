"""YR.no (MET Norway) — official free API.

Snow derivation mirrors yr.no's own site: precipitation falling at <= 1.0 C
counts as snow at roughly 1mm water -> 1cm snow.
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import TZ, UA, get

SOURCE = "yrno"
URL = ("https://api.met.no/weatherapi/locationforecast/2.0/compact"
       "?lat={lat}&lon={lon}&altitude={alt}")
SNOW_TEMP_C = 1.0


def collect(resort: Resort) -> dict[dt.date, float]:
    url = URL.format(lat=resort.lat, lon=resort.lon, alt=resort.alt)
    data = get(url, ua=UA).json()
    out: dict[dt.date, float] = {}
    covered_until: dt.datetime | None = None
    for step in data["properties"]["timeseries"]:
        t = dt.datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        if covered_until and t < covered_until:
            continue
        d = step["data"]
        if "next_1_hours" in d:
            precip, hours = d["next_1_hours"]["details"]["precipitation_amount"], 1
        elif "next_6_hours" in d:
            precip, hours = d["next_6_hours"]["details"]["precipitation_amount"], 6
        else:
            continue
        covered_until = t + dt.timedelta(hours=hours)
        temp = d["instant"]["details"]["air_temperature"]
        day = t.astimezone(TZ).date()
        out.setdefault(day, 0.0)
        if temp <= SNOW_TEMP_C and precip > 0:
            out[day] += precip  # 1mm water ~ 1cm snow
    return out
