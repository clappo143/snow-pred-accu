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


def merge_manual_actuals(con: sqlite3.Connection) -> int:
    """Backfill from data/manual_actuals.json ({"YYYY-MM-DD": cm}).

    Manual entries never override feed data — they only fill missing dates
    (e.g. season history from before tracking started).
    """
    import json

    path = DB_PATH.parent / "manual_actuals.json"
    if not path.exists():
        return 0
    entries = json.loads(path.read_text())
    n = 0
    for date, cm in entries.items():
        cur = con.execute(
            "INSERT OR IGNORE INTO actuals VALUES (?,?,?)", (date, float(cm), "manual")
        )
        n += cur.rowcount
    con.commit()
    if n:
        print(f"[ok] manual actuals: {n} date(s) backfilled")
    return n
