"""Authoritative ground truth: Perisher's own snow report.

Perisher publishes a resort-wide "24 Hrs" new-snow figure (to ~7am), plus
"7 Days" and "Natural Depth", server-rendered in the page HTML with an
"Updated: <day> <time>" stamp. This is Star_Hawk's exact yardstick and is
accurately dated with no reporting lag — unlike OnTheSnow's recent[], whose
per-day attribution was found to disagree with Perisher's own 7-day total
(57cm vs 37cm for the same week), so we treat OnTheSnow as fallback only.

Perisher exposes only the current snapshot (no per-day history), so this
gives clean actuals going forward but cannot backfill past days.
"""
from __future__ import annotations

import datetime as dt
import re

from .common import TZ, get, today

SOURCE = "perisher"
URL = "https://www.perisher.com.au/reports-cams/reports/snow-report"

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _item(html: str, title: str) -> float | None:
    """The cm value following a snowfall-item title ('24 Hrs', '7 Days'…).

    Titles may carry a trailing marker (e.g. 'Natural Depth*'), so we anchor
    on the title text rather than an exact tag boundary.
    """
    i = html.find(f">{title}")
    if i < 0:
        return None
    m = re.search(r"([\d.]+)\s*(?:</[^>]*>\s*)*<[^>]*>\s*cm", html[i : i + 300])
    return float(m.group(1)) if m else None


def _updated_date(html: str) -> dt.date:
    m = re.search(r"Updated:\s*</?[^>]*>?\s*(\d{1,2})\s+([A-Za-z]{3})", html)
    if not m:
        return today()
    day, mon = int(m.group(1)), _MONTHS.get(m.group(2)[:3].title())
    if not mon:
        return today()
    year = dt.datetime.now(TZ).year
    return dt.date(year, mon, day)


def collect() -> dict:
    html = get(URL).text
    snow_24h = _item(html, "24 Hrs")
    if snow_24h is None:
        raise ValueError("could not find 24 Hrs snowfall figure")
    return {
        "date": _updated_date(html),
        "snow_24h": snow_24h,
        "snow_7day": _item(html, "7 Days"),
        "natural_depth": _item(html, "Natural Depth"),
    }
