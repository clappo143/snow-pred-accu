"""SQLite storage. One forecast row per (resort, source, snapshot, target date).

Schema v2 (2026-07-10): adds `resort` to both tables and `run` ('am'/'pm')
to forecasts. Every snapshot is kept — the 6pm run no longer overwrites a
same-day morning capture — so accuracy can be scored at any lead, not just
the classic night-before call. connect() migrates a v1 database in place
(v1 rows become resort='perisher', run='pm').

Schema v3 (2026-07-11): adds nullable `reported_at` to actuals — the
timestamp the source says the report was issued (≈ when the measurement
was taken), ISO 8601 where the source exposes one. Existing rows keep
NULL. save_actual is rank-based so lagging proxies cannot overwrite official
figures, regardless of collection order.

Schema v4 (2026-07-12) adds an append-only `actual_observations` provenance
ledger and extends the effective `actuals` row with timestamp semantics,
source URL, depth/7-day context and update time. Existing readers remain
compatible. Manual actuals are protected corrections and outrank automation.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "snow.db"

# Sources that must never feed the ensemble: the ensemble itself, plus
# Snow-Forecast's extra elevation bands (bot/top) — the canonical mid band
# is the 'snowforecast' series; the extras are stored for elevation-gradient
# analysis only (see docs/reference-points.md). 'janesweather_cal' is the
# pre-2026-07-13 Jane's Weather series of calendar-day totals, retired when
# the collector moved to 7am→7am windows (see collectors/janesweather.py).
# Both BOM methodologies ('bom' and 'bom_meteye') feed the ensemble as
# independent forecasters — James's call 2026-07-11: eight forecasters, two
# of them the Bureau via different derivations, each accuracy-weighted on
# its own record.
NON_ENSEMBLE_SOURCES = (
    "ensemble", "snowforecast_bot", "snowforecast_top", "janesweather_cal",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    resort      TEXT NOT NULL,
    source      TEXT NOT NULL,
    issued_date TEXT NOT NULL,   -- local date the snapshot was taken
    run         TEXT NOT NULL,   -- 'am' (~7:45) or 'pm' (~18:00) snapshot
    target_date TEXT NOT NULL,   -- local calendar day the forecast is for
    snow_cm     REAL NOT NULL,
    PRIMARY KEY (resort, source, issued_date, run, target_date)
);
CREATE TABLE IF NOT EXISTS actuals (
    resort  TEXT NOT NULL,
    date    TEXT NOT NULL,       -- local date the 24h-to-7am period ended
    snow_cm REAL NOT NULL,
    source  TEXT NOT NULL,
    reported_at TEXT,            -- ISO 8601 report-issued stamp, if the source has one
    report_time_kind TEXT,
    source_url TEXT,
    natural_depth REAL,
    snow_7day REAL,
    updated_at TEXT,
    PRIMARY KEY (resort, date)
);
CREATE TABLE IF NOT EXISTS actual_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resort TEXT NOT NULL, date TEXT NOT NULL, snow_cm REAL NOT NULL,
    source TEXT NOT NULL, reported_at TEXT, report_time_kind TEXT,
    source_url TEXT, natural_depth REAL, snow_7day REAL,
    collected_at TEXT NOT NULL, raw_json TEXT, notes TEXT
);
CREATE INDEX IF NOT EXISTS actual_observations_resort_date
    ON actual_observations(resort, date);
"""

