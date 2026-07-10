"""Authoritative ground truth: the resort's own snow report.

Two scrapeable formats (resorts.py says which applies where):

  - "vail" (Perisher, Mt Hotham): the Vail-platform page server-renders a
    "24 Hrs" new-snow figure (to ~7am) plus "7 Days" and a depth item, with
    an "Updated: <day> <time>" stamp. Same markup on both sites, only the
    title case differs ("24 Hrs" vs "24 hrs"), so matching is
    case-insensitive. This is Star_Hawk's exact yardstick and is accurately
    dated with no reporting lag.

  - "falls_json" (Falls Creek): the WordPress JSON feed behind their snow
    report; ski patrol's fresh-snow figure (stamped ~6:15am) plus natural
    depth.

Thredbo and Mt Buller have no scrapeable official page (JS-rendered / not
found, probed 2026-07-10) — OnTheSnow is the primary source there instead.

The official pages expose only the current snapshot (no per-day history),
so this gives clean actuals going forward but cannot backfill past days.
"""
from __future__ import annotations

import datetime as dt
import re

from resorts import Resort

from .common import TZ, get, today

SOURCE = "official"

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _item(html: str, title: str) -> float | None:
    """The cm value following a snowfall-item title ('24 Hrs', '7 Days'…).

    Titles may carry a trailing marker (e.g. 'Natural Depth*') and differ in
    case between the Vail sites, so we search case-insensitively and anchor
    on the title text rather than an exact tag boundary.
    """
    m = re.search(f">{re.escape(title)}", html, re.I)
    if not m:
        return None
    i = m.start()
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


def _collect_vail(url: str) -> dict:
    html = get(url).text
    snow_24h = _item(html, "24 Hrs")
    if snow_24h is None:
        raise ValueError("could not find 24 Hrs snowfall figure")
    depth = _item(html, "Natural Depth")
    if depth is None:
        depth = _item(html, "Total")  # Hotham's label for the depth item
    return {
        "date": _updated_date(html),
        "snow_24h": snow_24h,
        "snow_7day": _item(html, "7 Days"),
        "natural_depth": depth,
    }


def _collect_falls_json(url: str) -> dict:
    patrol = get(url).json()["Patrol"]
    snow = patrol.get("PatrolFreshSnow")
    if snow in (None, ""):
        raise ValueError("no PatrolFreshSnow in feed")
    try:
        date = dt.datetime.strptime(patrol["PatrolDate"], "%d %B %Y").date()
    except (KeyError, ValueError):
        date = today()
    depth = patrol.get("PatrolNaturalSnowDepth")
    return {
        "date": date,
        "snow_24h": float(snow),
        "snow_7day": None,  # feed doesn't carry a 7-day total
        "natural_depth": float(depth) if depth not in (None, "") else None,
    }


def collect(resort: Resort) -> dict:
    if resort.official_kind == "vail":
        return _collect_vail(resort.official_url)
    if resort.official_kind == "falls_json":
        return _collect_falls_json(resort.official_url)
    raise ValueError(f"{resort.id} has no official report source configured")
