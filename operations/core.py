"""Normalized collection, archival storage, and export for operations signals.

The code is intentionally stdlib + requests: it runs alongside the canonical
Phase-1 collector and keeps collection failures local to their source.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from . import EXPORT_SCHEMA_VERSION, SCHEMA_VERSION

TZ = ZoneInfo("Australia/Sydney")
ROOT = Path(__file__).resolve().parents[1]
# Operations owns a dedicated database so its high-frequency, append-only
# archive does not collide with the Phase-1 forecast/actuals collector.
DEFAULT_DB = ROOT / "data" / "operations.sqlite"
LEGACY_DB = ROOT / "data" / "snow.db"
DEFAULT_RAW_DIR = ROOT / "data" / "operations" / "raw"
DEFAULT_EXPORT = ROOT / "data" / "operations_export_v1.json"
UA = "snow-pred-accu-operations/1 (+public operational telemetry; contact: repository owner)"

OBSERVED_STATUSES = {"active", "inactive", "mentioned", "none_flagged", "unavailable", "unknown"}
LAYERS = {"resort_report", "mountainops_runs", "resort_trails", "resort_lifts", "manual_observation"}


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def as_number(value: Any) -> float | int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip())
        return int(result) if result.is_integer() else result
    except (ValueError, TypeError):
        return None


def text(value: Any) -> str | None:
    if value is None:
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result or None


def boolish(value: Any) -> bool | None:
    value = text(value)
    if value is None:
        return None
    if value.lower() in {"yes", "y", "true", "open", "active", "on", "in progress"}:
        return True
    if value.lower() in {"no", "n", "false", "closed", "inactive", "off"}:
        return False
    return None


def narrative_text(value: Any) -> str | None:
    plain = re.sub(r"<[^>]*>", " ", str(value or ""))
    return text(plain)


def status_from_text(value: Any) -> str:
    """Only explicit on/in-progress/off language maps to active/inactive."""
    value = (text(value) or "").lower()
    if not value:
        return "unavailable"
    if value in {"on", "yes", "true", "active"} or any(token in value for token in ("in progress", "operating", "running", "guns on", "snowmaking on")):
        return "active"
    if any(token in value for token in ("stopped", "snowmaking off", "guns off", "not operating", "inactive")):
        return "inactive"
    if "snowmaking" in value or "snow-making" in value or "gun" in value:
        return "mentioned"
    return "unknown"


def parse_local(value: Any, formats: tuple[str, ...] = ()) -> str | None:
    raw = text(value)
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(raw, fmt).replace(tzinfo=TZ)
            return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return None


def combined_local(date: Any, clock: Any) -> str | None:
    return parse_local(f"{text(date) or ''} {text(clock) or ''}".strip(), (
        "%d %B %Y %I:%M %p", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M UTC",
    ))


@dataclass
class Snapshot:
    resortId: str
    layer: str
    source: str
    sourceUrl: str
    capturedAt: str
    snowmakingStatus: str
    sourceReportedAt: str | None = None
    sourceRecordId: str | None = None
    snowmakingRunCount: int | None = None
    snowGunCount: int | None = None
    machineMadeDepthCm: float | int | None = None
    naturalDepthCm: float | int | None = None
    openLiftCount: int | None = None
    openRunCount: int | None = None
    groomedRunCount: int | None = None
    activeSnowmakingRuns: list[dict[str, Any]] = field(default_factory=list)
    narrative: str | None = None
    retrievalStatus: str = "ok"
    warnings: list[str] = field(default_factory=list)
    rawPayloadRef: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    # Full normalized run/trail rows live in SQLite; the public snapshot keeps
    # only activeSnowmakingRuns so its read API stays compact.
    runs: list[dict[str, Any]] = field(default_factory=list, repr=False)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def payload(self) -> dict[str, Any]:
        if self.snowmakingStatus not in OBSERVED_STATUSES:
            raise ValueError(f"Invalid observed status {self.snowmakingStatus}")
        if self.layer not in LAYERS:
            raise ValueError(f"Invalid operations layer {self.layer}")
        return {
            "schemaVersion": SCHEMA_VERSION, "id": self.id, "resortId": self.resortId,
            "capturedAt": self.capturedAt, "sourceReportedAt": self.sourceReportedAt,
            "layer": self.layer, "source": self.source, "sourceUrl": self.sourceUrl,
            "sourceRecordId": self.sourceRecordId, "snowmakingStatus": self.snowmakingStatus,
            "snowmakingRunCount": self.snowmakingRunCount, "snowGunCount": self.snowGunCount,
            "machineMadeDepthCm": self.machineMadeDepthCm, "naturalDepthCm": self.naturalDepthCm,
            "openLiftCount": self.openLiftCount, "openRunCount": self.openRunCount,
            "groomedRunCount": self.groomedRunCount, "activeSnowmakingRuns": self.activeSnowmakingRuns,
            "narrative": self.narrative, "retrievalStatus": self.retrievalStatus,
            "warnings": self.warnings, "rawPayloadRef": self.rawPayloadRef,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class SourceSpec:
    name: str
    resort_id: str
    layer: str
    url: str
    parser: Callable[[Any, str], Snapshot]
    kind: str


def report_falls(raw: Any, captured: str) -> Snapshot:
    patrol = raw.get("Patrol") or {}
    slope = raw.get("SlopeMaintenance") or {}
    overview = raw.get("Overview") or {}
    current = slope.get("CurrentStatus")
    status = status_from_text(current)
    narrative = narrative_text(overview.get("ReportAnnouncements") or overview.get("ReportWhatsHappening"))
    if status == "unavailable" and narrative and re.search(r"snow[ -]?making|guns?", narrative, re.I):
        status = "mentioned"
    reported = combined_local(slope.get("LastUpdateDate"), slope.get("LastUpdateTime"))
    reported = reported or combined_local(patrol.get("PatrolDate"), patrol.get("PatrolTime"))
    return Snapshot("falls", "resort_report", "falls_official_report", FALLS_URL, captured, status,
        sourceReportedAt=reported, naturalDepthCm=as_number(patrol.get("PatrolNaturalSnowDepth")),
        narrative=narrative, provenance={"slopeMaintenanceStatus": current, "freshSnowCm": as_number(patrol.get("PatrolFreshSnow"))})


def report_hotham(raw: str, captured: str) -> Snapshot:
    root = ET.fromstring(raw)
    val = lambda tag: text(root.findtext(tag))
    snowmaking = val("Snowmaking")
    run_count = as_number(val("RunsSnowmaking"))
    status = status_from_text(snowmaking)
    if isinstance(run_count, (int, float)) and run_count > 0:
        status = "active"
    return Snapshot("hotham", "resort_report", "hotham_official_report", HOTHAM_URL, captured, status,
        sourceReportedAt=parse_local(val("_LastUpdated")), snowmakingRunCount=int(run_count) if run_count is not None else None,
        machineMadeDepthCm=as_number(val("AvSnowdepthinSnowmakingAreas")), naturalDepthCm=as_number(val("CurrentSnowdepth")),
        narrative=narrative_text(val("ResortReport")), provenance={"snowmakingText": snowmaking, "freshSnowCm": as_number(val("TwentyFourHourSnowfall"))})


def report_perisher(raw: str, captured: str) -> Snapshot:
    root = ET.fromstring(raw)
    val = lambda tag: text(root.findtext(tag))
    guns = as_number(val("snow_guns"))
    warnings: list[str] = []
    status = "unavailable"
    if isinstance(guns, (int, float)) and guns > 0:
        status = "active"
        warnings.append("snow_guns is treated as an operating count from current report context; retain raw field and re-check upstream semantics if its meaning changes.")
    elif guns is not None:
        status = "unknown"
        warnings.append("snow_guns is zero/ambiguous; it is not interpreted as plant off.")
    narrative = narrative_text(val("today"))
    if status == "unavailable" and narrative and re.search(r"snow[ -]?making|guns?", narrative, re.I):
        status = "mentioned"
    return Snapshot("perisher", "resort_report", "perisher_official_report", PERISHER_URL, captured, status,
        sourceReportedAt=parse_local(val("date"), ("%d/%m/%Y %H:%M",)), snowGunCount=int(guns) if guns is not None else None,
        naturalDepthCm=as_number(val("snowdepth")), groomedRunCount=int(as_number(val("groomed_runs")) or 0) if as_number(val("groomed_runs")) is not None else None,
        openLiftCount=int(as_number(val("lifts_number")) or 0) if as_number(val("lifts_number")) is not None else None,
        narrative=narrative, warnings=warnings, provenance={"snowGunsRaw": val("snow_guns")})


def report_buller(raw: Any, captured: str) -> Snapshot:
    report = raw.get("snow_report") or {}
    narrative = narrative_text(report.get("weather_description"))
    explicit = report.get("snowmaking") if report.get("snowmaking") is not None else raw.get("snowmaking")
    # A weather narrative that says nothing about snowmaking is not an
    # ambiguous snowmaking field. It is simply unavailable for this capture.
    status = status_from_text(explicit) if explicit is not None else (
        status_from_text(narrative) if narrative and re.search(r"snow[ -]?making|guns?", narrative, re.I) else "unavailable"
    )
    return Snapshot("buller", "resort_report", "buller_weather_widget", BULLER_WIDGET_URL, captured, status,
        sourceReportedAt=parse_local(raw.get("last_updated")), machineMadeDepthCm=as_number(report.get("average_made")),
        naturalDepthCm=as_number(report.get("average_natural")), openLiftCount=as_number(raw.get("open_lifts_count")),
        openRunCount=as_number(raw.get("open_trails_count")), narrative=narrative,
        warnings=["Widget update time is a source-refresh time, not a physical snow-patrol measurement timestamp."],
        provenance={"widgetUpdatedAt": raw.get("last_updated"), "snowmakingField": report.get("snowmaking") or raw.get("snowmaking")})


def report_bawbaw(raw: str, captured: str) -> Snapshot:
    narrative = narrative_text(raw)
    status = "mentioned" if narrative and re.search(r"snow[ -]?making|snow guns?", narrative, re.I) else "unavailable"
    return Snapshot("bawbaw", "resort_report", "bawbaw_public_report", BAWBAW_URL, captured, status,
        narrative=narrative[:2000] if narrative else None,
        warnings=["No structured current snowmaking field was found; narrative is preserved without inferring activation."],
        provenance={"sourceSurface": "public report page"})


def _run_status(value: Any) -> str:
    # Boolean/no values in operational run feeds are flags, not an affirmative
    # plant shutdown claim.  Zero active flags therefore remains none_flagged.
    return "active" if boolish(value) is True else "none_flagged"


def runs_mountainops(resort: str, url: str, raw: Any, captured: str) -> Snapshot:
    rows = raw if isinstance(raw, list) else []
    active_runs: list[dict[str, Any]] = []; normalized_runs: list[dict[str, Any]] = []
    open_runs = groomed = 0
    flags_seen = False
    warnings: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        flagged = row.get("Snowmaking")
        if flagged is not None:
            flags_seen = True
        status = _run_status(flagged)
        run_status = text(row.get("RunStatus"))
        groom = boolish(row.get("Groomed"))
        if (run_status or "").lower() == "open": open_runs += 1
        if groom is True: groomed += 1
        run = {"canonicalRunId": None, "upstreamRunId": str(row.get("ID")) if row.get("ID") is not None else None,
               "name": text(row.get("Name")) or "Unnamed run", "area": text(row.get("Location")),
               "runStatus": run_status, "snowmakingStatus": status, "groomed": groom,
               "capturedAt": captured, "source": f"{resort}_mountainops_runs", "sourceUrl": url}
        normalized_runs.append(run)
        if status == "active": active_runs.append(run)
    status = "active" if active_runs else "none_flagged" if flags_seen else "unavailable"
    if status == "none_flagged": warnings.append("No run-level active snowmaking flags; this does not assert the plant was off or that the feed is comprehensive.")
    return Snapshot(resort, "mountainops_runs", f"{resort}_mountainops_runs", url, captured, status,
        openRunCount=open_runs, groomedRunCount=groomed, activeSnowmakingRuns=active_runs,
        warnings=warnings, provenance={"upstreamRowCount": len(rows), "flagField": "Snowmaking"}, runs=normalized_runs)


def trails_buller(raw: Any, captured: str) -> Snapshot:
    rows = raw if isinstance(raw, list) else []
    active_runs: list[dict[str, Any]] = []; normalized_runs: list[dict[str, Any]] = []
    flags_seen = False
    open_runs = groomed = 0
    for row in rows:
        if not isinstance(row, dict): continue
        flag = row.get("snowmaking")
        if flag is not None: flags_seen = True
        status = _run_status(flag)
        run_status = text(row.get("status")); groom = boolish(row.get("grooming"))
        if (run_status or "").lower() == "open": open_runs += 1
        if groom is True: groomed += 1
        run = {"canonicalRunId": None, "upstreamRunId": str(row.get("id")) if row.get("id") is not None else None,
               "name": text(row.get("name")) or "Unnamed trail", "area": text(row.get("area")),
               "runStatus": run_status, "snowmakingStatus": status, "groomed": groom,
               "capturedAt": captured, "source": "buller_trails", "sourceUrl": BULLER_TRAILS_URL}
        normalized_runs.append(run)
        if status == "active": active_runs.append(run)
    status = "active" if active_runs else "none_flagged" if flags_seen else "unavailable"
    return Snapshot("buller", "resort_trails", "buller_trails", BULLER_TRAILS_URL, captured, status,
        openRunCount=open_runs, groomedRunCount=groomed, activeSnowmakingRuns=active_runs,
        warnings=[] if status != "none_flagged" else ["No active per-trail flags; this does not mean snowmaking was off."],
        provenance={"upstreamRowCount": len(rows), "flagField": "snowmaking"}, runs=normalized_runs)


def lifts(resort: str, source: str, url: str, raw: Any, captured: str) -> Snapshot:
    rows = raw if isinstance(raw, list) else []
    open_count = 0
    for row in rows:
        if isinstance(row, dict) and any((text(row.get(key)) or "").lower() == "open" for key in ("LiftStatus", "status", "Status")):
            open_count += 1
    return Snapshot(resort, "resort_lifts", source, url, captured, "unavailable", openLiftCount=open_count,
        warnings=["Lift feed has no snowmaking field; status is unavailable, not inactive."],
        provenance={"upstreamRowCount": len(rows)})


def trails_thredbo(raw: Any, captured: str) -> Snapshot:
    rows = raw if isinstance(raw, list) else []
    return Snapshot("thredbo_top", "resort_trails", "thredbo_trails", THREDBO_TRAILS_URL, captured, "unavailable",
        warnings=["Current public Thredbo trail/lift feed exposes operational state but no defensible snowmaking metric."],
        provenance={"upstreamRowCount": len(rows), "testedFieldFamilies": ["acf", "global_lift_trail_main_status"]})


FALLS_URL = "https://www.fallscreek.com.au/wp-content/uploads/FCSnowReport.json"
HOTHAM_URL = "https://snowreport.mthotham.com.au/resources/SnowReport.xml"
PERISHER_URL = "https://www.perisher.com.au/media_files/snowreport12.xml"
BULLER_WIDGET_URL = "https://api.mtbuller.com.au/api/weather/widget"
BULLER_TRAILS_URL = "https://api.mtbuller.com.au/api/trails"
BULLER_LIFTS_URL = "https://api.mtbuller.com.au/api/lifts"
THREDBO_TRAILS_URL = "https://www.thredbo.com.au/wp-json/thredbo/v1/get-lifts-and-trails"
BAWBAW_URL = "https://www.mountbawbaw.com.au/reports/snow-weather/"

SOURCES: list[SourceSpec] = [
    SourceSpec("falls_official_report", "falls", "resort_report", FALLS_URL, report_falls, "json"),
    SourceSpec("hotham_official_report", "hotham", "resort_report", HOTHAM_URL, report_hotham, "text"),
    SourceSpec("perisher_official_report", "perisher", "resort_report", PERISHER_URL, report_perisher, "text"),
    SourceSpec("buller_weather_widget", "buller", "resort_report", BULLER_WIDGET_URL, report_buller, "json"),
    SourceSpec("bawbaw_public_report", "bawbaw", "resort_report", BAWBAW_URL, report_bawbaw, "text"),
    SourceSpec("falls_mountainops_runs", "falls", "mountainops_runs", "https://fc-mountainops.vailresorts.com.au/api/public/run-status", lambda raw, cap: runs_mountainops("falls", "https://fc-mountainops.vailresorts.com.au/api/public/run-status", raw, cap), "json"),
    SourceSpec("hotham_mountainops_runs", "hotham", "mountainops_runs", "https://mh-mountainops.vailresorts.com.au/api/public/run-status", lambda raw, cap: runs_mountainops("hotham", "https://mh-mountainops.vailresorts.com.au/api/public/run-status", raw, cap), "json"),
    SourceSpec("perisher_mountainops_runs", "perisher", "mountainops_runs", "https://pb-mountainops.vailresorts.com.au/api/public/run-status", lambda raw, cap: runs_mountainops("perisher", "https://pb-mountainops.vailresorts.com.au/api/public/run-status", raw, cap), "json"),
    SourceSpec("falls_mountainops_lifts", "falls", "resort_lifts", "https://fc-mountainops.vailresorts.com.au/api/public/lift-status", lambda raw, cap: lifts("falls", "falls_mountainops_lifts", "https://fc-mountainops.vailresorts.com.au/api/public/lift-status", raw, cap), "json"),
    SourceSpec("hotham_mountainops_lifts", "hotham", "resort_lifts", "https://mh-mountainops.vailresorts.com.au/api/public/lift-status", lambda raw, cap: lifts("hotham", "hotham_mountainops_lifts", "https://mh-mountainops.vailresorts.com.au/api/public/lift-status", raw, cap), "json"),
    SourceSpec("perisher_mountainops_lifts", "perisher", "resort_lifts", "https://pb-mountainops.vailresorts.com.au/api/public/lift-status", lambda raw, cap: lifts("perisher", "perisher_mountainops_lifts", "https://pb-mountainops.vailresorts.com.au/api/public/lift-status", raw, cap), "json"),
    SourceSpec("buller_trails", "buller", "resort_trails", BULLER_TRAILS_URL, trails_buller, "json"),
    SourceSpec("buller_lifts", "buller", "resort_lifts", BULLER_LIFTS_URL, lambda raw, cap: lifts("buller", "buller_lifts", BULLER_LIFTS_URL, raw, cap), "json"),
    SourceSpec("thredbo_trails", "thredbo_top", "resort_trails", THREDBO_TRAILS_URL, trails_thredbo, "json"),
]


def validate_canonical_ids() -> None:
    path = Path(os.environ.get("ALPINE_RESORT_IDENTITIES_PATH", "/Users/jamesclapham/Projects/Alpine-Weather-Dashboard/contracts/v1/alpine-resort-identities.json"))
    if not path.exists():
        return  # deployment may consume the already-versioned export remotely
    ids = {entry["canonicalId"] for entry in json.loads(path.read_text())["resorts"]}
    bad = {spec.resort_id for spec in SOURCES} - ids
    if bad:
        raise RuntimeError(f"Operations source has no Phase-0 canonical resort ID: {sorted(bad)}")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS operations_raw_payloads (
 payload_hash TEXT PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
 first_captured_at TEXT NOT NULL, response_at TEXT NOT NULL, http_status INTEGER,
 content_type TEXT, archive_ref TEXT NOT NULL, parser_version INTEGER NOT NULL,
 normalized_record_ids TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations_snapshots (
 id TEXT PRIMARY KEY, resort_id TEXT NOT NULL, source TEXT NOT NULL, layer TEXT NOT NULL,
 captured_at TEXT NOT NULL, source_reported_at TEXT, payload_hash TEXT,
 retrieval_status TEXT NOT NULL, snowmaking_status TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 FOREIGN KEY(payload_hash) REFERENCES operations_raw_payloads(payload_hash)
);
CREATE INDEX IF NOT EXISTS operations_snapshot_latest ON operations_snapshots(resort_id, layer, captured_at DESC);
CREATE INDEX IF NOT EXISTS operations_snapshot_source ON operations_snapshots(source, captured_at DESC);
CREATE TABLE IF NOT EXISTS operations_runs (
 snapshot_id TEXT NOT NULL, upstream_run_id TEXT, name TEXT NOT NULL, area TEXT,
 run_status TEXT, snowmaking_status TEXT NOT NULL, groomed INTEGER, source TEXT NOT NULL,
 source_url TEXT NOT NULL, captured_at TEXT NOT NULL,
 FOREIGN KEY(snapshot_id) REFERENCES operations_snapshots(id)
);
"""


