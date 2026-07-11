"""BOM — undocumented JSON API used by the BOM app/site.

Daily forecast gives rain min/max (mm) and temps. When the day is cold
enough that precipitation falls as snow (max <= SNOW_TMAX_C), we take the
midpoint of the rain range at ~1mm water -> 1cm snow.

Also OR'd in: BOM's own qualitative call (short_text/extended_text
mentioning "snow"). Caught 2026-07-11 comparing against bom_meteye: at
Mt Hotham the API's temp_max ran 4-6C for four straight days while
short_text read "Snow showers" every one of them — temp_max there isn't
representative of where the snow is actually falling, so a pure numeric
gate silently zeroed out days BOM itself was calling snow. Checked full
week across all five resorts: every day this adds was explicitly
"snow"-texted by BOM, and no dry/sunny day is affected.

daily_rain() is shared with collectors/bom_meteye.py so both BOM
methodologies work from the identical precip base and differ only in how
they attribute it to snow.
"""
from __future__ import annotations

import datetime as dt

from resorts import Resort

from .common import TZ, get

SOURCE = "bom"
URL = "https://api.weather.bom.gov.au/v1/locations/{geohash}/forecasts/daily"
SNOW_TMAX_C = 2.0


def daily_rain(
    resort: Resort,
) -> dict[dt.date, tuple[float, float, float | None, bool]]:
    """Per local calendar day: (rain_min_mm, rain_max_mm, temp_max, snow_text)."""
    data = get(URL.format(geohash=resort.bom_geohash)).json()
    out: dict[dt.date, tuple[float, float, float | None, bool]] = {}
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
        text = f"{day.get('short_text') or ''} {day.get('extended_text') or ''}"
        out[date] = (float(lo), float(hi), day.get("temp_max"), "snow" in text.lower())
    return out


def collect(resort: Resort) -> dict[dt.date, float]:
    return {
        date: (lo + hi) / 2
        if ((tmax is not None and tmax <= SNOW_TMAX_C) or snow_text)
        else 0.0
        for date, (lo, hi, tmax, snow_text) in daily_rain(resort).items()
    }
