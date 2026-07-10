"""One-shot historical backfill of Open-Meteo's archived forecasts.

Open-Meteo's Historical Forecast API serves the model forecasts it archived
each day (a genuine ~1-day-lead prediction, not a reanalysis of observations).
We write each day as a night-before forecast row (issued_date = target − 1,
run='pm') under source 'openmeteo', so it slots straight into the scoring
join and earns a real season-long accuracy record wherever we hold an actual.

Usage:  python backfill_openmeteo.py [START] [END] [RESORT]
        (dates YYYY-MM-DD; RESORT an id from resorts.py or 'all')
Defaults: START=2026-06-01, END=yesterday (AEST), RESORT=all.

Caveat worth remembering: Open-Meteo daily totals are local midnight-midnight,
while the resort report is ~7am-7am, so storm days can sit ±1 day off the
reported date — day-matched accuracy carries some timing noise.
"""
from __future__ import annotations

import datetime as dt
import sys

import store
from collectors.common import TZ, get
from resorts import RESORTS, Resort

SOURCE = "openmeteo"


def fetch(resort: Resort, start: dt.date, end: dt.date) -> dict[dt.date, float]:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude={resort.lat}&longitude={resort.lon}&elevation={resort.alt}"
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
    which = argv[2] if len(argv) > 2 else "all"
    targets = list(RESORTS.values()) if which == "all" else [RESORTS[which]]

    con = store.connect()
    for resort in targets:
        series = fetch(resort, start, end)
        inserted = 0
        for target, cm in series.items():
            issued = target - dt.timedelta(days=1)
            inserted += con.execute(
                "INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?)",
                (resort.id, SOURCE, issued.isoformat(), "pm",
                 target.isoformat(), cm),
            ).rowcount
        con.commit()
        print(f"[ok] openmeteo backfill {resort.id}: {len(series)} days "
              f"{start}..{end}, {inserted} new row(s) (existing preserved)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