def _portable_archive_ref(value: str | Path | None) -> str | None:
    """Keep archive references usable after an Actions checkout is committed."""
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            return str(path)
    return path.as_posix()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _migrate_legacy_operations(con: sqlite3.Connection, db_path: Path) -> None:
    """One-time copy of Phase-3 rows collected before the dedicated DB existed."""
    if db_path.resolve() != DEFAULT_DB.resolve() or not LEGACY_DB.exists():
        return
    if con.execute("SELECT COUNT(*) FROM operations_snapshots").fetchone()[0]:
        return
    legacy = sqlite3.connect(LEGACY_DB)
    try:
        if not _table_exists(legacy, "operations_snapshots"):
            return
        if _table_exists(legacy, "operations_raw_payloads"):
            for row in legacy.execute("SELECT * FROM operations_raw_payloads"):
                values = list(row)
                values[7] = _portable_archive_ref(values[7])
                con.execute("INSERT OR IGNORE INTO operations_raw_payloads VALUES (?,?,?,?,?,?,?,?,?,?)", values)
        for row in legacy.execute("SELECT * FROM operations_snapshots"):
            values = list(row)
            payload = json.loads(values[9])
            payload["rawPayloadRef"] = _portable_archive_ref(payload.get("rawPayloadRef"))
            values[9] = json.dumps(payload, sort_keys=True)
            con.execute("INSERT OR IGNORE INTO operations_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", values)
        if _table_exists(legacy, "operations_runs"):
            for row in legacy.execute("SELECT * FROM operations_runs"):
                con.execute("INSERT INTO operations_runs VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        con.commit()
    finally:
        legacy.close()


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA_SQL)
    _migrate_legacy_operations(con, db_path)
    return con


