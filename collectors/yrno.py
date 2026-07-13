"""YR.no (MET Norway) — official free API.

Snow derivation mirrors yr.no's own site: precipitation falling at <= 1.0 C
counts as snow at roughly 1mm water -> 1cm snow.

Windowing (changed 2026-07-13, same fix as Jane's Weather): steps used to
be bucketed by local calendar date, a 7-hour skew against the 7am→7am
resort report. Steps are now spread into per-hour amounts and summed into
7am→7am windows via common.windows_7am: the value stored for target date
D covers 7am D to 7am D+1 local, exactly the window of the resort report
dated D+1 that score.py joins it to.

The compact feed is hourly for ~2.5 days, then 6-hourly blocks whose
boundaries (04/10/16/22 local in winter) straddle the 7am cut — each
block's precip is apportioned uniformly across its hours, so a 4am–10am
block splits 3h/3h across two windows. The whole block is still gated on
its single instant temperature, as before. The feed extends ~9.5 days, so
the last calendar day the old bucketing emitted is usually an incomplete
window and is now dropped rather than stored truncated.

Rows collected before the change were calendar-day totals and live on
under source name 'yrno_cal' (non-ensemble, undisplayed); the canonical
'yrno' series restarts clean — YR has no past-runs archive, so no
backfill is possible.
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import TZ, UA, get, windows_7am

SOURCE = "yrno"
URL = ("https://api.met.no/weatherapi/locationforecast/2.0/compact"
       "?lat={lat}&lon={lon}&altitude={alt}")
SNOW_TEMP_C = 1.0


def collect(resort: Resort) -> dict[dt.date, float]:
    url = URL.format(lat=resort.lat, lon=resort.lon, alt=resort.alt)
    data = get(url, ua=UA).json()
    hours: list[tuple[dt.datetime, float]] = []
    covered_until: dt.datetime | None = None
    for step in data["properties"]["timeseries"]:
        t = dt.datetime.fromisoformat(step["time"].replace("Z", "+00:00"))
        if covered_until and t < covered_until:
            continue
        d = step["data"]
        if "next_1_hours" in d:
            precip, n = d["next_1_hours"]["details"]["precipitation_amount"], 1
        elif "next_6_hours" in d:
            precip, n = d["next_6_hours"]["details"]["precipitation_amount"], 6
        else:
            continue
        covered_until = t + dt.timedelta(hours=n)
        temp = d["instant"]["details"]["air_temperature"]
        cm = precip if temp <= SNOW_TEMP_C and precip > 0 else 0.0  # 1mm water ~ 1cm snow
        local = t.astimezone(TZ)
        for i in range(n):
            hours.append((local + dt.timedelta(hours=i), cm / n))
    hours.sort(key=lambda h: h[0])
    out = windows_7am(hours) if hours else {}
    if not out:
        raise ValueError("no complete 7am-window in series")
    return out
