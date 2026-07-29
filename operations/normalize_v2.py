"""Registry-driven normalization for the isolated operations-v2 lane.

Identity contract
-----------------
Capture IDs hash ``sourceId + canonical retrievedAt + payloadHash``.  Replaying
the same retrieval envelope is therefore a no-op, while unchanged bytes polled
again at a later instant remain a distinct capture.  Every observation ID
includes its capture ID plus collection, canonical source path, subject and
ordinal.  No normalized identity is random and no identity depends on mutable
database state.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from .registry import expand_comma_delimited, load_registries, parse_wind_speed_range

PARSER_VERSION = "operations-v2-phase2b"
LOCAL_TZ = ZoneInfo("Australia/Melbourne")
COLLECTIONS = ("metricObservations", "snowmakingObservations", "assetStatusObservations", "aggregateObservations", "narratives")


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def stable_id(kind: str, **identity: Any) -> str:
    return f"v2-{kind}-{hashlib.sha256(_canon(identity).encode()).hexdigest()}"


def _utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _aware_iso(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return _utc(parsed)


def _source_instant(value: Any, rule: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if rule == "falls_last_update_utc_v1":
            return _utc(dt.datetime.strptime(text, "%Y-%m-%d %H:%M UTC").replace(tzinfo=dt.timezone.utc))
        if rule == "hotham_local_iso_v1":
            parsed = dt.datetime.fromisoformat(text)
            return _utc(parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ))
        if rule == "vail_mountainops_local_timestamp_v1":
            parsed = dt.datetime.strptime(re.sub(r"\s+", " ", text), "%d-%m-%Y %I:%M%p")
            return _utc(parsed.replace(tzinfo=LOCAL_TZ))
    except ValueError:
        return None
    return _aware_iso(text)


def _date(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _xml(body: str) -> dict[str, Any]:
    root = ET.fromstring(body)

    def node(element: ET.Element) -> Any:
        children = list(element)
        if not children:
            return (element.text or "").strip()
        result: dict[str, Any] = {}
        for child in children:
            value = node(child)
            if child.tag in result:
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]
                result[child.tag].append(value)
            else:
                result[child.tag] = value
        return result

    return node(root)


def decode_body(body: str, content_type: str | None) -> Any:
    if "json" in (content_type or "").casefold() or body.lstrip().startswith(("{", "[")):
        return json.loads(body)
    return _xml(body)


def _tokens(path: str) -> list[str]:
    if path.startswith("$[*]."):
        return path[5:].split(".")
    return path[2:].split(".") if path.startswith("$.") else []


def values_at(payload: Any, path: str) -> list[tuple[Any, dict[str, Any] | None, int]]:
    nodes: list[tuple[Any, dict[str, Any] | None, int]]
    if path.startswith("$[*]."):
        nodes = [(row, row if isinstance(row, dict) else None, i) for i, row in enumerate(payload if isinstance(payload, list) else [], 1)]
    else:
        nodes = [(payload, None, 0)]
    for token in _tokens(path):
        plural = token.endswith("[*]")
        key = token[:-3] if plural else token
        next_nodes: list[tuple[Any, dict[str, Any] | None, int]] = []
        for value, context, ordinal in nodes:
            if not isinstance(value, dict) or key not in value:
                continue
            child = value[key]
            if plural:
                for index, item in enumerate(child if isinstance(child, list) else [child], 1):
                    next_nodes.append((item, item if isinstance(item, dict) else context, ordinal * 10000 + index))
            else:
                next_nodes.append((child, context or value, ordinal))
        nodes = next_nodes
    return nodes


def _field(context: dict[str, Any] | None, path: str) -> Any:
    if not context:
        return None
    key = _tokens(path)[-1].replace("[*]", "") if _tokens(path) else path
    return context.get(key)


def _number(raw: Any) -> float | int | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", raw.replace(",", ""))
    if not match:
        return None
    value = float(match.group())
    return int(value) if value.is_integer() else value


def _value(raw: Any, kind: str) -> tuple[Any, str]:
    if raw is None:
        return None, "unavailable"
    if isinstance(raw, str) and not raw.strip():
        return None, "blank"
    if kind in {"number", "integer"}:
        value = _number(raw)
        if value is None or (kind == "integer" and not float(value).is_integer()):
            return None, "unknown"
        value = int(value) if kind == "integer" else value
        return value, "explicit_zero" if value == 0 else "observed"
    if kind == "date":
        value = _date(raw)
        return (value, "observed") if value else (None, "unknown")
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw, "observed"
        text = str(raw).strip().casefold()
        if text in {"true", "yes", "1"}: return True, "observed"
        if text in {"false", "no", "0"}: return False, "observed"
        return None, "unknown"
    return str(raw), "observed"


def _status(raw: Any) -> str:
    text = str(raw or "").casefold().strip()
    if text in {"open", "operating", "on", "yes", "active"}: return "open"
    if text in {"closed", "off"}: return "closed"
    if "standby" in text: return "standby"
    if "hold" in text: return "on_hold"
    if "delay" in text: return "delayed"
    if "partial" in text: return "partial"
    if "sched" in text: return "scheduled"
    return "unknown"


def _snow_state(raw: Any, signal: str) -> str:
    text = str(raw or "").casefold().strip()
    if signal == "run_snowmaking_flag":
        return "active" if text in {"yes", "y", "on", "true", "1", "active"} else "unknown"
    if "standby" in text: return "standby"
    if text in {"on", "active", "operating", "yes"}: return "active"
    if text in {"off", "inactive", "stopped"}: return "inactive"
    return "unknown"


def _groomed(raw: Any) -> bool | None:
    if isinstance(raw, bool): return raw
    text = str(raw or "").casefold().strip()
    if text in {"yes", "y", "true", "groomed", "1"}: return True
    if text in {"no", "n", "false", "not groomed", "0"}: return False
    return None


def _norm_name(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _diagnostics() -> dict[str, Any]:
    return {"warnings": [], "unknownSourceFields": [], "unmappedAssetCount": 0,
            "knownNormalizedFieldsEncountered": [], "knownNormalizedFieldsAbsent": [],
            "rawOnlyFieldsEncountered": [], "ignoredFieldsEncountered": [],
            "parsingFailures": [], "duplicateListNames": [], "crossStatusListOverlaps": [],
            "malformedValues": [], "emittedObservationCounts": {}}


@dataclass
class NormalizedCapture:
    capture: dict[str, Any]
    raw_payload: dict[str, Any]
    records: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {name: [] for name in COLLECTIONS})
    diagnostics: dict[str, Any] = field(default_factory=_diagnostics)


def _asset_maps(assets: dict[str, Any]) -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str, str, str], dict]]:
    by_id: dict[tuple[str, str], dict] = {}
    by_name: dict[tuple[str, str, str, str], dict] = {}
    for asset in assets["assets"]:
        for mapping in asset.get("sourceMappings", []):
            source_id = mapping["sourceId"]
            if mapping.get("upstreamAssetId") is not None:
                by_id[(source_id, str(mapping["upstreamAssetId"]))] = asset
            if mapping.get("upstreamName"):
                by_name[(source_id, asset["resortId"], asset["assetClass"], _norm_name(mapping["upstreamName"]))] = asset
    return by_id, by_name


def _mapped(source: dict[str, Any], asset_spec: dict[str, Any] | None, context: dict[str, Any] | None,
            fallback: Any, by_id: dict, by_name: dict) -> tuple[dict | None, Any, Any, str, list[str]]:
    spec = asset_spec or {}
    identity_path = spec.get("upstreamIdentityPath", "")
    identity = _field(context, identity_path) if context else fallback
    identity_key = _tokens(identity_path)[-1].casefold() if _tokens(identity_path) else ""
    upstream_id = identity if identity_key in {"id", "assetid"} else None
    upstream_name = None
    if context:
        for key in ("Name", "name", "RunName", "LiftName", "ParkName", "ActivityName", "CrossCountryName"):
            if context.get(key) not in (None, ""):
                upstream_name = context[key]; break
    if upstream_name is None and upstream_id is None:
        upstream_name = identity
    asset_class = spec.get("assetClass") or ("run" if context and ("RunName" in context or "RunStatus" in context) else "area")
    asset = by_id.get((source["sourceId"], str(upstream_id))) if upstream_id is not None else None
    if asset is None and upstream_name is not None:
        asset = by_name.get((source["sourceId"], source["resortId"], asset_class, _norm_name(upstream_name)))
    warnings = [] if asset else ["unmapped upstream asset retained"]
    return asset, upstream_id, upstream_name, asset_class, warnings


def _capture_metadata(envelope: dict[str, Any], source: dict[str, Any], payload: Any,
                      assets: dict, sources: dict, metrics: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    payload_hash = envelope.get("payloadHash") or hashlib.sha256(envelope.get("body", "").encode()).hexdigest()
    retrieved = _aware_iso(envelope.get("capturedAt") or envelope.get("retrievedAt") or envelope.get("responseAt"))
    if retrieved is None:
        raise ValueError("capture requires an aware retrieval timestamp")
    response = _aware_iso(envelope.get("responseAt"))
    rule = source.get("captureTimestampRuleId")
    source_reported = None
    if source["sourceId"] == "falls_official_report" and isinstance(payload, dict):
        source_reported = _source_instant(payload.get("LastUpdate"), rule)
    elif source["sourceId"] == "hotham_official_report" and isinstance(payload, dict):
        source_reported = _source_instant(payload.get("_LastUpdated"), rule)
    elif rule == "vail_mountainops_local_timestamp_v1" and isinstance(payload, list):
        instants = [_source_instant(row.get("TimeStamp"), rule) for row in payload if isinstance(row, dict)]
        source_reported = max((x for x in instants if x), default=None)

    retrieved_dt = dt.datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
    operational_date = None
    if source["sourceId"] == "perisher_official_report" and isinstance(payload, dict):
        operational_date = _date(payload.get("date"))
    if operational_date is None and source_reported:
        operational_date = dt.datetime.fromisoformat(source_reported.replace("Z", "+00:00")).astimezone(LOCAL_TZ).date().isoformat()
    if operational_date is None:
        operational_date = retrieved_dt.astimezone(LOCAL_TZ).date().isoformat()

    warnings = []
    freshness = None
    if source_reported:
        freshness = (retrieved_dt - dt.datetime.fromisoformat(source_reported.replace("Z", "+00:00"))).total_seconds() / 60
        if freshness < 0:
            warnings.append(f"sourceReportedAt is {abs(freshness):.1f} minutes after retrievedAt; freshness retained as null")
            freshness = None
    if envelope.get("warning"):
        warnings.append(f"live request failed: {envelope['warning']}")
    capture_id = stable_id("capture", sourceId=source["sourceId"], retrievedAt=retrieved, payloadHash=payload_hash)
    raw_ref = str(envelope.get("rawPayloadRef") or envelope.get("_path") or f"temporary/{source['sourceId']}/{retrieved}/{payload_hash}.json")
    capture = {"captureId": capture_id, "resortId": source["resortId"], "sourceId": source["sourceId"],
               "sourceLayer": source["layer"], "sourceRole": source["sourceRole"], "retrievedAt": retrieved,
               "responseAt": response, "sourceReportedAt": source_reported, "operationalDate": operational_date,
               "httpStatus": envelope.get("httpStatus"), "contentType": envelope.get("contentType"),
               "payloadHash": payload_hash, "rawPayloadRef": raw_ref, "parserVersion": PARSER_VERSION,
               "retrievalStatus": "ok" if (envelope.get("httpStatus") or 200) < 400 else "failed",
               "freshnessMinutes": freshness, "warnings": warnings, "assetRegistryRevision": assets["contentHash"],
               "sourceInventoryRevision": sources["contentHash"], "metricCatalogueRevision": metrics["contentHash"]}
    raw = {"descriptorId": stable_id("raw", captureId=capture_id, sourceId=source["sourceId"], payloadHash=payload_hash, rawPayloadRef=raw_ref),
           "payloadHash": payload_hash, "sourceId": source["sourceId"], "sourceUrl": source["url"],
           "firstCapturedAt": retrieved, "responseAt": response, "httpStatus": capture["httpStatus"],
           "contentType": capture["contentType"], "rawPayloadRef": raw_ref, "parserVersion": PARSER_VERSION}
    return capture, raw


def normalize_envelope(envelope: dict[str, Any], source_id: str) -> NormalizedCapture:
    assets, source_registry, metrics = load_registries()
    source = next(item for item in source_registry["sources"] if item["sourceId"] == source_id)
    payload: Any = None
    parse_error: Exception | None = None
    if (envelope.get("httpStatus") or 200) < 400:
        try:
            payload = decode_body(envelope.get("body", ""), envelope.get("contentType"))
        except Exception as exc:
            parse_error = exc
    capture, raw = _capture_metadata(envelope, source, payload, assets, source_registry, metrics)
    output = NormalizedCapture(capture, raw)
    if capture["warnings"]:
        output.diagnostics["warnings"].extend(capture["warnings"])
    if capture["retrievalStatus"] == "failed":
        _finish(output)
        return output
    if parse_error:
        capture["retrievalStatus"] = "partial"
        warning = f"body parse failed: {parse_error}"
        capture["warnings"].append(warning)
        output.diagnostics["warnings"].append(warning)
        output.diagnostics["parsingFailures"].append(warning)
        _finish(output)
        return output

    by_id, by_name = _asset_maps(assets)
    metric_defs = {item["metricKey"]: item for item in metrics["metrics"]}
    _classify_fields(output, payload, source)
    if source_id.endswith("_mountainops_runs") or source_id.endswith("_mountainops_lifts") or source_id.endswith("_mountainops_lifts_rich"):
        _normalise_mountainops(output, payload, source, by_id, by_name)
    else:
        _normalise_report(output, payload, source, by_id, by_name, metric_defs)
    _finish(output)
    return output


def _finish(output: NormalizedCapture) -> None:
    for key in COLLECTIONS:
        output.diagnostics["emittedObservationCounts"][key] = len(output.records[key])
    output.diagnostics["warnings"] = list(dict.fromkeys(output.diagnostics["warnings"]))


def _classify_fields(output: NormalizedCapture, payload: Any, source: dict[str, Any]) -> None:
    known = {spec["path"]: spec for spec in source["fieldCoverage"]}
    for path, spec in known.items():
        matches = values_at(payload, path)
        if spec["disposition"] == "normalized":
            output.diagnostics["knownNormalizedFieldsEncountered" if matches else "knownNormalizedFieldsAbsent"].append(path)
        elif matches:
            key = "rawOnlyFieldsEncountered" if spec["disposition"] == "raw_only" else "ignoredFieldsEncountered"
            output.diagnostics[key].append(path)
    if isinstance(payload, dict):
        roots = {path.split(".")[1].replace("[*]", "") for path in known if path.startswith("$.")}
        output.diagnostics["unknownSourceFields"].extend(f"$.{key}" for key in payload if key not in roots)
    elif isinstance(payload, list):
        known_keys = {_tokens(path)[0] for path in known if path.startswith("$[*].") and _tokens(path)}
        unknown = sorted({key for row in payload if isinstance(row, dict) for key in row if key not in known_keys})
        output.diagnostics["unknownSourceFields"].extend(f"$[*].{key}" for key in unknown)
    for path in output.diagnostics["unknownSourceFields"]:
        output.diagnostics["warnings"].append(f"unknown source field retained raw: {path}")


def _oid(output: NormalizedCapture, kind: str, source_field: str, ordinal: int, subject: Any = None) -> str:
    return stable_id(kind, captureId=output.capture["captureId"], collection=kind,
                     sourceField=source_field, subject=subject, ordinal=ordinal)


def _observed(output: NormalizedCapture, spec: dict[str, Any], payload: Any) -> str | None:
    rule = spec.get("parserRuleId")
    if rule in {"hotham_depth_measurement_date_v1", "date_only_no_instant_v1"}:
        return None
    if rule in {"falls_patrol_local_datetime_v1", "falls_slope_maintenance_local_datetime_v1"} and isinstance(payload, dict):
        section = payload.get("Patrol" if rule.startswith("falls_patrol") else "SlopeMaintenance") or {}
        date_value = section.get("PatrolDate" if rule.startswith("falls_patrol") else "LastUpdateDate")
        time_value = section.get("PatrolTime" if rule.startswith("falls_patrol") else "LastUpdateTime")
        parsed_date = _date(date_value)
        if parsed_date and time_value:
            for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
                try:
                    clock = dt.datetime.strptime(str(time_value).strip(), fmt).time()
                    return _utc(dt.datetime.combine(dt.date.fromisoformat(parsed_date), clock, LOCAL_TZ))
                except ValueError:
                    pass
    timestamp_rule = spec.get("timestampRule") or ""
    if timestamp_rule.startswith("combine $.SlopeMaintenance") and isinstance(payload, dict):
        part = payload.get("SlopeMaintenance") or {}
        date_value, time_value = part.get("LastUpdateDate"), part.get("LastUpdateTime")
        parsed_date = _date(date_value)
        if parsed_date and time_value:
            for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
                try:
                    clock = dt.datetime.strptime(str(time_value).strip(), fmt).time()
                    return _utc(dt.datetime.combine(dt.date.fromisoformat(parsed_date), clock, LOCAL_TZ))
                except ValueError:
                    pass
    if "CrossCountryLastGroomed" in timestamp_rule:
        return None
    return output.capture["sourceReportedAt"] or output.capture["retrievedAt"]


def _normalise_report(output: NormalizedCapture, payload: Any, source: dict[str, Any], by_id: dict, by_name: dict, metric_defs: dict) -> None:
    list_specs = {spec["path"]: spec for spec in source["fieldCoverage"] if spec.get("listExpansionSpec")}
    overlap_groups: set[tuple[str, ...]] = set()
    for spec in source["fieldCoverage"]:
        matches = values_at(payload, spec["path"])
        if spec["disposition"] != "normalized" or not matches:
            continue
        for raw, context, ordinal in matches:
            if spec.get("listExpansionSpec"):
                _emit_list(output, source, spec, raw, ordinal, payload, by_id, by_name, overlap_groups)
            elif spec.get("parserRuleId") == "hotham_wind_range_v1":
                _emit_wind(output, source, spec, raw, ordinal, metric_defs)
            else:
                destinations = ([spec] if spec.get("destinationCollection") else []) + spec.get("emissions", [])
                for destination in destinations:
                    _emit_destination(output, source, spec, destination, raw, context, ordinal, payload, by_id, by_name, metric_defs)


def _emit_wind(output: NormalizedCapture, source: dict, spec: dict, raw: Any, ordinal: int, metric_defs: dict) -> None:
    parsed = parse_wind_speed_range(raw)
    blank = raw is None or (isinstance(raw, str) and not raw.strip())
    for index, destination in enumerate(spec["emissions"]):
        key = destination["metricKey"]
        value = parsed[index] if parsed else None
        status = "blank" if blank else "unknown" if parsed is None else "explicit_zero" if value == 0 else "observed"
        if parsed is None and not blank:
            warning = f"{spec['path']}: malformed wind value retained without numeric fabrication"
            output.diagnostics["malformedValues"].append({"path": spec["path"], "rawValue": raw})
            output.diagnostics["warnings"].append(warning)
        output.records["metricObservations"].append(_metric_row(output, source, spec, destination, key, value, status, raw, ordinal + index, metric_defs, output.capture["sourceReportedAt"] or output.capture["retrievedAt"]))


def _emit_list(output: NormalizedCapture, source: dict, spec: dict, raw: Any, ordinal: int, payload: Any,
               by_id: dict, by_name: dict, overlap_groups: set[tuple[str, ...]]) -> None:
    try:
        names, duplicates = expand_comma_delimited(raw)
    except TypeError:
        warning = f"{spec['path']}: expected comma-delimited string"
        output.diagnostics["parsingFailures"].append(warning)
        output.diagnostics["warnings"].append(warning)
        return
    if duplicates:
        output.diagnostics["duplicateListNames"].append({"sourceField": spec["path"], "names": duplicates})
        output.diagnostics["warnings"].append(f"{spec['path']}: duplicate list names retained")
    list_spec = spec["listExpansionSpec"]
    member = list_spec["memberValue"]
    destinations = spec.get("emissions", [])
    evidence_destination = next((item for item in destinations if item["destinationCollection"] != "aggregateObservations"), None)
    if evidence_destination:
        for index, name in enumerate(names):
            context = {"Name": name}
            if evidence_destination["destinationCollection"] == "assetStatusObservations":
                asset, uid, upstream_name, asset_class, warnings = _mapped(source, evidence_destination.get("assetSpec"), context, name, by_id, by_name)
                if warnings: output.diagnostics["unmappedAssetCount"] += 1
                row = _asset_row(output, source, spec["path"], ordinal * 10000 + index, asset, uid, upstream_name,
                                 asset_class, {"list": raw, "member": name}, evidence_destination["observationRole"],
                                 member.get("operationalStatus"), member.get("groomed"), warnings,
                                 observed=output.capture["sourceReportedAt"] or output.capture["retrievedAt"])
                output.records["assetStatusObservations"].append(row)
            else:
                asset, uid, upstream_name, asset_class, warnings = _mapped(source, evidence_destination.get("assetSpec"), context, name, by_id, by_name)
                if warnings: output.diagnostics["unmappedAssetCount"] += 1
                output.records["snowmakingObservations"].append(_snow_row(
                    output, source, spec["path"], ordinal * 10000 + index, evidence_destination,
                    {"list": raw, "member": name}, member.get("snowmakingState") or "unknown", asset, uid,
                    upstream_name, warnings, area=list_spec.get("area")))

    aggregate_destination = next((item for item in destinations if item["destinationCollection"] == "aggregateObservations"), None)
    if aggregate_destination:
        unique_names = {_norm_name(name) for name in names}
        derivation = list_spec["aggregateDerivation"]
        denominator = None
        fields = derivation["denominatorSourceFields"]
        if fields:
            union: set[str] = set()
            all_present = True
            status_sets: list[tuple[str, set[str]]] = []
            for path in fields:
                matches = values_at(payload, path)
                if not matches:
                    all_present = False; continue
                try:
                    field_names, _ = expand_comma_delimited(matches[0][0])
                except TypeError:
                    all_present = False; continue
                normalized = {_norm_name(name) for name in field_names}
                union.update(normalized); status_sets.append((path, normalized))
            denominator = len(union) if all_present else None
            group = tuple(sorted(fields))
            if group not in overlap_groups:
                overlap_groups.add(group)
                for left_index, (left_path, left) in enumerate(status_sets):
                    for right_path, right in status_sets[left_index + 1:]:
                        for name in sorted(left & right):
                            item = {"normalizedName": name, "sourceFields": [left_path, right_path]}
                            output.diagnostics["crossStatusListOverlaps"].append(item)
                            output.diagnostics["warnings"].append(f"conflicting list membership retained for {name}: {left_path}, {right_path}")
        _aggregate_row(output, source, spec["path"], raw, aggregate_destination, len(unique_names), denominator,
                       ordinal, area=list_spec.get("area"))


def _metric_row(output: NormalizedCapture, source: dict, spec: dict, destination: dict, key: str, value: Any,
                status: str, raw: Any, ordinal: int, metric_defs: dict, observed_at: str | None) -> dict[str, Any]:
    return {"observationId": _oid(output, "metric", spec["path"], ordinal, key), "captureId": output.capture["captureId"],
            "resortId": source["resortId"], "metricKey": key, "value": value, "unit": metric_defs[key]["unit"],
            "valueStatus": status, "observationRole": destination["observationRole"], "scope": destination["scope"],
            "subjectId": None, "locationLabel": _location_label(spec["path"], spec.get("notes", "")),
            "observedAt": observed_at, "effectiveFrom": None, "effectiveTo": None, "sourceField": spec["path"],
            "rawValue": raw, "semanticQualifier": destination.get("semanticQualifier") or "",
            "interpretationConfidence": destination["interpretationConfidence"], "notes": []}


def _emit_destination(output: NormalizedCapture, source: dict, spec: dict, destination: dict, raw: Any,
                      context: dict | None, ordinal: int, payload: Any, by_id: dict, by_name: dict, metric_defs: dict) -> None:
    collection = destination["destinationCollection"]
    observed_at = _observed(output, spec, payload)
    if collection == "metricObservations":
        key = destination["metricKey"]
        value, status = _value(raw, destination["normalizedValueType"])
        if status == "unknown":
            output.diagnostics["malformedValues"].append({"path": spec["path"], "rawValue": raw})
            output.diagnostics["warnings"].append(f"{spec['path']}: malformed value retained as unknown")
        row = _metric_row(output, source, spec, destination, key, value, status, raw, ordinal, metric_defs, observed_at)
        if spec.get("parserRuleId") == "hotham_depth_measurement_date_v1" and isinstance(payload, dict):
            measurement_date = _date(payload.get("AvSnowdepthLastMeasuredOn"))
            if measurement_date: row["notes"].append(f"measurementDate={measurement_date}")
        output.records[collection].append(row)
    elif collection == "snowmakingObservations":
        snow = destination.get("snowmakingSpec") or spec.get("snowmakingSpec") or {}
        numeric = _number(raw) if destination.get("destinationField") == "numericValue" else None
        state = _snow_state(raw, snow["signalType"])
        asset = uid = name = None; warnings = []
        if destination.get("scope") == "asset":
            asset, uid, name, _, warnings = _mapped(source, destination.get("assetSpec") or spec.get("assetSpec"), context, raw, by_id, by_name)
            if warnings: output.diagnostics["unmappedAssetCount"] += 1
        output.records[collection].append(_snow_row(output, source, spec["path"], ordinal, destination, raw, state,
                                                    asset, uid, name, warnings, numeric=numeric, observed_at=observed_at))
    elif collection == "aggregateObservations":
        aggregate_spec = destination.get("aggregateSpec") or {}
        denominator = _denominator(payload, aggregate_spec.get("denominatorSource"))
        _aggregate_row(output, source, spec["path"], raw, destination, _number(raw), denominator, ordinal)
    elif collection == "assetStatusObservations":
        asset, uid, name, asset_class, warnings = _mapped(source, destination.get("assetSpec") or spec.get("assetSpec"), context, raw, by_id, by_name)
        if warnings: output.diagnostics["unmappedAssetCount"] += 1
        field_name = destination["destinationField"]
        operational = _status(raw) if field_name == "operationalStatus" else None
        groomed = _groomed(raw) if field_name == "groomed" else None
        output.records[collection].append(_asset_row(output, source, spec["path"], ordinal, asset, uid, name,
                                                     asset_class, raw, destination["observationRole"], operational,
                                                     groomed, warnings, observed=observed_at))
    elif collection == "narratives" and raw not in (None, ""):
        output.records[collection].append({"observationId": _oid(output, "narrative", spec["path"], ordinal),
            "captureId": output.capture["captureId"], "resortId": source["resortId"], "narrativeType": "daily_report",
            "headline": str(raw) if destination["destinationField"] == "headline" else "",
            "body": str(raw) if destination["destinationField"] == "body" else "", "author": None,
            "observationRole": destination["observationRole"], "observedAt": observed_at,
            "effectiveFrom": None, "effectiveTo": None, "sourceField": spec["path"]})


def _location_label(path: str, notes: str = "") -> str | None:
    text = f"{path} {notes}".casefold().replace(" ", "")
    for label in ("Village Bowl", "Sun Valley", "North Side", "Top station", "Snowmaking areas"):
        if label.casefold().replace(" ", "") in text:
            return label
    return None


def _denominator(payload: Any, source: str | None) -> int | float | None:
    if not source:
        return None
    fields = re.findall(r"\$?\.?([A-Za-z_][A-Za-z0-9_]*)", source)
    if not isinstance(payload, dict) or not fields or any(name not in payload for name in fields):
        return None
    values = [_number(payload[name]) for name in fields]
    return sum(values) if all(value is not None for value in values) else None


def _aggregate_row(output: NormalizedCapture, source: dict, field: str, raw: Any, destination: dict,
                   numerator: Any, denominator: Any, ordinal: int, area: str | None = None) -> None:
    numerator = _number(numerator)
    if numerator is None:
        return
    aggregate = destination.get("aggregateSpec") or {}
    denominator_scope = aggregate.get("denominatorScope", "unknown")
    if denominator is None:
        denominator_scope = "unknown"
    output.records["aggregateObservations"].append({"observationId": _oid(output, "aggregate", field, ordinal, aggregate.get("numeratorStatus")),
        "captureId": output.capture["captureId"], "resortId": source["resortId"],
        "assetClass": aggregate.get("assetClass", "area"), "area": area, "status": aggregate.get("numeratorStatus", "reported"),
        "numerator": numerator, "denominator": denominator, "denominatorScope": denominator_scope,
        "observationRole": destination["observationRole"], "observedAt": output.capture["sourceReportedAt"] or output.capture["retrievedAt"],
        "sourceField": field, "rawValue": raw, "notes": []})


def _asset_row(output: NormalizedCapture, source: dict, field: str, ordinal: int, asset: dict | None, uid: Any,
               name: Any, asset_class: str, raw: Any, role: str, operational: str | None = None,
               groomed: bool | None = None, warnings: list[str] | None = None, open_time: str | None = None,
               close_time: str | None = None, scheduled: bool | None = None, observed: str | None = None) -> dict[str, Any]:
    return {"observationId": _oid(output, "asset", field, ordinal, uid if uid is not None else name),
            "captureId": output.capture["captureId"], "resortId": source["resortId"],
            "assetId": asset["assetId"] if asset else None, "upstreamAssetId": str(uid) if uid is not None else None,
            "upstreamName": str(name) if name is not None else None, "assetClass": asset_class,
            "operationalStatus": operational or "unknown", "statusReason": [], "rawStatus": raw,
            "observationRole": role, "scheduled": scheduled, "expectedToOpen": None, "openTime": open_time,
            "closeTime": close_time, "queueMinutes": None, "condition": None, "groomed": groomed,
            "observedAt": observed, "effectiveFrom": None, "effectiveTo": None,
            "interpretationConfidence": "high", "warnings": warnings or [], "snowmakingObservationIds": []}


def _snow_row(output: NormalizedCapture, source: dict, field: str, ordinal: int, destination: dict, raw: Any,
              state: str, asset: dict | None, uid: Any, name: Any, warnings: list[str], area: str | None = None,
              numeric: Any = None, observed_at: str | None = None) -> dict[str, Any]:
    snow = destination.get("snowmakingSpec") or {}
    return {"observationId": _oid(output, "snow", field, ordinal, uid if uid is not None else name or snow.get("signalType")),
            "captureId": output.capture["captureId"], "resortId": source["resortId"], "scope": destination["scope"],
            "subjectAssetId": asset["assetId"] if asset else None, "area": area, "signalType": snow["signalType"],
            "normalizedState": state, "numericValue": numeric, "unit": "count" if numeric is not None else None,
            "rawValue": raw, "sourceField": field, "observationRole": destination["observationRole"],
            "semanticMeaning": snow["semanticMeaning"], "interpretationConfidence": destination["interpretationConfidence"],
            "observedAt": observed_at or output.capture["sourceReportedAt"] or output.capture["retrievedAt"],
            "effectiveFrom": None, "effectiveTo": None, "notes": [], "upstreamAssetId": str(uid) if uid is not None else None,
            "upstreamName": str(name) if name is not None else None, "warnings": warnings}


def _clock(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if text.startswith("1970-"):
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.time().replace(tzinfo=None).isoformat()
        for fmt in ("%I:%M%p", "%I:%M %p", "%H:%M", "%H:%M:%S"):
            try:
                return dt.datetime.strptime(text, fmt).time().isoformat()
            except ValueError:
                pass
    except ValueError:
        pass
    return None


def _rich_schedule(value: Any, *, present: bool, output: NormalizedCapture, ordinal: int) -> bool | None:
    """Apply vail_rich_binary_schedule_flag_v1 without type coercion.

    The public rich feed's reviewed wire type is an integer.  Python booleans
    are deliberately rejected even though ``bool`` subclasses ``int``.
    """
    if not present:
        return None
    if type(value) is int and value in (0, 1):
        return bool(value)
    warning = (
        f"$[{ordinal - 1}].isScheduled: expected exact integer 0 or 1; "
        f"retained {value!r} raw and emitted scheduled null"
    )
    output.diagnostics["warnings"].append(warning)
    output.diagnostics["malformedValues"].append(
        {"path": "$[*].isScheduled", "rowOrdinal": ordinal, "rawValue": value,
         "parserRuleId": "vail_rich_binary_schedule_flag_v1"}
    )
    return None


def _normalise_mountainops(output: NormalizedCapture, payload: Any, source: dict, by_id: dict, by_name: dict) -> None:
    if not isinstance(payload, list):
        warning = "MountainOps payload is not a list"
        output.capture["retrievalStatus"] = "partial"
        output.capture["warnings"].append(warning)
        output.diagnostics["parsingFailures"].append(warning)
        output.diagnostics["warnings"].append(warning)
        return
    is_run = source["sourceId"].endswith("_runs")
    is_rich = source["sourceId"].endswith("_rich")
    status_counts: dict[str, int] = {}
    groomed_count = 0
    affirmative = 0
    for ordinal, row in enumerate(payload, 1):
        if not isinstance(row, dict):
            output.diagnostics["parsingFailures"].append(f"row {ordinal}: expected object")
            continue
        if is_rich:
            uid, name, area = row.get("id"), row.get("name"), row.get("publicArea")
            code = row.get("statusCode")
            status = {1: "open", 2: "closed", 3: "on_hold"}.get(code, "unknown")
            raw_status = {"statusCode": code}
            if code not in {1, 2, 3}:
                output.diagnostics["warnings"].append(f"$[*].statusCode: unknown rich status code {code!r} retained raw")
                output.diagnostics["malformedValues"].append({"path": "$[*].statusCode", "rawValue": code})
            observed = output.capture["retrievedAt"]
            open_time, close_time = _clock(row.get("openTime")), _clock(row.get("closeTime"))
            if "isScheduled" in row:
                raw_status["isScheduled"] = row.get("isScheduled")
            scheduled = _rich_schedule(
                row.get("isScheduled"), present="isScheduled" in row, output=output, ordinal=ordinal
            )
            if "queueMinsEstimate" in row:
                output.diagnostics["rawOnlyFieldsEncountered"].append("$[*].queueMinsEstimate")
        else:
            uid = row.get("ID") if is_run else row.get("Id")
            name, area = row.get("Name"), row.get("Location")
            raw_text = row.get("RunStatus") if is_run else row.get("Status")
            raw_id = row.get("StatusID") if is_run else row.get("StatusId")
            status = _status(raw_text)
            raw_status = {"status": raw_text, "statusId": raw_id}
            observed = _source_instant(row.get("TimeStamp"), source.get("captureTimestampRuleId")) if not is_run else None
            observed = observed or output.capture["retrievedAt"]
            open_time, close_time = _clock(row.get("OpenTime")), _clock(row.get("CloseTime"))
            scheduled = None
        asset_spec = {"assetClass": "run" if is_run else "lift", "upstreamIdentityPath": "$[*].ID" if is_run else "$[*].id" if is_rich else "$[*].Id"}
        asset, _, _, asset_class, warnings = _mapped(source, asset_spec, row, uid, by_id, by_name)
        if warnings: output.diagnostics["unmappedAssetCount"] += 1
        snow_ids: list[str] = []
        groomed = _groomed(row.get("Groomed")) if is_run and "Groomed" in row else None
        if groomed is True: groomed_count += 1
        if is_run and "Snowmaking" in row:
            raw_snow = row.get("Snowmaking")
            state = _snow_state(raw_snow, "run_snowmaking_flag")
            affirmative += state == "active"
            destination = {"scope": "asset", "observationRole": "measurement", "interpretationConfidence": "unverified",
                           "snowmakingSpec": {"signalType": "run_snowmaking_flag", "semanticMeaning": "unverified per-run upstream flag; individual NO normalizes unknown"}}
            snow_warnings = list(warnings)
            if state == "unknown": snow_warnings.append("Raw non-affirmative flag does not establish plant inactivity")
            snow = _snow_row(output, source, "$[*].Snowmaking", ordinal, destination, raw_snow, state, asset, uid, name,
                             snow_warnings, area=area, observed_at=observed)
            output.records["snowmakingObservations"].append(snow)
            snow_ids.append(snow["observationId"])
        asset_row = _asset_row(output, source, "$[*].RunStatus" if is_run else "$[*].statusCode" if is_rich else "$[*].Status",
                               ordinal, asset, uid, name, asset_class, raw_status, "live_actual", status, groomed,
                               warnings, open_time, close_time, scheduled, observed)
        asset_row["snowmakingObservationIds"] = snow_ids
        output.records["assetStatusObservations"].append(asset_row)
        status_counts[status] = status_counts.get(status, 0) + 1

    denominator = len(payload)
    for index, (status, count) in enumerate(sorted(status_counts.items()), 1):
        destination = {"observationRole": "live_actual", "aggregateSpec": {"assetClass": "run" if is_run else "lift",
                      "numeratorStatus": status, "denominatorScope": "all_listed"}}
        _aggregate_row(output, source, "$[*].RunStatus" if is_run else "$[*].statusCode" if is_rich else "$[*].Status",
                       {"rowsListed": denominator, "status": status, "count": count}, destination, count, denominator, index)
    if is_run:
        destination = {"observationRole": "live_actual", "aggregateSpec": {"assetClass": "run", "numeratorStatus": "groomed", "denominatorScope": "all_listed"}}
        _aggregate_row(output, source, "$[*].Groomed", {"rowsListed": denominator, "groomed": groomed_count}, destination,
                       groomed_count, denominator, 1000)
        if affirmative == 0:
            destination = {"scope": "resort", "observationRole": "measurement", "interpretationConfidence": "unverified",
                           "snowmakingSpec": {"signalType": "run_snowmaking_flag", "semanticMeaning": "complete-feed summary: no row was affirmatively flagged; not plant inactivity"}}
            output.records["snowmakingObservations"].append(_snow_row(
                output, source, "$[*].Snowmaking", 999999, destination,
                {"affirmativeFlags": 0, "rowsExamined": denominator}, "none_flagged", None, None, None, [],
                observed_at=output.capture["retrievedAt"]))
