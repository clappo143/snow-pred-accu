"""Structural and semantic validation for producer-owned v2 registries."""
from __future__ import annotations
import hashlib, json, os, re
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parents[1]
ASSETS_PATH=ROOT/"config/operations_assets_v1.json"; SOURCES_PATH=ROOT/"config/operations_sources_v2.json"; METRICS_PATH=ROOT/"config/operations_metrics_v2.json"
IDENTITIES_PATH=ROOT/"contracts/alpine-resort-identities.v1.json"
def canonical(value:Any)->bytes:return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def content_hash(payload:dict[str,Any])->str:
 return hashlib.sha256(canonical({k:v for k,v in payload.items() if k not in {"contentHash","revision"}})).hexdigest()
def identity_path(explicit:Path|None=None)->Path:
 candidates=[explicit,Path(os.environ["ALPINE_RESORT_IDENTITIES_PATH"]) if os.environ.get("ALPINE_RESORT_IDENTITIES_PATH") else None,IDENTITIES_PATH]
 for path in candidates:
  if path and path.exists():return path
 raise FileNotFoundError("Resort identities not found; pass identity_path, set ALPINE_RESORT_IDENTITIES_PATH, or restore contracts/alpine-resort-identities.v1.json")
def identity_ids(path:Path|None=None)->set[str]:return {r["canonicalId"] for r in json.loads(identity_path(path).read_text())["resorts"]}
def schema_errors(payload:dict[str,Any],schema_name:str)->list[str]:
 schema=json.loads((ROOT/"contracts"/schema_name).read_text())
 return [f"{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(payload)]
def expand_comma_delimited(raw_value:Any)->tuple[list[str],list[str]]:
 """Execute the reviewed list shape without performing production normalization."""
 if not isinstance(raw_value,str):raise TypeError("comma-delimited source value must be a string")
 names=[part.strip() for part in raw_value.split(",") if part.strip()]
 seen:set[str]=set();duplicates:list[str]=[]
 for name in names:
  key=" ".join(name.casefold().split())
  if key in seen:duplicates.append(name)
  seen.add(key)
 return names,duplicates
_WIND_RANGE=re.compile(r"^\s*(?:[^0-9]*?\s+)?([0-9]+(?:\.[0-9]+)?)\s*(?:-\s*([0-9]+(?:\.[0-9]+)?))?\s*(?:km\s*/?\s*h|kmh)?\s*$",re.IGNORECASE)
def parse_wind_speed_range(raw_value:Any)->tuple[float,float]|None:
 """Dry-run the declared Hotham range grammar; malformed values never fabricate numbers."""
 if not isinstance(raw_value,str) or not raw_value.strip():return None
 match=_WIND_RANGE.fullmatch(raw_value)
 if not match:return None
 low=float(match.group(1));high=float(match.group(2) or match.group(1))
 if high<low:return None
 return low,high
def validate_asset_registry(payload:dict[str,Any],resort_ids:set[str],source_registry:dict[str,Any]|None=None)->list[str]:
 errors=schema_errors(payload,"operations-assets.v1.schema.json"); ids=set(); mappings=set(); names=set(); groups={}
 sources={s["sourceId"]:s for s in (source_registry or {}).get("sources",[])}
 if payload.get("contentHash")!=content_hash(payload) or payload.get("revision")!=content_hash(payload):errors.append("asset registry contentHash/revision mismatch")
 for asset in payload.get("assets",[]):
  aid=asset.get("assetId")
  if aid in ids:errors.append(f"duplicate assetId: {aid}")
  ids.add(aid)
  if asset.get("resortId") not in resort_ids:errors.append(f"unknown resortId: {asset.get('resortId')}")
  if asset.get("physicalAssetGroupId"):groups[aid]=asset["physicalAssetGroupId"]
  for mapping in asset.get("sourceMappings",[]):
   source=sources.get(mapping.get("sourceId")) if sources else None
   if sources and not source:errors.append(f"unknown mapping sourceId: {mapping.get('sourceId')}")
   elif source and source["resortId"]!=asset.get("resortId"):errors.append(f"mapping source resort mismatch: {aid}")
   upstream=mapping.get("upstreamAssetId")
   key=(mapping.get("sourceId"),str(upstream)) if upstream is not None else (mapping.get("sourceId"),asset.get("resortId"),asset.get("assetClass"),mapping.get("upstreamName"))
   target=mappings if upstream is not None else names
   if key in target:errors.append(f"duplicate source mapping: {key}")
   target.add(key)
 for start in groups:
  seen=set(); node=start
  while node in groups:
   if node in seen:errors.append(f"cyclic physical group reference: {start}");break
   seen.add(node);node=groups[node]
 return errors
def validate_source_registry(payload:dict[str,Any],resort_ids:set[str])->list[str]:
 errors=schema_errors(payload,"operations-sources.v2.schema.json");seen=set()
 if payload.get("contentHash")!=content_hash(payload) or payload.get("revision")!=content_hash(payload):errors.append("source registry contentHash/revision mismatch")
 metric_types={m["metricKey"]:m["valueType"] for m in json.loads(METRICS_PATH.read_text()).get("metrics",[])}
 export_schema=json.loads((ROOT/"contracts/operations-export.v2.schema.json").read_text())
 collection_defs={"captures":"capture","metricObservations":"metric","snowmakingObservations":"snowmaking","assetStatusObservations":"assetStatus","aggregateObservations":"aggregate","narratives":"narrative","assets":"asset","rawPayloads":"rawPayload"}
 for source in payload.get("sources",[]):
  if source.get("sourceId") in seen:errors.append(f"duplicate sourceId: {source.get('sourceId')}")
  seen.add(source.get("sourceId"))
  if source.get("resortId") not in resort_ids:errors.append(f"unknown source resortId: {source.get('resortId')}")
  paths=set()
  coverage=source.get("fieldCoverage",[]);known_paths={field.get("path") for field in coverage}
  for field in coverage:
   path=field.get("path")
   if path in paths:errors.append(f"{source.get('sourceId')}: duplicate or contradictory field spec {path}")
   paths.add(path)
   if field.get("disposition")!="normalized":continue
   destinations=[]
   if field.get("destinationCollection") is not None:destinations.append(field)
   destinations.extend(field.get("emissions",[]))
   signatures=set()
   for destination_spec in destinations:
    collection=destination_spec.get("destinationCollection");destination=destination_spec.get("destinationField");definition=collection_defs.get(collection)
    if not definition or destination not in export_schema.get("$defs",{}).get(definition,{}).get("properties",{}):errors.append(f"{source.get('sourceId')} {path}: destination absent from export schema: {collection}.{destination}")
    metric_key=destination_spec.get("metricKey")
    if collection=="metricObservations":
     if metric_key not in metric_types:errors.append(f"{source.get('sourceId')} {path}: metric destination absent from catalogue")
     elif destination_spec.get("normalizedValueType")!=metric_types[metric_key]:errors.append(f"{source.get('sourceId')} {path}: normalized metric type {destination_spec.get('normalizedValueType')} != catalogue {metric_types[metric_key]}")
    signature=(collection,destination,metric_key,destination_spec.get("observationRole"),destination_spec.get("scope"),json.dumps(destination_spec.get("aggregateSpec"),sort_keys=True),json.dumps(destination_spec.get("assetSpec"),sort_keys=True),json.dumps(destination_spec.get("snowmakingSpec"),sort_keys=True))
    if signature in signatures:errors.append(f"{source.get('sourceId')} {path}: contradictory duplicate emission")
    signatures.add(signature)
    if source.get("sourceRole")=="daily_report" and destination_spec.get("observationRole")=="live_actual":errors.append(f"{source.get('sourceId')} {path}: official daily-report evidence cannot be live_actual")
   list_spec=field.get("listExpansionSpec")
   if list_spec:
    if field.get("sourceValueType")!="comma_delimited_string":errors.append(f"{source.get('sourceId')} {path}: list expansion must use comma_delimited_string, not scalar parsing")
    if not field.get("emissions"):errors.append(f"{source.get('sourceId')} {path}: list expansion requires emissions")
    derivation=list_spec.get("aggregateDerivation",{})
    if not derivation.get("sameCapture"):errors.append(f"{source.get('sourceId')} {path}: list aggregate must be same-capture")
    for denominator_path in derivation.get("denominatorSourceFields",[]):
     if denominator_path not in known_paths:errors.append(f"{source.get('sourceId')} {path}: aggregate denominator source absent: {denominator_path}")
   elif field.get("sourceValueType")=="comma_delimited_string":errors.append(f"{source.get('sourceId')} {path}: comma-delimited field requires listExpansionSpec")
 return errors
def validate_metric_catalogue(payload:dict[str,Any])->list[str]:
 errors=schema_errors(payload,"operations-metrics.v2.schema.json");seen=set()
 if payload.get("contentHash")!=content_hash(payload):errors.append("metric catalogue contentHash mismatch")
 for metric in payload.get("metrics",[]):
  if metric.get("metricKey") in seen:errors.append(f"duplicate metricKey: {metric.get('metricKey')}")
  seen.add(metric.get("metricKey"))
 return errors
def load_registries(identity_path_arg:Path|None=None)->tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
 assets=json.loads(ASSETS_PATH.read_text());sources=json.loads(SOURCES_PATH.read_text());metrics=json.loads(METRICS_PATH.read_text());ids=identity_ids(identity_path_arg)
 errors=validate_source_registry(sources,ids)+validate_metric_catalogue(metrics)+validate_asset_registry(assets,ids,sources)
 if errors:raise ValueError("\n".join(errors))
 return assets,sources,metrics
