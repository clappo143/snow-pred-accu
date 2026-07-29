#!/usr/bin/env python3
"""Deterministically harden the v2 export schema from its component schemas."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/"contracts/operations-export.v2.schema.json"; s=json.loads(p.read_text())
asset_schema=json.loads((ROOT/"contracts/operations-assets.v1.schema.json").read_text())
source_schema=json.loads((ROOT/"contracts/operations-sources.v2.schema.json").read_text())
metrics=json.loads((ROOT/"config/operations_metrics_v2.json").read_text())["metrics"]
for field in ["assetRegistryRevision","assetRegistryCompleteness","sourceInventoryRevision","sourceInventoryCompleteness","metricCatalogueRevision"]:
 if field not in s["required"]: s["required"].append(field)
s["properties"].update({
 "assetRegistryRevision":{"type":"string","pattern":"^[a-f0-9]{64}$"},"sourceInventoryRevision":{"type":"string","pattern":"^[a-f0-9]{64}$"},"metricCatalogueRevision":{"type":"string","pattern":"^[a-f0-9]{64}$"},"assetRegistryCompleteness":{"enum":["partial","complete"]},"sourceInventoryCompleteness":{"enum":["partial","complete"]},
 "assets":{"type":"array","items":{"$ref":"#/$defs/asset"}},
 "sourceInventory":{"type":"array","items":{"$ref":"#/$defs/source"}},
 "rawPayloads":{"type":"array","items":{"$ref":"#/$defs/rawPayload"}},
 "diagnostics":{"$ref":"#/$defs/diagnostics"}
})
s["$defs"]["asset"]=asset_schema["$defs"]["asset"]
s["$defs"]["mapping"]=asset_schema["$defs"]["mapping"]
s["$defs"]["sourceCoverage"]=source_schema["$defs"]["coverage"]
for name in ("aggregateSpec","assetSpec","snowSpec","emission","listExpansionSpec"):
 s["$defs"][name]=source_schema["$defs"][name]
source=source_schema["$defs"]["source"]
source=json.loads(json.dumps(source).replace('#/$defs/coverage','#/$defs/sourceCoverage'))
s["$defs"]["source"]=source
s["$defs"]["rawPayload"]={"type":"object","additionalProperties":False,"required":["payloadHash","sourceId","sourceUrl","firstCapturedAt","responseAt","httpStatus","contentType","rawPayloadRef","parserVersion"],"properties":{"payloadHash":{"type":"string","pattern":"^[a-f0-9]{64}$"},"sourceId":{"type":"string"},"sourceUrl":{"type":"string","format":"uri"},"firstCapturedAt":{"$ref":"#/$defs/time"},"responseAt":{"$ref":"#/$defs/time"},"httpStatus":{"type":["integer","null"]},"contentType":{"type":["string","null"]},"rawPayloadRef":{"type":"string","minLength":1},"parserVersion":{"type":"string"}}}
s["$defs"]["diagnostics"]={"type":"object","additionalProperties":False,"required":["warnings","unknownSourceFields","unmappedAssetCount"],"properties":{"warnings":{"type":"array","items":{"type":"string"}},"unknownSourceFields":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["sourceId","path","captureId"],"properties":{"sourceId":{"type":"string"},"path":{"type":"string"},"captureId":{"type":"string"}}}},"unmappedAssetCount":{"type":"integer","minimum":0}}}
s["$defs"]["metric"]["properties"]["metricKey"]={"enum":[m["metricKey"] for m in metrics]}
s["$defs"]["metric"]["properties"]["unit"]={"enum":sorted({m["unit"] for m in metrics})}
capture=s["$defs"]["capture"]
for field in ["assetRegistryRevision","sourceInventoryRevision","metricCatalogueRevision"]:
 if field not in capture["required"]:capture["required"].append(field)
capture["properties"].update({"assetRegistryRevision":{"type":"string","pattern":"^[a-f0-9]{64}$"},"sourceInventoryRevision":{"type":"string","pattern":"^[a-f0-9]{64}$"},"metricCatalogueRevision":{"type":"string","pattern":"^[a-f0-9]{64}$"}})
capture["allOf"]=[{"if":{"properties":{"retrievalStatus":{"enum":["ok","partial"]}}},"then":{"properties":{"payloadHash":{"type":"string","pattern":"^[a-f0-9]{64}$"},"rawPayloadRef":{"type":"string","minLength":1}},"required":["payloadHash","rawPayloadRef"]}},{"if":{"properties":{"retrievalStatus":{"const":"failed"}}},"then":{"properties":{"payloadHash":{"type":"null"},"rawPayloadRef":{"type":"null"}}}},{"if":{"properties":{"retrievalStatus":{"const":"not_modified"}}},"then":{"properties":{"payloadHash":{"type":"string","pattern":"^[a-f0-9]{64}$"},"rawPayloadRef":{"type":"string","minLength":1}}}}]
snow=s["$defs"]["snowmaking"]
for field in ["upstreamAssetId","upstreamName","warnings"]:
 if field not in snow["required"]:snow["required"].append(field)
snow["properties"].update({"upstreamAssetId":{"$ref":"#/$defs/nullableString"},"upstreamName":{"$ref":"#/$defs/nullableString"},"warnings":{"type":"array","items":{"type":"string"}}})
snow["properties"]["signalType"]["enum"]=[x for x in snow["properties"]["signalType"]["enum"] if x not in {"machine_made_depth","snowmaking_volume"}]
asset=s["$defs"]["assetStatus"]
if "snowmakingSignal" in asset["required"]:asset["required"].remove("snowmakingSignal")
if "snowmakingObservationIds" not in asset["required"]:asset["required"].append("snowmakingObservationIds")
asset["properties"].pop("snowmakingSignal",None); asset["properties"]["snowmakingObservationIds"]={"type":"array","items":{"type":"string"},"uniqueItems":True}
p.write_text(json.dumps(s,indent=2,sort_keys=True)+"\n")
