from __future__ import annotations
import copy, hashlib, json, sqlite3, subprocess, tempfile, unittest
from collections import Counter
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from operations.migrations_v2 import migrate_v2
from operations.registry import ROOT, content_hash, expand_comma_delimited, identity_ids, load_registries, parse_wind_speed_range, validate_asset_registry, validate_source_registry
from operations.schema_v2 import assert_valid_export, validate_export

FIXTURE=ROOT/"tests/fixtures/operations/v2/example_operations_export_v2.json"
def example():return json.loads(FIXTURE.read_text())
class ExportHardeningTest(unittest.TestCase):
 def reject(self,mutate,needle=None):
  p=example();mutate(p);errors=validate_export(p);self.assertTrue(errors)
  if needle:self.assertTrue(any(needle in e for e in errors),errors)
 def test_self_contained_fixture_and_hotham_semantics(self):
  p=example();assert_valid_export(p);by={x["observationId"]:x for x in p["snowmakingObservations"]}
  self.assertEqual((by["plant-standby"]["scope"],by["plant-standby"]["normalizedState"],by["plant-standby"]["interpretationConfidence"]),("resort","standby","high"))
  self.assertEqual((by["runs-five"]["scope"],by["runs-five"]["numericValue"],by["runs-five"]["semanticMeaning"]),("resort",5,"unverified reported count; not guns or proven active runs"))
  self.assertEqual((by["run-no"]["scope"],by["run-no"]["normalizedState"]),("asset","unknown"));self.assertTrue(by["run-no"]["subjectAssetId"]);self.assertTrue(by["run-no"]["warnings"])
  self.assertEqual(by["feed-none"]["normalizedState"],"none_flagged");self.assertEqual(p["conflicts"],[])
 def test_structural_garbage_rejected(self):
  self.reject(lambda p:p["assets"].__setitem__(0,{"garbage":True}),"schema assets")
  self.reject(lambda p:p["sourceInventory"].__setitem__(0,{"garbage":True}),"schema sourceInventory")
  self.reject(lambda p:p["rawPayloads"].__setitem__(0,{"garbage":True}),"schema rawPayloads")
 def test_metric_type_and_value_states(self):
  self.reject(lambda p:p["metricObservations"][0].__setitem__("value","107"),"wrong metric value type")
  p=example();base=p["metricObservations"][0];p["metricObservations"]=[]
  for oid,status,value in (("zero","explicit_zero",0),("blank","blank",None),("unavailable","unavailable",None),("unknown","unknown",None)):
   p["metricObservations"].append({**base,"observationId":oid,"valueStatus":status,"value":value})
  assert_valid_export(p)
 def test_invalid_time_and_intervals(self):
  self.reject(lambda p:p.__setitem__("generatedAt","yesterday"),"schema generatedAt")
  self.reject(lambda p:(p.__setitem__("windowStart","2026-07-14T00:00:00Z"),p.__setitem__("windowEnd","2026-07-13T00:00:00Z")),"windowStart after")
  self.reject(lambda p:(p["metricObservations"][0].__setitem__("effectiveFrom","2026-07-14T00:00:00Z"),p["metricObservations"][0].__setitem__("effectiveTo","2026-07-13T00:00:00Z")),"effectiveFrom after")
 def test_cross_resort_unknown_asset_and_class(self):
  self.reject(lambda p:p["metricObservations"][0].__setitem__("resortId","falls"),"observation/capture resort mismatch")
  self.reject(lambda p:p["assetStatusObservations"][0].__setitem__("assetId","missing"),"unknown mapped asset")
  self.reject(lambda p:p["assetStatusObservations"][0].__setitem__("assetClass","lift"),"asset resort/class mismatch")
 def test_source_and_provenance_rejections(self):
  self.reject(lambda p:p["captures"][0].__setitem__("sourceId","missing"),"unknown sourceId")
  self.reject(lambda p:p["captures"][1].__setitem__("sourceLayer","resort_report"),"source registry layer mismatch")
  self.reject(lambda p:p["captures"][0].__setitem__("sourceRole","live_asset_status"),"source registry sourceRole mismatch")
  self.reject(lambda p:p["captures"][0].__setitem__("payloadHash",None),"schema captures")
  self.reject(lambda p:p["captures"][0].__setitem__("payloadHash","abc"),"schema captures")
  self.reject(lambda p:p.__setitem__("rawPayloads",p["rawPayloads"][1:]),"usable raw payload descriptor")
 def test_conflicts_and_unmapped_snowmaking(self):
  self.reject(lambda p:p["conflicts"].append({"conflictId":"c","resortId":"hotham","fieldFamily":"x","subjectId":None,"observationIds":["run-no","missing"],"conflictType":"value","detectedAt":"2026-07-13T00:00:00Z","status":"open","resolution":None,"notes":[]}),"unknown observation reference")
  def unmapped(p):
   r=next(x for x in p["snowmakingObservations"] if x["observationId"]=="run-no");r.update(subjectAssetId=None,upstreamAssetId=None,upstreamName=None,warnings=[])
  self.reject(unmapped,"unmapped snowmaking asset")
  def cross(p):
   p["conflicts"].append({"conflictId":"c","resortId":"falls","fieldFamily":"x","subjectId":None,"observationIds":["run-no","plant-standby"],"conflictType":"value","detectedAt":"2026-07-13T00:00:00Z","status":"open","resolution":None,"notes":[]})
  self.reject(cross,"spans different resorts")
 def test_duplicate_observation_and_source_mapping_rejected(self):
  self.reject(lambda p:p["snowmakingObservations"][1].__setitem__("observationId",p["snowmakingObservations"][0]["observationId"]),"duplicate record id")
  def duplicate_mapping(p):p["assets"].append({**copy.deepcopy(p["assets"][0]),"assetId":"duplicate-map"})
  self.reject(duplicate_mapping,"duplicate source mapping")
 def test_falls_depth_locations_coexist(self):
  p=example();base=p["metricObservations"][0];p["metricObservations"]=[{**base,"observationId":"falls-village","locationLabel":"Village","value":85},{**base,"observationId":"falls-summit","locationLabel":"Summit","value":140}];assert_valid_export(p)