# Actuals-source precedence: higher rank wins; equal rank may refresh its
# own row (e.g. a later official scrape correcting the morning figure).
SOURCE_RANK = {"onthesnow": 1, "snowatch": 2, "official": 3, "manual": 4}


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _migrate_v1(con: sqlite3.Connection) -> None:
    """Upgrade a v1 database (no resort/run columns) in place. Idempotent."""
    cols = _columns(con, "forecasts")
    if cols and "resort" not in cols:
        con.executescript("""
            ALTER TABLE forecasts RENAME TO forecasts_v1;
            CREATE TABLE forecasts (
                resort TEXT NOT NULL, source TEXT NOT NULL,
                issued_date TEXT NOT NULL, run TEXT NOT NULL,
                target_date TEXT NOT NULL, snow_cm REAL NOT NULL,
                PRIMARY KEY (resort, source, issued_date, run, target_date)
            );
            INSERT INTO forecasts
                SELECT 'perisher', source, issued_date, 'pm', target_date, snow_cm
                FROM forecasts_v1;
            DROP TABLE forecasts_v1;
        """)
        print("[ok] migrated forecasts to schema v2 (resort + run columns)")
    cols = _columns(con, "actuals")
    if cols and "resort" not in cols:
        con.executescript("""
            ALTER TABLE actuals RENAME TO actuals_v1;
            CREATE TABLE actuals (
                resort TEXT NOT NULL, date TEXT NOT NULL,
                snow_cm REAL NOT NULL, source TEXT NOT NULL,
                PRIMARY KEY (resort, date)
            );
            INSERT INTO actuals
                SELECT 'perisher', date, snow_cm, source FROM actuals_v1;
            DROP TABLE actuals_v1;
        """)
        print("[ok] migrated actuals to schema v2 (resort column)")
    con.commit()


def _migrate_v3(con: sqlite3.Connection) -> None:
    """Add actuals.reported_at (nullable). Idempotent."""
    cols = _columns(con, "actuals")
    if cols and "reported_at" not in cols:
        con.execute("ALTER TABLE actuals ADD COLUMN reported_at TEXT")
        con.commit()
        print("[ok] migrated actuals to schema v3 (reported_at column)")


def _migrate_v4(con: sqlite3.Connection) -> None:
    """Add v4 columns without rebuilding or dropping the effective table."""
    cols = set(_columns(con, "actuals"))
    for name, sql_type in {
        "report_time_kind": "TEXT", "source_url": "TEXT",
        "natural_depth": "REAL", "snow_7day": "REAL", "updated_at": "TEXT",
    }.items():
        if cols and name not in cols:
            con.execute(f"ALTER TABLE actuals ADD COLUMN {name} {sql_type}")
    con.commit()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    _migrate_v1(con)
    con.executescript(SCHEMA)
    _migrate_v3(con)
    _migrate_v4(con)
    con.executescript(SCHEMA)
    return con


def load_forecasts_for_issued(
    con: sqlite3.Connection,
    resort: str,
    issued: dt.date,
    run: str,
    exclude: tuple[str, ...] = NON_ENSEMBLE_SOURCES,
) -> dict[str, dict[dt.date, float]]:
    """Every source's forecast rows for one resort's snapshot, regardless of
    which script invocation collected them (used to rebuild the ensemble
    when collectors run in separate jobs at different times)."""
    out: dict[str, dict[dt.date, float]] = {}
    rows = con.execute(
        "SELECT source, target_date, snow_cm FROM forecasts "
        "WHERE resort=? AND issued_date=? AND run=?",
        (resort, issued.isoformat(), run),
    ).fetchall()
    for source, target, cm in rows:
        if source in exclude:
            continue
        out.setdefault(source, {})[dt.date.fromisoformat(target)] = cm
    return out


def save_forecasts(
    con: sqlite3.Connection,
    resort: str,
    source: str,
    issued: dt.date,
    run: str,
    forecasts: dict[dt.date, float],
) -> None:
    con.executemany(
        "INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?)",
        [(resort, source, issued.isoformat(), run, d.isoformat(), cm)
         for d, cm in forecasts.items()],
    )
    con.commit()