def archive_raw(raw_dir: Path, source: str, url: str, captured: str, response_at: str, status: int | None, content_type: str | None, body: str, ids: list[str]) -> tuple[str, str]:
    digest = hashlib.sha256(body.encode()).hexdigest()
    day = captured[:10]
    destination = raw_dir / day / source / f"{digest}.json"
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {"url": url, "capturedAt": captured, "responseAt": response_at, "httpStatus": status,
                   "contentType": content_type, "payloadHash": digest, "parserVersion": SCHEMA_VERSION,
                   "normalizedRecordIds": ids, "body": body}
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temporary.replace(destination)
    return digest, _portable_archive_ref(destination) or str(destination)


def save_snapshot(con: sqlite3.Connection, snapshot: Snapshot, payload_hash: str | None, raw_ref: str | None, response_at: str | None = None, http_status: int | None = None, content_type: str | None = None) -> None:
    snapshot.rawPayloadRef = raw_ref
    payload = snapshot.payload()
    con.execute("INSERT INTO operations_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", (
        snapshot.id, snapshot.resortId, snapshot.source, snapshot.layer, snapshot.capturedAt, snapshot.sourceReportedAt,
        payload_hash, snapshot.retrievalStatus, snapshot.snowmakingStatus, json.dumps(payload, sort_keys=True),
    ))
    con.executemany("INSERT INTO operations_runs VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (snapshot.id, run["upstreamRunId"], run["name"], run["area"], run["runStatus"], run["snowmakingStatus"],
         None if run["groomed"] is None else int(run["groomed"]), run["source"], run["sourceUrl"], run["capturedAt"])
        for run in (snapshot.runs or snapshot.activeSnowmakingRuns)
    ])
    con.commit()


