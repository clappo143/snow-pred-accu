"""Transactional append-only persistence for the temporary v2 lane."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .migrations_v2 import migrate_v2
from .registry import load_registries

TABLES = {"metricObservations": "operations_v2_metric_observations",
          "snowmakingObservations": "operations_v2_snowmaking_observations",
          "assetStatusObservations": "operations_v2_asset_status_observations",
          "aggregateObservations": "operations_v2_aggregate_observations",
          "narratives": "operations_v2_narratives"}


class ImmutableCollisionError(ValueError):
    """A deterministic identity was encountered with different content."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def connect_temporary(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("CREATE TABLE IF NOT EXISTS operations_raw_payloads (payload_hash TEXT PRIMARY KEY,source TEXT NOT NULL,source_url TEXT NOT NULL,first_captured_at TEXT NOT NULL,response_at TEXT,http_status INTEGER,content_type TEXT,archive_ref TEXT NOT NULL,parser_version TEXT NOT NULL,normalized_record_ids TEXT NOT NULL)")
    migrate_v2(con)
    return con


def insert_immutable(con: sqlite3.Connection, table: str, key_columns: tuple[str, ...], key_values: tuple[Any, ...],
                     compare_columns: tuple[str, ...], compare_values: tuple[Any, ...],
                     insert_columns: tuple[str, ...], insert_values: tuple[Any, ...], label: str) -> bool:
    """Insert once; exact content is a no-op and divergent content is rejected."""
    where = " AND ".join(f"{column}=?" for column in key_columns)
    existing = con.execute(f"SELECT {','.join(compare_columns)} FROM {table} WHERE {where}", key_values).fetchone()
    if existing is not None:
        actual = tuple(existing[column] for column in compare_columns)
        if actual != compare_values:
            raise ImmutableCollisionError(f"same-ID/different-content collision in {label}: {key_values!r}")
        return False
    placeholders = ",".join("?" for _ in insert_columns)
    con.execute(f"INSERT INTO {table} ({','.join(insert_columns)}) VALUES ({placeholders})", insert_values)
    return True


def _insert_observation(con: sqlite3.Connection, table: str, row: dict[str, Any], collection: str, revision: str) -> None:
    oid, encoded = row["observationId"], _json(row)
    if collection == "metricObservations":
        columns = ("observation_id", "capture_id", "resort_id", "metric_key", "observed_at", "observation_json")
        values = (oid, row["captureId"], row["resortId"], row["metricKey"], row["observedAt"], encoded)
    elif collection == "snowmakingObservations":
        columns = ("observation_id", "capture_id", "resort_id", "asset_registry_revision", "subject_asset_id", "signal_type", "observed_at", "observation_json")
        values = (oid, row["captureId"], row["resortId"], revision, row["subjectAssetId"], row["signalType"], row["observedAt"], encoded)
    elif collection == "assetStatusObservations":
        columns = ("observation_id", "capture_id", "resort_id", "asset_registry_revision", "asset_id", "upstream_asset_id", "asset_class", "observed_at", "observation_json")
        values = (oid, row["captureId"], row["resortId"], revision, row["assetId"], row["upstreamAssetId"], row["assetClass"], row["observedAt"], encoded)
    elif collection == "aggregateObservations":
        columns = ("observation_id", "capture_id", "resort_id", "asset_class", "status", "observed_at", "observation_json")
        values = (oid, row["captureId"], row["resortId"], row["assetClass"], row["status"], row["observedAt"], encoded)
    else:
        columns = ("observation_id", "capture_id", "resort_id", "narrative_type", "observed_at", "observation_json")
        values = (oid, row["captureId"], row["resortId"], row["narrativeType"], row["observedAt"], encoded)
    insert_immutable(con, table, ("observation_id",), (oid,), ("observation_json",), (encoded,), columns, values, f"{collection} observation")


