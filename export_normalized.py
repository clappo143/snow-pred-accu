"""Export a stable, provenance-preserving feed for Alpine Weather Dashboard.

The v1 contract is additive: consumers should ignore unknown fields and select
by ``schemaVersion`` rather than relying on the filename alone.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import store
from collectors.common import TZ
from resorts import RESORTS
from score import HEADLINE, accuracy, accuracy_by_lead

SCHEMA_VERSION = 1
OUT = Path(__file__).parent / "data" / "alpine_export_v1.json"
# This is deliberately a separate file from the older normalized export.
# Alpine's Phase-2 adapter refuses to coerce that legacy shape because it
# cannot prove layer selection or protected manual corrections.
OFFICIAL_REPORT_OUT = Path(__file__).parent / "data" / "official-report-export.v1.json"
OFFICIAL_REPORT_ARCHIVE_DIR = OFFICIAL_REPORT_OUT.parent / "official-report-exports"
OFFICIAL_REPORT_SCHEMA_VERSION = "alpine.official-report-export.v1"
IDENTITY_SCHEMA_VERSION = "alpine.resort-identities.v1"
OFFICIAL_REPORT_PRODUCER = "snow-pred-accu official-report exporter"
RESORT_ALIASES = {
    "perisher": "perisher", "thredbo": "thredbo_top", "hotham": "hotham",
    "fallscreek": "falls", "buller": "buller",
}
ALPINE_OWNED_RESORTS = ["bawbaw"]

# None means the provider does not state a point elevation. Approximate
# terrain/grid values are retained with a qualifier rather than presented as
# surveyed heights. See docs/reference-points.md.
REFERENCE_ELEVATIONS = {
    "snowforecast": [1867, 1701, 1652, 1690, 1590],
    "openmeteo": [1720, 1700, 1750, 1650, 1700],
    "yrno": [1720, 1700, 1750, 1650, 1700],
    "bom": [1737, 1785, 1624, 1637, 1661],
    "bom_meteye": [1737, 1785, 1624, 1637, 1661],
    "snowatch": [None] * 5,
    "mountainwatch": [1830, 1830, 1850, 1770, 1710],
    "janesweather": [1721, 1367, 1710, 1563, 1597],
}
RID_ORDER = list(RESORTS)


def weighted_median(values: list[tuple[float, float]]) -> float:
    """Weighted median matching dashboard.py's browser implementation."""
    ordered = sorted(values)
    total = sum(weight for _, weight in ordered)
    cumulative = 0.0
    for i, (value, weight) in enumerate(ordered):
        cumulative += weight
        if cumulative > total / 2:
            return value
        if cumulative == total / 2:
            return (value + ordered[min(i + 1, len(ordered) - 1)][0]) / 2
    return ordered[-1][0]


def _elevation(source: str, rid: str) -> tuple[int | None, str]:
    value = REFERENCE_ELEVATIONS.get(source, [None] * 5)[RID_ORDER.index(rid)]
    if source == "snowatch":
        return None, "resort-wide; provider states no point elevation"
    if source in {"bom", "bom_meteye", "janesweather"}:
        return value, "approximate terrain height at provider grid/reference point"
    return value, "provider-stated or explicitly requested elevation"


def _latest_series(con, rid: str, source: str) -> tuple[dict, dict[str, float]]:
    row = con.execute(
        "SELECT issued_date,run FROM forecasts WHERE resort=? AND source=? "
        "ORDER BY issued_date DESC,run DESC LIMIT 1", (rid, source),
    ).fetchone()
    if not row:
        return {}, {}
    issued, run = row
    values = dict(con.execute(
        "SELECT target_date,snow_cm FROM forecasts WHERE resort=? AND source=? "
        "AND issued_date=? AND run=? ORDER BY target_date",
        (rid, source, issued, run),
    ).fetchall())
    return {"issuedDate": issued, "run": run}, values


