"""BOM MetEye text views — the ADFD grids sampled at the resort's grid point.

www.bom.gov.au/places/{slug}/forecast/detailed/ is the server-rendered
"text views" page behind MetEye's map (probed 2026-07-11): 7 days × 8
three-hourly blocks, each with a forecaster-edited Snow yes/no flag (the
same ADFD "Weather: Snow" grid the MetEye map paints blue) plus
probabilistic precipitation ("10/25/50% chance of more than N mm").

Snowfall here = the sum of 50th-percentile precip over exactly the blocks
the Bureau flags as snow, at ~1mm water -> 1cm snow — the same convention
as collectors/bom.py, which instead gates the *whole day's* rain range on
temp_max <= 2°C. Run in parallel with 'bom' precisely so the two
methodologies can be scored against each other.

Blocks show "–" for hours already past (and beyond the flag horizon);
those count as no-snow rather than failing the day.
"""
from __future__ import annotations

import datetime as dt
import html
import re

from resorts import Resort

from .common import get, today

SOURCE = "bom_meteye"
URL = "https://www.bom.gov.au/places/{slug}/forecast/detailed/"

_PRECIP_ROW = "50% chance of more than (mm)"
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


def collect(resort: Resort) -> dict[dt.date, float]:
    text = get(URL.format(slug=resort.meteye_slug)).text
    out: dict[dt.date, float] = {}
    for part in re.split(r"<h2[^>]*>", text)[1:]:
        date = _day_date(_clean(part.split("</h2>", 1)[0]), today())
        if date is None:
            continue
        rows = _rows(part)
        snow, precip = rows.get(_SNOW_ROW), rows.get(_PRECIP_ROW)
        if not snow or not precip:
            continue
        cm = 0.0
        for flag, mm in zip(snow, precip):
            if flag == "Yes":
                m = re.search(r"\d+(?:\.\d+)?", mm)
                cm += float(m.group(0)) if m else 0.0
        out[date] = cm
    if not out:
        raise ValueError(f"no forecast days parsed for {resort.meteye_slug}")
    return out
