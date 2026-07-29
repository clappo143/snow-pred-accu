"""Structural and provenance-aware semantic validation for resolved view v1."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def structural_errors(payload: dict[str, Any], schema_path: str | Path | None = None) -> list[str]:
    path = Path(schema_path) if schema_path else Path(__file__).resolve().parents[1] / "contracts/operations-resolved-view.v1.schema.json"
    schema = json.loads(path.read_text())
    return [f"resolved schema {'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
            for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)]


def validate_resolved(payload: dict[str, Any], evidence: dict[str, Any], policy: dict[str, Any], *,
                      schema_path: str | Path | None = None) -> list[str]:
    from .resolve_v2 import ASSET_FIELDS, _lane, _time, canonical_bytes, fingerprint

    errors = structural_errors(payload, schema_path)
    if payload.get("evidenceFingerprint") != fingerprint(evidence):
        errors.append("evidence fingerprint mismatch")
    if payload.get("resolutionPolicyRevision") != policy.get("revision"):
        errors.append("policy revision mismatch")
    if payload.get("sourceInventoryRevision") != evidence.get("sourceInventoryRevision"):
        errors.append("source catalogue revision mismatch")
    for key in ("assetRegistryRevision", "metricCatalogueRevision"):
        if payload.get(key) != evidence.get(key):
            errors.append(f"{key} mismatch")
    observations = {row["observationId"]: row for name in ("metricObservations", "snowmakingObservations", "assetStatusObservations", "aggregateObservations", "narratives") for row in evidence.get(name, [])}
    captures = {row["captureId"]: row for row in evidence.get("captures", [])}
    fields = {row["fieldFamily"]: row for row in policy.get("assetFieldPolicies", [])}
    try:
        as_of = _time(payload.get("asOf"))
    except (TypeError, ValueError):
        as_of = None
    resolution_keys: set[tuple[str, str]] = set()
    semantic_records: set[tuple[Any, ...]] = set()
    conflict_ids: set[str] = set()

    def validate_selection(selection: dict[str, Any] | None, resort: str, lane: str, field: str) -> None:
        if selection is None:
            return
        oid = selection.get("selectedObservationId")
        row = observations.get(oid)
        if not row:
            errors.append(f"selection references absent observation: {oid}")
            return
        capture = captures.get(row.get("captureId"))
        if not capture:
            errors.append(f"selection {oid}: source capture absent")
            return
        if selection.get("captureId") != capture["captureId"] or selection.get("sourceId") != capture["sourceId"]:
            errors.append(f"selection {oid}: capture/source mismatch")
        if row.get("resortId") != resort or capture.get("resortId") != resort:
            errors.append(f"selection {oid}: cross-resort subject")
        try:
            if _lane(row["observationRole"], policy) != lane:
                errors.append(f"selection {oid}: invalid lane/role combination")
        except ValueError as exc:
            errors.append(str(exc))
        if as_of and _time(capture["retrievedAt"]) > as_of:
            errors.append(f"selection {oid}: future capture included before retrieval time")
        for alternate in selection.get("alternateCandidateObservationIds", []):
            if alternate not in observations:
                errors.append(f"selection {oid}: alternate observation absent: {alternate}")
                continue
            alternate_capture = captures.get(observations[alternate].get("captureId"))
            if alternate_capture and as_of and _time(alternate_capture["retrievedAt"]) > as_of:
                errors.append(f"selection {oid}: future alternate included before retrieval time: {alternate}")
        if field in ASSET_FIELDS:
            family = fields.get(field, {})
            expected_known = row.get(field) not in family.get("knownUnknownValues", [])
            if selection.get("known") != expected_known:
                errors.append(f"selection {oid}: known flag disagrees with field contract")
        if not selection.get("known"):
            def is_known(candidate: dict[str, Any]) -> bool:
                if field in ASSET_FIELDS:
                    return candidate.get(field) not in fields.get(field, {}).get("knownUnknownValues", [])
                if "valueStatus" in candidate:
                    return candidate.get("valueStatus") in {"observed", "explicit_zero"}
                if "normalizedState" in candidate:
                    return candidate.get("normalizedState") not in {"unknown", "unavailable"} or candidate.get("numericValue") is not None
                if "numerator" in candidate:
                    return True
                if "body" in candidate:
                    return candidate.get("headline") is not None or candidate.get("body") != ""
                return False
            known_alternates = [alternate for alternate in selection.get("alternateCandidateObservationIds", [])
                                if alternate in observations and is_known(observations[alternate])]
            if known_alternates and "explicit policy override" not in selection.get("selectionReason", ""):
                errors.append(f"selection {oid}: selected unknown over eligible known candidate without explicit policy reason")

    for resort in payload.get("resorts", []):
        rid = resort.get("resortId")
        for collection in ("assetFields", "metrics", "snowmakingSignals", "aggregates", "narratives"):
            for record in resort.get(collection, []):
                duplicate_key = (rid, record.get("resolutionKey"))
                if duplicate_key in resolution_keys:
                    errors.append(f"duplicate resolved subject-field-lane record: {duplicate_key}")
                resolution_keys.add(duplicate_key)
                if record.get("subject", {}).get("resortId") != rid:
                    errors.append(f"resolved record {record.get('resolutionKey')}: cross-resort subject")
                lane_rows = record.get("lanes", [])
                lanes = [row.get("lane") for row in lane_rows]
                if len(lanes) != len(set(lanes)):
                    errors.append(f"resolved record {record.get('resolutionKey')}: duplicate lane")
                for lane_row in lane_rows:
                    semantic_key = (rid, collection, canonical_bytes(record.get("subject", {})), record.get("fieldFamily"), lane_row.get("lane"),
                                    record.get("unit"), record.get("scope"), record.get("locationLabel"), record.get("denominatorScope"))
                    if semantic_key in semantic_records:
                        errors.append(f"duplicate resolved subject-field-lane record: {record.get('resolutionKey')}:{lane_row.get('lane')}")
                    semantic_records.add(semantic_key)
                    validate_selection(lane_row.get("selection"), rid, lane_row.get("lane"), record.get("fieldFamily"))
                preferred = record.get("preferredSelection")
                if preferred:
                    matching = [row.get("selection") for row in lane_rows
                                if row.get("selection", {}).get("selectedObservationId") == preferred.get("selectedObservationId")]
                    if not matching:
                        errors.append(f"resolved record {record.get('resolutionKey')}: preferred selection absent from lanes")
                    elif not any(canonical_bytes(preferred) == canonical_bytes(selection) for selection in matching):
                        errors.append(f"resolved record {record.get('resolutionKey')}: preferred selection differs from selected lane entry")
                if collection == "aggregates":
                    for lane_row in lane_rows:
                        value = lane_row.get("selection", {}).get("value", {})
                        if value.get("fraction") is not None and (value.get("denominator") is None or value.get("denominator") <= 0):
                            errors.append(f"resolved aggregate {record.get('resolutionKey')}: fraction with null/zero denominator")

    for conflict in payload.get("conflicts", []):
        cid = conflict.get("conflictId")
        if cid in conflict_ids:
            errors.append(f"duplicate conflict: {cid}")
        conflict_ids.add(cid)
        refs = conflict.get("observationIds", [])
        rows = [observations.get(ref) for ref in refs]
        if any(row is None for row in rows):
            errors.append(f"conflict {cid}: absent observation reference")
            continue
        if any(row.get("resortId") != conflict.get("resortId") for row in rows):
            errors.append(f"conflict {cid}: incompatible subjects/resorts")
        if conflict.get("conflictType") == "known_value_disagreement":
            roles = {row["observationRole"] for row in rows}
            if len(roles) != 1:
                errors.append(f"conflict {cid}: incompatible roles")
            if all("unit" in row for row in rows) and len({row.get("unit") for row in rows}) != 1:
                errors.append(f"conflict {cid}: incompatible units")
            if all("scope" in row for row in rows) and len({row.get("scope") for row in rows}) != 1:
                errors.append(f"conflict {cid}: incompatible scopes")
            if all("locationLabel" in row for row in rows) and len({row.get("locationLabel") for row in rows}) != 1:
                errors.append(f"conflict {cid}: incompatible metric locations")
            if all("assetId" in row for row in rows):
                mapped = {row.get("assetId") for row in rows if row.get("assetId")}
                if len(mapped) > 1:
                    errors.append(f"conflict {cid}: incompatible canonical subjects")
                if not mapped:
                    identities = {(captures[row["captureId"]]["sourceId"], row.get("assetClass"), row.get("upstreamAssetId"), row.get("upstreamName")) for row in rows}
                    if len(identities) > 1:
                        errors.append(f"conflict {cid}: incompatible source-scoped unmapped subjects")
            if all("metricKey" in row for row in rows) and len({(row.get("metricKey"), row.get("subjectId")) for row in rows}) != 1:
                errors.append(f"conflict {cid}: incompatible metric subjects")
        selected_conflict = conflict.get("selectedDisplayObservationId")
        if selected_conflict is not None and selected_conflict not in observations:
            errors.append(f"conflict {cid}: selected display observation absent")
    comparison_ids = [row.get("comparisonId") for row in payload.get("planActualComparisons", [])]
    if len(comparison_ids) != len(set(comparison_ids)):
        errors.append("duplicate plan-versus-actual comparison")
    for comparison in payload.get("planActualComparisons", []):
        validate_selection(comparison.get("plan"), comparison.get("resortId"), "plan", comparison.get("fieldFamily"))
        validate_selection(comparison.get("actual"), comparison.get("resortId"), "actual", comparison.get("fieldFamily"))
    # The full document is a deterministic derivation. Reconstruct it with
    # result validation disabled to avoid recursion, then compare ordinary JSON
    # values section by section. This binds every selection, alternate, ID,
    # comparison, conflict, diagnostic, timestamp, and metadata field to the
    # supplied evidence, policy, as-of, and generated time.
    try:
        from .resolve_v2 import resolve_export
        expected = resolve_export(evidence, payload.get("asOf"), policy, generated_at=payload.get("generatedAt"), _validate_result=False)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"unable to reconstruct deterministic resolved view: {exc}")
    else:
        for key in expected:
            if canonical_bytes(payload.get(key)) != canonical_bytes(expected[key]):
                errors.append(f"derived section mismatch: {key}")
    return errors


def assert_valid_resolved(payload: dict[str, Any], evidence: dict[str, Any], policy: dict[str, Any], *,
                          schema_path: str | Path | None = None) -> None:
    errors = validate_resolved(payload, evidence, policy, schema_path=schema_path)
    if errors:
        raise ValueError("\n".join(errors))
