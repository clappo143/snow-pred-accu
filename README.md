# snow-pred-accu

Automated snowfall forecast accuracy tracker for Perisher (NSW, Australia).
An automated reimplementation of Star_Hawk's manual
[ski.com.au daily forecast comparisons](https://www.ski.com.au/xf/threads/2025-daily-forecast-comparisons.94372/) —
see [FEASIBILITY.md](FEASIBILITY.md) for the reverse-engineering and source research.

## How it works

Two daily jobs (GitHub Actions cron, or run locally):

- **`run_evening.py`** (~6pm AEST) — snapshots every provider's snowfall
  forecast for the coming days into SQLite, plus an accuracy-weighted
  ensemble forecast. A collector that fails to parse exits non-zero
  (never recorded as 0cm) so scraper breakage is loud.
- **`run_morning.py`** (~7:45am AEST) — records the resort-reported 24h
  snowfall, scores yesterday evening's forecasts (24h lead), and
  regenerates the charts in `charts/`.

## Sources (v1)

| Source | Method |
|---|---|
| YR.no | official `api.met.no` API; snow = precip falling at ≤1°C, 1mm≈1cm |
| BOM | `api.weather.bom.gov.au` JSON; rain-range midpoint on days with tmax ≤2°C |
| Snow-Forecast.com | server-rendered table, Perisher-Blue mid-mountain |
| Mountainwatch | server-rendered 7-day table, anchored to its day labels |
| **actuals** | OnTheSnow embedded JSON (`snow.last24`) = Perisher resort report |

Weatherzone / Jane's Weather / OpenSnow are deliberately deferred
(JS apps / paywalls — see FEASIBILITY.md §2).

## Scoring

`accuracy = 100 × max(0, 1 − MAE / mean(max(actual, 2cm)))` per source over
the season, on 24h-lead forecasts only. The 2cm floor keeps long snowless
runs from dominating; rankings are meaningless until real snow falls.
The ensemble weights each provider by its running accuracy (equal weights
until there is history).

## Running locally

```
pip install -r requirements.txt
python run_evening.py   # snapshot forecasts
python run_morning.py   # actuals + scoring + charts
```

Data lives in `data/snow.db`; charts in `charts/*.png`. Both are committed
back by the Actions workflow so the repo is the archive.
