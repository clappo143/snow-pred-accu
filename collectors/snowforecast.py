"""Snow-Forecast.com — server-rendered forecast table (mid-mountain).

The table has a days header row (colspan = number of AM/PM/night columns)
and a data-row="snow" row with one cell per period, in cm ("—" = 0).
"""
from __future__ import annotations

import datetime as dt
import re

from resorts import Resort

from .common import get, today

SOURCE = "snowforecast"
URL = "https://www.snow-forecast.com/resorts/{slug}/6day/mid"


def _row_cells(html: str, row: str) -> list[str]:
    i = html.find(f'data-row="{row}"')
    if i < 0:
        raise ValueError(f"row {row!r} not found")
    seg = html[i : i + html[i:].find("</tr>")]
    return [
        re.sub(r"<[^>]+>", "", c).strip()
        for c in re.findall(r"<td[^>]*>(.*?)</td>", seg, re.S)
    ]


def _resolve_date(day_of_month: int, start: dt.date) -> dt.date:
    # the table's first column can still be yesterday shortly after midnight
    # or on a stale cache, so begin the search one day back
    for offset in range(-1, 32):
        d = start + dt.timedelta(days=offset)
        if d.day == day_of_month:
            return d
    raise ValueError(f"cannot resolve day-of-month {day_of_month}")


def collect(resort: Resort) -> dict[dt.date, float]:
    html = get(URL.format(slug=resort.snowforecast_slug)).text
    i = html.find('data-row="days"')
    seg = html[i : i + html[i:].find("</tr>")]
    days = re.findall(r'colspan="(\d+)"[^>]*data-value="([^"]+)"', seg)
    if not days:
        days = [
            (c, v)
            for v, c in re.findall(r'data-value="([^"]+)"[^>]*colspan="(\d+)"', seg)
        ]
    snow_cells = _row_cells(html, "snow")

    out: dict[dt.date, float] = {}
    idx = 0
    start = today()
    for colspan, label in days:
        dom = int(label.rsplit("_", 1)[1])
        date = _resolve_date(dom, start)
        total = 0.0
        for cell in snow_cells[idx : idx + int(colspan)]:
            if cell and cell != "—":
                total += float(cell)
        idx += int(colspan)
        out[date] = total
    return out