def record_raw(con: sqlite3.Connection, digest: str, source: SourceSpec, captured: str, response_at: str, status: int | None, content_type: str | None, archive_ref: str, ids: list[str]) -> None:
    con.execute("INSERT OR IGNORE INTO operations_raw_payloads VALUES (?,?,?,?,?,?,?,?,?,?)", (
        digest, source.name, source.url, captured, response_at, status, content_type, archive_ref, SCHEMA_VERSION, json.dumps(ids),
    ))
    existing = con.execute("SELECT normalized_record_ids FROM operations_raw_payloads WHERE payload_hash=?", (digest,)).fetchone()
    if existing:
        prior = json.loads(existing[0])
        merged = list(dict.fromkeys([*prior, *ids]))
        con.execute("UPDATE operations_raw_payloads SET normalized_record_ids=? WHERE payload_hash=?", (json.dumps(merged), digest))
    con.commit()


def fetch_source(spec: SourceSpec, raw_dir: Path, con: sqlite3.Connection, session: requests.Session) -> tuple[Snapshot, bool]:
    captured = iso_now()
    try:
        response = session.get(spec.url, headers={"User-Agent": UA, "Accept": "application/json,text/xml,text/html;q=0.9"}, timeout=(5, 18))
        response_at = iso_now()
        body = response.text
        if not response.ok:
            raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
        raw = response.json() if spec.kind == "json" else body
        snapshot = spec.parser(raw, captured)
        snapshot.provenance.update({"httpStatus": response.status_code, "contentType": response.headers.get("content-type"), "parserVersion": SCHEMA_VERSION})
        digest, raw_ref = archive_raw(raw_dir, spec.name, spec.url, captured, response_at, response.status_code, response.headers.get("content-type"), body, [snapshot.id])
        record_raw(con, digest, spec, captured, response_at, response.status_code, response.headers.get("content-type"), raw_ref, [snapshot.id])
        save_snapshot(con, snapshot, digest, raw_ref)
        return snapshot, False
    except Exception as exc:
        # A failed source is a visible failed capture, never a false inactive/zero observation.
        snapshot = Snapshot(spec.resort_id, spec.layer, spec.name, spec.url, captured, "unknown", retrievalStatus="failed",
            warnings=[f"Fetch/parser failure: {type(exc).__name__}: {exc}"], provenance={"parserVersion": SCHEMA_VERSION})
        save_snapshot(con, snapshot, None, None)
        return snapshot, True


