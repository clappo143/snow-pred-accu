"""Snowatch — 15-day Perisher forecast, server-rendered.

Each day is a day_header ("WEDNESDAY 8TH") followed by a SNOW line that is
either "0cm" or a range like "2 - 7cm"; ranges become their midpoint.
Their forecast period is 6am-6am (vs the resort report's 7am-7am) — close
enough to score, noted here for honesty.
"""
from __future__ import annotations

import datetime as dt
import re

from .common import get, today

SOURCE = "snowatch"
URL = "https://www.snowatch.com.au/15-day-forecasts/perisher/"

_BLOCK = re.compile(
    r"day_header'>\s*[A-Z]+\s+(\d{1,2})(?:ST|ND|RD|TH)"
    r".*?SNOW:(?:&nbsp;|\s)*([\d\s.-]+?)\s*cm",
    re.S | re.I,
)


def _resolve_date(day_of_month: int, start: dt.date) -> dt.date:
    for offset in range(-1, 32):
        d = start + dt.timedelta(days=offset)
        if d.day == day_of_month:
            return d
    raise ValueError(f"cannot resolve day-of-month {day_of_month}")


def collect() -> dict[dt.date, float]:
    html = get(URL).text
    blocks = _BLOCK.findall(html)
    if not blocks:
        raise ValueError("no day/snow blocks found")
    out: dict[dt.date, float] = {}
    anchor = today()
    for dom, snow in blocks:
        date = _resolve_date(int(dom), anchor)
        anchor = date  # keep subsequent days sequential across month ends
        parts = [float(p) for p in re.split(r"\s*-\s*", snow.strip()) if p]
        out[date] = sum(parts) / len(parts) if parts else 0.0
    return out
