"""Ground truth: resort-reported daily snowfall via OnTheSnow's embedded JSON.

We use the per-date `recent[]` history rather than the `last24` counter:
the counter goes stale when the resort stops updating (it held last week's
storm total for days), while `recent[]` carries explicit 0cm days and can
be backfilled — each morning run upserts every date it contains.

Primary source for Thredbo and Mt Buller (no scrapeable official page);
fallback for the resorts with an official report. Caveat from v1: for
Perisher its per-day attribution disagreed with the resort's own 7-day
total (57cm vs 37cm for the same week), so official reports win wherever
we have them.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from resorts import Resort

from .common import get, today

SOURCE = "onthesnow"
URL = "https://www.onthesnow.com/australia/{slug}/skireport"
MAX_LAG_DAYS = 4  # warn when the feed's newest entry is older than this


def collect(resort: Resort) -> dict[dt.date, float]:
    """Reported snowfall (cm) per date, newest entries last."""
    html = get(URL.format(slug=resort.onthesnow_slug)).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ not found")
    full = json.loads(m.group(1))["props"]["pageProps"]["fullResort"]
    recent = full.get("recent") or []
    if not recent:
        raise ValueError("no recent[] snowfall history in feed")
    out = {
        dt.date.fromisoformat(r["date"]): float(r["snow"])
        for r in recent
        if r.get("snow") is not None
    }
    lag = (today() - max(out)).days
    if lag > MAX_LAG_DAYS:
        print(f"[warn] onthesnow {resort.id} report is {lag} days behind "
              f"(newest {max(out)})")
    return out
