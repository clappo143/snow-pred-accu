"""Authoritative ground truth: the resort's own snow report.

Four scrapeable formats (resorts.py says which applies where):

  - "perisher_xml" (Perisher): the resort's snowreport12.xml feed carries
    the 24-hour/top figure, seven-day figure, depth and report timestamp.

  - "hotham_xml" (Mt Hotham): the resort's SnowReport.xml feed carries
    snowfall, depth and a second-precision _LastUpdated timestamp.

  - "falls_json" (Falls Creek): the WordPress JSON feed behind their snow
    report; ski patrol's fresh-snow figure (stamped ~6:15am) plus natural
    depth.

  - "thredbo_xml" (Thredbo): thredbo.com.au/weather/snow-report/ serves a
    raw LivePass snowReport XML document — <snow24Hours amount>,
    <snow7Days>, <avgsnowdepth>, and a full ISO `updated` attribute.
    (Re-probed 2026-07-11; the 2026-07-10 probe hit the wrong URL.)

  - "buller_json" (Mt Buller): the API supplies the snow amount, while the
    public snow-report page supplies the separate "Ski Patrol update" time.
    The API's last_updated is a whole-widget weather refresh and must not be
    treated as the snow measurement/report time.

Each collect() also returns `reported_at` and `report_time_kind`. The latter
makes the timestamp semantics explicit: `report_publication`,
`patrol_observation`, or `documented_measurement` where the resort documents
the gauge-reading procedure. A generic API refresh is never promoted to a
snow-report timestamp.

The official pages expose only the current snapshot (no per-day history),
so this gives clean actuals going forward but cannot backfill past days.
"""
from __future__ import annotations

import datetime as dt
import re

from resorts import Resort

from .common import TZ, get, today

SOURCE = "official"
BULLER_REPORT_URL = "https://www.mtbuller.com.au/winter/snow-weather/snow-report"
THREDBO_REPORT_URL = "https://www.thredbo.com.au/weather/weather-report/"

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
    date, reported_at = _updated_stamp(html)
    return {
        "date": date,
        "snow_24h": snow_24h,
        "snow_7day": _item(html, "7 Days"),
        "natural_depth": depth,
        "reported_at": reported_at,
        "report_time_kind": "report_publication" if reported_at else None,
        "source_url": url,
    }


def _collect_perisher_xml(url: str) -> dict:
    from xml.etree import ElementTree

    root = ElementTree.fromstring(get(url).text)
    stamp = " ".join((root.findtext("date") or "").split())
    try:
        reported = dt.datetime.strptime(stamp, "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
    except ValueError as exc:
        raise ValueError("invalid Perisher report date") from exc
    def number(*tags: str) -> float | None:
        for tag in tags:
            value = root.findtext(tag)
            if value not in (None, ""):
                return float(value.strip())
        return None
    snow = number("new_snow_24hrs_top", "new_snow_24hrs")
    if snow is None:
        raise ValueError("no Perisher 24-hour snowfall")
    return {
        "date": reported.date(), "snow_24h": snow,
        "snow_7day": number("new_snow_7days"),
        "natural_depth": number("snowdepth"),
        "reported_at": reported.isoformat(),
        "report_time_kind": "report_publication", "source_url": url,
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
        "report_time_kind": "patrol_observation" if reported_at else None,
        "source_url": url,
    }


def _section_item(seg: str, label: str) -> float | None:
    """The cm value in the <h2> following a labelled tile in the Hotham
    'Natural snow fall and depth' section."""
    m = re.search(
        re.escape(label) + r"\s*(?:<[^>]*>\s*)*([\d.]+)\s*cm", seg)
    return float(m.group(1)) if m else None


def _collect_hotham_html(url: str) -> dict:
    html = get(url).text
    i = html.find("Natural snow fall and depth")
    if i < 0:
        raise ValueError("snow fall and depth section not found")
    seg = html[i : i + 4000]
    snow_24h = _section_item(seg, "Last 24hrs")
    if snow_24h is None:
        raise ValueError("could not find Last 24hrs figure")
    date, reported_at = today(), None
    m = re.search(
        r"Issued:\s*[A-Za-z]+\s+(\d{1,2})\s+([A-Za-z]+),\s*"
        r"(\d{1,2}):(\d{2})\s*(AM|PM)", seg, re.I)
    if m:
        try:
            date = dt.datetime.strptime(
                f"{m.group(1)} {m.group(2)} {dt.datetime.now(TZ).year}",
                "%d %B %Y").date()
            hour = int(m.group(3)) % 12 + (12 if m.group(5).upper() == "PM" else 0)
            reported_at = dt.datetime(
                date.year, date.month, date.day, hour, int(m.group(4)),
                tzinfo=TZ).isoformat()
        except ValueError:
            date, reported_at = today(), None
    return {
        "date": date,
        "snow_24h": snow_24h,
        "snow_7day": _section_item(seg, "Last 7 Days"),
        "natural_depth": _section_item(seg, "Depth"),
        "reported_at": reported_at,
        "report_time_kind": "report_publication" if reported_at else None,
        "source_url": url,
    }


def _collect_hotham_xml(url: str) -> dict:
    from xml.etree import ElementTree

    root = ElementTree.fromstring(get(url).text)
    stamp = (root.findtext("_LastUpdated") or "").strip()
    try:
        reported = dt.datetime.fromisoformat(stamp).replace(tzinfo=TZ)
    except ValueError as exc:
        raise ValueError("invalid Hotham _LastUpdated") from exc
    def number(tag: str) -> float | None:
        value = root.findtext(tag)
        return float(value.strip()) if value not in (None, "") else None
    snow = number("TwentyFourHourSnowfall")
    if snow is None:
        raise ValueError("no Hotham TwentyFourHourSnowfall")
    return {
        "date": reported.date(), "snow_24h": snow,
        "snow_7day": number("SevenDaySnowfall"),
        "natural_depth": number("CurrentSnowdepth"),
        "reported_at": reported.isoformat(),
        "report_time_kind": "report_publication", "source_url": url,
    }


def _xml_amount(root, tag: str) -> float | None:
    el = root.find(f".//{tag}")
    amt = el.get("amount") if el is not None else None
    return float(amt) if amt not in (None, "") else None


def _thredbo_report_stamp(html: str) -> str | None:
    """Publication stamp from the narrative weather report page."""
    m = re.search(
        r'class="report-date".*?(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4}),\s*'
        r'(\d{1,2}):(\d{2})\s*(AM|PM)', html, re.I | re.S)
    if not m:
        return None
    month = _MONTHS.get(m.group(2).title())
    if not month:
        return None
    hour = int(m.group(4)) % 12 + (12 if m.group(6).upper() == "PM" else 0)
    return dt.datetime(int(m.group(3)), month, int(m.group(1)), hour,
                       int(m.group(5)), tzinfo=TZ).isoformat()


def _collect_thredbo_xml(url: str) -> dict:
    from xml.etree import ElementTree

    root = ElementTree.fromstring(get(url).text)
    snow_24h = _xml_amount(root, "snow24Hours")
    if snow_24h is None:
        raise ValueError("no snow24Hours amount in XML")
    xml_updated_at = root.get("updated")  # data-feed refresh, retained as raw context
    try:
        reported_at = _thredbo_report_stamp(get(THREDBO_REPORT_URL).text)
    except Exception:
        reported_at = None
    # If the publication page is temporarily unavailable, the snow feed's own
    # update remains a transparent fallback rather than losing the actual.
    reported_at = reported_at or xml_updated_at
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
        "report_time_kind": "report_publication" if reported_at else None,
        "source_url": THREDBO_REPORT_URL,
        "data_url": url,
        "xml_updated_at": xml_updated_at,
    }


