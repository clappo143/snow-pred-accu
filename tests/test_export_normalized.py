import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import export_normalized
import store
from export_normalized import build_official_report_export, weighted_median


class ExportTests(unittest.TestCase):
    def test_weighted_median_even_split_matches_dashboard(self):
        self.assertEqual(weighted_median([(0, 50), (10, 50)]), 5)

    def test_weighted_median_resists_low_weight_outlier(self):
        self.assertEqual(weighted_median([(4, 50), (5, 50), (40, 1)]), 5)


class OfficialReportExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = store.DB_PATH
        store.DB_PATH = Path(self.tmp.name) / "snow.db"
        self.con = store.connect()

    def tearDown(self):
        self.con.close()
        store.DB_PATH = self.original
        self.tmp.cleanup()

    def test_strict_export_preserves_layers_and_manual_protection(self):
        day = dt.date(2026, 7, 12)
        store.save_actual(
            self.con, "buller", day, 0, "snowatch",
            source_url="https://www.snowatch.com.au/",
            raw={"snow": 0}, collected_at="2026-07-12T06:00:00+00:00",
        )
        store.save_actual(
            self.con, "buller", day, 14, "official",
            reported_at="2026-07-12T07:15:00+10:00",
            report_time_kind="documented_measurement",
            source_url="https://www.mtbuller.com.au/winter/snow-weather/snow-report",
            natural_depth=26, raw={"snow": 14},
            collected_at="2026-07-12T07:16:00+00:00",
        )
        store.save_actual(
            self.con, "buller", day, 13, "manual", notes="Verified correction.",
            collected_at="2026-07-12T07:17:00+00:00",
        )
        payload = build_official_report_export(self.con, "2026-07-12T08:00:00+00:00")
        self.assertEqual(payload["schemaVersion"], "alpine.official-report-export.v1")
        report = next(item for item in payload["reports"] if item["canonicalResortId"] == "buller")
        self.assertEqual(report["effective"]["values"]["snow24hCm"], 13)
        self.assertEqual(report["effective"]["values"]["snow24hStatus"], "observed_value")
        self.assertEqual(report["effective"]["reportTimeKind"], "unknown")
        manual = next(layer for layer in report["layers"] if layer["sourceName"] == "manual")
        self.assertTrue(manual["manualProtection"]["protected"])
        self.assertIn("snow24hCm", manual["manualProtection"]["fields"])
        snowatch = next(layer for layer in report["layers"] if layer["sourceName"] == "snowatch")
        self.assertEqual(snowatch["values"]["snow24hStatus"], "observed_zero")
        self.assertEqual(snowatch["sourceKind"], "aggregator")
        selected = report["effective"]["selectedLayers"]
        self.assertIn(selected["snow24hCm"], {layer["layerId"] for layer in report["layers"]})
        self.assertEqual(json.loads(json.dumps(payload))["producer"], export_normalized.OFFICIAL_REPORT_PRODUCER)

    def test_legacy_effective_row_is_explicitly_stale_projection(self):
        self.con.execute(
            "INSERT INTO actuals (resort,date,snow_cm,source) VALUES (?,?,?,?)",
            ("perisher", "2026-07-10", 4, "official"),
        )
        self.con.commit()
        payload = build_official_report_export(self.con, "2026-07-12T08:00:00+00:00")
        report = next(item for item in payload["reports"] if item["canonicalResortId"] == "perisher")
        projection = report["layers"][0]
        self.assertEqual(projection["collectionStatus"], "stale")
        self.assertEqual(projection["freshness"]["status"], "stale")
        self.assertTrue(projection["rawPayload"]["effectiveRecord"])


if __name__ == "__main__":
    unittest.main()
