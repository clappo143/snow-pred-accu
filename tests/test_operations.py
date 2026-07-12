from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from operations.core import (
    BULLER_TRAILS_URL, Snapshot, activation_interval, archive_raw, connect,
    combined_local, coverage, disagreements, fetch_source, report_buller, report_falls,
    report_hotham, report_perisher, runs_mountainops, save_snapshot, trails_buller, SourceSpec,
)

FIXTURES = Path(__file__).parent / "fixtures" / "operations"
CAPTURED = "2026-07-12T00:00:00Z"


class OperationsTest(unittest.TestCase):
    def fixture_json(self, name: str):
        return json.loads((FIXTURES / name).read_text())

    def test_report_normalization(self):
        falls = report_falls(self.fixture_json("falls_active.json"), CAPTURED)
        self.assertEqual(falls.resortId, "falls")
        self.assertEqual(falls.snowmakingStatus, "active")
        self.assertNotEqual(falls.capturedAt, falls.sourceReportedAt)
        hotham = report_hotham((FIXTURES / "hotham_active.xml").read_text(), CAPTURED)
        self.assertEqual((hotham.snowmakingStatus, hotham.snowmakingRunCount), ("active", 5))
        perisher = report_perisher((FIXTURES / "perisher_guns.xml").read_text(), CAPTURED)
        self.assertEqual((perisher.snowmakingStatus, perisher.snowGunCount), ("active", 226))
        self.assertTrue(perisher.warnings)
        buller = report_buller(self.fixture_json("buller_widget.json"), CAPTURED)
        self.assertEqual((buller.machineMadeDepthCm, buller.naturalDepthCm, buller.snowmakingStatus), (56, 26, "active"))

    def test_run_flags_preserve_none_flagged(self):
        active = runs_mountainops("falls", "https://example.test/runs", self.fixture_json("mountainops_runs.json"), CAPTURED)
        self.assertEqual(active.snowmakingStatus, "active")
        zero = runs_mountainops("falls", "https://example.test/runs", [{"ID": 1, "Name": "A", "Snowmaking": "NO"}], CAPTURED)
        self.assertEqual(zero.snowmakingStatus, "none_flagged")
        self.assertNotEqual(zero.snowmakingStatus, "inactive")
        trails = trails_buller(self.fixture_json("buller_trails.json"), CAPTURED)
        self.assertEqual(trails.snowmakingStatus, "active")

    def test_unknown_and_failed_never_become_zero(self):
        run = runs_mountainops("falls", "https://example.test/runs", [{"ID": 1, "Name": "A"}], CAPTURED)
        self.assertEqual(run.snowmakingStatus, "unavailable")
        failed = Snapshot("thredbo_top", "resort_trails", "test", "https://example.test", CAPTURED, "unknown", retrievalStatus="failed")
        self.assertIsNone(failed.snowGunCount)
        self.assertEqual(failed.snowmakingStatus, "unknown")

    def test_raw_dedupe_and_append_only_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); con = connect(root / "ops.db")
            digest1, ref1 = archive_raw(root / "raw", "test", "https://example.test", CAPTURED, CAPTURED, 200, "application/json", '{"a":1}', [])
            digest2, ref2 = archive_raw(root / "raw", "test", "https://example.test", "2026-07-12T00:10:00Z", CAPTURED, 200, "application/json", '{"a":1}', [])
            self.assertEqual((digest1, ref1), (digest2, ref2))
            a = Snapshot("falls", "resort_report", "test", "https://example.test", CAPTURED, "active")
            b = Snapshot("falls", "resort_report", "test", "https://example.test", "2026-07-12T00:10:00Z", "active")
            save_snapshot(con, a, digest1, ref1); save_snapshot(con, b, digest1, ref1)
            self.assertEqual(con.execute("select count(*) from operations_snapshots").fetchone()[0], 2)
            run_snapshot = runs_mountainops("falls", "https://example.test/runs", self.fixture_json("mountainops_runs.json"), CAPTURED)
            save_snapshot(con, run_snapshot, digest1, ref1)
            self.assertEqual(con.execute("select count(*) from operations_runs").fetchone()[0], 2)

    def test_coverage_and_interval_censoring(self):
        rows = [
            Snapshot("falls", "resort_report", "a", "https://e", "2026-07-12T02:00:00Z", "mentioned").payload(),
            Snapshot("falls", "resort_report", "a", "https://e", "2026-07-12T02:10:00Z", "active").payload(),
        ]
        self.assertEqual(coverage(rows)["maximumGapMinutes"], 10)
        self.assertEqual(coverage([{**rows[0], "source": "report"}, {**rows[0], "id": "other", "source": "runs"}])["expectedCaptures"], 2)
        self.assertEqual(activation_interval(rows), {"earliest": "2026-07-12T02:00:00Z", "latest": "2026-07-12T02:10:00Z"})

    def test_timestamp_midnight_and_disagreement(self):
        self.assertEqual(combined_local("12 July 2026", "12:05 AM"), "2026-07-11T14:05:00Z")
        report = Snapshot("falls", "resort_report", "report", "https://example.test/report", CAPTURED, "active").payload()
        runs = Snapshot("falls", "mountainops_runs", "runs", "https://example.test/runs", CAPTURED, "none_flagged").payload()
        self.assertEqual(disagreements([report, runs])[0]["runFeedStatus"], "none_flagged")

    def test_one_source_failure_does_not_stop_next_source(self):
        class FailedSession:
            def get(self, *_args, **_kwargs): raise RuntimeError("fixture network down")
        class GoodResponse:
            ok = True; text = '{"ok":true}'; status_code = 200; headers = {"content-type": "application/json"}
            def json(self): return {"ok": True}
        class GoodSession:
            def get(self, *_args, **_kwargs): return GoodResponse()
        parser = lambda _raw, captured: Snapshot("falls", "resort_report", "good", "https://example.test/good", captured, "active")
        bad = SourceSpec("bad", "falls", "resort_report", "https://example.test/bad", parser, "json")
        good = SourceSpec("good", "falls", "resort_report", "https://example.test/good", parser, "json")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); con = connect(root / "ops.db")
            _, failed = fetch_source(bad, root / "raw", con, FailedSession())
            ok, good_failed = fetch_source(good, root / "raw", con, GoodSession())
            self.assertTrue(failed); self.assertFalse(good_failed); self.assertEqual(ok.snowmakingStatus, "active")


if __name__ == "__main__":
    unittest.main()
