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

# Perisher Valley
LAT, LON, ALT = -36.405, 148.410, 1720


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def get(url: str, ua: str = BROWSER_UA, **kw) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": ua}, timeout=30, **kw)
    r.raise_for_status()
    return r
