from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from operations.export_v2 import export
from operations.normalize_v2 import normalize_envelope
from operations.registry import ROOT, content_hash, load_registries
from operations.resolve_v2 import canonical_bytes, load_policy, resolve_export, validate_policy
from operations.resolved_schema import validate_resolved
from operations.storage_v2 import connect_temporary, persist

POLICY_PATH = ROOT / "config/operations_resolution_policy_v1.json"
EXAMPLE = ROOT / "tests/fixtures/operations/v2/example_operations_export_v2.json"
ARCHIVE = ROOT / "data/operations/raw/2026-07-12"
AS_OF = "2026-07-13T06:30:00Z"
GENERATED = "2026-07-13T06:31:00Z"


class ResolverFixture:
    def __init__(self):
        self.assets, self.sources, self.metrics = load_registries()
        self.sources_by_id = {row["sourceId"]: row for row in self.sources["sources"]}
        self.asset = next(row for row in self.assets["assets"] if row["assetId"] == "falls.lift.falls_mountainops_lifts.1")
        self.captures = []
        self.raw = []
        self.rows = {name: [] for name in ("metricObservations", "snowmakingObservations", "assetStatusObservations", "aggregateObservations", "narratives")}

    def capture(self, source_id, retrieved, capture_id=None, *, status="ok", http_status=200, warnings=None):
        source = self.sources_by_id[source_id]
        capture_id = capture_id or f"cap-{source_id}-{retrieved}"
        digest = hashlib.sha256(capture_id.encode()).hexdigest()
        self.captures.append({"captureId": capture_id, "resortId": source["resortId"], "sourceId": source_id,
                              "sourceLayer": source["layer"], "sourceRole": source["sourceRole"], "retrievedAt": retrieved,
                              "responseAt": retrieved, "sourceReportedAt": retrieved, "operationalDate": retrieved[:10],
                              "httpStatus": http_status, "contentType": "application/json", "payloadHash": digest,
                              "rawPayloadRef": f"fixtures/raw/{digest}.json", "parserVersion": "resolver-fixture-v1", "retrievalStatus": "ok",
                              "freshnessMinutes": 0, "warnings": warnings or [], "assetRegistryRevision": self.assets["contentHash"],
                              "sourceInventoryRevision": self.sources["contentHash"], "metricCatalogueRevision": self.metrics["contentHash"]})
        self.captures[-1]["retrievalStatus"] = status
        if status == "failed":
            self.captures[-1]["payloadHash"] = None
            self.captures[-1]["rawPayloadRef"] = None
        self.raw.append({"payloadHash": digest, "rawPayloadRef": f"fixtures/raw/{digest}.json", "sourceId": source_id,
                         "sourceUrl": source["url"], "contentType": "application/json", "httpStatus": 200,
                         "responseAt": retrieved, "firstCapturedAt": retrieved, "parserVersion": "resolver-fixture-v1"})
        return capture_id

    def asset_status(self, oid, capture_id, role="live_actual", status="unknown", observed=None, **values):
        capture = next(row for row in self.captures if row["captureId"] == capture_id)
        row = {"observationId": oid, "captureId": capture_id, "resortId": "falls", "assetId": self.asset["assetId"],
               "upstreamAssetId": "1", "upstreamName": "Boardwalk Carpet", "assetClass": "lift", "operationalStatus": status,
               "statusReason": [], "rawStatus": status, "observationRole": role, "scheduled": None, "expectedToOpen": None,
               "openTime": None, "closeTime": None, "queueMinutes": None, "condition": None, "groomed": None,
               "observedAt": observed or capture["retrievedAt"], "effectiveFrom": None, "effectiveTo": None,
               "interpretationConfidence": "high", "warnings": [], "snowmakingObservationIds": []}
        row.update(values)
        self.rows["assetStatusObservations"].append(row)
        return row

    def metric(self, oid, capture_id, value, *, role="measurement", metric="location_depth_cm", location="Village", status="observed", unit="cm", observed=None):
        capture = next(row for row in self.captures if row["captureId"] == capture_id)
        row = {"observationId": oid, "captureId": capture_id, "resortId": capture["resortId"], "metricKey": metric,
               "value": value, "unit": unit, "valueStatus": status, "observationRole": role, "scope": "location",
               "subjectId": None, "locationLabel": location, "observedAt": observed or capture["retrievedAt"],
               "effectiveFrom": None, "effectiveTo": None, "sourceField": "fixture", "rawValue": value,
               "semanticQualifier": "fixture", "interpretationConfidence": "high", "notes": []}
        self.rows["metricObservations"].append(row)
        return row

    def aggregate(self, oid, capture_id, numerator, denominator, scope="all_listed", role="live_actual", status="open"):
        capture = next(row for row in self.captures if row["captureId"] == capture_id)
        self.rows["aggregateObservations"].append({"observationId": oid, "captureId": capture_id, "resortId": capture["resortId"],
                                                   "assetClass": "lift", "area": None, "status": status, "numerator": numerator,
                                                   "denominator": denominator, "denominatorScope": scope, "observationRole": role,
                                                   "observedAt": capture["retrievedAt"], "sourceField": "fixture", "rawValue": str(numerator), "notes": []})

    def payload(self, conflicts=None):
        used_sources = {capture["sourceId"] for capture in self.captures}
        # Embedded asset mappings must retain every exact source they cite.
        used_sources.update(mapping["sourceId"] for mapping in self.asset["sourceMappings"])
        return {"schemaVersion": "alpine.operations-export.v2", "producer": "resolver test fixture", "generatedAt": GENERATED,
                "windowStart": min((row["retrievedAt"] for row in self.captures), default="2026-07-13T00:00:00Z"),
                "windowEnd": max((row["retrievedAt"] for row in self.captures), default="2026-07-13T00:00:00Z"),
                "identitySchemaVersion": "alpine.resort-identities.v1", "assetRegistrySchemaVersion": self.assets["schemaVersion"],
                "assetRegistryRevision": self.assets["contentHash"], "sourceInventoryRevision": self.sources["contentHash"],
                "metricCatalogueRevision": self.metrics["contentHash"], "assetRegistryCompleteness": "partial",
                "sourceInventoryCompleteness": "partial", "assets": [self.asset],
                "sourceInventory": [row for row in self.sources["sources"] if row["sourceId"] in used_sources],
                "captures": self.captures, "rawPayloads": self.raw, **self.rows, "conflicts": conflicts or [],
                "diagnostics": {"warnings": [], "unknownSourceFields": [], "unmappedAssetCount": 0}}


