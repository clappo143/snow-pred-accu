"""Draft 2020-12 structural plus producer-specific v2 validation."""
from __future__ import annotations
import datetime as dt, json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker
from .registry import ROOT, load_registries

EXPORT_VERSION="alpine.operations-export.v2"
COLLECTIONS=("metricObservations","snowmakingObservations","assetStatusObservations","aggregateObservations","narratives")
def structural_errors(payload:dict[str,Any])->list[str]:
 schema=json.loads((ROOT/"contracts/operations-export.v2.schema.json").read_text())
 return [f"schema {'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(payload)]
def stamp(value:str|None):return dt.datetime.fromisoformat(value.replace("Z","+00:00")) if value else None
def interval(row:dict[str,Any],errors:list[str]):
 try:
  start,end=stamp(row.get("effectiveFrom")),stamp(row.get("effectiveTo"))
  if start and end and start>end:errors.append(f"{row.get('observationId')}: effectiveFrom after effectiveTo")
 except (ValueError,TypeError):pass # structural format error is authoritative
def validate_export(payload:dict[str,Any])->list[str]:
 errors=structural_errors(payload)
 try: assets_registry,sources_registry,metrics_registry=load_registries()
 except Exception as exc:return errors+[f"registry validation failed: {exc}"]
 sources={s.get("sourceId"):s for s in payload.get("sourceInventory",[]) if isinstance(s,dict) and s.get("sourceId")}; canonical_sources={s["sourceId"]:s for s in sources_registry["sources"]}
 assets={a.get("assetId"):a for a in payload.get("assets",[]) if isinstance(a,dict) and a.get("assetId")}; canonical_assets={a["assetId"]:a for a in assets_registry["assets"]};metric_defs={m["metricKey"]:m for m in metrics_registry["metrics"]}
 if len(assets)!=len(payload.get("assets",[])):errors.append("duplicate or malformed embedded asset IDs")
 if len(sources)!=len(payload.get("sourceInventory",[])):errors.append("duplicate or malformed embedded source IDs")
 if payload.get("assetRegistryRevision")!=assets_registry["contentHash"]:errors.append("assetRegistryRevision does not match producer registry")
 if payload.get("sourceInventoryRevision")!=sources_registry["contentHash"]:errors.append("sourceInventoryRevision does not match producer source registry")
 if payload.get("metricCatalogueRevision")!=metrics_registry["contentHash"]:errors.append("metricCatalogueRevision does not match producer metric catalogue")
 for aid,asset in assets.items():
  if aid not in canonical_assets:errors.append(f"embedded asset {aid}: unknown for declared revision")
  elif asset!=canonical_assets[aid]:errors.append(f"embedded asset {aid}: content differs from declared revision")
 if payload.get("assetRegistryCompleteness")=="complete" and (set(assets)!=set(canonical_assets) or any(assets.get(k)!=v for k,v in canonical_assets.items())):errors.append("complete asset registry must exactly equal canonical revision")
 for sid,source in sources.items():
  if sid not in canonical_sources:errors.append(f"embedded source {sid}: unknown for declared revision")
  elif source!=canonical_sources[sid]:errors.append(f"embedded source {sid}: content differs from declared revision")
 if payload.get("sourceInventoryCompleteness")=="complete" and (set(sources)!=set(canonical_sources) or any(sources.get(k)!=v for k,v in canonical_sources.items())):errors.append("complete source inventory must exactly equal canonical revision")
 try:
  if stamp(payload.get("windowStart"))>stamp(payload.get("windowEnd")):errors.append("windowStart after windowEnd")
 except (ValueError,TypeError):pass
 seen=set(); captures={}; observation_resorts={}
 for capture in payload.get("captures",[]):
  cid=capture.get("captureId")
  if cid in seen:errors.append(f"duplicate record id: {cid}")
  seen.add(cid);captures[cid]=capture
  source=sources.get(capture.get("sourceId")); canonical=canonical_sources.get(capture.get("sourceId"))
  if not source or not canonical:errors.append(f"capture {cid}: unknown sourceId {capture.get('sourceId')}")
  else:
   for key in ("resortId","layer","sourceRole"):
    capture_key="sourceLayer" if key=="layer" else key
    if capture.get(capture_key)!=source.get(key) or source.get(key)!=canonical.get(key):errors.append(f"capture {cid}: source registry {key} mismatch")
  for key in ("assetRegistryRevision","sourceInventoryRevision","metricCatalogueRevision"):
   if capture.get(key)!=payload.get(key):errors.append(f"capture {cid}: {key} mismatch")
  if capture.get("retrievalStatus") in {"ok","partial","not_modified"}:
   matches=[r for r in payload.get("rawPayloads",[]) if r.get("payloadHash")==capture.get("payloadHash") and r.get("rawPayloadRef")==capture.get("rawPayloadRef") and r.get("sourceId")==capture.get("sourceId")]
   if not matches:errors.append(f"capture {cid}: usable raw payload descriptor required")
 for descriptor in payload.get("rawPayloads",[]):
  source=sources.get(descriptor.get("sourceId"))
  if not source:errors.append(f"raw payload {descriptor.get('payloadHash')}: source missing from embedded inventory")
  elif descriptor.get("sourceUrl")!=source.get("url"):errors.append(f"raw payload {descriptor.get('payloadHash')}: sourceUrl differs from source registry")
 for name in COLLECTIONS:
  for row in payload.get(name,[]):
   oid=row.get("observationId")
   if oid in seen:errors.append(f"duplicate record id: {oid}")
   seen.add(oid);observation_resorts[oid]=row.get("resortId");interval(row,errors)
   capture=captures.get(row.get("captureId"))
   if not capture:errors.append(f"{oid}: unknown captureId")
   elif row.get("resortId")!=capture.get("resortId"):errors.append(f"{oid}: observation/capture resort mismatch")
 for row in payload.get("metricObservations",[]):
  definition=metric_defs.get(row.get("metricKey")); value=row.get("value");status=row.get("valueStatus")
  if not definition:errors.append(f"unknown metricKey: {row.get('metricKey')}");continue
  if row.get("unit")!=definition["unit"]:errors.append(f"{row.get('metricKey')}: invalid unit")
  kind=definition["valueType"]
  valid=(kind=="number" and isinstance(value,(int,float)) and not isinstance(value,bool)) or (kind=="integer" and isinstance(value,int) and not isinstance(value,bool)) or (kind=="string" and isinstance(value,str)) or (kind=="date" and isinstance(value,str) and FormatChecker().conforms(value,"date")) or (kind=="date-time" and isinstance(value,str) and FormatChecker().conforms(value,"date-time")) or (kind=="boolean" and isinstance(value,bool))
  if status in {"observed","explicit_zero"} and not valid:errors.append(f"{row.get('observationId')}: wrong metric value type")
  if status=="explicit_zero" and value!=0:errors.append(f"{row.get('observationId')}: explicit_zero requires zero")
  if status in {"blank","unavailable","not_applicable","unknown"} and value is not None:errors.append(f"{row.get('observationId')}: {status} requires null")
 for name in ("assetStatusObservations","snowmakingObservations"):
  for row in payload.get(name,[]):
   aid=row.get("assetId") if name=="assetStatusObservations" else row.get("subjectAssetId")
   if aid:
    asset=assets.get(aid)
    if not asset:errors.append(f"{row.get('observationId')}: unknown mapped asset {aid}")
    elif asset.get("resortId")!=row.get("resortId") or (name=="assetStatusObservations" and asset.get("assetClass")!=row.get("assetClass")):errors.append(f"{row.get('observationId')}: asset resort/class mismatch")
   elif name=="snowmakingObservations" and row.get("scope")=="asset" and (not (row.get("upstreamAssetId") or row.get("upstreamName")) or not row.get("warnings")):
    errors.append(f"{row.get('observationId')}: unmapped snowmaking asset requires upstream identity and warning")
 snow_by_id={x.get("observationId"):x for x in payload.get("snowmakingObservations",[])}
 for row in payload.get("assetStatusObservations",[]):
  if not row.get("assetId") and (not (row.get("upstreamAssetId") or row.get("upstreamName")) or not row.get("warnings")):errors.append(f"{row.get('observationId')}: unmapped asset requires upstream identity and warning")
  for ref in row.get("snowmakingObservationIds",[]):
   snow=snow_by_id.get(ref)
   if not snow:errors.append(f"{row.get('observationId')}: unknown snowmaking observation {ref}");continue
   if snow.get("captureId")!=row.get("captureId"):errors.append(f"{row.get('observationId')}: cross-capture snowmaking reference {ref}")
   if snow.get("resortId")!=row.get("resortId"):errors.append(f"{row.get('observationId')}: cross-resort snowmaking reference {ref}")
   if snow.get("scope")!="asset":errors.append(f"{row.get('observationId')}: snowmaking reference {ref} must be asset scoped")
   if row.get("assetId") and snow.get("subjectAssetId")!=row.get("assetId"):errors.append(f"{row.get('observationId')}: cross-asset snowmaking reference {ref}")
   if not row.get("assetId") and (row.get("upstreamAssetId"),row.get("upstreamName"))!=(snow.get("upstreamAssetId"),snow.get("upstreamName")):errors.append(f"{row.get('observationId')}: snowmaking upstream identity mismatch {ref}")
 mapping_ids=set();mapping_names=set()
 for asset in payload.get("assets",[]):
  for mapping in asset.get("sourceMappings",[]):
   source=sources.get(mapping.get("sourceId"))
   if not source:errors.append(f"asset {asset.get('assetId')}: unknown mapping source")
   elif source.get("resortId")!=asset.get("resortId"):errors.append(f"asset {asset.get('assetId')}: mapping source resort mismatch")
   upstream=mapping.get("upstreamAssetId");key=(mapping.get("sourceId"),str(upstream)) if upstream is not None else (mapping.get("sourceId"),asset.get("resortId"),asset.get("assetClass"),mapping.get("upstreamName"));target=mapping_ids if upstream is not None else mapping_names
   if key in target:errors.append(f"duplicate source mapping: {key}")
   target.add(key)
 for conflict in payload.get("conflicts",[]):
  refs=conflict.get("observationIds",[])
  if any(ref not in observation_resorts for ref in refs):errors.append(f"conflict {conflict.get('conflictId')}: unknown observation reference")
  if any(observation_resorts.get(ref)!=conflict.get("resortId") for ref in refs):errors.append(f"conflict {conflict.get('conflictId')}: spans different resorts")
  observations={x.get("observationId"):x for name in COLLECTIONS for x in payload.get(name,[])}
  roles={observations.get(ref,{}).get("observationRole") for ref in refs}
  if len(roles)>1:errors.append(f"conflict {conflict.get('conflictId')}: different observation roles are not a direct conflict")
  intervals=[]
  for ref in refs:
   row=observations.get(ref,{})
   intervals.append((stamp(row.get("effectiveFrom")) or stamp(row.get("observedAt")),stamp(row.get("effectiveTo")) or stamp(row.get("observedAt"))))
  if intervals and all(a and b for a,b in intervals) and max(a for a,_ in intervals)>min(b for _,b in intervals):errors.append(f"conflict {conflict.get('conflictId')}: effective times do not overlap")
 return errors
def assert_valid_export(payload:dict[str,Any])->None:
 errors=validate_export(payload)
 if errors:raise ValueError("\n".join(errors))
def load_and_validate(path:Path)->dict[str,Any]:
 payload=json.loads(path.read_text());assert_valid_export(payload);return payload
