"""Offline replay and deliberately bounded live probe for the v2 lane."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .export_v2 import export
from .normalize_v2 import normalize_envelope
from .registry import load_registries
from .storage_v2 import connect_temporary, persist

RICH_FIELDS = (
    "id", "name", "publicArea", "statusCode", "isScheduled",
    "openTime", "closeTime", "queueMinsEstimate",
)
REVIEWED_STATUS_DISPOSITION = [
    {"statusCode": 1, "normalizedStatus": "open", "classification": "independently_reviewed_direct_observation",
     "rowSupportedByCheckedEvidence": False, "note": "Reviewed same-poll observation; no durable supporting row was retained."},
    {"statusCode": 2, "normalizedStatus": "closed", "classification": "row_supported",
     "rowSupportedByCheckedEvidence": True},
    {"statusCode": 3, "normalizedStatus": "on_hold", "classification": "row_supported_prior_sample",
     "rowSupportedByCheckedEvidence": True, "evidenceSampleId": "hotham-big-d-code-3"},
    {"statusCode": 6, "normalizedStatus": "unknown", "classification": "unresolved",
     "rowSupportedByCheckedEvidence": False, "note": "Observed with compact Closed but not mapped; schedule correlation is not status semantics."},
]
REVIEWED_STATUS_SAMPLES = [{
    "sampleId": "hotham-big-d-code-3", "reviewedDate": "2026-07-13", "resortId": "hotham",
    "compact": {"id": 10, "name": "Big D", "location": "Hotham", "status": "On Hold", "statusId": 3},
    "rich": {"id": 10, "name": "Big D", "publicArea": "Hotham", "statusCode": 3},
    "identityMatch": True,
}]


def archived(root: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    for path in sorted(root.glob("*/*.json")):
        source = path.parent.name
        if source.startswith(("falls_", "hotham_", "perisher_")):
            item = json.loads(path.read_text())
            item["_path"] = str(path)
            yield source, item


def live(selected: set[str]) -> Iterable[tuple[str, dict[str, Any]]]:
    _, sources, _ = load_registries()
    for source in sources["sources"]:
        if source["sourceId"] not in selected:
            continue
        req = urllib.request.Request(
            source["url"], headers={"User-Agent": "snow-pred-accu operations-v2 bounded probe/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                body = response.read().decode("utf-8", errors="replace")
                now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
                yield source["sourceId"], {
                    "body": body, "capturedAt": now, "responseAt": now,
                    "contentType": response.headers.get_content_type(), "httpStatus": response.status,
                }
        except Exception as exc:
            now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            yield source["sourceId"], {
                "body": "", "capturedAt": now, "responseAt": now, "contentType": "text/plain",
                "httpStatus": 599, "warning": f"live request failed: {type(exc).__name__}: {exc}",
            }


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    return type(value).__name__


def _value_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: collections.Counter[str] = collections.Counter()
    for row in rows:
        if field not in row:
            counts["missing"] += 1
        else:
            value = row[field]
            counts[f"{_type_name(value)}:{json.dumps(value, sort_keys=True)}"] += 1
    return dict(sorted(counts.items()))


def build_comparison_report(
    responses: dict[str, dict[str, Any]], *, generated_at: str
) -> dict[str, Any]:
    """Build auditable observed evidence; reviewed dispositions remain separate."""
    reports = []
    observed_pairs: collections.Counter[tuple[Any, Any, Any]] = collections.Counter()
    for resort in ("falls", "hotham", "perisher"):
        compact_source = f"{resort}_mountainops_lifts"
        rich_source = f"{resort}_mountainops_lifts_rich"
        compact_response = responses.get(compact_source, {})
        rich_response = responses.get(rich_source, {})
        compact = compact_response.get("rows") or []
        rich = rich_response.get("rows") or []
        compact_rows = [{
            "id": row.get("Id"), "name": str(row.get("Name") or "").strip(),
            "location": str(row.get("Location") or "").strip(), "status": row.get("Status"),
            "statusId": row.get("StatusId"),
        } for row in compact if isinstance(row, dict)]
        rich_rows = [{key: row.get(key) for key in RICH_FIELDS} for row in rich if isinstance(row, dict)]
        rich_index = {row["id"]: row for row in rich_rows}
        compact_ids = {row["id"] for row in compact_rows}
        matches = []
        for row in compact_rows:
            other = rich_index.get(row["id"])
            identity_match = bool(other and (row["name"], row["location"]) ==
                                  (str(other.get("name") or "").strip(), str(other.get("publicArea") or "").strip()))
            matches.append({"id": row["id"], "identityMatch": identity_match, "compact": row, "rich": other})
            if other:
                observed_pairs[(row["statusId"], row["status"], other.get("statusCode"))] += 1
        missing_rich = sorted(row["id"] for row in compact_rows if row["id"] not in rich_index)
        extra_rich = sorted(row["id"] for row in rich_rows if row["id"] not in compact_ids)
        mismatch_count = sum(not row["identityMatch"] for row in matches) + len(extra_rich)
        reports.append({
            "resortId": resort, "compactSourceId": compact_source, "richSourceId": rich_source,
            "compactRetrievedAt": compact_response.get("retrievedAt"),
            "richRetrievedAt": rich_response.get("retrievedAt"),
            "compactCount": len(compact_rows), "richCount": len(rich_rows),
            "identity": {"matchCount": len(matches) - sum(not row["identityMatch"] for row in matches),
                         "mismatchCount": mismatch_count, "missingRichIds": missing_rich,
                         "extraRichIds": extra_rich,
                         "allIdentityMatches": bool(compact_rows and len(compact_rows) == len(rich_rows) and mismatch_count == 0)},
            "observedStatusPairs": [
                {"compactStatusId": status_id, "compactStatus": status, "richStatusCode": code, "count": count}
                for (status_id, status, code), count in sorted(
                    collections.Counter((r["compact"]["statusId"], r["compact"]["status"], r["rich"]["statusCode"])
                                        for r in matches if r["rich"]).items(), key=lambda item: repr(item[0]))
            ],
            "isScheduledValueTypeCounts": _value_counts(rich_rows, "isScheduled"),
            "queueValueTypeCounts": _value_counts(rich_rows, "queueMinsEstimate"),
            "rows": matches,
        })
    return {
        "schemaVersion": "alpine.vail-rich-compact-comparison.v2",
        "generatedAt": generated_at,
        "identityRule": "same bounded probe: numeric ID, trimmed name, and compact Location/rich publicArea must all match",
        "identityPromotionGate": "identity fields only; transient status agreement is not required",
        "observedStatusPairs": [
            {"compactStatusId": status_id, "compactStatus": status, "richStatusCode": code, "count": count}
            for (status_id, status, code), count in sorted(observed_pairs.items(), key=lambda item: repr(item[0]))
        ],
        "reviewedStatusDisposition": [
            {**row, "rowSupportedByCurrentReport": any(
                code == row["statusCode"] and row["normalizedStatus"] == {1: "open", 2: "closed", 3: "on_hold"}.get(status_id)
                for (status_id, _status, code) in observed_pairs
            )} for row in REVIEWED_STATUS_DISPOSITION
        ],
        "reviewedStatusSamples": REVIEWED_STATUS_SAMPLES,
        "scheduleDisposition": "vail_rich_binary_schedule_flag_v1: exact integer 0=false and 1=true; other values/types are null with diagnostics; independent of operational status",
        "queueDisposition": "raw_only: repeated all-zero samples do not prove explicit no-wait semantics; no queue timestamp is fabricated",
        "reports": reports,
    }


def _probe_counts() -> dict[str, Any]:
    return {"captures": 0, "captureWarnings": 0, "diagnosticWarnings": 0,
            "unknownSourceFields": 0, "malformedValues": 0, "unmappedObservations": 0,
            "unknownRichStatusCodesBySource": {}, "scheduledBySource": {},
            **{key: 0 for key in ("metricObservations", "snowmakingObservations", "assetStatusObservations",
                                  "aggregateObservations", "narratives")}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-archive", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--mapping-report", type=Path)
    parser.add_argument("--window-start")
    parser.add_argument("--window-end")
    args = parser.parse_args()
    if not args.from_archive and not args.live:
        parser.error("choose --from-archive or --live")
    db = args.db or Path(tempfile.mkstemp(prefix="operations-v2-", suffix=".sqlite")[1])
    out = args.out or Path(tempfile.mkstemp(prefix="operations-export-v2-", suffix=".json")[1])
    con = connect_temporary(db)
    selected = set(args.source) or {
        source["sourceId"] for source in load_registries()[1]["sources"]
        if source["sourceId"].startswith(("falls_", "hotham_", "perisher_"))
    }
    stream = archived(args.from_archive) if args.from_archive else live(selected)
    counts = _probe_counts()
    diagnostic_warning_keys: set[tuple[str, str]] = set()
    live_responses: dict[str, dict[str, Any]] = {}
    for source, item in stream:
        if source not in selected:
            continue
        normalized = normalize_envelope(item, source)
        persist(con, normalized)
        counts["captures"] += 1
        counts["captureWarnings"] += len(set(normalized.capture["warnings"]))
        diagnostic_warning_keys.update((source, warning) for warning in normalized.diagnostics["warnings"])
        counts["unknownSourceFields"] += len(set(normalized.diagnostics["unknownSourceFields"]))
        counts["malformedValues"] += len(normalized.diagnostics["malformedValues"])
        counts["unmappedObservations"] += normalized.diagnostics["unmappedAssetCount"]
        for key in ("metricObservations", "snowmakingObservations", "assetStatusObservations",
                    "aggregateObservations", "narratives"):
            counts[key] += len(normalized.records[key])
        if source.endswith("_rich"):
            rows = normalized.records["assetStatusObservations"]
            status_codes: collections.Counter[str] = collections.Counter()
            for row in rows:
                raw = row.get("rawStatus") or {}
                code = raw.get("statusCode") if isinstance(raw, dict) else raw
                if row["operationalStatus"] == "unknown":
                    status_codes[str(code)] += 1
            if status_codes:
                counts["unknownRichStatusCodesBySource"][source] = dict(sorted(status_codes.items()))
            source_payload = []
            if item.get("httpStatus") == 200:
                try:
                    source_payload = json.loads(item["body"])
                except Exception:
                    pass
            counts["scheduledBySource"][source] = {
                "upstreamValueTypeCounts": _value_counts(source_payload if isinstance(source_payload, list) else [], "isScheduled"),
                "normalizedNonNull": sum(row.get("scheduled") is not None for row in rows),
                "normalizedTrue": sum(row.get("scheduled") is True for row in rows),
                "normalizedFalse": sum(row.get("scheduled") is False for row in rows),
                "rows": len(rows),
            }
        if args.live and item.get("httpStatus") == 200:
            try:
                rows = json.loads(item["body"])
                live_responses[source] = {"rows": rows, "retrievedAt": normalized.capture["retrievedAt"]}
            except Exception:
                pass
    counts["diagnosticWarnings"] = len(diagnostic_warning_keys)
    start = args.window_start or con.execute("select min(retrieved_at) from operations_v2_captures").fetchone()[0]
    end = args.window_end or con.execute("select max(retrieved_at) from operations_v2_captures").fetchone()[0]
    export(con, out, window_start=start, window_end=end)
    if args.mapping_report:
        report = build_comparison_report(
            live_responses, generated_at=dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        )
        args.mapping_report.parent.mkdir(parents=True, exist_ok=True)
        args.mapping_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"db": str(db), "out": str(out), **counts}, sort_keys=True))
    con.close()


if __name__ == "__main__":
    main()
