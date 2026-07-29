"""Pure deterministic Phase 3 resolver for bounded operations-v2 evidence.

The core function has no network, database, scheduler, forecast, or dashboard
dependencies.  Input validation is injectable to keep relocation practical.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .schema_v2 import assert_valid_export

ASSET_FIELDS = ("operationalStatus", "scheduled", "expectedToOpen", "openTime", "closeTime", "queueMinutes", "groomed", "condition", "statusReason")
USABLE_CAPTURE_STATES = {"ok", "partial", "not_modified"}
COLLECTIONS = ("metricObservations", "snowmakingObservations", "assetStatusObservations", "aggregateObservations", "narratives")
OBSERVATION_ROLES = {"live_actual", "report_summary", "measurement", "morning_plan", "expected", "advisory", "unknown"}
FRESHNESS_STATES = {"fresh", "stale", "future_timestamp_anomaly"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{fingerprint(value)[:24]}"


def _time(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(dt.timezone.utc)


def _iso(value: str | dt.datetime) -> str:
    parsed = _time(value) if isinstance(value, str) else value
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_policy(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def validate_policy(policy: dict[str, Any], source_inventory: Iterable[dict[str, Any]] | None = None) -> list[str]:
    from jsonschema import Draft202012Validator, FormatChecker
    from .registry import ROOT, content_hash

    schema = json.loads((ROOT / "contracts/operations-resolution-policy.v1.schema.json").read_text())
    errors = [f"policy schema {'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
              for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(policy)]
    if policy.get("revision") != content_hash(policy):
        errors.append("policy revision does not match canonical policy content")
    fields = [row.get("fieldFamily") for row in policy.get("assetFieldPolicies", [])]
    if set(fields) != set(ASSET_FIELDS) or len(fields) != len(set(fields)):
        errors.append("policy must define every resolver asset field family exactly once")
    roles = [role for values in policy.get("roleLanes", {}).values() for role in values]
    if len(roles) != len(set(roles)):
        errors.append("each observation role must belong to exactly one lane")
    if set(roles) != OBSERVATION_ROLES:
        errors.append(f"policy must assign every observation role exactly once; missing={sorted(OBSERVATION_ROLES - set(roles))}, extra={sorted(set(roles) - OBSERVATION_ROLES)}")
    lanes = set(policy.get("roleLanes", {}))
    expected_order = {f"{lane}:{freshness}" for lane in lanes for freshness in FRESHNESS_STATES}
    actual_order = policy.get("preferredDisplayOrder", [])
    if len(actual_order) != len(set(actual_order)):
        errors.append("preferred display order contains duplicate tokens")
    if set(actual_order) != expected_order:
        errors.append(f"preferred display order must contain every reachable lane/freshness token exactly once; missing={sorted(expected_order - set(actual_order))}, dead={sorted(set(actual_order) - expected_order)}")
    vocabulary = set(policy.get("sourceVocabulary", []))
    referenced = set()
    for row in policy.get("assetFieldPolicies", []):
        priorities = row.get("sourcePriorityByLane", {})
        invalid_lanes = set(priorities) - lanes
        if invalid_lanes:
            errors.append(f"{row.get('fieldFamily')}: source priority lanes absent from roleLanes: {sorted(invalid_lanes)}")
        for lane, sources in priorities.items():
            if len(sources) != len(set(sources)):
                errors.append(f"{row.get('fieldFamily')}:{lane}: duplicate source priorities")
            referenced.update(sources)
    if not referenced <= vocabulary:
        errors.append(f"policy source IDs absent from resolver vocabulary: {sorted(referenced - vocabulary)}")
    if source_inventory is not None:
        embedded = {row.get("sourceId") for row in source_inventory}
        if not embedded <= vocabulary:
            errors.append(f"embedded source IDs absent from resolver vocabulary: {sorted(embedded - vocabulary)}")
    return errors


def _lane(role: str, policy: dict[str, Any]) -> str:
    for lane, roles in policy["roleLanes"].items():
        if role in roles:
            return lane
    raise ValueError(f"observation role absent from policy lanes: {role}")


def _subject(row: dict[str, Any], capture: dict[str, Any], *, asset: bool = False) -> dict[str, Any]:
    if asset:
        if row.get("assetId"):
            return {"kind": "canonical_asset", "resortId": row["resortId"], "assetClass": row["assetClass"], "subjectId": row["assetId"]}
        upstream = row.get("upstreamAssetId")
        name = " ".join((row.get("upstreamName") or "").casefold().split())
        token = str(upstream) if upstream is not None else name
        key = f"unmapped:{row['resortId']}:{capture['sourceId']}:{row['assetClass']}:{token}"
        return {"kind": "source_scoped_unmapped", "resortId": row["resortId"], "assetClass": row["assetClass"], "subjectId": key,
                "sourceId": capture["sourceId"], "upstreamAssetId": upstream, "upstreamName": row.get("upstreamName")}
    return {"kind": "semantic_scope", "resortId": row["resortId"], "subjectId": row.get("subjectId")}


def _freshness(row: dict[str, Any], capture: dict[str, Any], source: dict[str, Any], as_of: dt.datetime,
               policy: dict[str, Any]) -> tuple[str, float, float | None, int]:
    retrieved = _time(capture["retrievedAt"])
    observed = _time(row.get("observedAt"))
    threshold = source["freshnessPolicyMinutes"]
    capture_age = (as_of - retrieved).total_seconds() / 60
    observation_age = (as_of - observed).total_seconds() / 60 if observed else None
    tolerance = policy["futureTimestampToleranceMinutes"]
    if observed and observation_age < -tolerance:
        state = "future_timestamp_anomaly"
    elif capture_age <= threshold:
        state = "fresh"
    else:
        state = "stale"
    return state, capture_age, observation_age, threshold


def _eligible(row: dict[str, Any], as_of: dt.datetime) -> bool:
    start, end = _time(row.get("effectiveFrom")), _time(row.get("effectiveTo"))
    return not ((start and start > as_of) or (end and end < as_of))


def _candidate(row: dict[str, Any], capture: dict[str, Any], source: dict[str, Any], as_of: dt.datetime,
               policy: dict[str, Any], value: Any, known: bool) -> dict[str, Any]:
    state, capture_age, observation_age, threshold = _freshness(row, capture, source, as_of, policy)
    return {"observationId": row["observationId"], "captureId": row["captureId"], "sourceId": capture["sourceId"],
            "observationRole": row["observationRole"], "lane": _lane(row["observationRole"], policy),
            "observedAt": row.get("observedAt"), "captureRetrievedAt": capture["retrievedAt"],
            "captureAgeMinutes": round(capture_age, 6), "observationAgeMinutes": round(observation_age, 6) if observation_age is not None else None,
            "freshnessThresholdMinutes": threshold, "freshnessState": state, "value": value, "known": known,
            "effectiveFrom": row.get("effectiveFrom"), "effectiveTo": row.get("effectiveTo")}


def _source_rank(candidate: dict[str, Any], field_policy: dict[str, Any] | None) -> int:
    if not field_policy:
        return 10_000
    ordered = field_policy.get("sourcePriorityByLane", {}).get(candidate["lane"], [])
    return ordered.index(candidate["sourceId"]) if candidate["sourceId"] in ordered else 10_000


def _select(candidates: list[dict[str, Any]], field_policy: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    freshness_rank = {"fresh": 0, "future_timestamp_anomaly": 1, "stale": 2, "unknown": 3}
    ordered = sorted(candidates, key=lambda c: (not c["known"], freshness_rank[c["freshnessState"]], _source_rank(c, field_policy),
                                                 -_time(c["captureRetrievedAt"]).timestamp(), c["observationId"]))
    selected = ordered[0]
    alternates = sorted(c["observationId"] for c in candidates if c["observationId"] != selected["observationId"])
    if selected["known"]:
        reason = "selected known value by freshness, reviewed source precedence, then latest capture"
        if any(not c["known"] and c["freshnessState"] == "fresh" for c in candidates):
            reason = "selected known value; fresh unknown evidence is retained as an alternate and cannot erase it"
        elif selected["freshnessState"] == "stale":
            reason = "selected stale known value because no fresher known candidate exists in this lane"
    else:
        reason = "no eligible known value exists in this lane; retained best unresolved evidence"
    public = {k: selected[k] for k in ("value", "captureId", "sourceId", "observationRole", "observedAt", "captureRetrievedAt",
                                       "captureAgeMinutes", "observationAgeMinutes", "freshnessThresholdMinutes", "freshnessState", "known")}
    public.update(resolutionId=stable_id("resolution", {"key": key, "lane": selected["lane"], "observationId": selected["observationId"]}),
                  selectedObservationId=selected["observationId"], selectionReason=reason,
                  alternateCandidateObservationIds=alternates)
    return public


def _preferred(lanes: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any] | None:
    selections = [(row["lane"], row["selection"]) for row in lanes if row["selection"]]
    if not selections:
        return None
    order = {token: index for index, token in enumerate(policy["preferredDisplayOrder"])}
    known = [(lane, selection) for lane, selection in selections if selection["known"]]
    pool = known or selections
    lane, selection = min(pool, key=lambda item: (order.get(f"{item[0]}:{item[1]['freshnessState']}", 10_000), item[1]["selectedObservationId"]))
    return selection


def _record(group_key: tuple[Any, ...], field: str, subject: dict[str, Any], candidates: list[dict[str, Any]],
            policy: dict[str, Any], field_policy: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    key = stable_id("key", {"group": group_key, "field": field})
    lanes = []
    for lane in policy["roleLanes"]:
        selected = _select([candidate for candidate in candidates if candidate["lane"] == lane], field_policy, key)
        if selected:
            lanes.append({"lane": lane, "selection": selected})
    record = {"resolutionKey": key, "fieldFamily": field, "subject": subject, "lanes": lanes, "preferredSelection": _preferred(lanes, policy)}
    record.update(extra)
    return record


def _values_differ(left: Any, right: Any) -> bool:
    return canonical_bytes(left) != canonical_bytes(right)


def _overlap_or_window(left: dict[str, Any], right: dict[str, Any], minutes: int) -> bool:
    ls, le = _time(left.get("effectiveFrom")), _time(left.get("effectiveTo"))
    rs, re = _time(right.get("effectiveFrom")), _time(right.get("effectiveTo"))
    if (ls or le) and (rs or re):
        ls = ls or _time(left["observedAt"]) or _time(left["captureRetrievedAt"])
        le = le or _time(left["observedAt"]) or _time(left["captureRetrievedAt"])
        rs = rs or _time(right["observedAt"]) or _time(right["captureRetrievedAt"])
        re = re or _time(right["observedAt"]) or _time(right["captureRetrievedAt"])
        return max(ls, rs) <= min(le, re)
    delta = abs((_time(left["captureRetrievedAt"]) - _time(right["captureRetrievedAt"])).total_seconds()) / 60
    return delta <= minutes


def _direct_conflicts(resort: str, field: str, subject: dict[str, Any], candidates: list[dict[str, Any]],
                      selected: dict[str, Any] | None, as_of: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            if not (left["known"] and right["known"]) or left["observationRole"] != right["observationRole"] or not _values_differ(left["value"], right["value"]):
                continue
            same_capture = left["captureId"] == right["captureId"]
            if left["sourceId"] == right["sourceId"] and not same_capture:
                continue  # transition in one source's time series
            if not _overlap_or_window(left, right, policy["conflictWindowMinutes"]):
                continue
            ids = sorted([left["observationId"], right["observationId"]])
            descriptor = {"resortId": resort, "field": field, "subject": subject, "observationIds": ids}
            conflicts.append({"conflictId": stable_id("conflict", descriptor), "resortId": resort, "fieldFamily": field,
                              "subject": subject, "observationIds": ids, "conflictType": "known_value_disagreement",
                              "detectedAt": as_of, "selectedDisplayObservationId": selected.get("selectedObservationId") if selected else None,
                              "selectionReason": selected.get("selectionReason") if selected else None, "status": "unresolved",
                              "notes": ["Display selection does not resolve or remove the underlying evidence disagreement."]})
    unique = {row["conflictId"]: row for row in conflicts}
    return [unique[key] for key in sorted(unique)]


def resolve_export(export_payload: dict[str, Any], as_of: str | dt.datetime, policy: dict[str, Any], *,
                   generated_at: str | dt.datetime | None = None,
                   input_validator: Callable[[dict[str, Any]], None] = assert_valid_export,
                   _validate_result: bool = True) -> dict[str, Any]:
    """Resolve supplied evidence without mutation, I/O, wall-clock ranking, or repository state."""
    input_validator(export_payload)
    errors = validate_policy(policy, export_payload.get("sourceInventory", []))
    if errors:
        raise ValueError("\n".join(errors))
    if policy["sourceCatalogueRevision"] != export_payload["sourceInventoryRevision"]:
        raise ValueError("policy/source catalogue revision mismatch")
    as_of_dt = _time(_iso(as_of))
    generated = _iso(generated_at if generated_at is not None else dt.datetime.now(dt.timezone.utc))
    as_of_iso = _iso(as_of_dt)
    captures_all = {row["captureId"]: row for row in export_payload["captures"]}
    eligible_captures = {cid: row for cid, row in captures_all.items()
                         if _time(row["retrievedAt"]) <= as_of_dt and row["retrievalStatus"] in USABLE_CAPTURE_STATES}
    future_capture_ids = sorted(cid for cid, row in captures_all.items() if _time(row["retrievedAt"]) > as_of_dt)
    capture_detail_fields = ("captureId", "resortId", "sourceId", "retrievalStatus", "retrievedAt", "httpStatus", "warnings")
    def capture_detail(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**{key: row.get(key) for key in capture_detail_fields}, **extra}
    excluded_future_captures = [capture_detail(captures_all[cid], exclusionReason="retrieved_after_as_of") for cid in future_capture_ids]
    unusable_captures = [capture_detail(row, exclusionReason="retrieval_status_not_usable") for row in captures_all.values()
                         if _time(row["retrievedAt"]) <= as_of_dt and row["retrievalStatus"] not in USABLE_CAPTURE_STATES]
    capture_warnings = [capture_detail(row) for row in captures_all.values()
                        if _time(row["retrievedAt"]) <= as_of_dt and row.get("warnings")]
    sources = {row["sourceId"]: row for row in export_payload["sourceInventory"]}
    field_policies = {row["fieldFamily"]: row for row in policy["assetFieldPolicies"]}
    resort_ids = sorted({row["resortId"] for row in eligible_captures.values()})
    buckets: dict[str, dict[str, dict[tuple[Any, ...], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]]] = {
        resort: {name: {} for name in ("assetFields", "metrics", "snowmakingSignals", "aggregates", "narratives")} for resort in resort_ids}
    conflicts: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    aggregate_issues: list[dict[str, Any]] = []

    def add(resort: str, collection: str, key: tuple[Any, ...], subject: dict[str, Any], candidate: dict[str, Any], extra: dict[str, Any]) -> None:
        if key not in buckets[resort][collection]:
            buckets[resort][collection][key] = (subject, [], extra)
        buckets[resort][collection][key][1].append(candidate)

    for row in export_payload["assetStatusObservations"]:
        capture = eligible_captures.get(row["captureId"])
        if not capture or not _eligible(row, as_of_dt):
            continue
        subject = _subject(row, capture, asset=True)
        subject_key = subject["subjectId"]
        for field in ASSET_FIELDS:
            value = row[field]
            known = value not in field_policies[field]["knownUnknownValues"]
            candidate = _candidate(row, capture, sources[capture["sourceId"]], as_of_dt, policy, value, known)
            add(row["resortId"], "assetFields", (subject_key, field), subject, candidate, {})

    for row in export_payload["metricObservations"]:
        capture = eligible_captures.get(row["captureId"])
        if not capture or not _eligible(row, as_of_dt):
            continue
        lane = _lane(row["observationRole"], policy)
        subject = _subject(row, capture)
        if row["scope"] in {"asset", "location"} and not row.get("subjectId") and not row.get("locationLabel"):
            # Phase 2 cannot identify some repeated array metrics. Never collapse
            # those rows merely because their missing identities look alike.
            unresolved = f"unmapped-metric:{row['resortId']}:{capture['sourceId']}:{row['metricKey']}:{row['observationId']}"
            subject = {"kind": "source_scoped_unmapped", "resortId": row["resortId"], "subjectId": unresolved,
                       "sourceId": capture["sourceId"], "upstreamAssetId": None, "upstreamName": None}
        key = (row["metricKey"], row["scope"], subject.get("subjectId"), row.get("locationLabel"), row["unit"], lane)
        known = row["valueStatus"] in {"observed", "explicit_zero"}
        candidate = _candidate(row, capture, sources[capture["sourceId"]], as_of_dt, policy, row["value"], known)
        add(row["resortId"], "metrics", key, subject, candidate,
            {"unit": row["unit"], "scope": row["scope"], "locationLabel": row.get("locationLabel")})

    for row in export_payload["snowmakingObservations"]:
        capture = eligible_captures.get(row["captureId"])
        if not capture or not _eligible(row, as_of_dt):
            continue
        if row.get("subjectAssetId"):
            subject = {"kind": "canonical_asset", "resortId": row["resortId"], "subjectId": row["subjectAssetId"]}
        elif row["scope"] == "asset":
            shadow = {**row, "assetId": None, "assetClass": "run"}
            subject = _subject(shadow, capture, asset=True)
        else:
            subject = {"kind": "semantic_scope", "resortId": row["resortId"], "subjectId": row.get("area")}
        lane = _lane(row["observationRole"], policy)
        # Signal type, scope, asset/area, and lane are intentionally independent dimensions.
        key = (row["signalType"], row["scope"], subject["subjectId"], row.get("area"), row.get("unit"), lane)
        value = {"state": row["normalizedState"], "numericValue": row["numericValue"], "unit": row["unit"], "semanticMeaning": row["semanticMeaning"]}
        known = row["normalizedState"] not in {"unknown", "unavailable"} or row["numericValue"] is not None
        candidate = _candidate(row, capture, sources[capture["sourceId"]], as_of_dt, policy, value, known)
        add(row["resortId"], "snowmakingSignals", key, subject, candidate,
            {"unit": row.get("unit"), "scope": row["scope"], "locationLabel": row.get("area")})

    for row in export_payload["aggregateObservations"]:
        capture = eligible_captures.get(row["captureId"])
        if not capture:
            continue
        lane = _lane(row["observationRole"], policy)
        subject = {"kind": "aggregate_scope", "resortId": row["resortId"], "subjectId": row["assetClass"], "assetClass": row["assetClass"], "area": row.get("area")}
        key = (row["assetClass"], row.get("area"), row["status"], row["denominatorScope"])
        fraction = row["numerator"] / row["denominator"] if row["denominator"] is not None and row["denominator"] > 0 else None
        value = {"numerator": row["numerator"], "denominator": row["denominator"], "fraction": fraction}
        candidate = _candidate(row, capture, sources[capture["sourceId"]], as_of_dt, policy, value, True)
        add(row["resortId"], "aggregates", key, subject, candidate,
            {"unit": None, "scope": row.get("area"), "locationLabel": row.get("area"), "denominatorScope": row["denominatorScope"]})
        if row["numerator"] < 0 or (row["denominator"] is not None and row["denominator"] < 0):
            aggregate_issues.append({"type": "negative_value", "observationIds": [row["observationId"]]})
        if row["denominator"] is not None and row["numerator"] > row["denominator"]:
            aggregate_issues.append({"type": "numerator_exceeds_denominator", "observationIds": [row["observationId"]]})

    for row in export_payload["narratives"]:
        capture = eligible_captures.get(row["captureId"])
        if not capture or not _eligible(row, as_of_dt):
            continue
        lane = _lane(row["observationRole"], policy)
        subject = {"kind": "narrative", "resortId": row["resortId"], "subjectId": row["narrativeType"]}
        key = (row["narrativeType"], lane)
        value = {"headline": row["headline"], "body": row["body"], "author": row["author"]}
        candidate = _candidate(row, capture, sources[capture["sourceId"]], as_of_dt, policy, value, bool(row["headline"] is not None or row["body"] != ""))
        add(row["resortId"], "narratives", key, subject, candidate, {})

    # Shared-denominator status partition diagnostics are capture-local only.
    partitions: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in export_payload["aggregateObservations"]:
        if row["captureId"] in eligible_captures and row["denominator"] is not None and row["status"] in {"open", "closed", "standby", "on_hold", "delayed"}:
            partitions[(row["captureId"], row["assetClass"], row.get("area"), row["denominatorScope"], row["denominator"])].append(row)
    for key, rows in partitions.items():
        if sum(row["numerator"] for row in rows) > key[-1]:
            ids = sorted(row["observationId"] for row in rows)
            aggregate_issues.append({"type": "status_partition_exceeds_shared_denominator", "observationIds": ids})
            descriptor = {"resortId": rows[0]["resortId"], "field": "aggregate_partition", "observationIds": ids}
            conflicts.append({"conflictId": stable_id("conflict", descriptor), "resortId": rows[0]["resortId"], "fieldFamily": "aggregate_partition",
                              "subject": {"kind": "aggregate_scope", "resortId": rows[0]["resortId"], "subjectId": rows[0]["assetClass"]},
                              "observationIds": ids, "conflictType": "aggregate_invariant_violation", "detectedAt": as_of_iso,
                              "selectedDisplayObservationId": None, "selectionReason": None, "status": "unresolved",
                              "notes": ["Known status counts exceed their shared same-observation denominator."]})

    resorts = []
    for resort in resort_ids:
        resolved_collections: dict[str, list[dict[str, Any]]] = {}
        for collection, groups in buckets[resort].items():
            records = []
            for key in sorted(groups, key=lambda item: canonical_bytes(item)):
                subject, candidates, extra = groups[key]
                if collection == "assetFields":
                    field = key[1]
                elif collection == "aggregates":
                    field = f"aggregate:{key[2]}"
                else:
                    field = key[0]
                record = _record(key, field, subject, candidates, policy, field_policies.get(field), **extra)
                records.append(record)
                if collection in {"assetFields", "metrics"}:
                    conflicts.extend(_direct_conflicts(resort, field, subject, candidates, record["preferredSelection"], as_of_iso, policy))
                lane_map = {row["lane"]: row["selection"] for row in record["lanes"]}
                if collection in {"assetFields", "aggregates"} and lane_map.get("plan") and lane_map.get("actual") and lane_map["plan"]["known"] and lane_map["actual"]["known"]:
                    descriptor = {"resortId": resort, "field": field, "subject": subject, "plan": lane_map["plan"]["selectedObservationId"], "actual": lane_map["actual"]["selectedObservationId"]}
                    different = _values_differ(lane_map["plan"]["value"], lane_map["actual"]["value"])
                    if different:
                        comparisons.append({"comparisonId": stable_id("comparison", descriptor), "resortId": resort, "fieldFamily": field,
                                            "subject": subject, "plan": lane_map["plan"], "actual": lane_map["actual"],
                                            "different": True,
                                            "notes": ["Plan-versus-actual differences are operational comparisons, not direct conflicts."]})
            resolved_collections[collection] = records
        resorts.append({"resortId": resort, **resolved_collections})

    conflicts = sorted({row["conflictId"]: row for row in conflicts}.values(), key=lambda row: row["conflictId"])
    comparisons.sort(key=lambda row: row["comparisonId"])
    all_records = [record for resort in resorts for name in ("assetFields", "metrics", "snowmakingSignals", "aggregates", "narratives") for record in resort[name]]
    selections = [lane["selection"] for record in all_records for lane in record["lanes"]]
    unmapped = {record["subject"]["subjectId"] for record in all_records if record["subject"].get("kind") == "source_scoped_unmapped"}
    observations = {row["observationId"]: row for name in COLLECTIONS for row in export_payload[name]}
    active_input_conflicts = []
    excluded_input_conflicts = []
    for conflict in export_payload.get("conflicts", []):
        reasons = set()
        for observation_id in conflict.get("observationIds", []):
            observation = observations.get(observation_id)
            if observation is None:
                reasons.add("missing_observation")
                continue
            capture = captures_all.get(observation.get("captureId"))
            if capture is None:
                reasons.add("missing_capture")
            elif _time(capture["retrievedAt"]) > as_of_dt:
                reasons.add("capture_retrieved_after_as_of")
            elif capture["retrievalStatus"] not in USABLE_CAPTURE_STATES:
                reasons.add("capture_not_usable")
            if not _eligible(observation, as_of_dt):
                reasons.add("observation_effective_period_ineligible")
        if reasons:
            excluded_input_conflicts.append({"conflictId": conflict["conflictId"], "observationIds": sorted(conflict.get("observationIds", [])),
                                             "reasons": sorted(reasons)})
        else:
            active_input_conflicts.append(conflict)
    result = {
        "schemaVersion": "alpine.operations-resolved-view.v1", "producer": "portable operations-v2 Phase 3 resolver",
        "generatedAt": generated, "asOf": as_of_iso, "sourceEvidenceSchemaVersion": export_payload["schemaVersion"],
        "evidenceWindow": {"start": export_payload["windowStart"], "end": export_payload["windowEnd"]},
        "evidenceFingerprint": fingerprint(export_payload), "resolutionPolicyRevision": policy["revision"],
        "assetRegistryRevision": export_payload["assetRegistryRevision"], "sourceInventoryRevision": export_payload["sourceInventoryRevision"],
        "metricCatalogueRevision": export_payload["metricCatalogueRevision"], "resorts": resorts, "conflicts": conflicts,
        "inputEvidenceConflicts": active_input_conflicts, "planActualComparisons": comparisons,
        "diagnostics": {"eligibleCaptureCount": len(eligible_captures), "excludedFutureCaptureIds": future_capture_ids,
                        "excludedFutureCaptures": sorted(excluded_future_captures, key=lambda row: row["captureId"]),
                        "unusableCaptures": sorted(unusable_captures, key=lambda row: row["captureId"]),
                        "captureWarnings": sorted(capture_warnings, key=lambda row: row["captureId"]),
                        "excludedInputEvidenceConflicts": sorted(excluded_input_conflicts, key=lambda row: row["conflictId"]),
                        "staleSelectionCount": sum(item["freshnessState"] == "stale" for item in selections),
                        "unresolvedSelectionCount": sum(not item["known"] for item in selections), "unmappedSubjectCount": len(unmapped),
                        "aggregateIssues": sorted(aggregate_issues, key=lambda row: canonical_bytes(row)),
                        "warnings": sorted(set(export_payload.get("diagnostics", {}).get("warnings", [])))}
    }
    if _validate_result:
        from .resolved_schema import assert_valid_resolved
        assert_valid_resolved(result, export_payload, policy)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_path", required=True, type=Path)
    parser.add_argument("--out", dest="output_path", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--generated-at", help="Inject output timestamp; defaults to current UTC time")
    parser.add_argument("--policy", type=Path, default=Path(__file__).resolve().parents[1] / "config/operations_resolution_policy_v1.json")
    args = parser.parse_args(argv)
    evidence = json.loads(args.input_path.read_text())
    result = resolve_export(evidence, args.as_of, load_policy(args.policy), generated_at=args.generated_at)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
