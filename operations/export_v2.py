"""Capture-first bounded export for temporary operations-v2 evidence."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Callable

from .registry import load_registries
from .schema_v2 import assert_valid_export
from .storage_v2 import TABLES


def _iso(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("export window timestamps must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def export(con, out: str | Path, *, window_start: str, window_end: str,
           clock: Callable[[], str] | None = None) -> dict:
    """Export all evidence for captures in the inclusive requested UTC window."""
    start, end = _iso(window_start), _iso(window_end)
    if start > end:
        raise ValueError("window_start must not be after window_end")
    capture_rows = list(con.execute(
        "SELECT * FROM operations_v2_captures WHERE retrieved_at>=? AND retrieved_at<=? ORDER BY retrieved_at,capture_id",
        (start, end)))
    captures = []
    for row in capture_rows:
        item = dict(row)
        captures.append({"captureId": item["capture_id"], "resortId": item["resort_id"], "sourceId": item["source_id"],
                         "sourceLayer": item["source_layer"], "sourceRole": item["source_role"], "retrievedAt": item["retrieved_at"],
                         "responseAt": item["response_at"], "sourceReportedAt": item["source_reported_at"], "operationalDate": item["operational_date"],
                         "httpStatus": item["http_status"], "contentType": item["content_type"], "payloadHash": item["payload_hash"],
                         "rawPayloadRef": item["raw_payload_ref"], "parserVersion": item["parser_version"], "retrievalStatus": item["retrieval_status"],
                         "freshnessMinutes": item["freshness_minutes"], "assetRegistryRevision": item["asset_registry_revision"],
                         "sourceInventoryRevision": item["source_inventory_revision"], "metricCatalogueRevision": item["metric_catalogue_revision"],
                         "warnings": json.loads(item["warnings_json"])})
    capture_ids = [item["captureId"] for item in captures]

    current_assets, current_sources, current_metrics = load_registries()
    revisions = {(item["assetRegistryRevision"], item["sourceInventoryRevision"], item["metricCatalogueRevision"]) for item in captures}
    if len(revisions) > 1:
        raise ValueError("selected captures span multiple catalogue revisions; v2 top-level contract cannot represent them truthfully")
    if revisions:
        asset_revision, source_revision, metric_revision = next(iter(revisions))
        asset_row = con.execute("SELECT registry_json FROM operations_v2_registry_snapshots WHERE registry_revision=?", (asset_revision,)).fetchone()
        source_row = con.execute("SELECT inventory_json FROM operations_v2_source_inventory_snapshots WHERE source_inventory_revision=?", (source_revision,)).fetchone()
        metric_row = con.execute("SELECT catalogue_json FROM operations_v2_metric_catalogue_snapshots WHERE metric_catalogue_revision=?", (metric_revision,)).fetchone()
        if not all((asset_row, source_row, metric_row)):
            raise ValueError("selected capture catalogue snapshot is incomplete")
        assets, sources, metrics = json.loads(asset_row[0]), json.loads(source_row[0]), json.loads(metric_row[0])
        if (assets["contentHash"], sources["contentHash"], metrics["contentHash"]) != (
                current_assets["contentHash"], current_sources["contentHash"], current_metrics["contentHash"]):
            raise ValueError("selected captures use a non-current catalogue revision; refusing to relabel them")
    else:
        assets, sources, metrics = current_assets, current_sources, current_metrics

    payload = {"schemaVersion": "alpine.operations-export.v2", "producer": "snow-pred-accu temporary Phase 2B",
               "generatedAt": _iso((clock or _now)()), "windowStart": start, "windowEnd": end,
               "identitySchemaVersion": "alpine.resort-identities.v1", "assetRegistrySchemaVersion": assets["schemaVersion"],
               "assetRegistryRevision": assets["contentHash"], "sourceInventoryRevision": sources["contentHash"],
               "metricCatalogueRevision": metrics["contentHash"], "assetRegistryCompleteness": "complete",
               "sourceInventoryCompleteness": "complete", "assets": assets["assets"], "sourceInventory": sources["sources"],
               "captures": captures, "rawPayloads": [], "conflicts": [],
               "diagnostics": {"warnings": [], "unknownSourceFields": [], "unmappedAssetCount": 0}}

    if capture_ids:
        placeholders = ",".join("?" for _ in capture_ids)
        for row in con.execute(f"SELECT descriptor_json FROM operations_v2_raw_descriptors WHERE capture_id IN ({placeholders}) ORDER BY capture_id,descriptor_id", capture_ids):
            payload["rawPayloads"].append(json.loads(row[0]))
        for row in con.execute(f"SELECT capture_id,diagnostics_json FROM operations_v2_capture_diagnostics WHERE capture_id IN ({placeholders}) ORDER BY capture_id", capture_ids):
            diagnostics = json.loads(row[1])
            payload["diagnostics"]["warnings"].extend(diagnostics.get("warnings", []))
            for key in ("parsingFailures", "duplicateListNames", "crossStatusListOverlaps", "malformedValues"):
                for item in diagnostics.get(key, []):
                    payload["diagnostics"]["warnings"].append(f"{row[0]} {key}: {json.dumps(item, sort_keys=True, ensure_ascii=False)}")
            for path in diagnostics.get("unknownSourceFields", []):
                payload["diagnostics"]["unknownSourceFields"].append({"sourceId": next(c["sourceId"] for c in captures if c["captureId"] == row[0]), "path": path, "captureId": row[0]})
            payload["diagnostics"]["unmappedAssetCount"] += diagnostics.get("unmappedAssetCount", 0)
        for collection, table in TABLES.items():
            rows = con.execute(
                f"SELECT o.observation_json FROM {table} o JOIN operations_v2_captures c ON c.capture_id=o.capture_id "
                f"WHERE o.capture_id IN ({placeholders}) ORDER BY c.retrieved_at,o.capture_id,o.observation_id", capture_ids)
            payload[collection] = [json.loads(row[0]) for row in rows]
    else:
        for collection in TABLES:
            payload[collection] = []
    payload["diagnostics"]["warnings"] = list(dict.fromkeys(payload["diagnostics"]["warnings"]))
    assert_valid_export(payload)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_suffix(out.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, out)
    return payload