def latest_snapshots(con: sqlite3.Connection, resort_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE resort_id=?" if resort_id else ""
    args = (resort_id,) if resort_id else ()
    rows = con.execute(f"""SELECT snapshot_json FROM operations_snapshots {where}
        ORDER BY captured_at DESC""", args).fetchall()
    seen: set[tuple[str, str]] = set(); out = []
    for (raw,) in rows:
        snap = json.loads(raw); key = (snap["resortId"], snap["layer"])
        if key not in seen:
            out.append(snap); seen.add(key)
    return out


def history(con: sqlite3.Connection, resort_id: str, start: str, end: str, limit: int = 2500) -> list[dict[str, Any]]:
    rows = con.execute("SELECT snapshot_json FROM operations_snapshots WHERE resort_id=? AND captured_at>=? AND captured_at<=? ORDER BY captured_at ASC LIMIT ?", (resort_id, start, end, limit)).fetchall()
    return [json.loads(row[0]) for row in rows]


def planned_cadence_minutes(at: dt.datetime) -> int:
    """The documented scheduler cadence, evaluated in the alpine timezone."""
    local = at.astimezone(TZ)
    return 30 if local.hour >= 15 or local.hour < 10 else 60


def _expected_captures(stamps: list[dt.datetime], expected_minutes: int | None) -> tuple[int, str]:
    if not stamps:
        return 0, "configured"
    if expected_minutes is not None:
        span = max(0, (stamps[-1] - stamps[0]).total_seconds() / 60)
        return max(1, round(span / expected_minutes) + 1), f"fixed_{expected_minutes}m"
    units = 0.0
    for start, end in zip(stamps, stamps[1:]):
        cursor = start
        while cursor < end:
            local = cursor.astimezone(TZ)
            boundary = (local.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).astimezone(dt.timezone.utc)
            step_end = min(end, boundary)
            units += (step_end - cursor).total_seconds() / 60 / planned_cadence_minutes(cursor)
            cursor = step_end
    return max(1, round(units) + 1), "schedule_30m_night_60m_day"


def _single_source_coverage(snapshots: list[dict[str, Any]], expected_minutes: int | None) -> dict[str, Any]:
    ok = [s for s in snapshots if s["retrievalStatus"] != "failed"]
    if not snapshots:
        return {"expectedCaptures": 0, "actualCaptures": 0, "coveragePct": None, "firstCapture": None, "lastCapture": None, "maximumGapMinutes": None, "statusCounts": {}, "parserFailures": 0}
    stamps = sorted(dt.datetime.fromisoformat(s["capturedAt"].replace("Z", "+00:00")) for s in snapshots)
    expected, cadence_mode = _expected_captures(stamps, expected_minutes)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(stamps, stamps[1:])]
    counts = {status: sum(s["snowmakingStatus"] == status for s in snapshots) for status in OBSERVED_STATUSES}
    return {"expectedCaptures": expected, "actualCaptures": len(ok), "coveragePct": round(min(100, len(ok) / expected * 100), 1),
            "firstCapture": snapshots[0]["capturedAt"], "lastCapture": snapshots[-1]["capturedAt"],
            "maximumGapMinutes": round(max(gaps), 1) if gaps else 0, "statusCounts": counts,
            "parserFailures": len(snapshots) - len(ok), "cadenceMinutes": expected_minutes,
            "cadenceMode": cadence_mode,
            "timingNote": "Status changes are interval-censored between captures; first observation is not an exact activation time."}


