"""Additive, idempotent SQLite DDL for operations v2 (never auto-run)."""
from __future__ import annotations
import sqlite3

DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS operations_v2_registry_snapshots (registry_revision TEXT PRIMARY KEY,schema_version TEXT NOT NULL,created_at TEXT NOT NULL,registry_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations_v2_source_inventory_snapshots (source_inventory_revision TEXT PRIMARY KEY,schema_version TEXT NOT NULL,created_at TEXT NOT NULL,inventory_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations_v2_metric_catalogue_snapshots (metric_catalogue_revision TEXT PRIMARY KEY,schema_version TEXT NOT NULL,created_at TEXT NOT NULL,catalogue_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations_v2_captures (capture_id TEXT PRIMARY KEY,resort_id TEXT NOT NULL,source_id TEXT NOT NULL,source_layer TEXT NOT NULL,source_role TEXT NOT NULL,retrieved_at TEXT NOT NULL,response_at TEXT,source_reported_at TEXT,operational_date TEXT,http_status INTEGER,content_type TEXT,payload_hash TEXT,raw_payload_ref TEXT,parser_version TEXT NOT NULL,retrieval_status TEXT NOT NULL,freshness_minutes REAL,asset_registry_revision TEXT NOT NULL,source_inventory_revision TEXT NOT NULL,metric_catalogue_revision TEXT NOT NULL,warnings_json TEXT NOT NULL,FOREIGN KEY(payload_hash) REFERENCES operations_raw_payloads(payload_hash),FOREIGN KEY(asset_registry_revision) REFERENCES operations_v2_registry_snapshots(registry_revision),FOREIGN KEY(source_inventory_revision) REFERENCES operations_v2_source_inventory_snapshots(source_inventory_revision),FOREIGN KEY(metric_catalogue_revision) REFERENCES operations_v2_metric_catalogue_snapshots(metric_catalogue_revision));
CREATE INDEX IF NOT EXISTS operations_v2_capture_resort_time ON operations_v2_captures(resort_id,retrieved_at DESC);
CREATE INDEX IF NOT EXISTS operations_v2_capture_source_time ON operations_v2_captures(source_id,retrieved_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_assets (registry_revision TEXT NOT NULL,asset_id TEXT NOT NULL,resort_id TEXT NOT NULL,asset_class TEXT NOT NULL,registry_json TEXT NOT NULL,PRIMARY KEY(registry_revision,asset_id),FOREIGN KEY(registry_revision) REFERENCES operations_v2_registry_snapshots(registry_revision));
CREATE INDEX IF NOT EXISTS operations_v2_assets_resort_class ON operations_v2_assets(resort_id,asset_class);
CREATE TABLE IF NOT EXISTS operations_v2_metric_observations (observation_id TEXT PRIMARY KEY,capture_id TEXT NOT NULL,resort_id TEXT NOT NULL,metric_key TEXT NOT NULL,observed_at TEXT,observation_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id));
CREATE INDEX IF NOT EXISTS operations_v2_metric_resort_time ON operations_v2_metric_observations(resort_id,metric_key,observed_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_snowmaking_observations (observation_id TEXT PRIMARY KEY,capture_id TEXT NOT NULL,resort_id TEXT NOT NULL,asset_registry_revision TEXT NOT NULL,subject_asset_id TEXT,signal_type TEXT NOT NULL,observed_at TEXT,observation_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id),FOREIGN KEY(asset_registry_revision,subject_asset_id) REFERENCES operations_v2_assets(registry_revision,asset_id));
CREATE INDEX IF NOT EXISTS operations_v2_snowmaking_resort_time ON operations_v2_snowmaking_observations(resort_id,signal_type,observed_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_asset_status_observations (observation_id TEXT PRIMARY KEY,capture_id TEXT NOT NULL,resort_id TEXT NOT NULL,asset_registry_revision TEXT NOT NULL,asset_id TEXT,upstream_asset_id TEXT,asset_class TEXT NOT NULL,observed_at TEXT,observation_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id),FOREIGN KEY(asset_registry_revision,asset_id) REFERENCES operations_v2_assets(registry_revision,asset_id));
CREATE INDEX IF NOT EXISTS operations_v2_asset_status_asset_time ON operations_v2_asset_status_observations(asset_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS operations_v2_asset_status_resort_time ON operations_v2_asset_status_observations(resort_id,asset_class,observed_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_aggregate_observations (observation_id TEXT PRIMARY KEY,capture_id TEXT NOT NULL,resort_id TEXT NOT NULL,asset_class TEXT NOT NULL,status TEXT NOT NULL,observed_at TEXT,observation_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id));
CREATE INDEX IF NOT EXISTS operations_v2_aggregate_resort_time ON operations_v2_aggregate_observations(resort_id,asset_class,observed_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_narratives (observation_id TEXT PRIMARY KEY,capture_id TEXT NOT NULL,resort_id TEXT NOT NULL,narrative_type TEXT NOT NULL,observed_at TEXT,observation_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id));
CREATE INDEX IF NOT EXISTS operations_v2_narrative_resort_time ON operations_v2_narratives(resort_id,narrative_type,observed_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_conflicts (conflict_id TEXT PRIMARY KEY,resort_id TEXT NOT NULL,field_family TEXT NOT NULL,subject_id TEXT,detected_at TEXT NOT NULL,status TEXT NOT NULL,conflict_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS operations_v2_conflict_resort_time ON operations_v2_conflicts(resort_id,detected_at DESC);
CREATE TABLE IF NOT EXISTS operations_v2_raw_descriptors (descriptor_id TEXT PRIMARY KEY,capture_id TEXT NOT NULL UNIQUE,payload_hash TEXT NOT NULL,source_id TEXT NOT NULL,raw_payload_ref TEXT NOT NULL,descriptor_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id));
CREATE INDEX IF NOT EXISTS operations_v2_raw_descriptor_hash ON operations_v2_raw_descriptors(payload_hash,source_id);
CREATE TABLE IF NOT EXISTS operations_v2_capture_diagnostics (capture_id TEXT PRIMARY KEY,diagnostics_json TEXT NOT NULL,FOREIGN KEY(capture_id) REFERENCES operations_v2_captures(capture_id));
"""

def migrate_v2(connection: sqlite3.Connection) -> None:
    connection.executescript(DDL)
    connection.commit()
