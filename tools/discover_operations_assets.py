#!/usr/bin/env python3
"""Create a reviewable asset-registry candidate from archived structured feeds.

The tool is offline and deterministic.  It never fetches or edits raw payloads;
promotion is an explicit copy/review step, not part of this program.
"""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RICH_EVIDENCE = ROOT / "tests/fixtures/operations/v2/vail_rich_compact_comparison_2026-07-13.json"
SPECS = {
 "falls_mountainops_lifts": ("falls","lift","falls_mountainops_lifts"), "falls_mountainops_runs": ("falls","run","falls_mountainops_runs"),
 "hotham_mountainops_lifts": ("hotham","lift","hotham_mountainops_lifts"), "hotham_mountainops_runs": ("hotham","run","hotham_mountainops_runs"),
 "perisher_mountainops_lifts": ("perisher","lift","perisher_mountainops_lifts"), "perisher_mountainops_runs": ("perisher","run","perisher_mountainops_runs"),
 "buller_lifts": ("buller","lift","buller_lifts"), "buller_trails": ("buller","run","buller_trails"),
 "thredbo_trails": ("thredbo_top",None,"thredbo_lifts_trails"),
}
def slug(value): return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
def latest(source, raw_root):
    files=sorted((raw_root/source).glob("*.json"))
    if not files: return []
    body=json.loads(files[-1].read_text()).get("body", "[]")
    return json.loads(body)
def latest_body(source, raw_root):
    files=sorted((raw_root/source).glob("*.json"))
    return json.loads(files[-1].read_text()).get("body", "") if files else ""
def clean_html(value): return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",value)).strip()
def norm_name(value): return " ".join(str(value).casefold().split())
def rich_verified_ids():
    payload=json.loads(RICH_EVIDENCE.read_text())
    result={}
    for report in payload["reports"]:
        identity=report.get("identity") or {}
        if not identity.get("allIdentityMatches") or report["compactCount"]!=report["richCount"]:
            raise ValueError(f"rich identity comparison is not proven for {report['resortId']}")
        result[report["compactSourceId"]]={str(row["id"]) for row in report["rows"] if row.get("identityMatch")}
    return result
def falls_official_names(raw_root):
    found={}
    def add(asset_class,name,path):
        if name not in (None,""):found.setdefault((asset_class,str(name)),set()).add(path)
    for envelope_path in sorted((raw_root/"falls_official_report").glob("*.json")):
        payload=json.loads(json.loads(envelope_path.read_text())["body"])
        for row in ((payload.get("Lifts") or {}).get("Lift") or []):add("lift",row.get("LiftName"),"$.Lifts.Lift[*].LiftName")
        for row in ((payload.get("Parks") or {}).get("Park") or []):add("terrain_park",row.get("ParkName"),"$.Parks.Park[*].ParkName")
        for row in ((payload.get("CrossCountryList") or {}).get("CrossCountry") or []):add("cross_country_trail",row.get("CrossCountryName"),"$.CrossCountryList.CrossCountry[*].CrossCountryName")
        for row in ((payload.get("Activities") or {}).get("Activity") or []):add("activity",row.get("ActivityName"),"$.Activities.Activity[*].ActivityName")
        for group_name,group in payload.items():
            if group_name.startswith("Groomed") and isinstance(group,dict):
                for row in group.get("Run") or []:add("run",row.get("RunName"),f"$.{group_name}.Run[*].RunName")
        for row in ((payload.get("RunStatus") or {}).get("Runs") or []):add("run",row.get("RunName"),"$.RunStatus.Runs[*].RunName")
        for key,value in payload.items():
            asset_class="lift" if key in {"liftsOpen","liftsClosed","liftsStandby"} else "activity" if key in {"ActivitiesOpen","ActivitiesClosed"} else "run" if key.startswith(("Groomed","Snowmaking")) and key.endswith("List") else None
            if asset_class and isinstance(value,str):
                for name in (part.strip() for part in value.split(",") if part.strip()):add(asset_class,name,f"$.{key}")
    return found
