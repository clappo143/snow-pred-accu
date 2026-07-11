"""Authoritative ground truth: the resort's own snow report.

Four scrapeable formats (resorts.py says which applies where):

  - "vail" (Perisher, Mt Hotham): the Vail-platform page server-renders a
    "24 Hrs" new-snow figure (to ~7am) plus "7 Days" and a depth item, with
    an "Updated: <day> <time>" stamp. Same markup on both sites, only the
    title case differs ("24 Hrs" vs "24 hrs"), so matching is
    case-insensitive. This is Star_Hawk's exact yardstick and is accurately
    dated with no reporting lag.

  - "falls_json" (Falls Creek): the WordPress JSON feed behind their snow
    report; ski patrol's fresh-snow figure (stamped ~6:15am) plus natural
    depth.

  - "thredbo_xml" (Thredbo): thredbo.com.au/weather/snow-report/ serves a
    raw LivePass snowReport XML document — <snow24Hours amount>,
    <snow7Days>, <avgsnowdepth>, and a full ISO `updated` attribute.
    (Re-probed 2026-07-11; the 2026-07-10 probe hit the wrong URL.)

  - "buller_json" (Mt Buller): api.mtbuller.com.au/api/weather/widget, the
    JSON feed behind the JS-rendered mtbuller.com.au snow report page —
    snow_report.snow_last_24_hours plus an ISO last_updated stamp.

Each collect() also returns `reported_at`, the source's report-issued
timestamp (ISO 8601, or None where the source has no usable stamp) — an
approximation of when the measurement was taken.

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


def _updated_stamp(html: str) -> tuple[dt.date, str | None]:
    """(report date, ISO reported_at or None) from the Vail 'Updated:' line,
    e.g. 'Updated: 11 Jul 7:05am' (year implied, AEST/AEDT local)."""
    m = re.search(
        r"Updated:\s*(?:</?[^>]*>\s*)*(\d{1,2})\s+([A-Za-z]{3})"
        r"(?:\s+(\d{1,2}):(\d{2})\s*(am|pm))?",
        html, re.I)
    if not m:
        return today(), None
    day, mon = int(m.group(1)), _MONTHS.get(m.group(2)[:3].title())
    if not mon:
        return today(), None
    year = dt.datetime.now(TZ).year
    date = dt.date(year, mon, day)
    if m.group(3) is None:
        return date, None
    hour, minute = int(m.group(3)) % 12, int(m.group(4))
    if m.group(5).lower() == "pm":
        hour += 12
    reported = dt.datetime(year, mon, day, hour, minute, tzinfo=TZ)
    return date, reported.isoformat()


def _collect_vail(url: str) -> dict:
    html = get(url).text
    snow_24h = _item(html, "24 Hrs")
    if snow_24h is None:
        raise ValueError("could not find 24 Hrs snowfall figure")
    depth = _item(html, "Natural Depth")
    if depth is None:
        depth = _item(html, "Total")  # Hotham's label for the depth item
    date, reported_at = _updated_stamp(html)
    return {
        "date": date,
        "snow_24h": snow_24h,
        "snow_7day": _item(html, "7 Days"),
        "natural_depth": depth,
        "reported_at": reported_at,
    }


def _collect_falls_json(url: str) -> dict:
    patrol = get(url).json()["Patrol"]
    snow = patrol.get("PatrolFreshSnow")
    if snow in (None, ""):
        raise ValueError("no PatrolFreshSnow in feed")
    date, reported_at = today(), None
    try:
        date = dt.datetime.strptime(patrol["PatrolDate"], "%d %B %Y").date()
        try:  # e.g. PatrolTime: '6:15 AM'
            t = dt.datetime.strptime(patrol["PatrolTime"].strip(), "%I:%M %p")
            reported_at = dt.datetime.combine(
                date, t.time(), tzinfo=TZ).isoformat()
        except (KeyError, ValueError):
            pass
    except (KeyError, ValueError):
        pass
    depth = patrol.get("PatrolNaturalSnowDepth")
    return {
        "date": date,
        "snow_24h": float(snow),
        "snow_7day": None,  # feed doesn't carry a 7-day total
        "natural_depth": float(depth) if depth not in (None, "") else None,
        "reported_at": reported_at,
    }


def _xml_amount(root, tag: str) -> float | None:
    el = root.find(f".//{tag}")
    amt = el.get("amount") if el is not None else None
    return float(amt) if amt not in (None, "") else None


def _collect_thredbo_xml(url: str) -> dict:
    from xml.etree import ElementTree

    root = ElementTree.fromstring(get(url).text)
    snow_24h = _xml_amount(root, "snow24Hours")
    if snow_24h is None:
        raise ValueError("no snow24Hours amount in XML")
    reported_at = root.get("updated")  # full ISO 8601 with offset
    try:
        date = dt.datetime.fromisoformat(reported_at).date()
    except (TypeError, ValueError):
        date, reported_at = today(), None
    depth = _xml_amount(root, "avgsnowdepth")
    if depth is None:
        depth = _xml_amount(root, "base")
    return {
        "date": date,
        "snow_24h": snow_24h,
        "snow_7day": _xml_amount(root, "snow7Days"),
        "natural_depth": depth,
        "reported_at": reported_at,
    }


def _collect_buller_json(url: str) -> dict:
    feed = get(url).json()
    rep = feed.get("snow_report") or {}
    snow = rep.get("snow_last_24_hours")
    if snow is None:
        raise ValueError("no snow_report.snow_last_24_hours in feed")
    reported_at = feed.get("last_updated")  # ISO 8601 with offset
    try:
        date = dt.datetime.fromisoformat(reported_at).date()
    except (TypeError, ValueError):
        date, reported_at = today(), None
    depth = rep.get("average_natural")
    return {
        "date": date,
        "snow_24h": float(snow),
        "snow_7day": None,  # widget only has 24/48/72h and season totals
        "natural_depth": float(depth) if depth is not None else None,
        "reported_at": reported_at,
    }


_COLLECTORS = {
    "vail": _collect_vail,
    "falls_json": _collect_falls_json,
    "thredbo_xml": _collect_thredbo_xml,
    "buller_json": _collect_buller_json,
}


def collect(resort: Resort) -> dict:
    fn = _COLLECTORS.get(resort.official_kind)
    if fn is None:
        raise ValueError(f"{resort.id} has no official report source configured")
    return fn(resort.official_url)
