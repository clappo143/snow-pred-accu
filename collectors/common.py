"""Shared helpers for all collectors."""
from __future__ import annotations

import datetime as dt
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


def run_now() -> str:
    """Which snapshot slot the clock says this is: 'am' before noon AEST
    (the ~7:45 run, right after most providers' morning issuance), else
    'pm' (the classic ~6pm evening snapshot)."""
    return "am" if dt.datetime.now(TZ).hour < 12 else "pm"


def get(url: str, ua: str = BROWSER_UA, **kw) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": ua}, timeout=30, **kw)
    r.raise_for_status()
    return r