def save_actual(
    con: sqlite3.Connection,
    resort: str,
    date: dt.date,
    cm: float,
    source: str,
    reported_at: str | None = None,
    report_time_kind: str | None = None,
    source_url: str | None = None,
    natural_depth: float | None = None,
    snow_7day: float | None = None,
    raw: dict | list | str | None = None,
    notes: str | None = None,
    collected_at: str | None = None,
) -> None:
    """Rank-based upsert (see SOURCE_RANK): a source only replaces an
    existing row of equal or lower rank, so a lagging proxy (snowatch,
    onthesnow) can never overwrite an official-report figure, while
    an equal-rank re-collection may refresh its own row."""
    import json

    collected_at = collected_at or dt.datetime.now(dt.timezone.utc).isoformat()
    raw_json = json.dumps(raw, default=str, sort_keys=True) if raw is not None else None
    con.execute(
        "INSERT INTO actual_observations "
        "(resort,date,snow_cm,source,reported_at,report_time_kind,source_url,"
        "natural_depth,snow_7day,collected_at,raw_json,notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (resort, date.isoformat(), cm, source, reported_at, report_time_kind,
         source_url, natural_depth, snow_7day, collected_at, raw_json, notes),
    )
    rank = SOURCE_RANK.get(source, 0)
    row = con.execute(
        "SELECT source FROM actuals WHERE resort=? AND date=?",
        (resort, date.isoformat()),
    ).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO actuals (resort,date,snow_cm,source,reported_at,"
            "report_time_kind,source_url,natural_depth,snow_7day,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (resort, date.isoformat(), cm, source, reported_at,
             report_time_kind, source_url, natural_depth, snow_7day, collected_at),
        )
    elif rank >= SOURCE_RANK.get(row[0], 0):
        con.execute(
            "UPDATE actuals SET snow_cm=?,source=?,reported_at=?,report_time_kind=?,"
            "source_url=?,natural_depth=?,snow_7day=?,updated_at=?"
            " WHERE resort=? AND date=?",
            (cm, source, reported_at, report_time_kind, source_url,
             natural_depth, snow_7day, collected_at, resort, date.isoformat()),
        )
    con.commit()


def merge_manual(con: sqlite3.Connection) -> tuple[int, int]:
    """Backfill from data/manual.json, a bundle the dashboard exports:

        {
          "actuals":   {"<resort>": {"YYYY-MM-DD": cm, ...}, ...},
          "forecasts": [{"resort": ..., "source": ..., "target_date": ...,
                         "cm": ...}, ...]
        }

    (v1 bundles — flat actuals, no resort keys — are read as Perisher.)

    Manual actuals are protected corrections and outrank automated feeds. A manual
    forecast is treated as the classic night-before call: issued_date =
    target_date − 1, run 'pm', so it slots into the existing scoring join.
    Manual actual dates use the same convention as the feed: the date is the
    morning the 24h-to-7am report was published.
    """
    import json

    path = DB_PATH.parent / "manual.json"
    if not path.exists():
        return (0, 0)
    bundle = json.loads(path.read_text())

    actuals = bundle.get("actuals") or {}
    if actuals and not all(isinstance(v, dict) for v in actuals.values()):
        actuals = {"perisher": actuals}  # v1 flat format
    na = 0
    for resort, days in actuals.items():
        for date, cm in days.items():
            cm = float(cm)
            existing = con.execute(
                "SELECT snow_cm,source FROM actuals WHERE resort=? AND date=?",
                (resort, date),
            ).fetchone()
            if existing == (cm, "manual"):
                continue
            save_actual(con, resort, dt.date.fromisoformat(date), float(cm),
                        "manual", notes="data/manual.json protected correction")
            na += 1

    nf = 0
    for row in bundle.get("forecasts") or []:
        target = dt.date.fromisoformat(row["target_date"])
        issued = (target - dt.timedelta(days=1)).isoformat()
        nf += con.execute(
            "INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?,?,?)",
            (row.get("resort", "perisher"), row["source"], issued, "pm",
             target.isoformat(), float(row["cm"])),
        ).rowcount

    con.commit()
    if na or nf:
        print(f"[ok] manual backfill: {na} actual(s), {nf} forecast(s)")
    return (na, nf)