def _actuals(con, rid: str) -> list[dict]:
    rows = con.execute(
        "SELECT date,snow_cm,source,reported_at,report_time_kind,source_url,"
        "natural_depth,snow_7day,updated_at FROM actuals WHERE resort=? "
        "ORDER BY date", (rid,),
    ).fetchall()
    out = []
    for row in rows:
        date, snow, source, reported, kind, url, depth, seven, updated = row
        layers = []
        for obs in con.execute(
            "SELECT source,snow_cm,reported_at,report_time_kind,source_url,"
            "natural_depth,snow_7day,collected_at,raw_json,notes "
            "FROM actual_observations WHERE resort=? AND date=? ORDER BY id",
            (rid, date),
        ):
            raw = None
            if obs[8]:
                try:
                    raw = json.loads(obs[8])
                except json.JSONDecodeError:
                    raw = obs[8]
            layers.append({
                "source": obs[0], "snow24hCm": obs[1], "reportedAt": obs[2],
                "reportTimeKind": obs[3], "sourceUrl": obs[4],
                "naturalDepthCm": obs[5], "snow7dCm": obs[6],
                "collectedAt": obs[7], "raw": raw, "notes": obs[9],
            })
        out.append({
            "resortId": RESORT_ALIASES[rid], "collectorResortId": rid,
            "date": date, "snow24hCm": snow, "effectiveSource": source,
            "reportedAt": reported, "reportTimeKind": kind,
            "sourceUrl": url, "naturalDepthCm": depth, "snow7dCm": seven,
            "updatedAt": updated, "layers": layers,
        })
    return out


_SOURCE_METADATA = {
    "manual": ("manual", 400),
    "official": ("official_resort", 300),
    "snowatch": ("aggregator", 200),
    "onthesnow": ("fallback", 100),
}
_TIME_KINDS = {
    "documented_measurement", "patrol_observation", "report_publication",
}


def _canonical_source(source: str) -> tuple[str, int]:
    """Map store precedence to the shared official-report contract."""
    return _SOURCE_METADATA.get(source, ("other", 0))


def _value_status(value: float | None) -> str:
    if value is None:
        return "not_provided"
    return "observed_zero" if value == 0 else "observed_value"


def _values(snow: float | None, depth: float | None) -> dict:
    return {
        "snow24hCm": snow,
        "snow24hStatus": _value_status(snow),
        "depthCm": depth,
        "depthStatus": _value_status(depth),
        # snow-pred-accu does not collect an official report temperature.
        # Null is intentional: never manufacture it from an unrelated feed.
        "temperatureC": None,
    }


def _time_kind(value: str | None) -> str:
    return value if value in _TIME_KINDS else "unknown"


def _absolute_url(value: str | None) -> str | None:
    return value if value and "://" in value else None


def _manual_protection(source: str, snow: float | None, depth: float | None,
                       reported_at: str | None, notes: str | None = None) -> dict:
    if source != "manual":
        return {"protected": False, "fields": [], "reason": None}
    fields = []
    if snow is not None:
        fields.append("snow24hCm")
    if depth is not None:
        fields.append("depthCm")
    if reported_at:
        fields.append("reportedAt")
    return {
        "protected": True,
        "fields": fields,
        "reason": notes or "Protected manual correction.",
    }


def _freshness(source: str, report_date: str, assessed_at: str,
               has_provenance: bool) -> dict:
    """Be conservative: date age is visible, but never inferred as a parser error."""
    today = dt.datetime.now(TZ).date().isoformat()
    if source == "manual":
        return {"status": "fresh", "assessedAt": assessed_at,
                "reason": "Protected manual correction."}
    if not has_provenance:
        return {"status": "stale", "assessedAt": assessed_at,
                "reason": "Legacy effective record has no append-only source observation."}
    if report_date < today:
        return {"status": "stale", "assessedAt": assessed_at,
                "reason": "Historical report date."}
    return {"status": "unknown", "assessedAt": assessed_at,
            "reason": "Freshness cannot be inferred solely from collection time."}