class RegistryHardeningTest(unittest.TestCase):
 def test_registries_and_all_schemas(self):
  load_registries()
  for path in sorted((ROOT/"contracts").glob("*.schema.json")):
   schema=json.loads(path.read_text());Draft202012Validator.check_schema(schema)
  assets,sources,metrics=load_registries()
  for schema_name,payload in (("operations-assets.v1.schema.json",assets),("operations-sources.v2.schema.json",sources),("operations-metrics.v2.schema.json",metrics)):
   Draft202012Validator(json.loads((ROOT/"contracts"/schema_name).read_text()),format_checker=FormatChecker()).validate(payload)
 def test_registry_revision_stability_and_generation(self):
  assets,sources,metrics=load_registries();self.assertEqual(assets["contentHash"],content_hash(assets));self.assertEqual(sources["contentHash"],content_hash(sources));self.assertEqual(metrics["contentHash"],content_hash(metrics))
  with tempfile.TemporaryDirectory() as d:
   candidate=Path(d)/"assets.json";subprocess.run(["python3","tools/discover_operations_assets.py","--out",str(candidate)],cwd=ROOT,check=True);self.assertEqual(candidate.read_bytes(),(ROOT/"config/operations_assets_v1.json").read_bytes())
   raw1=candidate.read_bytes();subprocess.run(["python3","tools/discover_operations_assets.py","--out",str(candidate)],cwd=ROOT,check=True);self.assertEqual(raw1,candidate.read_bytes())
   subprocess.run(["python3","tools/finalize_operations_registries.py","--out-dir",d],cwd=ROOT,check=True)
   for name in ("operations_assets_v1.json","operations_sources_v2.json","operations_metrics_v2.json"):self.assertEqual((Path(d)/name).read_bytes(),(ROOT/"config"/name).read_bytes())
 def test_thredbo_denominator_isolation(self):
  assets,_,_=load_registries();rows=[a for a in assets["assets"] if a["resortId"]=="thredbo_top"]
  groups=Counter(a["serviceVariant"] or a["assetClass"] for a in rows)
  self.assertGreater(groups["skiing"],0);self.assertGreater(groups["scenic"],0);self.assertGreater(groups["mtb"],0);self.assertGreater(groups["summer"],0);self.assertGreater(groups["alpine_coaster"],0)
  winter=[a for a in rows if "winter_skiing_service" in a["denominatorEligibility"]]
  self.assertTrue(all(a["assetClass"]=="lift" and a["serviceVariant"]=="skiing" for a in winter))
 def test_registry_semantic_rejections(self):
  assets,sources,_=load_registries();bad=copy.deepcopy(assets);bad["assets"][1]["assetId"]=bad["assets"][0]["assetId"];bad["contentHash"]=content_hash(bad);bad["revision"]=bad["contentHash"];self.assertTrue(validate_asset_registry(bad,identity_ids(),sources))
  bad=copy.deepcopy(assets);bad["assets"][0]["sourceMappings"][0]["sourceId"]="missing";bad["contentHash"]=content_hash(bad);bad["revision"]=bad["contentHash"];self.assertTrue(any("unknown mapping" in e for e in validate_asset_registry(bad,identity_ids(),sources)))
  bad=copy.deepcopy(assets);bad["assets"][0]["sourceMappings"][0]["sourceId"]="hotham_official_report";bad["contentHash"]=content_hash(bad);bad["revision"]=bad["contentHash"];self.assertTrue(any("resort mismatch" in e for e in validate_asset_registry(bad,identity_ids(),sources)))
 def test_field_coverage_present(self):
  _,sources,_=load_registries()
  for source in sources["sources"]:self.assertTrue(source["fieldCoverage"]);self.assertEqual(source["unknownFieldPolicy"],"retain_raw_and_warn")

class SemanticIntegrityTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.assets,cls.sources,cls.metrics=load_registries();cls.by_source={s["sourceId"]:{f["path"]:f for f in s["fieldCoverage"]} for s in cls.sources["sources"]}
 def dest(self,source,path):
  f=self.by_source[source][path];return f["disposition"],f["destinationCollection"],f["destinationField"],f["metricKey"]
 def test_hotham_exact_semantics(self):
  expected={"$.TwentyFourHourSnowfall":"snowfall_24h_cm","$.SevenDaySnowfall":"snowfall_7d_cm","$.SeasonToDateSnowfall":"season_snowfall_cm","$.CurrentSnowdepth":"natural_depth_cm","$.AvSnowdepthinSnowmakingAreas":"machine_made_depth_cm","$.DateofLastSnowfall":"last_snowfall_date","$.Temperature":"current_temperature_c","$.OvernightMin":"overnight_min_temperature_c","$.ForecastMax":"forecast_max_temperature_c","$.WindDirection":"wind_direction_compass","$.Precipitation":"precipitation_condition","$.ExpectedVisibility":"visibility","$.ExpectedCloudCover":"cloud_cover","$.Forecast":"weather_forecast","$.Backcountry":"backcountry_advisory","$.Avalanche":"avalanche_advisory","$.HeliLink":"heli_operation_status"}
  for path,key in expected.items():self.assertEqual(self.dest("hotham_official_report",path),("normalized","metricObservations","value",key))
  wind=self.by_source["hotham_official_report"]["$.Wind"];self.assertEqual([e["metricKey"] for e in wind["emissions"]],["wind_speed_min_kmh","wind_speed_max_kmh"]);self.assertEqual(wind["observationRole"],None)
  self.assertEqual(self.by_source["hotham_official_report"]["$.ForecastMax"]["observationRole"],"expected")
  self.assertEqual(len(self.by_source["hotham_official_report"]),32);self.assertTrue(all(not f["notes"].startswith("Reviewed field") for f in self.by_source["hotham_official_report"].values()))
  self.assertEqual(self.dest("hotham_official_report","$.RunsSnowmaking")[:3],("normalized","snowmakingObservations","numericValue"));self.assertEqual(self.by_source["hotham_official_report"]["$.RunsSnowmaking"]["snowmakingSpec"]["signalType"],"runs_snowmaking_count")
  for path in ("$.LiftsOpen","$.LiftsStandby","$.LiftsClosed","$.RunsOpen","$.RunsGroomed"):self.assertEqual(self.dest("hotham_official_report",path)[1],"aggregateObservations")
  self.assertEqual(self.dest("hotham_official_report","$.ResortReport")[1],"narratives")
 def test_falls_exact_depth_snowfall_xc_and_wind(self):
  expected={"$.Patrol.PatrolFreshSnow":"snowfall_24h_cm","$.Patrol.PatrolNaturalSnowDepth":"natural_depth_cm","$.Patrol.PatrolVillageBowl":"location_depth_cm","$.Patrol.PatrolSunValley":"location_depth_cm","$.Patrol.PatrolNorthSide":"location_depth_cm","$.Patrol.SeasonalSnowfallToDate":"season_snowfall_cm","$.Weather.WindSpeed":"wind_speed_kmh","$.Weather.WindDirection":"wind_direction_compass","$.CrossCountryMaintenance.CrossCountryConditions":"cross_country_condition","$.CrossCountryMaintenance.CrossCountryCover":"cross_country_cover"}
  for path,key in expected.items():self.assertEqual(self.dest("falls_official_report",path)[3],key)
  self.assertEqual(self.dest("falls_official_report","$.CrossCountryMaintenance.CrossCountryTotalGroomed")[3],"cross_country_groomed_km");self.assertEqual(self.dest("falls_official_report","$.CrossCountryMaintenance.CrossCountryTotalGroomed24")[3],"cross_country_groomed_24h_km")
  self.assertEqual(self.dest("falls_official_report","$.SlopeMaintenance.CubicMetersofSnow")[3],"snowmaking_volume_m3")
  depth=self.by_source["falls_official_report"]["$.SlopeMaintenance.AverageSnowMaking"];self.assertEqual((depth["destinationCollection"],depth["metricKey"],depth["unitRule"]),("metricObservations","machine_made_depth_cm","cm; parse numeric"));self.assertIsNone(depth["snowmakingSpec"]);self.assertIn("LastUpdateDate",depth["timestampRule"])
  low=self.by_source["falls_official_report"]["$.Weather.TodaysLow"];self.assertEqual((low["metricKey"],low["observationRole"],low["interpretationConfidence"]),("daytime_low_temperature_c","report_summary","medium"));self.assertEqual(self.by_source["falls_official_report"]["$.Weather.OvernightMin"]["metricKey"],"overnight_min_temperature_c")
 def test_perisher_and_buller_exact_semantics(self):
  for path,key in {"$.snowdepth":"natural_depth_cm","$.new_snow_24hrs_top":"snowfall_24h_cm","$.new_snow_7days":"snowfall_7d_cm"}.items():self.assertEqual(self.dest("perisher_official_report",path)[3],key)
  self.assertEqual(self.dest("perisher_official_report","$.lifts_number")[1],"aggregateObservations");self.assertEqual(self.dest("perisher_official_report","$.groomed_runs")[1],"aggregateObservations");self.assertEqual(self.dest("perisher_official_report","$.snow_guns")[1],"snowmakingObservations")
  for path,key in {"$.temp":"current_temperature_c","$.feels_like":"feels_like_temperature_c","$.daytime_high":"daytime_high_temperature_c","$.overnight_low":"overnight_min_temperature_c","$.wind_speed":"wind_speed_kmh","$.wind_gust":"wind_gust_kmh","$.wind_direction":"wind_direction_degrees","$.precip_since_9_am":"precipitation_since_9am_mm","$.snow_report.snow_last_24_hours":"snowfall_24h_cm","$.snow_report.snow_last_48_hours":"snowfall_48h_cm","$.snow_report.snow_last_72_hours":"snowfall_72h_cm","$.snow_report.snow_season_total":"season_snowfall_cm"}.items():self.assertEqual(self.dest("buller_weather_widget",path)[3],key)
  for path in ("$.open_lifts_count","$.all_lifts_count","$.open_trails_count","$.all_trails_count","$.open_terrain_parks_count","$.all_terrain_parks_count"):self.assertEqual(self.dest("buller_weather_widget",path)[1],"aggregateObservations")
 def test_queue_and_status_timestamps_are_asset_observation_fields(self):
  self.assertEqual(self.dest("buller_lifts","$[*].wait_time_in_minutes")[:3],("normalized","assetStatusObservations","queueMinutes"));self.assertEqual(self.dest("buller_lifts","$[*].waiting_time_updated_at")[1:3],("assetStatusObservations","observedAt"));self.assertEqual(self.dest("thredbo_lifts_trails","$[*].modified")[1:3],("assetStatusObservations","observedAt"))
 def test_embedded_integrity_and_completeness(self):
  def tamper_asset(p):p["assets"][0]["displayName"]="Tampered"
  ExportHardeningTest().reject(tamper_asset,"content differs")
  def unknown_asset(p):p["assets"].append({**copy.deepcopy(p["assets"][0]),"assetId":"unknown.asset"})
  ExportHardeningTest().reject(unknown_asset,"unknown for declared revision")
  def complete_assets(p):p["assetRegistryCompleteness"]="complete"
  ExportHardeningTest().reject(complete_assets,"complete asset registry")
  def tamper_source(p):p["sourceInventory"][0]["notes"]="Tampered"
  ExportHardeningTest().reject(tamper_source,"content differs")
  def complete_sources(p):p["sourceInventoryCompleteness"]="complete"
  ExportHardeningTest().reject(complete_sources,"complete source inventory")
  def missing_source(p):p["sourceInventory"]=[s for s in p["sourceInventory"] if s["sourceId"]!="hotham_mountainops_runs"]
  ExportHardeningTest().reject(missing_source,"unknown sourceId")
  def bad_url(p):p["rawPayloads"][0]["sourceUrl"]="https://example.test/wrong"
  ExportHardeningTest().reject(bad_url,"sourceUrl differs")
 def test_snowmaking_reference_integrity(self):
  def cross_capture(p):next(x for x in p["snowmakingObservations"] if x["observationId"]=="run-no")["captureId"]="cap-report"
  ExportHardeningTest().reject(cross_capture,"cross-capture")
  def cross_asset(p):next(x for x in p["snowmakingObservations"] if x["observationId"]=="run-no")["subjectAssetId"]="missing"
  ExportHardeningTest().reject(cross_asset,"cross-asset")
 def test_different_roles_are_not_direct_conflicts(self):
  def add_conflict(p):p["conflicts"].append({"conflictId":"roles","resortId":"hotham","fieldFamily":"lift_count","subjectId":None,"observationIds":["morning-17","live-12"],"conflictType":"value","detectedAt":"2026-07-13T00:00:00Z","status":"open","resolution":None,"notes":[]})
  ExportHardeningTest().reject(add_conflict,"different observation roles")
 def test_field_spec_validation_rejects_bad_destination_and_duplicates(self):
  bad=copy.deepcopy(self.sources);field=bad["sources"][0]["fieldCoverage"][0];field.update(disposition="normalized",destinationCollection="metricObservations",destinationField="value",metricKey="not_a_metric",normalizedValueType="number",observationRole="measurement",scope="resort",timestampRule="capture",valueStatusHandling="preserve",interpretationConfidence="high");bad["contentHash"]=content_hash(bad);bad["revision"]=bad["contentHash"];self.assertTrue(any("absent from catalogue" in e for e in validate_source_registry(bad,identity_ids())))
  bad=copy.deepcopy(self.sources);bad["sources"][0]["fieldCoverage"].append(copy.deepcopy(bad["sources"][0]["fieldCoverage"][0]));bad["contentHash"]=content_hash(bad);bad["revision"]=bad["contentHash"];self.assertTrue(any("duplicate or contradictory" in e for e in validate_source_registry(bad,identity_ids())))

 def test_falls_list_expansion_specs_and_archived_shapes(self):
  falls=self.by_source["falls_official_report"]
  paths=["$.liftsOpen","$.liftsClosed","$.liftsStandby","$.ActivitiesOpen","$.ActivitiesClosed"]+[p for p in falls if (p.startswith("$.Groomed") or p.startswith("$.Snowmaking")) and p.endswith("List")]
  self.assertGreaterEqual(len(paths),20)
  for path in paths:
   row=falls[path];spec=row["listExpansionSpec"]
   self.assertEqual(row["sourceValueType"],"comma_delimited_string");self.assertEqual((spec["delimiter"],spec["trimWhitespace"],spec["removeEmptyElements"],spec["duplicateHandling"]),(",",True,"only_empty","retain_and_warn"));self.assertTrue(spec["preserveRaw"] and spec["retainUnmapped"]);self.assertEqual(len(row["emissions"]),2);self.assertEqual(row["emissions"][1]["destinationCollection"],"aggregateObservations")
  self.assertEqual(expand_comma_delimited("Day's End,Wombats Ramble,Boardwalk,"),(["Day's End","Wombats Ramble","Boardwalk"],[]))
  self.assertEqual(expand_comma_delimited(""),([],[]))
  names,duplicates=expand_comma_delimited("Mouse Trap, Mouse Trap,");self.assertEqual(names,["Mouse Trap","Mouse Trap"]);self.assertEqual(duplicates,["Mouse Trap"])
  lifts=falls["$.liftsClosed"];self.assertEqual(lifts["emissions"][0]["destinationCollection"],"assetStatusObservations");self.assertEqual(lifts["emissions"][0]["semanticQualifier"],"report-listed closed lift");self.assertEqual(lifts["listExpansionSpec"]["aggregateDerivation"]["denominatorSourceFields"],["$.liftsOpen","$.liftsClosed","$.liftsStandby"])
  snow=falls["$.SnowmakingFallsCreekList"];self.assertEqual(snow["emissions"][0]["destinationCollection"],"snowmakingObservations");self.assertEqual(snow["emissions"][0]["snowmakingSpec"]["signalType"],"run_snowmaking_flag");self.assertIn("does not establish resort plant state",snow["emissions"][0]["snowmakingSpec"]["semanticMeaning"])

 def test_roles_and_forecast_audit(self):
  falls=self.by_source["falls_official_report"]
  self.assertEqual(falls["$.Lifts.Lift[*].LiftStatusMorning"]["observationRole"],"morning_plan");self.assertEqual(falls["$.Lifts.Lift[*].LiftStatusAfternoon"]["observationRole"],"expected")
  for path in ("$.liftsOpen","$.ActivitiesOpen","$.Parks.Park[*].ParkStatus","$.RunStatus.Runs[*].RunGroomed"):self.assertEqual(falls[path]["observationRole"] if not falls[path]["emissions"] else falls[path]["emissions"][0]["observationRole"],"report_summary")
  self.assertEqual(self.by_source["falls_mountainops_lifts"]["$[*].Status"]["observationRole"],"live_actual")
  for source in self.sources["sources"]:
   for field in source["fieldCoverage"]:
    if "forecast" in field["path"].casefold() or (field.get("metricKey") and ("forecast" in field["metricKey"] or "expected" in field["metricKey"])):
     self.assertEqual(field["observationRole"],"expected",(source["sourceId"],field["path"]))

 def test_metric_normalized_types_exactly_match_catalogue(self):
  types={m["metricKey"]:m["valueType"] for m in self.metrics["metrics"]}
  for source in self.sources["sources"]:
   for field in source["fieldCoverage"]:
    destinations=([field] if field.get("disposition")=="normalized" and field.get("destinationCollection") else [])+field.get("emissions",[])
    for destination in destinations:
     if destination.get("destinationCollection")=="metricObservations":self.assertEqual(destination["normalizedValueType"],types[destination["metricKey"]],(source["sourceId"],field["path"]))

 def test_hotham_wind_range_dry_run(self):
  self.assertEqual(parse_wind_speed_range("Strong 61-80 km/h"),(61.0,80.0));self.assertEqual(parse_wind_speed_range("45 km/h"),(45.0,45.0));self.assertIsNone(parse_wind_speed_range(""));self.assertIsNone(parse_wind_speed_range("Strong winds"))

 def test_executable_spec_rejections(self):
  def validate_mutated(mutator):
   bad=copy.deepcopy(self.sources);mutator(bad);bad["contentHash"]=content_hash(bad);bad["revision"]=bad["contentHash"];return validate_source_registry(bad,identity_ids())
  def list_as_scalar(bad):next(s for s in bad["sources"] if s["sourceId"]=="falls_official_report")["fieldCoverage"][-1]["sourceValueType"]="number_string"
  falls_index=next(i for i,s in enumerate(self.sources["sources"]) if s["sourceId"]=="falls_official_report");list_index=next(i for i,f in enumerate(self.sources["sources"][falls_index]["fieldCoverage"]) if f["path"]=="$.liftsOpen")
  self.assertTrue(any("list expansion must use" in e for e in validate_mutated(lambda bad:bad["sources"][falls_index]["fieldCoverage"][list_index].__setitem__("sourceValueType","number_string"))))
  self.assertTrue(any("catalogue" in e for e in validate_mutated(lambda bad:bad["sources"][falls_index]["fieldCoverage"][list_index]["emissions"].append({**copy.deepcopy(bad["sources"][falls_index]["fieldCoverage"][list_index]["emissions"][0]),"destinationCollection":"metricObservations","destinationField":"value","metricKey":"missing_metric","normalizedValueType":"number"}))))
  self.assertTrue(any("denominator source absent" in e for e in validate_mutated(lambda bad:bad["sources"][falls_index]["fieldCoverage"][list_index]["listExpansionSpec"]["aggregateDerivation"]["denominatorSourceFields"].append("$.missing"))))
  morning_index=next(i for i,f in enumerate(self.sources["sources"][falls_index]["fieldCoverage"]) if f["path"]=="$.Lifts.Lift[*].LiftStatusMorning")
  self.assertTrue(any("cannot be live_actual" in e for e in validate_mutated(lambda bad:bad["sources"][falls_index]["fieldCoverage"][morning_index].__setitem__("observationRole","live_actual"))))

