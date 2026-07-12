import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

import store


class StoreV4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = store.DB_PATH
        store.DB_PATH = Path(self.tmp.name) / "snow.db"

    def tearDown(self):
        store.DB_PATH = self.original
        self.tmp.cleanup()

    def test_migrates_v3_shape_additively(self):
        con = sqlite3.connect(store.DB_PATH)
        con.executescript("""
        CREATE TABLE forecasts (
          resort TEXT,source TEXT,issued_date TEXT,run TEXT,target_date TEXT,
          snow_cm REAL,PRIMARY KEY(resort,source,issued_date,run,target_date));
        CREATE TABLE actuals (
          resort TEXT,date TEXT,snow_cm REAL,source TEXT,reported_at TEXT,
          PRIMARY KEY(resort,date));
        """)
        con.close()
        con = store.connect()
        columns = {r[1] for r in con.execute("pragma table_info(actuals)")}
        self.assertTrue({"report_time_kind", "source_url", "updated_at"} <= columns)
        self.assertIn("actual_observations", {
            r[0] for r in con.execute("select name from sqlite_master where type='table'")})

    def test_observations_append_and_manual_is_protected(self):
        con = store.connect()
        day = dt.date(2026, 7, 12)
        store.save_actual(con, "buller", day, 14, "official")
        store.save_actual(con, "buller", day, 13, "manual")
        store.save_actual(con, "buller", day, 99, "official")
        effective = con.execute(
            "select snow_cm,source from actuals where resort='buller'"
        ).fetchone()
        self.assertEqual(effective, (13.0, "manual"))
        self.assertEqual(con.execute(
            "select count(*) from actual_observations").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
