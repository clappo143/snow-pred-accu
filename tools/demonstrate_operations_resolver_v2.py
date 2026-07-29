#!/usr/bin/env python3
"""Reproducible /tmp-only Phase 3 archive and adversarial demonstrations."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.export_v2 import export
from operations.normalize_v2 import normalize_envelope
from operations.registry import ROOT
from operations.resolve_v2 import load_policy, resolve_export
from operations.storage_v2 import connect_temporary, persist

ARCHIVE_SOURCES = (
    "falls_official_report", "falls_mountainops_lifts", "falls_mountainops_runs",
    "hotham_official_report", "hotham_mountainops_lifts", "hotham_mountainops_runs",
    "perisher_official_report", "perisher_mountainops_lifts", "perisher_mountainops_runs",
)


def summary(result: dict) -> dict:
    records = [record for resort in result["resorts"] for collection in ("assetFields", "metrics", "snowmakingSignals", "aggregates", "narratives") for record in resort[collection]]
    assets = {record["subject"]["subjectId"] for resort in result["resorts"] for record in resort["assetFields"]}
    lanes = Counter(lane["lane"] for record in records for lane in record["lanes"])
    return {"resortCount": len(result["resorts"]), "resolvedAssetCount": len(assets), "selectedFieldsByLane": dict(sorted(lanes.items())),
            "staleSelections": result["diagnostics"]["staleSelectionCount"], "unresolvedSelections": result["diagnostics"]["unresolvedSelectionCount"],
            "planActualComparisons": len(result["planActualComparisons"]),
            "conflictsByType": dict(sorted(Counter(row["conflictType"] for row in result["conflicts"]).items())),
            "unmappedSubjects": result["diagnostics"]["unmappedSubjectCount"], "resolverDiagnostics": result["diagnostics"]}


def delivery_size(input_path: Path, resolved_path: Path, result: dict) -> dict:
    collections = ("assetFields", "metrics", "snowmakingSignals", "aggregates", "narratives")
    record_counts = {name: sum(len(resort[name]) for resort in result["resorts"]) for name in collections}
    selections = [lane["selection"] for resort in result["resorts"] for name in collections
                  for record in resort[name] for lane in record["lanes"]]
    input_bytes = input_path.read_bytes(); resolved_bytes = resolved_path.read_bytes()
    return {
        "inputExportBytes": len(input_bytes), "resolvedOutputBytes": len(resolved_bytes),
        "inputExportGzipBytes": len(gzip.compress(input_bytes, mtime=0)),
        "resolvedOutputGzipBytes": len(gzip.compress(resolved_bytes, mtime=0)),
        "resolvedRecordCounts": record_counts,
        "knownSelectionCount": sum(selection["known"] for selection in selections),
        "unresolvedSelectionCount": sum(not selection["known"] for selection in selections),
    }


def archive_export(output_dir: Path) -> tuple[dict, str, str]:
    database = output_dir / "archive-v2.sqlite"
    if database.exists():
        database.unlink()
    connection = connect_temporary(database)
    normalized = []
    try:
        archive = ROOT / "data/operations/raw/2026-07-12"
        for source in ARCHIVE_SOURCES:
            paths = sorted((archive / source).glob("*.json"), key=lambda path: json.loads(path.read_text())["capturedAt"])
            for path in list(dict.fromkeys((paths[0], paths[-1]))):
                envelope = json.loads(path.read_text()); envelope["_path"] = str(path)
                item = normalize_envelope(envelope, source); persist(connection, item); normalized.append(item)
        start = min(item.capture["retrievedAt"] for item in normalized); end = max(item.capture["retrievedAt"] for item in normalized)
        payload = export(connection, output_dir / "archive-export-v2.json", window_start=start, window_end=end,
                         clock=lambda: "2026-07-13T12:00:00Z")
        return payload, start, end
    finally:
        connection.close()


def add_plan_disagreement(evidence: dict) -> dict:
    payload = copy.deepcopy(evidence)
    actual = next(row for row in payload["assetStatusObservations"] if row["resortId"] == "falls" and row["assetClass"] == "lift" and row["assetId"] and row["operationalStatus"] in {"open", "closed"})
    report_capture = next(row for row in payload["captures"] if row["sourceId"] == "falls_official_report")
    plan = copy.deepcopy(actual); plan.update(observationId="demo-plan-disagreement", captureId=report_capture["captureId"], observationRole="morning_plan",
                                             operationalStatus="open" if actual["operationalStatus"] == "closed" else "closed",
                                             observedAt=actual["observedAt"], rawStatus="synthetic opposite morning plan")
    payload["assetStatusObservations"].append(plan)
    return payload


def add_live_conflict(evidence: dict) -> dict:
    payload = copy.deepcopy(evidence)
    actual = next(row for row in payload["assetStatusObservations"] if row["resortId"] == "falls" and row["assetClass"] == "lift" and row["assetId"] and row["operationalStatus"] in {"open", "closed"})
    compact_capture = next(row for row in payload["captures"] if row["captureId"] == actual["captureId"])
    rich_source = next(row for row in payload["sourceInventory"] if row["sourceId"] == "falls_mountainops_lifts_rich")
    retrieved = (dt.datetime.fromisoformat(compact_capture["retrievedAt"].replace("Z", "+00:00")) + dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    capture_id = "demo-rich-live-conflict"; digest = hashlib.sha256(capture_id.encode()).hexdigest()
    capture = copy.deepcopy(compact_capture); capture.update(captureId=capture_id, sourceId=rich_source["sourceId"], sourceLayer=rich_source["layer"],
                                                              sourceRole=rich_source["sourceRole"], retrievedAt=retrieved, responseAt=retrieved,
                                                              sourceReportedAt=None, payloadHash=digest, rawPayloadRef=f"/tmp/{digest}.json")
    payload["captures"].append(capture)
    payload["rawPayloads"].append({"payloadHash": digest, "rawPayloadRef": f"/tmp/{digest}.json", "sourceId": rich_source["sourceId"],
                                   "sourceUrl": rich_source["url"], "contentType": "application/json", "httpStatus": 200,
                                   "responseAt": retrieved, "firstCapturedAt": retrieved, "parserVersion": capture["parserVersion"]})
    rich = copy.deepcopy(actual); rich.update(observationId="demo-rich-opposite-known", captureId=capture_id,
                                              operationalStatus="open" if actual["operationalStatus"] == "closed" else "closed",
                                              observedAt=actual["observedAt"], rawStatus={"statusCode": "synthetic-reviewed-known-opposite"})
    payload["assetStatusObservations"].append(rich)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/operations-phase3-demo"))
    args = parser.parse_args(); args.out_dir.mkdir(parents=True, exist_ok=True)
    policy = load_policy(ROOT / "config/operations_resolution_policy_v1.json")
    evidence, start, end = archive_export(args.out_dir)
    start_time = dt.datetime.fromisoformat(start.replace("Z", "+00:00")); end_time = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
    times = {"early": start_time + (end_time - start_time) / 3, "final": end_time + dt.timedelta(minutes=1), "stale": end_time + dt.timedelta(days=2)}
    report = {}
    for label, when in times.items():
        as_of = when.isoformat().replace("+00:00", "Z")
        resolved = resolve_export(evidence, as_of, policy, generated_at="2026-07-15T12:00:00Z")
        (args.out_dir / f"{label}-resolved-v1.json").write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        report[label] = {"asOf": as_of, **summary(resolved)}
        if label == "final":
            final_result = resolved
    final_as_of = times["final"].isoformat().replace("+00:00", "Z")
    for label, payload in (("syntheticPlanActual", add_plan_disagreement(evidence)), ("syntheticLiveConflict", add_live_conflict(evidence))):
        resolved = resolve_export(payload, final_as_of, policy, generated_at="2026-07-15T12:00:00Z")
        (args.out_dir / f"{label}.json").write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        report[label] = summary(resolved)
    hotham = json.loads((ROOT / "tests/fixtures/operations/v2/example_operations_export_v2.json").read_text())
    resolved = resolve_export(hotham, "2026-07-13T06:30:00Z", policy, generated_at="2026-07-13T06:31:00Z")
    (args.out_dir / "hothamSnowmaking.json").write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
    report["hothamSnowmaking"] = {**summary(resolved), "signalGroups": len(resolved["resorts"][0]["snowmakingSignals"])}
    report["deliverySize"] = delivery_size(args.out_dir / "archive-export-v2.json", args.out_dir / "final-resolved-v1.json", final_result)
    (args.out_dir / "demonstration-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
