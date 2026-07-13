"""Shared helpers for all collectors."""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Australia/Sydney")
UA = "snow-pred-accu/0.1 (+https://github.com/clappo143/snow-pred-accu)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


WINDOW_START_HOUR = 7  # resort reports cover 24h to ~7am local
START_GRACE = dt.timedelta(hours=2)


def windows_7am(
    hours: list[tuple[dt.datetime, float]],
    grace: dt.timedelta = START_GRACE,
) -> dict[dt.date, float]:
    """Sum hourly snow into 7am→7am windows keyed by the window's start
    date — the exact window of the resort report dated D+1 that score.py
    joins target date D to.

    `hours` is a sorted list of (hour_start, snow_cm) where each value
    covers [t, t+1h). Only windows the series actually covers are emitted:
    through the final hour, and from no later than 7am + `grace` (which
    admits the ~7:45 am-run capture of the day-0 window without storing
    badly truncated totals; an evening capture of the mostly-elapsed day-0
    window is dropped — it was hindsight, and score.py excluded it anyway).
    """
    first, last = hours[0][0], hours[-1][0]
    tz = first.tzinfo
    out: dict[dt.date, float] = {}
    day = first.date() - dt.timedelta(days=1)
    while day <= last.date():
        start = dt.datetime.combine(day, dt.time(WINDOW_START_HOUR), tzinfo=tz)
        end = start + dt.timedelta(days=1)
        if first <= start + grace and last >= end - dt.timedelta(hours=1):
            total = sum(cm for t, cm in hours if start <= t < end)
            out[day] = round(total, 2)
        day += dt.timedelta(days=1)
    return out


def run_now() -> str:
    """Which snapshot slot the clock says this is: 'am' before noon AEST
    (the ~7:45 run, right after most providers' morning issuance), else
    'pm' (the classic ~6pm evening snapshot)."""
    return "am" if dt.datetime.now(TZ).hour < 12 else "pm"


def get(url: str, ua: str = BROWSER_UA, retries: int = 2, **kw) -> requests.Response:
    """GET with a couple of short-backoff retries on connect/read timeouts —
    api.open-meteo.com in particular has been flaky from GitHub-hosted
    runners (seen 2026-07-11/12), timing out a single collector without
    retrying it."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=30, **kw)
            r.raise_for_status()
            return r
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
