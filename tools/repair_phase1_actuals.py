"""One-off, idempotent Phase 1 repair for known legacy actual rows."""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store
from collectors.actuals_official import BULLER_REPORT_URL

JULY12 = {
    "perisher": (15.0, "2026-07-12T07:25:00+10:00", "report_publication"),
    "thredbo": (10.0, "2026-07-12T06:30:00+10:00", "report_publication"),
    "hotham": (17.0, "2026-07-12T07:33:00+10:00", "report_publication"),
    "fallscreek": (14.0, "2026-07-12T06:15:00+10:00", "patrol_observation"),
    "buller": (14.0, "2026-07-12T07:15:00+10:00", "documented_measurement"),
}


def main(dry_run: bool = False) -> None:
    # A dry run must itself be read-only, including on a pre-v4 database.
    con = sqlite3.connect(f"file:{store.DB_PATH}?mode=ro", uri=True)
    bad = con.execute(
        "SELECT resort,date,snow_cm FROM actuals WHERE source='perisher'"
    ).fetchall()
    print("legacy invalid-source rows:", bad)
    print("July 12 replacements:", JULY12)
    if dry_run:
        con.close()
        return
    con.close()
    backup_dir = Path(__file__).resolve().parents[1] / ".phase1-backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"snow-before-phase1-repair-{stamp}.db"
    shutil.copy2(store.DB_PATH, backup)
    print("backup:", backup)
    con = store.connect()

    # This row came from the v1 OnTheSnow-only collector; `perisher` was an
    # invalid provenance value, not a manual correction or official source.
    con.execute("UPDATE actuals SET source='onthesnow' WHERE source='perisher'")
    con.commit()
    urls = {
        "perisher": "https://www.perisher.com.au/media_files/snowreport12.xml",
        "thredbo": "https://www.thredbo.com.au/weather/snow-report/",
        "hotham": "https://snowreport.mthotham.com.au/resources/SnowReport.xml",
        "fallscreek": "https://www.fallscreek.com.au/wp-content/uploads/FCSnowReport.json",
        "buller": BULLER_REPORT_URL,
    }
    date = dt.date(2026, 7, 12)
    for resort, (snow, reported, kind) in JULY12.items():
        store.save_actual(
            con, resort, date, snow, "official", reported_at=reported,
            report_time_kind=kind, source_url=urls[resort],
            raw={"repair": "verified direct resort report", "snow24hCm": snow},
            notes="Phase 1 reconciliation of July 12 official report",
        )
    print("repair complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(parser.parse_args().dry_run)