def persist(con: sqlite3.Connection, normalized) -> None:
    """Persist one complete normalized capture atomically."""
    assets, sources, metrics = load_registries()
    capture = normalized.capture
    with con:
        snapshots = (
            ("operations_v2_registry_snapshots", "registry_revision", "registry_json", assets, assets["contentHash"], assets["schemaVersion"]),
            ("operations_v2_source_inventory_snapshots", "source_inventory_revision", "inventory_json", sources, sources["contentHash"], sources["schemaVersion"]),
            ("operations_v2_metric_catalogue_snapshots", "metric_catalogue_revision", "catalogue_json", metrics, metrics["contentHash"], metrics["schemaVersion"]),
        )
        for table, key, json_column, payload, revision, schema in snapshots:
            encoded = _json(payload)
            columns = (key, "schema_version", "created_at", json_column)
            values = (revision, schema, capture["retrievedAt"], encoded)
            insert_immutable(con, table, (key,), (revision,), ("schema_version", json_column), (schema, encoded), columns, values, table)

        for asset in assets["assets"]:
            encoded = _json(asset)
            columns = ("registry_revision", "asset_id", "resort_id", "asset_class", "registry_json")
            values = (assets["contentHash"], asset["assetId"], asset["resortId"], asset["assetClass"], encoded)
            insert_immutable(con, "operations_v2_assets", ("registry_revision", "asset_id"), values[:2],
                             ("resort_id", "asset_class", "registry_json"), values[2:], columns, values, "versioned asset")

        raw = normalized.raw_payload
        # Preserve the v1 hash-only table solely as the legacy FK target.  All
        # source/ref provenance is stored and exported from the v2 association.
        con.execute("INSERT OR IGNORE INTO operations_raw_payloads VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (raw["payloadHash"], raw["sourceId"], raw["sourceUrl"], raw["firstCapturedAt"], raw["responseAt"],
                     raw["httpStatus"], raw["contentType"], raw["rawPayloadRef"], raw["parserVersion"], "[]"))

        columns = ("capture_id", "resort_id", "source_id", "source_layer", "source_role", "retrieved_at", "response_at",
                   "source_reported_at", "operational_date", "http_status", "content_type", "payload_hash", "raw_payload_ref",
                   "parser_version", "retrieval_status", "freshness_minutes", "asset_registry_revision", "source_inventory_revision",
                   "metric_catalogue_revision", "warnings_json")
        values = (capture["captureId"], capture["resortId"], capture["sourceId"], capture["sourceLayer"], capture["sourceRole"],
                  capture["retrievedAt"], capture["responseAt"], capture["sourceReportedAt"], capture["operationalDate"],
                  capture["httpStatus"], capture["contentType"], capture["payloadHash"], capture["rawPayloadRef"],
                  capture["parserVersion"], capture["retrievalStatus"], capture["freshnessMinutes"], capture["assetRegistryRevision"],
                  capture["sourceInventoryRevision"], capture["metricCatalogueRevision"], _json(capture["warnings"]))
        insert_immutable(con, "operations_v2_captures", ("capture_id",), (capture["captureId"],),
                         columns[1:], values[1:], columns, values, "capture")

        descriptor = {key: value for key, value in raw.items() if key != "descriptorId"}
        descriptor_json = _json(descriptor)
        descriptor_values = (raw["descriptorId"], capture["captureId"], raw["payloadHash"], raw["sourceId"], raw["rawPayloadRef"], descriptor_json)
        insert_immutable(con, "operations_v2_raw_descriptors", ("descriptor_id",), (raw["descriptorId"],),
                         ("capture_id", "payload_hash", "source_id", "raw_payload_ref", "descriptor_json"), descriptor_values[1:],
                         ("descriptor_id", "capture_id", "payload_hash", "source_id", "raw_payload_ref", "descriptor_json"), descriptor_values,
                         "v2 raw descriptor")

        diagnostics_json = _json(normalized.diagnostics)
        insert_immutable(con, "operations_v2_capture_diagnostics", ("capture_id",), (capture["captureId"],),
                         ("diagnostics_json",), (diagnostics_json,), ("capture_id", "diagnostics_json"),
                         (capture["captureId"], diagnostics_json), "capture diagnostics")

        for collection, table in TABLES.items():
            for row in normalized.records[collection]:
                _insert_observation(con, table, row, collection, capture["assetRegistryRevision"])
