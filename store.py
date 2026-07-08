"""SQLite storage. One row per (source, snapshot date, target date)."""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "snow.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    source      TEXT NOT NULL,
    issued_date TEXT NOT NULL,   -- local date the snapshot was taken
    target_date TEXT NOT NULL,   -- local date the forecast is for
    snow_cm     REAL NOT NULL,
    PRIMARY KEY (source, issued_date, target_date)
);
CREATE TABLE IF NOT EXISTS actuals (
    date    TEXT PRIMARY KEY,    -- local date the 24h-to-7am period ended
    snow_cm REAL NOT NULL,
    source  TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def save_forecasts(
    con: sqlite3.Connection,
    source: str,
    issued: dt.date,
    forecasts: dict[dt.date, float],
) -> None:
    con.executemany(
        "INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?)",
        [(source, issued.isoformat(), d.isoformat(), cm) for d, cm in forecasts.items()],
    )
    con.commit()


def save_actual(con: sqlite3.Connection, date: dt.date, cm: float, source: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO actuals VALUES (?,?,?)", (date.isoformat(), cm, source)
    )
    con.commit()


def merge_manual(con: sqlite3.Connection) -> tuple[int, int]:
    """Backfill from data/manual.json, a bundle the dashboard exports:

        {
          "actuals":   {"YYYY-MM-DD": cm, ...},
          "forecasts": [{"source": ..., "target_date": "YYYY-MM-DD", "cm": ...}, ...]
        }

    Manual rows never override feed data (INSERT OR IGNORE) — they only fill
    gaps, e.g. historical predictions transcribed from the forum. A manual
    forecast is treated as a 24h-lead call: issued_date = target_date − 1, so
    it slots straight into the existing scoring join.
    """
    import datetime as _dt
    import json

    path = DB_PATH.parent / "manual.json"
    if not path.exists():
        return (0, 0)
    bundle = json.loads(path.read_text())

    na = 0
    for date, cm in (bundle.get("actuals") or {}).items():
        na += con.execute(
            "INSERT OR IGNORE INTO actuals VALUES (?,?,?)", (date, float(cm), "manual")
        ).rowcount

    nf = 0
    for row in bundle.get("forecasts") or []:
        target = _dt.date.fromisoformat(row["target_date"])
        issued = (target - _dt.timedelta(days=1)).isoformat()
        nf += con.execute(
            "INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?)",
            (row["source"], issued, target.isoformat(), float(row["cm"])),
        ).rowcount

    con.commit()
    if na or nf:
        print(f"[ok] manual backfill: {na} actual(s), {nf} forecast(s)")
    return (na, nf)