class MigrationTest(unittest.TestCase):
 def test_idempotent_and_v1_byte_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   con=sqlite3.connect(Path(d)/"x.db");con.execute("create table operations_raw_payloads(payload_hash text primary key)");con.execute("create table operations_snapshots(id text primary key,snapshot_json text)");con.execute("insert into operations_snapshots values('v1','{\"kept\":true}')");before=con.iterdump();before="\n".join(before);migrate_v2(con);migrate_v2(con);self.assertEqual(con.execute("select snapshot_json from operations_snapshots").fetchone()[0],'{"kept":true}');self.assertTrue(con.execute("select 1 from sqlite_master where name='operations_v2_registry_snapshots'").fetchone())
   con.execute("insert into operations_v2_registry_snapshots values('r','v1','t','{}')");con.execute("insert into operations_v2_source_inventory_snapshots values('s','v2','t','{}')");con.execute("insert into operations_v2_metric_catalogue_snapshots values('m','v2','t','{}')");con.execute("insert into operations_v2_assets values('r','a','falls','run','{}')");con.execute("insert into operations_v2_captures values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('c','falls','src','layer','role','t',None,None,None,None,None,None,None,'parser','failed',None,'r','s','m','[]'))
   con.execute("insert into operations_v2_asset_status_observations values(?,?,?,?,?,?,?,?,?)",('o','c','falls','r','a','1','run','t','{}'))
 def test_empty(self):
  with tempfile.TemporaryDirectory() as d:con=sqlite3.connect(Path(d)/"x.db");migrate_v2(con);migrate_v2(con)
if __name__=="__main__":unittest.main()