def apply_falls_official_mappings(assets,raw_root):
    candidates={}
    for asset in assets:
        if asset["resortId"]=="falls":candidates.setdefault((asset["assetClass"],norm_name(asset["displayName"])),[]).append(asset)
    entries=[]
    for (asset_class,name),paths in sorted(falls_official_names(raw_root).items()):
        matches=candidates.get((asset_class,norm_name(name)),[])
        classification="unmatched"
        if len(matches)>1:classification="ambiguous"
        elif len(matches)==1:
            classification="exact" if matches[0]["displayName"]==name else "normalized_exact"
            if not any(m["sourceId"]=="falls_official_report" and m.get("upstreamName")==name for m in matches[0]["sourceMappings"]):
                matches[0]["sourceMappings"].append({"sourceId":"falls_official_report","upstreamAssetId":None,"upstreamName":name,"upstreamArea":None,"validFrom":None,"validTo":None})
                matches[0]["sourceMappings"].sort(key=lambda m:(m["sourceId"],str(m.get("upstreamAssetId") or ""),m.get("upstreamName") or ""))
        entries.append({"assetClass":asset_class,"upstreamName":name,"normalizedName":norm_name(name),"sourcePaths":sorted(paths),"classification":classification,"matchedAssetIds":[a["assetId"] for a in matches]})
    counts={key:sum(item["classification"]==key for item in entries) for key in ("exact","normalized_exact","ambiguous","unmatched")}
    return {"schemaVersion":"alpine.falls-official-mapping-audit.v1","matchingRule":"source/resort/asset-class scoped exact or casefold-and-internal-whitespace normalized exact only","counts":counts,"entries":entries}
def thredbo_class(row):
    acf=row.get("acf") or {}; kind=acf.get("global_lift_trail_type") or ""; maps=acf.get("live_mountain_map_maps") or []
    excluded=bool(acf.get("exclude_from_count_on_weather_widget")); name=row.get("title") or ""
    if name=="Alpine Coaster": return "activity","alpine_coaster",[],"medium","provisional"
    if kind=="lift" and "winter" in maps and not excluded: return "lift","skiing",["service_records","winter_skiing_service"],"high","active"
    if kind=="scenic-lift": return "lift","scenic",["service_records"],"medium","provisional"
    if kind=="mtb-lift": return "transport","mtb",[],"high","active"
    if kind=="summer-lift": return "transport","summer",[],"medium","provisional"
    if kind in {"park", "terrain-park"}: return "terrain_park",None,["all_listed"],"high","active"
    if kind=="trail": return "run",None,["all_listed"],"high","active"
    return None
