"""Snow-Forecast.com — server-rendered forecast table, three elevation bands.

The site publishes separate bot/mid/top forecasts (the page's elevation
switcher states the metres for each band; probed live 2026-07-11):

    resort       top    mid    bot     resorts.py alt
    perisher     2034   1867   1700    1720
    thredbo      2037   1701   1365    1700
    hotham       1850   1652   1454    1750
    fallscreek   1780   1690   1600    1650
    buller       1790   1590   1390    1700

"mid" is the band closest to the other providers' ~1650-1750m reference
everywhere, so it keeps the canonical 'snowforecast' source name (unbroken
history/scoring). bot/top are also captured, stored as 'snowforecast_bot'
and 'snowforecast_top' — excluded from the ensemble (store.py) and absent
from the dashboard's source list, but available for elevation-gradient
analysis.

The table has a days header row (colspan = number of AM/PM/night columns)
and a data-row="snow" row with one cell per period, in cm ("—" = 0).
"""
from __future__ import annotations

import datetime as dt
import re
import sys

from resorts import Resort

from .common import get, today

SOURCE = "snowforecast"
URL = "https://www.snow-forecast.com/resorts/{slug}/6day/{level}"
# level -> stored source name; mid is canonical
LEVELS = {"mid": SOURCE, "bot": f"{SOURCE}_bot", "top": f"{SOURCE}_top"}
EXTRA_SOURCES = tuple(v for v in LEVELS.values() if v != SOURCE)


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


def _parse(html: str) -> dict[dt.date, float]:
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


def _collect_level(resort: Resort, level: str) -> dict[dt.date, float]:
    html = get(URL.format(slug=resort.snowforecast_slug, level=level)).text
    return _parse(html)


def collect(resort: Resort) -> dict[dt.date, float]:
    """Canonical mid-mountain forecast (the historical 'snowforecast' series)."""
    return _collect_level(resort, "mid")


def collect_multi(resort: Resort) -> dict[str, dict[dt.date, float]]:
    """All three elevation bands, keyed by stored source name.

    mid is mandatory (raises on failure, like collect()); bot/top are
    best-effort extras — a failure there must not cost us the canonical
    series, so it only warns.
    """
    out = {SOURCE: _collect_level(resort, "mid")}
    for level, name in LEVELS.items():
        if name == SOURCE:
            continue
        try:
            out[name] = _collect_level(resort, level)
        except Exception as e:  # noqa: BLE001 — extras are non-fatal
            print(f"[warn] {resort.id}/{name}: {e}", file=sys.stderr)
    return out
