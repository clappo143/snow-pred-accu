"""One-shot historical backfill of Open-Meteo's archived forecasts.

Open-Meteo's Historical Forecast API serves the model forecasts it archived
each day (a genuine ~1-day-lead prediction, not a reanalysis of observations).
We write each day as a 24h-lead forecast row (issued_date = target − 1) under
source 'openmeteo', so it slots straight into the existing scoring join and
earns a real season-long accuracy record wherever we hold an actual.

Usage:  python backfill_openmeteo.py [START] [END]      (dates YYYY-MM-DD)
Defaults: START=2026-06-01, END=yesterday (AEST).

Caveat worth remembering: Open-Meteo daily totals are local midnight-midnight,
while the resort report is ~7am-7am, so storm days can sit ±1 day off the
reported date — day-matched accuracy carries some timing noise.
"""
from __future__ import annotations

import datetime as dt
import sys

import store
from collectors.common import TZ, get

SOURCE = "openmeteo"
ELEV = 1720


def fetch(start: dt.date, end: dt.date) -> dict[dt.date, float]:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude=-36.4058&longitude=148.4117&elevation={ELEV}"
        f"&start_date={start}&end_date={end}"
        "&daily=snowfall_sum&timezone=Australia%2FSydney"
    )
    daily = get(url).json()["daily"]
    return {
        dt.date.fromisoformat(t): float(v or 0.0)
        for t, v in zip(daily["time"], daily["snowfall_sum"])
    }


def main(argv: list[str]) -> int:
    start = dt.date.fromisoformat(argv[0]) if len(argv) > 0 else dt.date(2026, 6, 1)
    end = (
        dt.date.fromisoformat(argv[1])
        if len(argv) > 1
        else dt.datetime.now(TZ).date() - dt.timedelta(days=1)
    )
    series = fetch(start, end)
    con = store.connect()
    inserted = 0
    for target, cm in series.items():
        issued = target - dt.timedelta(days=1)
        inserted += con.execute(
            "INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?)",
            (SOURCE, issued.isoformat(), target.isoformat(), cm),
        ).rowcount
    con.commit()
    print(f"[ok] openmeteo backfill: {len(series)} days {start}..{end}, "
          f"{inserted} new row(s) (existing rows preserved)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