def coverage(snapshots: list[dict[str, Any]], expected_minutes: int | None = None) -> dict[str, Any]:
    """Aggregate source-aware coverage; parallel layers do not inflate one feed's cadence."""
    if not snapshots:
        return _single_source_coverage([], expected_minutes)
    groups: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        groups.setdefault(snapshot["source"], []).append(snapshot)
    pieces = {source: _single_source_coverage(sorted(rows, key=lambda row: row["capturedAt"]), expected_minutes) for source, rows in groups.items()}
    first = min((piece["firstCapture"] for piece in pieces.values() if piece["firstCapture"]), default=None)
    last = max((piece["lastCapture"] for piece in pieces.values() if piece["lastCapture"]), default=None)
    expected = sum(piece["expectedCaptures"] for piece in pieces.values())
    actual = sum(piece["actualCaptures"] for piece in pieces.values())
    counts = {status: sum(snapshot["snowmakingStatus"] == status for snapshot in snapshots) for status in OBSERVED_STATUSES}
    gaps = [piece["maximumGapMinutes"] for piece in pieces.values() if piece["maximumGapMinutes"] is not None]
    return {"expectedCaptures": expected, "actualCaptures": actual, "coveragePct": round(min(100, actual / expected * 100), 1) if expected else None,
            "firstCapture": first, "lastCapture": last, "maximumGapMinutes": max(gaps) if gaps else None,
            "statusCounts": counts, "parserFailures": sum(piece["parserFailures"] for piece in pieces.values()),
            "cadenceMinutes": expected_minutes, "cadenceMode": "schedule_30m_night_60m_day" if expected_minutes is None else f"fixed_{expected_minutes}m", "sourceCount": len(pieces), "bySource": pieces,
            "timingNote": "Coverage is calculated independently per source then aggregated. Status changes are interval-censored between captures; first observation is not an exact activation time."}


