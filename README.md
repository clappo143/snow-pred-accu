# snow-pred-accu

Automated snowfall forecast accuracy tracker for the main Australian resorts —
Perisher, Thredbo, Mt Hotham, Falls Creek and Mt Buller (see `resorts.py`).
An automated reimplementation of Star_Hawk's manual
[ski.com.au daily forecast comparisons](https://www.ski.com.au/xf/threads/2025-daily-forecast-comparisons.94372/) —
see [FEASIBILITY.md](FEASIBILITY.md) for the reverse-engineering and source research.

## How it works

Two daily jobs (GitHub Actions cron, or run locally), each covering every
resort:

- **`run_evening.py`** (~6pm AEST) — snapshots every provider's snowfall
  forecast for the coming days into SQLite (`run='pm'`), plus an
  accuracy-weighted ensemble forecast per resort. A collector that fails to
  parse exits non-zero (never recorded as 0cm) so scraper breakage is loud.
- **`run_morning.py`** (~7:45am AEST) — records each resort's reported 24h
  snowfall, snapshots every forecaster *again* (`run='am'`, right after most
  providers' 6-7am issuance — the genuine "morning of" call), scores,
  regenerates the dashboard (`docs/index.html`) and the legacy Perisher
  PNGs in `charts/`.

Snowatch blocks GitHub's cloud-runner IPs, so its collector runs on a
self-hosted runner in both slots (`--only snowatch`); the cloud jobs pass
`--skip snowatch`.

## Sources (all resorts)

| Source | Method |
|---|---|
| YR.no | official `api.met.no` API; snow = precip falling at ≤1°C, 1mm≈1cm |
| BOM | `api.weather.bom.gov.au` JSON; rain-range midpoint on days with tmax ≤2°C |
| BOM MetEye | MetEye text views (`bom.gov.au/places/…/forecast/detailed/`): 3-hourly forecaster-edited Snow flags × 50th-pct precip, summed per day — the parallel BOM methodology; a full ensemble member alongside `bom`, each weighted on its own accuracy record |
| Snow-Forecast.com | server-rendered table, mid-mountain (canonical); bot/top elevation bands stored DB-only (see `docs/reference-points.md`) |
| Mountainwatch | server-rendered 7-day table, anchored to its day labels |
| Snowatch | server-rendered 15-day page; range midpoints |
| Jane's Weather | public forecast-edge API, `model=ml` |
| Open-Meteo | free model API; also backfillable (`backfill_openmeteo.py`) |
| **actuals** | official resort reports for all five (Perisher HTML, Hotham snow-report HTML, Falls Creek patrol JSON, Thredbo LivePass XML, Buller API amount + public-page patrol time); snowatch.com.au homepage table as mid-rank proxy; OnTheSnow as gap-filling fallback (rank precedence in `store.save_actual`) |

## Scoring — the window, and leads

A resort's morning report covers the 24h to ~7am, so the report published
the morning of **D+1** is the one that measures calendar day D. Every
forecast for day D is therefore scored against the actual dated D+1.
(Scoring D against the report dated D — the v1 approach — mostly measured
the *previous* day, much of which was already in the past when the evening
snapshot was taken.)

Because both an `am` and a `pm` snapshot of the full multi-day horizon are
kept, accuracy is scored per **lead**: "morning of" (am, ~0h before the
window), "night before" (pm, ~13h), "1 day out" (am), "1.5 days out"
(pm), … The dashboard's rankings have a lead selector plus an
accuracy-vs-lead curve, separating "sees events coming early" from "nails
the amount at short range". Evening snapshots of a window already 11h in
the past (pm, lead 0) are excluded as hindsight.

Per (source, lead): `accuracy = 100 × max(0, 1 − MAE / mean(max(actual, 2cm)))`
over the season. The 2cm floor keeps long snowless runs from dominating;
rankings are meaningless until real snow falls. The operational consensus
is an accuracy-weighted **median** (resistant to one extreme provider); the
historical DB `ensemble` remains the older accuracy-weighted mean and is
exported under an explicitly diagnostic name.

## Actual provenance and Alpine export

Schema v4 separates two concerns:

- `actual_observations` is an append-only record of every source observation,
  including retrieval time, source URL, raw payload and timestamp semantics.
- `actuals` is the single effective value used by scoring. Precedence is
  protected manual correction > official > Snowatch > OnTheSnow.

`report_time_kind` distinguishes report publication times from ski-patrol
observation/update times. In particular, Buller's weather-widget
`last_updated` is retained only as raw context; the snow report uses the
separate public-page Ski Patrol update time.

`python export_normalized.py` writes two versioned feeds:

- `data/alpine_export_v1.json` is the broader forecast/actual compatibility
  export.
- `data/official-report-export.v1.json` is the strict
  `alpine.official-report-export.v1` contract consumed by Alpine's
  timestamp-aligned auxiliary-gauge workflow. It retains append-only source
  layers, selected effective-layer IDs, explicit value states, timestamp
  semantics, raw-payload references, and protected manual corrections.

The scheduled workflows regenerate both feeds after every collection run.
Set Alpine's `PHASE1_OFFICIAL_REPORT_EXPORT` to the strict export path; the
legacy compatibility export is deliberately rejected for that use. Baw Baw
is deliberately absent from this producer because this project does not
collect its resort report; Alpine remains its canonical owner rather than
receiving invented cross-project provenance.

Every strict export is also retained at
`data/official-report-exports/<sha256>.json`. Auxiliary sidecars record that
exact SHA-256, so a historical report-window audit can be reproduced against
the immutable producer artifact rather than a later mutable "latest" export.

## Running locally

```
pip install -r requirements.txt
python run_evening.py   # snapshot forecasts (all resorts)
python run_morning.py   # actuals + am snapshot + scoring + dashboard
python backfill_openmeteo.py [START] [END] [resort|all]
python export_normalized.py  # stable feed for Alpine Weather Dashboard
python -m unittest discover -s tests
```

Data lives in `data/snow.db` (schema v4; migrations from older schemas are
automatic and additive); the dashboard in
`docs/index.html`. Both are committed back by the Actions workflow so the
repo is the archive.
