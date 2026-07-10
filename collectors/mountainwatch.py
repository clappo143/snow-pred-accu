"""Mountainwatch — server-rendered 7-day forecast.

One "Snow: X cm" cell per day. Days are anchored to the "<Dayname> <dom>"
header labels rather than assumed to start today: the page is sometimes
served stale, still beginning on the previous day.
"""
from __future__ import annotations

import datetime as dt
import re

from resorts import Resort

from .common import get, today

SOURCE = "mountainwatch"
URL = "https://www.mountainwatch.com/australia/{slug}/weather/"

_DAY_LABEL = re.compile(
    r">\s*(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\s+(\d{1,2})\s*<"
)


def _resolve_date(day_of_month: int, start: dt.date) -> dt.date:
    for offset in range(-1, 32):
        d = start + dt.timedelta(days=offset)
        if d.day == day_of_month:
            return d
    raise ValueError(f"cannot resolve day-of-month {day_of_month}")


def collect(resort: Resort) -> dict[dt.date, float]:
    html = get(URL.format(slug=resort.mountainwatch_slug)).text
    values = re.findall(r"Snow:\s*([\d.]+)\s*cm", html)
    doms = _DAY_LABEL.findall(html)
    if not values:
        raise ValueError("no snow values found")
    if not doms:
        raise ValueError("no day labels found to anchor dates")
    first = _resolve_date(int(doms[0]), today())
    return {
        first + dt.timedelta(days=i): float(v) for i, v in enumerate(values)
    }
