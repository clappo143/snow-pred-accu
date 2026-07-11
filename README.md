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
| **actuals** | official resort reports for all five (Perisher & Hotham Vail HTML, Falls Creek WP JSON patrol feed, Thredbo LivePass XML, Buller widget JSON); snowatch.com.au homepage table as mid-rank proxy; OnTheSnow as gap-filling fallback (rank precedence in `store.save_actual`) |

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
rankings are meaningless until real snow falls. The ensemble weights each
provider by its running night-before accuracy (equal weights until there is
history).

## Running locally

```
pip install -r requirements.txt
python run_evening.py   # snapshot forecasts (all resorts)
python run_morning.py   # actuals + am snapshot + scoring + dashboard
python backfill_openmeteo.py [START] [END] [resort|all]
```

Data lives in `data/snow.db` (schema v2: keyed by resort and am/pm run —
`store.py` migrates a v1 database automatically); the dashboard in
`docs/index.html`. Both are committed back by the Actions workflow so the
repo is the archive.