def _raw(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _canonical_layer(*, layer_id: str, source: str, report_date: str,
                     snow: float | None, depth: float | None,
                     reported_at: str | None, report_time_kind: str | None,
                     source_url: str | None, scraped_at: str, raw_payload,
                     raw_ref: str | None, notes: str | None,
                     has_provenance: bool, collection_status: str = "success") -> dict:
    source_kind, source_rank = _canonical_source(source)
    parser_errors = []
    if source_url and not _absolute_url(source_url):
        parser_errors.append("Stored source URL is not absolute; omitted from canonical export.")
    layer = {
        "layerId": layer_id,
        "sourceKind": source_kind,
        "sourceName": source,
        "sourceRank": source_rank,
        "collectionStatus": collection_status,
        "values": _values(snow, depth),
        "reportedAt": reported_at,
        "reportTimeKind": _time_kind(report_time_kind),
        "sourceUrl": _absolute_url(source_url),
        "scrapedAt": scraped_at,
        "freshness": _freshness(source, report_date, scraped_at, has_provenance),
        "manualProtection": _manual_protection(source, snow, depth, reported_at, notes),
        "parserErrors": parser_errors,
        "rawPayloadRef": raw_ref,
    }
    if raw_payload is not None:
        layer["rawPayload"] = raw_payload
    return layer


def _pick_layer(layers: list[dict], source: str, field: str, value) -> str | None:
    """Return the latest source layer whose field actually matches the effective row."""
    candidates = [layer for layer in layers
                  if layer["sourceName"] == source and layer["values"].get(field) == value]
    return candidates[-1]["layerId"] if candidates else None


def build_official_report_export(con, generated_at: str | None = None) -> dict:
    """Render the strict Phase-1 contract consumed by Alpine Phase 2.

    The effective table is a precedence projection; the append-only ledger is
    retained as raw layers. When pre-v4 rows lack a matching ledger entry, a
    clearly stale projection layer makes that limitation explicit instead of
    silently claiming source provenance.
    """
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).isoformat()
    reports = []
    for collector_id in RESORTS:
        canonical_id = RESORT_ALIASES[collector_id]
        effective_rows = con.execute(
            "SELECT date,snow_cm,source,reported_at,report_time_kind,source_url,"
            "natural_depth,snow_7day,updated_at FROM actuals WHERE resort=? ORDER BY date",
            (collector_id,),
        ).fetchall()
        for date, snow, source, reported_at, kind, source_url, depth, _seven, updated_at in effective_rows:
            source_rows = con.execute(
                "SELECT id,source,snow_cm,reported_at,report_time_kind,source_url,"
                "natural_depth,collected_at,raw_json,notes "
                "FROM actual_observations WHERE resort=? AND date=? ORDER BY id",
                (collector_id, date),
            ).fetchall()
            layers = []
            for observation_id, layer_source, layer_snow, layer_reported, layer_kind, layer_url, layer_depth, collected, raw_json, notes in source_rows:
                layers.append(_canonical_layer(
                    layer_id=f"{canonical_id}|{date}|{layer_source}|{observation_id}",
                    source=layer_source, report_date=date, snow=layer_snow, depth=layer_depth,
                    reported_at=layer_reported, report_time_kind=layer_kind,
                    source_url=layer_url, scraped_at=collected, raw_payload=_raw(raw_json),
                    raw_ref=f"sqlite:actual_observations/{observation_id}", notes=notes,
                    has_provenance=True,
                ))

            snow_layer = _pick_layer(layers, source, "snow24hCm", snow)
            depth_layer = _pick_layer(layers, source, "depthCm", depth)
            reported_layer = next((layer["layerId"] for layer in reversed(layers)
                                   if layer["sourceName"] == source and layer["reportedAt"] == reported_at), None)
            if snow_layer is None:
                # Old database rows can predate the provenance ledger. Preserve
                # them without pretending they were a direct raw capture.
                projection_id = f"{canonical_id}|{date}|{source}|effective-projection"
                layers.append(_canonical_layer(
                    layer_id=projection_id, source=source, report_date=date,
                    snow=snow, depth=depth, reported_at=reported_at,
                    report_time_kind=kind, source_url=source_url,
                    scraped_at=updated_at or generated_at,
                    raw_payload={"effectiveRecord": True, "collectorResortId": collector_id,
                                 "date": date, "source": source},
                    raw_ref=f"sqlite:actuals/{collector_id}/{date}", notes=None,
                    has_provenance=False, collection_status="stale",
                ))
                snow_layer = depth_layer = reported_layer = projection_id

            selected = {
                "snow24hCm": snow_layer,
                "depthCm": depth_layer,
                "temperatureC": None,
                "reportedAt": reported_layer,
            }
            selected_ids = {x for x in selected.values() if x is not None}
            reports.append({
                "canonicalResortId": canonical_id,
                "reportDate": date,
                "layers": layers,
                "effective": {
                    "selectionMode": "single_layer" if len(selected_ids) <= 1 else "field_merged",
                    "selectedLayers": selected,
                    "selectionReason": (
                        "Protected manual correction outranks automated sources."
                        if source == "manual" else
                        f"Effective {source} row selected using store source precedence."
                    ),
                    "values": _values(snow, depth),
                    "reportedAt": reported_at,
                    "reportTimeKind": _time_kind(kind),
                },
            })
    return {
        "schemaVersion": OFFICIAL_REPORT_SCHEMA_VERSION,
        "identitySchemaVersion": IDENTITY_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "producer": OFFICIAL_REPORT_PRODUCER,
        "reports": reports,
    }