class OperationsResolverV2Test(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(POLICY_PATH)

    def resolved(self, fixture, as_of=AS_OF):
        return resolve_export(fixture.payload(), as_of, self.policy, generated_at=GENERATED)

    def asset_record(self, result, field):
        return next(row for row in result["resorts"][0]["assetFields"] if row["fieldFamily"] == field)

    def test_01_exact_replay_is_byte_identical_and_all_provenance_exists(self):
        f = ResolverFixture(); cap = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); f.asset_status("closed", cap, status="closed")
        evidence = f.payload()
        first = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)
        second = resolve_export(copy.deepcopy(evidence), AS_OF, copy.deepcopy(self.policy), generated_at=GENERATED)
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        evidence_ids = {row["observationId"] for name in f.rows for row in evidence[name]}
        refs = {lane["selection"]["selectedObservationId"] for resort in first["resorts"] for group in resort.values() if isinstance(group, list) for record in group for lane in record.get("lanes", [])}
        self.assertTrue(refs <= evidence_ids)

    def test_02_as_of_excludes_later_capture_and_same_source_is_transition(self):
        f = ResolverFixture(); early = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); late = f.capture("falls_mountainops_lifts", "2026-07-13T07:00:00Z")
        f.asset_status("early-closed", early, status="closed"); f.asset_status("later-open", late, status="open")
        early_result = self.resolved(f); self.assertEqual("early-closed", self.asset_record(early_result, "operationalStatus")["preferredSelection"]["selectedObservationId"])
        final = self.resolved(f, "2026-07-13T07:30:00Z"); self.assertEqual("later-open", self.asset_record(final, "operationalStatus")["preferredSelection"]["selectedObservationId"])
        self.assertEqual([], final["conflicts"])

    def test_03_plan_actual_comparison_not_conflict_and_actual_preferred(self):
        f = ResolverFixture(); plan = f.capture("falls_official_report", "2026-07-13T06:10:00Z"); actual = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z")
        f.asset_status("planned-open", plan, role="morning_plan", status="open"); f.asset_status("actual-closed", actual, status="closed")
        result = self.resolved(f); record = self.asset_record(result, "operationalStatus")
        self.assertEqual("actual-closed", record["preferredSelection"]["selectedObservationId"])
        self.assertTrue(result["planActualComparisons"][0]["different"]); self.assertEqual([], result["conflicts"])

    def test_04_fresh_actual_beats_report_and_stale_known_remains(self):
        f = ResolverFixture(); report = f.capture("falls_official_report", "2026-07-13T06:20:00Z"); live = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z")
        f.asset_status("report-open", report, role="report_summary", status="open"); f.asset_status("live-closed", live, status="closed")
        result = self.resolved(f); self.assertEqual("live-closed", self.asset_record(result, "operationalStatus")["preferredSelection"]["selectedObservationId"])
        stale = self.resolved(f, "2026-07-14T06:30:00Z"); self.assertEqual("stale", self.asset_record(stale, "operationalStatus")["preferredSelection"]["freshnessState"])

    def test_05_fresh_unknown_does_not_erase_stale_known(self):
        f = ResolverFixture(); known = f.capture("falls_mountainops_lifts", "2026-07-13T00:00:00Z"); unknown = f.capture("falls_mountainops_lifts_rich", "2026-07-13T06:20:00Z")
        f.asset_status("stale-closed", known, status="closed"); f.asset_status("fresh-code-6", unknown, status="unknown")
        evidence = f.payload(); result = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)
        selected = self.asset_record(result, "operationalStatus")["preferredSelection"]
        self.assertEqual("stale-closed", selected["selectedObservationId"]); self.assertIn("fresh unknown", selected["selectionReason"])
        tampered = copy.deepcopy(result); record = self.asset_record(tampered, "operationalStatus"); lane = record["lanes"][0]["selection"]
        unknown_capture = next(row for row in evidence["captures"] if row["captureId"] == unknown)
        lane.update(selectedObservationId="fresh-code-6", captureId=unknown, sourceId=unknown_capture["sourceId"], value="unknown", known=False,
                    alternateCandidateObservationIds=["stale-closed"], selectionReason="newer")
        self.assertTrue(any("selected unknown over eligible known" in error for error in validate_resolved(tampered, evidence, self.policy)))

    def test_06_compact_closed_rich_code6_scheduled_true_are_independent(self):
        f = ResolverFixture(); compact = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); rich = f.capture("falls_mountainops_lifts_rich", "2026-07-13T06:00:30Z")
        f.asset_status("compact-closed", compact, status="closed"); f.asset_status("rich-code-6", rich, status="unknown", scheduled=True)
        result = self.resolved(f)
        self.assertEqual("closed", self.asset_record(result, "operationalStatus")["preferredSelection"]["value"])
        self.assertIs(True, self.asset_record(result, "scheduled")["preferredSelection"]["value"])
        self.assertEqual([], result["conflicts"])
        queue = self.asset_record(result, "queueMinutes")["preferredSelection"]
        self.assertFalse(queue["known"]); self.assertIsNone(queue["value"])

    def test_07_known_live_disagreement_conflicts_equal_values_corroborate(self):
        for rich_status, expected in (("open", 1), ("closed", 0)):
            with self.subTest(rich_status=rich_status):
                f = ResolverFixture(); compact = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); rich = f.capture("falls_mountainops_lifts_rich", "2026-07-13T06:01:00Z")
                f.asset_status("compact-closed", compact, status="closed"); f.asset_status(f"rich-{rich_status}", rich, status=rich_status)
                result = self.resolved(f); conflicts = [row for row in result["conflicts"] if row["fieldFamily"] == "operationalStatus"]
                self.assertEqual(expected, len(conflicts))
                if expected: self.assertEqual({"compact-closed", "rich-open"}, set(conflicts[0]["observationIds"]))

    def test_08_hotham_snowmaking_signals_coexist(self):
        evidence = json.loads(EXAMPLE.read_text()); result = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)
        signals = result["resorts"][0]["snowmakingSignals"]
        self.assertEqual(4, len(signals)); self.assertEqual({"plant_state", "runs_snowmaking_count", "run_snowmaking_flag"}, {row["fieldFamily"] for row in signals})
        values = [lane["selection"]["value"] for row in signals for lane in row["lanes"]]
        self.assertTrue(any(value["state"] == "standby" for value in values)); self.assertTrue(any(value["numericValue"] == 5 for value in values))
        self.assertTrue(any(value["state"] == "none_flagged" for value in values)); self.assertEqual([], result["conflicts"])

    def test_09_metric_semantic_keys_locations_roles_and_time_series(self):
        f = ResolverFixture(); first = f.capture("falls_official_report", "2026-07-13T05:00:00Z"); second = f.capture("falls_official_report", "2026-07-13T06:00:00Z")
        f.metric("village-old", first, 80, location="Village"); f.metric("village-new", second, 85, location="Village")
        f.metric("summit", second, 140, location="Summit"); f.metric("expected", second, 90, location="Village", role="expected")
        result = self.resolved(f); metrics = result["resorts"][0]["metrics"]
        self.assertEqual(3, len(metrics)); self.assertEqual({"Village", "Summit"}, {row["locationLabel"] for row in metrics})
        village_reported = next(row for row in metrics if row["locationLabel"] == "Village" and row["lanes"][0]["lane"] == "reported")
        self.assertEqual("village-new", village_reported["preferredSelection"]["selectedObservationId"]); self.assertEqual([], result["conflicts"])

    def test_09b_unidentified_asset_metrics_do_not_collapse_or_false_conflict(self):
        f = ResolverFixture(); cap = f.capture("falls_official_report", "2026-07-13T06:00:00Z")
        left = f.metric("unidentified-a", cap, "Good", metric="cross_country_condition", location=None, unit="text")
        right = f.metric("unidentified-b", cap, "Poor", metric="cross_country_condition", location=None, unit="text")
        left["scope"] = right["scope"] = "asset"
        result = self.resolved(f); metrics = result["resorts"][0]["metrics"]
        self.assertEqual(2, len(metrics)); self.assertEqual([], result["conflicts"])
        self.assertTrue(all(row["subject"]["kind"] == "source_scoped_unmapped" for row in metrics))

    def test_10_aggregates_preserve_scopes_and_validate_fraction_invariants(self):
        f = ResolverFixture(); cap = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z")
        f.aggregate("unknown-denom", cap, 5, None, scope="unknown"); f.aggregate("all-listed", cap, 12, 10, scope="all_listed")
        result = self.resolved(f); aggregates = result["resorts"][0]["aggregates"]
        self.assertEqual(2, len(aggregates)); self.assertEqual({"unknown", "all_listed"}, {row["denominatorScope"] for row in aggregates})
        unknown = next(row for row in aggregates if row["denominatorScope"] == "unknown")
        self.assertIsNone(unknown["preferredSelection"]["value"]["fraction"])
        self.assertEqual("numerator_exceeds_denominator", result["diagnostics"]["aggregateIssues"][0]["type"])

    def test_11_unmapped_identity_is_source_scoped(self):
        f = ResolverFixture(); compact = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); report = f.capture("falls_official_report", "2026-07-13T06:00:00Z")
        left = f.asset_status("unmapped-live", compact, status="closed"); right = f.asset_status("unmapped-report", report, role="report_summary", status="closed")
        for row in (left, right): row.update(assetId=None, upstreamAssetId=None, upstreamName="Mystery Lift", warnings=["unmapped"])
        result = self.resolved(f); subjects = {row["subject"]["subjectId"] for row in result["resorts"][0]["assetFields"] if row["fieldFamily"] == "operationalStatus"}
        self.assertEqual(2, len(subjects)); self.assertTrue(all(subject.startswith("unmapped:falls:") for subject in subjects))

    def test_12_future_label_is_anomaly_not_future_knowledge(self):
        f = ResolverFixture(); cap = f.capture("falls_official_report", "2026-07-13T06:00:00Z")
        f.metric("future-labelled", cap, 85, observed="2026-07-14T06:00:00Z")
        selected = self.resolved(f)["resorts"][0]["metrics"][0]["preferredSelection"]
        self.assertEqual("future_timestamp_anomaly", selected["freshnessState"]); self.assertEqual(85, selected["value"])

    def test_13_tampered_policy_and_evidence_fingerprints_rejected(self):
        f = ResolverFixture(); cap = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); f.asset_status("closed", cap, status="closed")
        evidence = f.payload(); result = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)
        tampered = copy.deepcopy(result); tampered["evidenceFingerprint"] = "0" * 64
        self.assertTrue(any("fingerprint" in error for error in validate_resolved(tampered, evidence, self.policy)))
        bad_policy = copy.deepcopy(self.policy); bad_policy["conflictWindowMinutes"] += 1
        with self.assertRaisesRegex(ValueError, "policy revision"):
            resolve_export(evidence, AS_OF, bad_policy, generated_at=GENERATED)

    def test_14_empty_window_is_valid_and_input_conflicts_remain_visible(self):
        empty = json.loads(EXAMPLE.read_text())
        for name in ("captures", "rawPayloads", "metricObservations", "snowmakingObservations", "assetStatusObservations", "aggregateObservations", "narratives"):
            empty[name] = []
        empty["conflicts"] = []
        result = resolve_export(empty, AS_OF, self.policy, generated_at=GENERATED)
        self.assertEqual([], result["resorts"]); self.assertEqual(0, result["diagnostics"]["eligibleCaptureCount"])
        f = ResolverFixture(); a = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); b = f.capture("falls_mountainops_lifts_rich", "2026-07-13T06:01:00Z")
        f.asset_status("a", a, status="closed"); f.asset_status("b", b, status="open", observed="2026-07-13T06:00:00Z")
        conflict = {"conflictId": "phase2-conflict", "resortId": "falls", "fieldFamily": "operationalStatus", "subjectId": f.asset["assetId"],
                    "observationIds": ["a", "b"], "conflictType": "value", "detectedAt": "2026-07-13T06:01:00Z", "status": "open", "resolution": None, "notes": []}
        evidence = f.payload([conflict]); result = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)
        self.assertEqual([conflict], result["inputEvidenceConflicts"])

    def test_15_real_archived_replay_resolves(self):
        sources = ["falls_official_report", "falls_mountainops_lifts", "falls_mountainops_runs", "hotham_official_report", "hotham_mountainops_lifts", "hotham_mountainops_runs", "perisher_official_report", "perisher_mountainops_lifts", "perisher_mountainops_runs"]
        with tempfile.TemporaryDirectory() as tmp:
            con = connect_temporary(Path(tmp) / "operations-v2.sqlite")
            self.addCleanup(con.close)
            normalized = []
            for source in sources:
                path = sorted((ARCHIVE / source).glob("*.json"))[0]
                envelope = json.loads(path.read_text()); envelope["_path"] = str(path)
                item = normalize_envelope(envelope, source); persist(con, item); normalized.append(item)
            start = min(item.capture["retrievedAt"] for item in normalized); end = max(item.capture["retrievedAt"] for item in normalized)
            evidence = export(con, Path(tmp) / "export.json", window_start=start, window_end=end, clock=lambda: GENERATED)
            result = resolve_export(evidence, "2026-07-13T12:00:00Z", self.policy, generated_at=GENERATED)
            self.assertEqual(3, len(result["resorts"])); self.assertGreater(sum(len(row["assetFields"]) for row in result["resorts"]), 0)

    def test_16_every_derived_field_is_bound_by_deterministic_reconstruction(self):
        f = ResolverFixture()
        compact = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z")
        rich = f.capture("falls_mountainops_lifts_rich", "2026-07-13T06:01:00Z")
        plan = f.capture("falls_official_report", "2026-07-13T06:02:00Z")
        f.asset_status("compact-closed", compact, status="closed")
        f.asset_status("rich-open", rich, status="open")
        f.asset_status("planned-open", plan, role="morning_plan", status="open")
        evidence = f.payload()
        original = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)

        def preferred(payload):
            return self.asset_record(payload, "operationalStatus")["preferredSelection"]

        def lane(payload):
            return self.asset_record(payload, "operationalStatus")["lanes"][0]

        mutations = {
            "preferred value": lambda p: preferred(p).__setitem__("value", "tampered"),
            "lane value": lambda p: lane(p)["selection"].__setitem__("value", "tampered"),
            "known state": lambda p: lane(p)["selection"].__setitem__("known", False),
            "selected observation": lambda p: lane(p)["selection"].__setitem__("selectedObservationId", "rich-open"),
            "capture": lambda p: lane(p)["selection"].__setitem__("captureId", rich),
            "source": lambda p: lane(p)["selection"].__setitem__("sourceId", "falls_mountainops_lifts_rich"),
            "observation role": lambda p: lane(p)["selection"].__setitem__("observationRole", "measurement"),
            "lane": lambda p: lane(p).__setitem__("lane", "reported"),
            "observed time": lambda p: lane(p)["selection"].__setitem__("observedAt", "2026-07-13T05:59:00Z"),
            "capture time": lambda p: lane(p)["selection"].__setitem__("captureRetrievedAt", "2026-07-13T05:59:00Z"),
            "capture age": lambda p: lane(p)["selection"].__setitem__("captureAgeMinutes", 999),
            "observation age": lambda p: lane(p)["selection"].__setitem__("observationAgeMinutes", 999),
            "freshness threshold": lambda p: lane(p)["selection"].__setitem__("freshnessThresholdMinutes", 999),
            "freshness state": lambda p: lane(p)["selection"].__setitem__("freshnessState", "stale"),
            "resolution id": lambda p: lane(p)["selection"].__setitem__("resolutionId", "resolution-tampered"),
            "resolution key": lambda p: self.asset_record(p, "operationalStatus").__setitem__("resolutionKey", "key-tampered"),
            "selection reason": lambda p: lane(p)["selection"].__setitem__("selectionReason", "tampered"),
            "alternate omitted": lambda p: lane(p)["selection"].__setitem__("alternateCandidateObservationIds", []),
            "alternate membership": lambda p: lane(p)["selection"]["alternateCandidateObservationIds"].append("planned-open"),
            "conflict id": lambda p: p["conflicts"][0].__setitem__("conflictId", "conflict-tampered"),
            "conflict selected provenance": lambda p: p["conflicts"][0].__setitem__("selectedDisplayObservationId", "rich-open"),
            "comparison id": lambda p: p["planActualComparisons"][0].__setitem__("comparisonId", "comparison-tampered"),
            "comparison provenance": lambda p: p["planActualComparisons"][0]["actual"].__setitem__("sourceId", "falls_mountainops_lifts_rich"),
            "diagnostics": lambda p: p["diagnostics"].__setitem__("eligibleCaptureCount", 99),
            "catalogue metadata": lambda p: p.__setitem__("metricCatalogueRevision", "0" * 64),
            "evidence metadata": lambda p: p.__setitem__("evidenceWindow", {"start": GENERATED, "end": GENERATED}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                tampered = json.loads(json.dumps(original))
                mutate(tampered)
                self.assertTrue(validate_resolved(tampered, evidence, self.policy), name)

    def test_17_preferred_selection_must_equal_lane_after_json_round_trip(self):
        f = ResolverFixture(); cap = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z")
        f.asset_status("closed", cap, status="closed")
        evidence = f.payload(); result = json.loads(json.dumps(resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)))
        record = self.asset_record(result, "operationalStatus")
        self.assertEqual(canonical_bytes(record["preferredSelection"]), canonical_bytes(record["lanes"][0]["selection"]))
        record["preferredSelection"]["selectionReason"] = "tampered independently"
        errors = validate_resolved(result, evidence, self.policy)
        self.assertTrue(any("preferred selection differs" in error or "derived section mismatch" in error for error in errors))

    def test_18_future_input_conflict_is_excluded_with_diagnostic(self):
        f = ResolverFixture(); early = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); late = f.capture("falls_mountainops_lifts_rich", "2026-07-13T07:00:00Z")
        f.asset_status("early", early, status="closed"); f.asset_status("late", late, status="open", observed="2026-07-13T06:00:00Z")
        conflict = {"conflictId": "phase2-future", "resortId": "falls", "fieldFamily": "operationalStatus", "subjectId": f.asset["assetId"],
                    "observationIds": ["early", "late"], "conflictType": "value", "detectedAt": "2026-07-13T07:00:00Z", "status": "open", "resolution": None, "notes": []}
        evidence = f.payload([conflict]); result = resolve_export(evidence, AS_OF, self.policy, generated_at=GENERATED)
        self.assertEqual([], result["inputEvidenceConflicts"])
        self.assertEqual([{"conflictId": "phase2-future", "observationIds": ["early", "late"], "reasons": ["capture_retrieved_after_as_of"]}], result["diagnostics"]["excludedInputEvidenceConflicts"])
        tampered = copy.deepcopy(result); tampered["inputEvidenceConflicts"] = [conflict]
        self.assertTrue(validate_resolved(tampered, evidence, self.policy))

    def test_19_failed_partial_and_future_capture_diagnostics_are_distinct(self):
        f = ResolverFixture()
        failed = f.capture("falls_official_report", "2026-07-13T05:00:00Z", status="failed", http_status=503, warnings=["upstream unavailable"])
        partial = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z", status="partial", warnings=["one row rejected"])
        future = f.capture("falls_mountainops_lifts_rich", "2026-07-13T07:00:00Z", warnings=["future warning"])
        f.asset_status("partial-value", partial, status="closed")
        result = self.resolved(f); diagnostics = result["diagnostics"]
        self.assertEqual([failed], [row["captureId"] for row in diagnostics["unusableCaptures"]])
        self.assertEqual(503, diagnostics["unusableCaptures"][0]["httpStatus"])
        self.assertEqual([future], [row["captureId"] for row in diagnostics["excludedFutureCaptures"]])
        self.assertEqual({failed, partial}, {row["captureId"] for row in diagnostics["captureWarnings"]})
        self.assertEqual(1, diagnostics["eligibleCaptureCount"])

    def test_20_conflict_roles_require_exact_match(self):
        for left_role, right_role in (("measurement", "report_summary"), ("morning_plan", "expected")):
            with self.subTest(left_role=left_role, right_role=right_role):
                f = ResolverFixture(); cap = f.capture("falls_official_report", "2026-07-13T06:00:00Z")
                f.asset_status("left", cap, role=left_role, status="closed")
                f.asset_status("right", cap, role=right_role, status="open")
                self.assertEqual([], self.resolved(f)["conflicts"])
        f = ResolverFixture(); compact = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z"); rich = f.capture("falls_mountainops_lifts_rich", "2026-07-13T06:01:00Z")
        f.asset_status("compact", compact, role="live_actual", status="closed"); f.asset_status("rich", rich, role="live_actual", status="open")
        self.assertEqual(1, len(self.resolved(f)["conflicts"]))

    def test_21_structural_empty_status_reason_is_unresolved_but_nonempty_is_known(self):
        f = ResolverFixture(); cap = f.capture("falls_mountainops_lifts", "2026-07-13T06:00:00Z")
        f.asset_status("empty", cap, status="closed", statusReason=[])
        record = self.asset_record(self.resolved(f), "statusReason")
        self.assertFalse(record["preferredSelection"]["known"])
        f.rows["assetStatusObservations"][0]["statusReason"] = ["wind"]
        record = self.asset_record(self.resolved(f), "statusReason")
        self.assertTrue(record["preferredSelection"]["known"]); self.assertEqual(["wind"], record["preferredSelection"]["value"])

    def test_22_policy_rejects_semantic_and_ordering_tampering(self):
        def errors_for(mutator):
            candidate = copy.deepcopy(self.policy); mutator(candidate); candidate["revision"] = content_hash(candidate)
            return validate_policy(candidate)
        cases = {
            "unknown priority lane": lambda p: p["assetFieldPolicies"][0]["sourcePriorityByLane"].__setitem__("bogus", []),
            "dead valueField": lambda p: p["assetFieldPolicies"][0].__setitem__("valueField", "operationalStatus"),
            "missing role": lambda p: p["roleLanes"]["plan"].remove("expected"),
            "duplicate role": lambda p: p["roleLanes"]["reported"].append("live_actual"),
            "incomplete order": lambda p: p["preferredDisplayOrder"].pop(),
            "dead order token": lambda p: p["preferredDisplayOrder"].__setitem__(-1, "unknown:unknown"),
            "duplicate priority": lambda p: p["assetFieldPolicies"][0]["sourcePriorityByLane"]["actual"].append("falls_mountainops_lifts"),
            "invalid source": lambda p: p["assetFieldPolicies"][0]["sourcePriorityByLane"]["actual"].append("not_a_source"),
        }
        for name, mutator in cases.items():
            with self.subTest(name=name):
                self.assertTrue(errors_for(mutator), name)


if __name__ == "__main__":
    unittest.main()