def _buller_patrol_stamp(html: str, year: int) -> tuple[dt.date, str] | None:
    """Parse the patrol narrative stamp, e.g. Sunday 12th July 7:15am."""
    i = html.lower().find("ski patrol update")
    if i < 0:
        return None
    plain = re.sub(r"<[^>]+>", " ", html[i : i + 5000])
    m = re.search(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+"
        r"(\d{1,2}):(\d{2})\s*(am|pm)", plain, re.I)
    if not m:
        return None
    try:
        date = dt.datetime.strptime(
            f"{m.group(1)} {m.group(2)} {year}", "%d %B %Y").date()
    except ValueError:
        return None
    hour = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "pm" else 0)
    stamp = dt.datetime(
        date.year, date.month, date.day, hour, int(m.group(4)), tzinfo=TZ)
    return date, stamp.isoformat()


def _collect_buller_json(url: str) -> dict:
    feed = get(url).json()
    rep = feed.get("snow_report") or {}
    snow = rep.get("snow_last_24_hours")
    if snow is None:
        raise ValueError("no snow_report.snow_last_24_hours in feed")
    widget_updated_at = feed.get("last_updated")  # weather/widget refresh only
    try:
        year = dt.datetime.fromisoformat(widget_updated_at).year
    except (TypeError, ValueError):
        year = dt.datetime.now(TZ).year
    patrol = None
    try:
        patrol = _buller_patrol_stamp(get(BULLER_REPORT_URL).text, year)
    except Exception:
        # The snow amount remains useful even if the independent page request
        # fails. Crucially, we do not substitute the widget refresh time.
        pass
    date, reported_at = patrol if patrol else (today(), None)
    depth = rep.get("average_natural")
    return {
        "date": date,
        "snow_24h": float(snow),
        "snow_7day": None,  # widget only has 24/48/72h and season totals
        "natural_depth": float(depth) if depth is not None else None,
        "reported_at": reported_at,
        "report_time_kind": "documented_measurement" if reported_at else None,
        "source_url": BULLER_REPORT_URL,
        "data_url": url,
        "widget_updated_at": widget_updated_at,
    }


_COLLECTORS = {
    "vail": _collect_vail,
    "perisher_xml": _collect_perisher_xml,
    "hotham_html": _collect_hotham_html,
    "hotham_xml": _collect_hotham_xml,
    "falls_json": _collect_falls_json,
    "thredbo_xml": _collect_thredbo_xml,
    "buller_json": _collect_buller_json,
}


def collect(resort: Resort) -> dict:
    fn = _COLLECTORS.get(resort.official_kind)
    if fn is None:
        raise ValueError(f"{resort.id} has no official report source configured")
    return fn(resort.official_url)
