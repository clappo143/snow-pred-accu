import copy
import collections
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from operations.export_v2 import export
from operations.normalize_v2 import normalize_envelope, stable_id
from operations.probe_v2 import build_comparison_report
from operations.registry import load_registries
from operations.storage_v2 import ImmutableCollisionError, TABLES, connect_temporary, persist

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data/operations/raw/2026-07-12"
RICH_EVIDENCE = ROOT / "tests/fixtures/operations/v2/vail_rich_compact_comparison_2026-07-13.json"


class OperationsV2Test(unittest.TestCase):
    def connect(self, path):
        con = connect_temporary(path)
        self.addCleanup(con.close)
        return con

    def envelope(self, source):
        path = next((ARCHIVE / source).glob("*.json"))
        item = json.loads(path.read_text())
        item["_path"] = str(path)
        return item

    def json_envelope(self, source, payload, captured="2026-07-13T00:00:00Z"):
        body = json.dumps(payload, separators=(",", ":"))
        return {"body": body, "capturedAt": captured, "responseAt": captured,
                "contentType": "application/json", "httpStatus": 200,
                "payloadHash": __import__("hashlib").sha256(body.encode()).hexdigest(),
                "rawPayloadRef": f"fixtures/{source}/{captured}.json"}

    def xml_payload(self, source):
        from operations.normalize_v2 import decode_body
        envelope = self.envelope(source)
        return decode_body(envelope["body"], envelope["contentType"])

    def test_registry_parser_parity_and_archived_sources(self):
        _, sources, _ = load_registries()
        scoped = [s for s in sources["sources"] if s["sourceId"].startswith(("falls_", "hotham_", "perisher_"))]
        self.assertEqual(12, len(scoped))
        archived = {path.parent.name for path in ARCHIVE.glob("*/*.json")}
        for source in scoped:
            if not source["sourceId"].endswith("_rich"):
                self.assertIn(source["sourceId"], archived)
                normalized = normalize_envelope(self.envelope(source["sourceId"]), source["sourceId"])
                classified = set(normalized.diagnostics["knownNormalizedFieldsEncountered"]) | set(normalized.diagnostics["knownNormalizedFieldsAbsent"])
                expected = {field["path"] for field in source["fieldCoverage"] if field["disposition"] == "normalized"}
                self.assertEqual(expected, classified, source["sourceId"])
            self.assertTrue(all("parserRuleId" in field for field in source["fieldCoverage"]))

    def test_capture_identity_exact_replay_and_unchanged_payload_new_poll(self):
        first_envelope = self.envelope("hotham_mountainops_runs")
        first = normalize_envelope(first_envelope, "hotham_mountainops_runs")
        second_envelope = copy.deepcopy(first_envelope)
        second_envelope["capturedAt"] = second_envelope["responseAt"] = "2026-07-14T00:00:00Z"
        second = normalize_envelope(second_envelope, "hotham_mountainops_runs")
        self.assertNotEqual(first.capture["captureId"], second.capture["captureId"])
        self.assertNotEqual(first.records["assetStatusObservations"][0]["observationId"], second.records["assetStatusObservations"][0]["observationId"])
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite")
            persist(con, first); persist(con, first); persist(con, second)
            self.assertEqual(2, con.execute("SELECT count(*) FROM operations_v2_captures").fetchone()[0])
            self.assertEqual(178, con.execute("SELECT count(*) FROM operations_v2_asset_status_observations").fetchone()[0])

    def test_same_id_different_content_capture_and_observation_rejected(self):
        normalized = normalize_envelope(self.envelope("hotham_mountainops_runs"), "hotham_mountainops_runs")
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite"); persist(con, normalized)
            changed = copy.deepcopy(normalized); changed.capture["contentType"] = "application/x-conflict"
            with self.assertRaisesRegex(ImmutableCollisionError, "capture"):
                persist(con, changed)
            changed = copy.deepcopy(normalized); original = changed.records["assetStatusObservations"][0]["operationalStatus"]; changed.records["assetStatusObservations"][0]["operationalStatus"] = "closed" if original != "closed" else "open"
            with self.assertRaisesRegex(ImmutableCollisionError, "observation"):
                persist(con, changed)

    def test_collisions_for_snapshots_assets_raw_descriptors_and_diagnostics(self):
        normalized = normalize_envelope(self.envelope("hotham_mountainops_runs"), "hotham_mountainops_runs")
        cases = [
            ("operations_v2_registry_snapshots", "registry_json"),
            ("operations_v2_source_inventory_snapshots", "inventory_json"),
            ("operations_v2_metric_catalogue_snapshots", "catalogue_json"),
            ("operations_v2_assets", "registry_json"),
            ("operations_v2_raw_descriptors", "descriptor_json"),
            ("operations_v2_capture_diagnostics", "diagnostics_json"),
        ]
        for table, column in cases:
            with self.subTest(table=table), tempfile.TemporaryDirectory() as tmp:
                con = self.connect(Path(tmp) / "v2.sqlite"); persist(con, normalized)
                con.execute(f"UPDATE {table} SET {column}='{{}}' WHERE rowid=(SELECT min(rowid) FROM {table})"); con.commit()
                with self.assertRaises(ImmutableCollisionError): persist(con, normalized)

    def test_adversarial_transaction_rolls_back_everything(self):
        normalized = normalize_envelope(self.envelope("hotham_mountainops_runs"), "hotham_mountainops_runs")
        bad = copy.deepcopy(normalized)
        collision = copy.deepcopy(bad.records["assetStatusObservations"][0])
        collision["operationalStatus"] = "open" if collision["operationalStatus"] != "open" else "closed"
        bad.records["assetStatusObservations"].append(collision)
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite")
            with self.assertRaises(ImmutableCollisionError): persist(con, bad)
            for table in ("operations_v2_captures", "operations_v2_registry_snapshots", "operations_v2_raw_descriptors", "operations_v2_asset_status_observations"):
                self.assertEqual(0, con.execute(f"SELECT count(*) FROM {table}").fetchone()[0], table)

    def test_timestamp_and_operational_date_rules(self):
        falls = normalize_envelope(self.json_envelope("falls_official_report", {"LastUpdate": "2026-07-13 13:20 UTC"}, "2026-07-13T13:30:00Z"), "falls_official_report")
        self.assertEqual("2026-07-13T13:20:00Z", falls.capture["sourceReportedAt"])
        self.assertEqual(10, falls.capture["freshnessMinutes"])
        hotham_payload = self.xml_payload("hotham_official_report"); hotham_payload["_LastUpdated"] = "2026-07-13T09:29:24"
        hotham = normalize_envelope(self.json_envelope("hotham_official_report", hotham_payload, "2026-07-13T00:00:00Z"), "hotham_official_report")
        self.assertEqual("2026-07-12T23:29:24Z", hotham.capture["sourceReportedAt"])
        self.assertEqual("2026-07-13", hotham.capture["operationalDate"])
        compact = [{"Id": 1, "Name": "Boardwalk Carpet", "Status": "Closed", "StatusId": 2, "Location": "Falls Creek", "OpenTime": "8:30AM", "CloseTime": "4:30PM", "TimeStamp": "13-07-2026  4:31PM"}]
        row = normalize_envelope(self.json_envelope("falls_mountainops_lifts", compact, "2026-07-13T07:00:00Z"), "falls_mountainops_lifts")
        self.assertEqual("2026-07-13T06:31:00Z", row.capture["sourceReportedAt"])
        perisher = normalize_envelope(self.json_envelope("perisher_official_report", {"date": "13/07/2026"}), "perisher_official_report")
        self.assertIsNone(perisher.capture["sourceReportedAt"]); self.assertEqual("2026-07-13", perisher.capture["operationalDate"])

    def test_falls_constant_list_semantics_duplicates_overlap_and_aggregates(self):
        payload = {"LastUpdate": "2026-07-13 00:00 UTC", "liftsOpen": "Alpha, Alpha", "liftsClosed": "Beta, Alpha", "liftsStandby": "Gamma",
                   "ActivitiesOpen": "Tubing", "ActivitiesClosed": "Sightseeing", "GroomedVillageBowlList": "Run One, Run One", "SnowmakingVillageBowlList": "Run Two"}
        normalized = normalize_envelope(self.json_envelope("falls_official_report", payload), "falls_official_report")
        statuses = [(row["upstreamName"], row["operationalStatus"]) for row in normalized.records["assetStatusObservations"] if isinstance(row["rawStatus"], dict) and "list" in row["rawStatus"] and row["upstreamName"] in {"Alpha", "Beta", "Gamma"}]
        self.assertIn(("Alpha", "open"), statuses); self.assertIn(("Beta", "closed"), statuses); self.assertIn(("Gamma", "standby"), statuses)
        groomed = [row for row in normalized.records["assetStatusObservations"] if isinstance(row["rawStatus"], dict) and row["rawStatus"].get("list") == "Run One, Run One"]
        self.assertEqual([True, True], [row["groomed"] for row in groomed])
        snow = [row for row in normalized.records["snowmakingObservations"] if row["sourceField"] == "$.SnowmakingVillageBowlList"]
        self.assertEqual("active", snow[0]["normalizedState"]); self.assertEqual("report_summary", snow[0]["observationRole"])
        falls_source = next(item for item in load_registries()[1]["sources"] if item["sourceId"] == "falls_official_report")
        snow_spec = next(item for item in falls_source["fieldCoverage"] if item["path"] == "$.SnowmakingVillageBowlList")
        self.assertEqual("run", snow_spec["listExpansionSpec"]["assetClass"])
        lifts = {row["status"]: row for row in normalized.records["aggregateObservations"] if row["sourceField"].startswith("$.lifts")}
        self.assertEqual((1, 3), (lifts["open"]["numerator"], lifts["open"]["denominator"]))
        self.assertTrue(normalized.diagnostics["duplicateListNames"]); self.assertTrue(normalized.diagnostics["crossStatusListOverlaps"])

    def test_hotham_actual_wind_normalizer(self):
        for raw, expected in (("Strong 61-80 km/h", [61.0, 80.0]), ("45 km/h", [45.0, 45.0])):
            payload = {"_LastUpdated": "2026-07-13T09:29:24", "Wind": raw}
            normalized = normalize_envelope(self.json_envelope("hotham_official_report", payload), "hotham_official_report")
            rows = [row for row in normalized.records["metricObservations"] if row["sourceField"] == "$.Wind"]
            self.assertEqual(expected, [row["value"] for row in rows]); self.assertEqual(2, len({row["observationId"] for row in rows}))
        blank = normalize_envelope(self.json_envelope("hotham_official_report", {"Wind": ""}), "hotham_official_report")
        self.assertEqual(["blank", "blank"], [row["valueStatus"] for row in blank.records["metricObservations"]])
        malformed = normalize_envelope(self.json_envelope("hotham_official_report", {"Wind": "violent-ish"}), "hotham_official_report")
        self.assertEqual(["unknown", "unknown"], [row["valueStatus"] for row in malformed.records["metricObservations"]]); self.assertTrue(malformed.diagnostics["malformedValues"])

    def test_metric_value_states_and_raw_json_types(self):
        payload = {"Patrol": {"PatrolFreshSnow": None, "PatrolNaturalSnowDepth": "", "PatrolVillageBowl": 0, "PatrolSunValley": "not measured"}}
        normalized = normalize_envelope(self.json_envelope("falls_official_report", payload), "falls_official_report")
        rows = {row["sourceField"]: row for row in normalized.records["metricObservations"]}
        self.assertEqual((None, "unavailable", None), (rows["$.Patrol.PatrolFreshSnow"]["value"], rows["$.Patrol.PatrolFreshSnow"]["valueStatus"], rows["$.Patrol.PatrolFreshSnow"]["rawValue"]))
        self.assertEqual("blank", rows["$.Patrol.PatrolNaturalSnowDepth"]["valueStatus"])
        self.assertEqual((0, "explicit_zero", 0), (rows["$.Patrol.PatrolVillageBowl"]["value"], rows["$.Patrol.PatrolVillageBowl"]["valueStatus"], rows["$.Patrol.PatrolVillageBowl"]["rawValue"]))
        self.assertEqual("unknown", rows["$.Patrol.PatrolSunValley"]["valueStatus"])
        self.assertNotIn("$.Patrol.PatrolNorthSide", rows); self.assertIn("$.Patrol.PatrolNorthSide", normalized.diagnostics["knownNormalizedFieldsAbsent"])

    def test_snowmaking_distinctions_and_links(self):
        report = normalize_envelope(self.envelope("hotham_official_report"), "hotham_official_report")
        self.assertTrue(any(row["signalType"] == "plant_state" and row["normalizedState"] == "standby" for row in report.records["snowmakingObservations"]))
        count = next(row for row in report.records["snowmakingObservations"] if row["signalType"] == "runs_snowmaking_count")
        self.assertEqual("unverified", count["interpretationConfidence"])
        feed = normalize_envelope(self.envelope("hotham_mountainops_runs"), "hotham_mountainops_runs")
        individual = [row for row in feed.records["snowmakingObservations"] if row["scope"] == "asset"]
        self.assertTrue(all(row["normalizedState"] == "unknown" and row["interpretationConfidence"] == "unverified" for row in individual))
        summary = next(row for row in feed.records["snowmakingObservations"] if row["scope"] == "resort")
        self.assertEqual(("none_flagged", {"affirmativeFlags": 0, "rowsExamined": 89}), (summary["normalizedState"], summary["rawValue"]))
        self.assertTrue(all(row["snowmakingObservationIds"] for row in feed.records["assetStatusObservations"]))

    def test_rich_binary_schedule_contract_clock_status_and_queue(self):
        row = {"id": 1, "name": "Boardwalk Carpet", "order": 1, "statusCode": 1, "typeCode": 2, "type": "Carpet",
               "publicAreaCode": 100, "publicArea": "Falls Creek", "isScheduled": 1,
               "openTime": "1970-01-01T09:00:00.000Z", "closeTime": "1970-01-01T16:30:00.000Z", "queueMinsEstimate": 0}
        normalized = normalize_envelope(self.json_envelope("falls_mountainops_lifts_rich", [row]), "falls_mountainops_lifts_rich")
        asset = normalized.records["assetStatusObservations"][0]
        self.assertEqual(("open", "09:00:00", "16:30:00", True, None), (asset["operationalStatus"], asset["openTime"], asset["closeTime"], asset["scheduled"], asset["queueMinutes"]))
        self.assertEqual(1, asset["rawStatus"]["isScheduled"])
        self.assertIn("$[*].queueMinsEstimate", normalized.diagnostics["rawOnlyFieldsEncountered"])
        row["statusCode"] = 6
        unknown = normalize_envelope(self.json_envelope("falls_mountainops_lifts_rich", [row], "2026-07-13T00:01:00Z"), "falls_mountainops_lifts_rich")
        self.assertEqual(("unknown", True), (unknown.records["assetStatusObservations"][0]["operationalStatus"], unknown.records["assetStatusObservations"][0]["scheduled"])); self.assertTrue(unknown.diagnostics["malformedValues"])
        expected_paths = {f"$[*].{key}" for key in ("id", "name", "order", "statusCode", "typeCode", "type", "publicAreaCode", "publicArea", "isScheduled", "openTime", "closeTime", "queueMinsEstimate")}
        sources = load_registries()[1]["sources"]
        for source in (item for item in sources if item["sourceId"].endswith("_mountainops_lifts_rich")):
            self.assertEqual(expected_paths, {field["path"] for field in source["fieldCoverage"]})
            schedule = next(field for field in source["fieldCoverage"] if field["path"] == "$[*].isScheduled")
            self.assertEqual(("integer", "vail_rich_binary_schedule_flag_v1"), (schedule["sourceValueType"], schedule["parserRuleId"]))

    def test_rich_schedule_zero_one_invalid_string_and_missing(self):
        base = {"id": 1, "name": "Boardwalk Carpet", "statusCode": 6, "publicArea": "Falls Creek"}
        cases = [(0, False, False), (1, True, False), (2, None, True), ("1", None, True), (True, None, True)]
        for index, (value, expected, malformed) in enumerate(cases):
            with self.subTest(value=value):
                row = {**base, "isScheduled": value}
                normalized = normalize_envelope(self.json_envelope("falls_mountainops_lifts_rich", [row], f"2026-07-13T00:0{index}:00Z"), "falls_mountainops_lifts_rich")
                asset = normalized.records["assetStatusObservations"][0]
                self.assertEqual(expected, asset["scheduled"])
                self.assertEqual("unknown", asset["operationalStatus"])
                schedule_errors = [item for item in normalized.diagnostics["malformedValues"] if item["path"] == "$[*].isScheduled"]
                self.assertEqual(malformed, bool(schedule_errors))
                self.assertEqual(value, asset["rawStatus"]["isScheduled"])
        missing = normalize_envelope(self.json_envelope("falls_mountainops_lifts_rich", [base], "2026-07-13T00:05:00Z"), "falls_mountainops_lifts_rich")
        self.assertIsNone(missing.records["assetStatusObservations"][0]["scheduled"])
        self.assertFalse(any(item["path"] == "$[*].isScheduled" for item in missing.diagnostics["malformedValues"]))

    def test_checked_rich_evidence_is_row_derived_and_schedules_normalize(self):
        evidence = json.loads(RICH_EVIDENCE.read_text())
        self.assertTrue(all(report["identity"]["allIdentityMatches"] for report in evidence["reports"]))
        row_pairs = collections.Counter(
            (row["compact"]["statusId"], row["compact"]["status"], row["rich"]["statusCode"])
            for report in evidence["reports"] for row in report["rows"]
        )
        reported_pairs = collections.Counter({
            (row["compactStatusId"], row["compactStatus"], row["richStatusCode"]): row["count"]
            for row in evidence["observedStatusPairs"]
        })
        self.assertEqual(row_pairs, reported_pairs)
        self.assertNotIn(1, {row["richStatusCode"] for row in evidence["observedStatusPairs"]})
        dispositions = {row["statusCode"]: row for row in evidence["reviewedStatusDisposition"]}
        self.assertEqual("independently_reviewed_direct_observation", dispositions[1]["classification"])
        self.assertEqual("unresolved", dispositions[6]["classification"])
        for report in evidence["reports"]:
            source = report["richSourceId"]
            rows = [row["rich"] for row in report["rows"]]
            normalized = normalize_envelope(self.json_envelope(source, rows), source)
            self.assertEqual(len(rows), sum(row["scheduled"] is not None for row in normalized.records["assetStatusObservations"]))

    def test_comparison_report_derives_pairs_types_and_identity(self):
        responses = {
            "falls_mountainops_lifts": {"retrievedAt": "2026-07-13T00:00:00Z", "rows": [{"Id": 1, "Name": "A", "Location": "Falls", "Status": "Closed", "StatusId": 2}]},
            "falls_mountainops_lifts_rich": {"retrievedAt": "2026-07-13T00:00:01Z", "rows": [{"id": 1, "name": "A", "publicArea": "Falls", "statusCode": 6, "isScheduled": 1, "openTime": "1970-01-01T09:00:00Z", "closeTime": None, "queueMinsEstimate": 0}]},
        }
        report = build_comparison_report(responses, generated_at="2026-07-13T01:00:00Z")
        falls = report["reports"][0]
        self.assertEqual("2026-07-13T01:00:00Z", report["generatedAt"])
        self.assertEqual([{"compactStatusId": 2, "compactStatus": "Closed", "richStatusCode": 6, "count": 1}], falls["observedStatusPairs"])
        self.assertEqual({"integer:1": 1}, falls["isScheduledValueTypeCounts"])
        self.assertEqual(0, falls["identity"]["mismatchCount"])

    def test_report_aggregate_denominator_rules(self):
        hotham = normalize_envelope(self.json_envelope("hotham_official_report", {"LiftsOpen": "5", "LiftsClosed": "3", "LiftsStandby": "2"}), "hotham_official_report")
        self.assertEqual({("open", 5, 10), ("closed", 3, 10), ("standby", 2, 10)}, {(row["status"], row["numerator"], row["denominator"]) for row in hotham.records["aggregateObservations"]})
        perisher = normalize_envelope(self.json_envelope("perisher_official_report", {"lifts_number": "20", "groomed_runs": "50", "date": "13/07/2026"}), "perisher_official_report")
        self.assertTrue(all(row["denominator"] is None and row["denominatorScope"] == "unknown" for row in perisher.records["aggregateObservations"]))

    def test_complete_archived_row_counts_and_aggregate_denominators(self):
        expected = {"falls_mountainops_runs": 80, "hotham_mountainops_runs": 89, "perisher_mountainops_runs": 121,
                    "falls_mountainops_lifts": 15, "hotham_mountainops_lifts": 14, "perisher_mountainops_lifts": 45}
        for source, count in expected.items():
            normalized = normalize_envelope(self.envelope(source), source)
            self.assertEqual(count, len(normalized.records["assetStatusObservations"]), source)
            self.assertTrue(all(row["denominator"] == count and row["denominatorScope"] == "all_listed" for row in normalized.records["aggregateObservations"]), source)

    def test_raw_descriptor_provenance_is_source_scoped(self):
        payload = [{"Id": 1, "Name": "X", "Status": "Closed", "StatusId": 2, "Location": "X", "OpenTime": "", "CloseTime": "", "TimeStamp": "13-07-2026 12:00AM"}]
        first = self.json_envelope("falls_mountainops_lifts", payload); second = copy.deepcopy(first); second["rawPayloadRef"] = "fixtures/hotham/same.json"
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite")
            persist(con, normalize_envelope(first, "falls_mountainops_lifts")); persist(con, normalize_envelope(second, "hotham_mountainops_lifts"))
            rows = [json.loads(row[0]) for row in con.execute("SELECT descriptor_json FROM operations_v2_raw_descriptors ORDER BY source_id")]
            self.assertEqual({"falls_mountainops_lifts", "hotham_mountainops_lifts"}, {row["sourceId"] for row in rows})
            self.assertEqual(1, len({row["payloadHash"] for row in rows})); self.assertEqual(2, len({row["sourceUrl"] for row in rows}))

    def test_capture_first_bounded_export_is_complete_and_deterministic(self):
        envelope = self.envelope("hotham_mountainops_runs")
        first = normalize_envelope(envelope, "hotham_mountainops_runs")
        later_envelope = copy.deepcopy(envelope); later_envelope["capturedAt"] = later_envelope["responseAt"] = "2026-07-14T00:00:00Z"
        later = normalize_envelope(later_envelope, "hotham_mountainops_runs")
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite"); persist(con, first); persist(con, later)
            payload = export(con, Path(tmp) / "out.json", window_start=later.capture["retrievedAt"], window_end=later.capture["retrievedAt"], clock=lambda: "2026-07-15T00:00:00Z")
            self.assertEqual([later.capture["captureId"]], [row["captureId"] for row in payload["captures"]])
            self.assertEqual(89, len(payload["assetStatusObservations"])); self.assertTrue(all(row["captureId"] == later.capture["captureId"] for name in TABLES for row in payload[name]))
            self.assertEqual("2026-07-15T00:00:00Z", payload["generatedAt"]); self.assertEqual(later.capture["retrievedAt"], payload["windowStart"])

    def test_export_rejects_mixed_catalogue_revisions(self):
        envelope = self.envelope("hotham_mountainops_runs"); first = normalize_envelope(envelope, "hotham_mountainops_runs")
        second_envelope = copy.deepcopy(envelope); second_envelope["capturedAt"] = second_envelope["responseAt"] = "2026-07-14T00:00:00Z"; second = normalize_envelope(second_envelope, "hotham_mountainops_runs")
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite"); persist(con, first); persist(con, second)
            con.execute("INSERT INTO operations_v2_metric_catalogue_snapshots VALUES('other','v2','2026-07-14T00:00:00Z','{}')")
            con.execute("UPDATE operations_v2_captures SET metric_catalogue_revision='other' WHERE capture_id=?", (second.capture["captureId"],)); con.commit()
            with self.assertRaisesRegex(ValueError, "multiple catalogue revisions"):
                export(con, Path(tmp) / "out.json", window_start=first.capture["retrievedAt"], window_end=second.capture["retrievedAt"])

    def test_diagnostics_persist_and_export_for_selected_window(self):
        normalized = normalize_envelope(self.json_envelope("hotham_official_report", {"Wind": "malformed gale"}), "hotham_official_report")
        with tempfile.TemporaryDirectory() as tmp:
            con = self.connect(Path(tmp) / "v2.sqlite"); persist(con, normalized)
            stored = json.loads(con.execute("SELECT diagnostics_json FROM operations_v2_capture_diagnostics").fetchone()[0])
            self.assertTrue(stored["malformedValues"])
            payload = export(con, Path(tmp) / "out.json", window_start=normalized.capture["retrievedAt"], window_end=normalized.capture["retrievedAt"], clock=lambda: "2026-07-15T00:00:00Z")
            self.assertTrue(any("malformedValues" in warning for warning in payload["diagnostics"]["warnings"]))

    def test_failed_live_capture_retains_warning_and_diagnostics(self):
        envelope = {"body": "", "capturedAt": "2026-07-13T00:00:00Z", "responseAt": "2026-07-13T00:00:00Z", "contentType": "text/plain", "httpStatus": 599, "warning": "timed out"}
        normalized = normalize_envelope(envelope, "falls_mountainops_lifts_rich")
        self.assertEqual("failed", normalized.capture["retrievalStatus"]); self.assertIn("timed out", normalized.capture["warnings"][0]); self.assertIn(normalized.capture["warnings"][0], normalized.diagnostics["warnings"])

    def test_deterministic_identity(self):
        self.assertEqual(stable_id("metric", a=1), stable_id("metric", a=1))
        self.assertNotEqual(stable_id("metric", a=1), stable_id("metric", a=2))


if __name__ == "__main__":
    unittest.main()