def build(con) -> dict:
    actual_rows: list[dict] = []
    forecast_rows: dict[str, dict] = {}
    run, lead = HEADLINE
    for rid, resort in RESORTS.items():
        actual_rows.extend(_actuals(con, rid))
        acc = accuracy(con, rid, run, lead)
        by_lead = accuracy_by_lead(con, rid)
        providers = {}
        active_values = {}
        latest_dates = []
        for source in sorted(REFERENCE_ELEVATIONS):
            snapshot, values = _latest_series(con, rid, source)
            if not snapshot:
                continue
            latest_dates.append(snapshot["issuedDate"])
            elev, qualifier = _elevation(source, rid)
            sample = next((x for x in by_lead.get(source, [])
                           if (x["run"], x["lead"]) == HEADLINE), None)
            providers[source] = {
                **snapshot, "values": values, "referenceElevationM": elev,
                "referenceElevationQualifier": qualifier,
                "accuracy": ({"pct": acc[source], "n": sample["n"],
                              "leadHours": sample["h"]} if sample else
                             {"pct": None, "n": 0, "leadHours": 13}),
            }
            active_values[source] = values
        dates = sorted({d for values in active_values.values() for d in values})
        operational = {}
        for date in dates:
            pairs = [(values[date], max(acc.get(source, 50.0), 1.0))
                     for source, values in active_values.items() if date in values]
            if pairs:
                operational[date] = round(weighted_median(pairs), 2)
        stored_snapshot, stored_values = _latest_series(con, rid, "ensemble")
        forecast_rows[RESORT_ALIASES[rid]] = {
            "collectorResortId": rid, "name": resort.name,
            "referenceElevationM": resort.alt,
            "latestIssuedDate": max(latest_dates, default=None),
            "providers": providers,
            "operationalConsensus": {
                "method": "accuracy_weighted_median", "values": operational,
            },
            "storedDiagnosticConsensus": {
                "method": "accuracy_weighted_mean", **stored_snapshot,
                "values": stored_values,
            },
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": dt.datetime.now(TZ).isoformat(),
        "resortAliases": RESORT_ALIASES,
        "alpineOwnedResorts": ALPINE_OWNED_RESORTS,
        "actuals": actual_rows,
        "forecasts": forecast_rows,
    }


def render(out: Path = OUT) -> Path:
    con = store.connect()
    payload = build(con)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    render_official_report_export(con)
    return out


def render_official_report_export(con=None, out: Path = OFFICIAL_REPORT_OUT) -> Path:
    """Write the latest strict export and an immutable content-addressed copy."""
    owns_connection = con is None
    con = con or store.connect()
    payload = build_official_report_export(con)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    out.parent.mkdir(exist_ok=True)
    out.write_text(content)
    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
    archive_path = OFFICIAL_REPORT_ARCHIVE_DIR / f"{fingerprint}.json"
    archive_path.parent.mkdir(exist_ok=True)
    if not archive_path.exists():
        archive_path.write_text(content)
    if owns_connection:
        con.close()
    return out


if __name__ == "__main__":
    print(render())
    print(OFFICIAL_REPORT_OUT)