def build(raw_root):
    assets=[]; verified_rich=rich_verified_ids()
    for archive_source,(resort,default_class,source_id) in SPECS.items():
      for row in latest(archive_source, raw_root):
        classified=thredbo_class(row) if resort=="thredbo_top" else None
        if resort=="thredbo_top" and not classified: continue
        cls,variant,eligibility,confidence,registry_status=classified if classified else (default_class,None,["all_listed"],"high","active")
        upstream=row.get("ID", row.get("Id", row.get("id"))); name=row.get("Name",row.get("name",row.get("title")))
        if upstream is None or not name: continue
        acf=row.get("acf") or {}; length=row.get("length"); upstream_kind=acf.get("global_lift_trail_type")
        match=re.search(r"[0-9]+", str(length or ""))
        asset_id=f"{resort}.{cls}.{source_id}.{upstream}"
        evidence={"upstreamType":upstream_kind,"excludeFromWeatherWidget":acf.get("exclude_from_count_on_weather_widget"),"winterMapMembership":"winter" in (acf.get("live_mountain_map_maps") or [])} if resort=="thredbo_top" else None
        mappings=[{"sourceId":source_id,"upstreamAssetId":str(upstream),"upstreamName":name,"upstreamArea":row.get("Location",row.get("area")),"validFrom":None,"validTo":None}]
        # Rich/compact Vail lift identities were directly compared on 2026-07-13
        # (ID, trimmed name and location matched for every listed service).
        if cls=="lift" and str(upstream) in verified_rich.get(source_id,set()):
            mappings.append({**mappings[0],"sourceId":source_id+"_rich"})
        assets.append({"assetId":asset_id,"resortId":resort,"assetClass":cls,"displayName":name,"area":row.get("Location",row.get("area")),"physicalAssetGroupId":None,"serviceVariant":variant,"liftType":row.get("type",acf.get("lift_type")),"trailType":acf.get("run_type"),"difficulty":row.get("Level",row.get("skill_level",acf.get("trail_difficulty"))),"lengthM":int(match.group()) if match else None,"registryStatus":registry_status,"activeFrom":None,"activeTo":None,"denominatorEligibility":eligibility,"identityConfidence":confidence,"classificationEvidence":evidence,"sourceMappings":mappings,"notes":"Generated from an archived official structured feed; ambiguous service classifications remain provisional."})
    report=latest("falls_official_report",raw_root)
    if isinstance(report,dict):
      report_groups=(("terrain_park",(report.get("Parks") or {}).get("Park") or [],"ParkName"),("cross_country_trail",(report.get("CrossCountryList") or {}).get("CrossCountry") or [],"CrossCountryName"))
      for cls,rows,name_key in report_groups:
       for row in rows:
        name=row.get(name_key)
        if not name: continue
        assets.append({"assetId":f"falls.{cls}.falls_official_report.{slug(name)}","resortId":"falls","assetClass":cls,"displayName":name,"area":row.get("ParkDetail"),"physicalAssetGroupId":None,"serviceVariant":None,"liftType":None,"trailType":None,"difficulty":None,"lengthM":None,"registryStatus":"active","activeFrom":None,"activeTo":None,"denominatorEligibility":["all_listed"],"identityConfidence":"high","classificationEvidence":{"sourcePath":"$.Parks.Park[*]" if cls=="terrain_park" else "$.CrossCountryList.CrossCountry[*]"},"sourceMappings":[{"sourceId":"falls_official_report","upstreamAssetId":None,"upstreamName":name,"upstreamArea":row.get("ParkDetail"),"validFrom":None,"validTo":None}],"notes":"Generated from an explicitly named asset in an archived official report; name-only mapping is source/resort/class scoped."})
    baw=latest_body("bawbaw_public_report",raw_root)
    section=re.search(r'id="ski-lifts"(.*?)(?:<h3>Webcams</h3>)',baw,re.S|re.I)
    current=None
    class_by_heading={"Ski Lifts":"lift","Ski Runs":"run","Toboggan & Terrain Parks":"toboggan_area","Cross Country Trails":"cross_country_trail"}
    if section:
     for level,content in re.findall(r"<h([34])[^>]*>(.*?)</h[34]>",section.group(1),re.S|re.I):
      label=clean_html(content.split("<span",1)[0])
      if level=="3": current=class_by_heading.get(clean_html(content))
      elif current and label:
       cls="terrain_park" if label=="Terrain Park Features" else current
       assets.append({"assetId":f"bawbaw.{cls}.bawbaw_snow_lift_report.{slug(label)}","resortId":"bawbaw","assetClass":cls,"displayName":label,"area":None,"physicalAssetGroupId":None,"serviceVariant":None,"liftType":None,"trailType":None,"difficulty":None,"lengthM":None,"registryStatus":"provisional","activeFrom":None,"activeTo":None,"denominatorEligibility":["all_listed"],"identityConfidence":"medium","classificationEvidence":{"sourceSection":clean_html(content)},"sourceMappings":[{"sourceId":"bawbaw_snow_lift_report","upstreamAssetId":None,"upstreamName":label,"upstreamArea":None,"validFrom":None,"validTo":None}],"notes":"Mechanically extracted explicit report heading; provisional pending registry review."})
    # Human-reviewed explicit names from the official Charlotte Pass on-mountain
    # report. This small seed is intentionally not presented as complete.
    for cls,names in (("lift",["Kosciuszko Triple Chair","Kosi Carpet","Guthries Double Chair","Pulpit T-Bar","Basin Poma"]),("run",["Top of the World","Easy Starter","Lucy's Lane"])):
     for name in names:
      assets.append({"assetId":f"charlotte_pass.{cls}.charlotte_pass_on_mountain_report.{slug(name)}","resortId":"charlotte_pass","assetClass":cls,"displayName":name,"area":None,"physicalAssetGroupId":None,"serviceVariant":None,"liftType":None,"trailType":None,"difficulty":None,"lengthM":None,"registryStatus":"provisional","activeFrom":None,"activeTo":None,"denominatorEligibility":["all_listed"],"identityConfidence":"medium","classificationEvidence":{"inventoryCompleteness":"incomplete"},"sourceMappings":[{"sourceId":"charlotte_pass_on_mountain_report","upstreamAssetId":None,"upstreamName":name,"upstreamArea":None,"validFrom":None,"validTo":None}],"notes":"Human-reviewed explicit name; Charlotte Pass inventory remains intentionally incomplete."})
    apply_falls_official_mappings(assets,raw_root)
    payload={"schemaVersion":"alpine.operations-assets.v1","generatedBy":"tools/discover_operations_assets.py","promotionStatus":"validated","registryCompleteness":"partial","assets":sorted(assets,key=lambda a:a["assetId"])}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest();payload["contentHash"]=digest;payload["revision"]=digest
    return payload
def main():
    p=argparse.ArgumentParser(); p.add_argument("--raw-root",type=Path,default=ROOT/"data/operations/raw/2026-07-12"); p.add_argument("--out",type=Path,required=True); p.add_argument("--falls-report",type=Path); a=p.parse_args()
    a.out.parent.mkdir(parents=True,exist_ok=True); payload=build(a.raw_root);a.out.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    if a.falls_report:
        a.falls_report.parent.mkdir(parents=True,exist_ok=True);a.falls_report.write_text(json.dumps(apply_falls_official_mappings(payload["assets"],a.raw_root),indent=2,sort_keys=True)+"\n")
if __name__=="__main__": main()
