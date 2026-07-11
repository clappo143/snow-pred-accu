"""BOM MetEye text views — the ADFD snow grid deciding *what falls as snow*.

www.bom.gov.au/places/{slug}/forecast/detailed/ is the server-rendered
"text views" page behind MetEye's map (probed 2026-07-11): 7 days × 8
three-hourly blocks, each with a forecaster-edited Snow yes/no flag (the
same ADFD "Weather: Snow" grid the MetEye map paints blue) plus
probabilistic precipitation ("10/25/50% chance of more than N mm").

Methodology (v2, 2026-07-11 pm): daily snow = the SAME daily rain-range
midpoint 'bom' uses (bom.daily_rain, ~1mm -> 1cm) × the fraction of that
precip falling in snow-flagged blocks. The fraction weights blocks by
their 3-hourly 25%-chance amounts; when those are all zero (light or
far-out days) it falls back to the plain share of flagged blocks. Blocks
showing "–" (hours already past) are excluded from the fraction, and days
with no flags at all are omitted rather than reported as 0.

The two BOM series are thus a controlled comparison — identical precip
base, different snow attribution: 'bom' gates the whole day on
temp_max <= 2°C; this source apportions by the Bureau's own 3-hourly snow
flags, so marginal-temperature and mixed rain/snow days are where they
part ways.

(v1 summed the blocks' 50th-percentile amounts directly, but medians
don't add — showery days sum to ~0 even when the daily median is 10mm+,
collapsing every forecast beyond ~48h. Caught by James on day one.)
"""
from __future__ import annotations

import datetime as dt
import html
import re

from resorts import Resort

from . import bom
from .common import get, today

SOURCE = "bom_meteye"
URL = "https://www.bom.gov.au/places/{slug}/forecast/detailed/"

_WEIGHT_ROW = "25% chance of more than (mm)"
_SNOW_ROW = "Snow"


def _clean(cell: str) -> str:
    """Cell text: weather flags are <img alt="Yes"/"No">, values plain text."""
    cell = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*/?>', r"\1", cell)
    cell = re.sub(r"<[^>]+>", "", cell)
    return html.unescape(cell).strip()


def _day_date(heading: str, anchor: dt.date) -> dt.date | None:
    """'Saturday 11 July' -> the date, year inferred from `anchor`."""
    m = re.match(r"\w+\s+(\d{1,2})\s+(\w+)", heading)
    if not m:
        return None
    try:
        parsed = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d %B")
    except ValueError:
        return None
    date = parsed.date().replace(year=anchor.year)
    if date < anchor - dt.timedelta(days=180):  # December page read in January
        date = date.replace(year=anchor.year + 1)
    return date


def _rows(day_html: str) -> dict[str, list[str]]:
    """Row label -> cleaned cell values, across all of the day's tables."""
    out: dict[str, list[str]] = {}
    for row in re.findall(r"<tr.*?</tr>", day_html, re.S):
        cells = [_clean(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) > 1:
            out.setdefault(cells[0], cells[1:])
    return out


def _mm(cell: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", cell)
    return float(m.group(0)) if m else 0.0


def snow_fractions(resort: Resort) -> dict[dt.date, float]:
    """Per day, the share of forecast precip falling in snow-flagged
    3-hourly blocks (0..1). Days without any Yes/No flags are absent."""
    text = get(URL.format(slug=resort.meteye_slug)).text
    out: dict[dt.date, float] = {}
    for part in re.split(r"<h2[^>]*>", text)[1:]:
        date = _day_date(_clean(part.split("</h2>", 1)[0]), today())
        if date is None:
            continue
        rows = _rows(part)
        snow = rows.get(_SNOW_ROW)
        if not snow:
            continue
        weights = rows.get(_WEIGHT_ROW, [])
        weights += [""] * (len(snow) - len(weights))
        flagged = [(f, _mm(w)) for f, w in zip(snow, weights) if f in ("Yes", "No")]
        if not flagged:
            continue
        total = sum(w for _f, w in flagged)
        if total > 0:
            out[date] = sum(w for f, w in flagged if f == "Yes") / total
        else:
            out[date] = sum(1 for f, _w in flagged if f == "Yes") / len(flagged)
    return out


def collect(resort: Resort) -> dict[dt.date, float]:
    fractions = snow_fractions(resort)
    if not fractions:
        raise ValueError(f"no snow flags parsed for {resort.meteye_slug}")
    rain = bom.daily_rain(resort)
    return {
        date: (lo + hi) / 2 * fractions[date]
        for date, (lo, hi, _tmax) in rain.items()
        if date in fractions
    }