def activation_interval(snapshots: list[dict[str, Any]]) -> dict[str, str | None]:
    """Bound a transition rather than pretending poll time is event time."""
    ordered = sorted(snapshots, key=lambda row: row["capturedAt"])
    previous: dict[str, Any] | None = None
    for row in ordered:
        if row.get("snowmakingStatus") == "active" and (previous is None or previous.get("snowmakingStatus") != "active"):
            return {"earliest": previous.get("capturedAt") if previous else None, "latest": row["capturedAt"]}
        previous = row
    return {"earliest": None, "latest": None}


def disagreements(latest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_resort: dict[str, dict[str, dict[str, Any]]] = {}
    for snap in latest:
        by_resort.setdefault(snap["resortId"], {})[snap["layer"]] = snap
    out = []
    for resort, layers in by_resort.items():
        report, runs = layers.get("resort_report"), layers.get("mountainops_runs") or layers.get("resort_trails")
        if report and runs and report["snowmakingStatus"] == "active" and runs["snowmakingStatus"] == "none_flagged":
            out.append({"resortId": resort, "start": max(report["capturedAt"], runs["capturedAt"]), "end": None,
                        "reportStatus": report["snowmakingStatus"], "runFeedStatus": runs["snowmakingStatus"], "modelViability": "not_evaluated_by_collector",
                        "reportSource": report["source"], "runSource": runs["source"],
                        "notes": ["Report says active while run/trail feed has no active flags. Neither layer is promoted as the winner."]})
    return out


def export(con: sqlite3.Connection, out: Path = DEFAULT_EXPORT) -> Path:
    latest = latest_snapshots(con)
    resorts = sorted({snap["resortId"] for snap in latest})
    histories: dict[str, list[dict[str, Any]]] = {}
    diagnostics: dict[str, Any] = {}
    end = iso_now(); start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat().replace("+00:00", "Z")
    for resort in resorts:
        histories[resort] = history(con, resort, start, end)
        diagnostics[resort] = {"coverage": coverage(histories[resort]), "sampleSize": len(histories[resort]),
                               "interpretation": "early calibration / diagnostic; operational choice is not determined only by wet-bulb viability."}
    raw_payloads = [{"payloadHash": row[0], "source": row[1], "sourceUrl": row[2], "firstCapturedAt": row[3],
                     "responseAt": row[4], "httpStatus": row[5], "contentType": row[6], "archiveRef": row[7],
                     "parserVersion": row[8], "normalizedRecordIds": json.loads(row[9])}
                    for row in con.execute("SELECT payload_hash,source,source_url,first_captured_at,response_at,http_status,content_type,archive_ref,parser_version,normalized_record_ids FROM operations_raw_payloads ORDER BY first_captured_at DESC LIMIT 5000")]
    payload = {"schemaVersion": EXPORT_SCHEMA_VERSION, "identitySchemaVersion": "alpine.resort-identities.v1", "generatedAt": iso_now(),
               "producer": "snow-pred-accu operations collector", "latest": latest, "history": histories,
               "diagnostics": diagnostics, "disagreements": disagreements(latest),
               "rawPayloads": raw_payloads,
               "sourceInventory": [{"source": s.name, "resortId": s.resort_id, "layer": s.layer, "url": s.url} for s in SOURCES]}
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".tmp"); temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n"); temp.replace(out)
    return out


def collect_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect public resort snowmaking operations telemetry.")
    parser.add_argument("--resort", default="all", help="canonical resort id or all")
    parser.add_argument("--source", default="all", help="source id or all")
    parser.add_argument("--once", action="store_true", help="one bounded collection pass (default)")
    parser.add_argument("--out", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    validate_canonical_ids()
    selected = [spec for spec in SOURCES if (args.resort == "all" or spec.resort_id == args.resort) and (args.source == "all" or spec.name == args.source)]
    if not selected:
        parser.error("No configured source matched --resort/--source")
    con = connect(args.db); session = requests.Session(); failures = 0
    for spec in selected:
        snapshot, failed = fetch_source(spec, args.raw_dir, con, session)
        failures += int(failed)
        print(f"[{snapshot.retrievalStatus}] {spec.name:<28} {snapshot.resortId:<12} {snapshot.snowmakingStatus:<13} {', '.join(snapshot.warnings[:1])}")
    exported = export(con, args.out)
    print(f"exported {exported} ({len(selected) - failures}/{len(selected)} sources successful)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(collect_main())
