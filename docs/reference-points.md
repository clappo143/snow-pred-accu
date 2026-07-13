# Forecast reference points (elevation audit)

Audited 2026-07-11 with live requests against every provider's real page/API.
Question: when a provider says "8cm tomorrow at Perisher", what point on the
mountain is that number for? For the cross-provider comparison and the
ensemble to be fair, they should all be roughly the mid/upper-mountain
heights in `resorts.py` (`alt`: 1650–1750 m).

## Reference table (metres)

| Provider | How determined | Perisher (alt 1720) | Thredbo (1700) | Hotham (1750) | Falls Ck (1650) | Buller (1700) |
|---|---|---|---|---|---|---|
| Snow-Forecast **mid** (canonical `snowforecast`) | stated in the page's elevation switcher | 1867 | 1701 | 1652 | 1690 | 1590 |
| Snow-Forecast bot (`snowforecast_bot`) | idem | 1700 | 1365 | 1454 | 1600 | 1390 |
| Snow-Forecast top (`snowforecast_top`) | idem | 2034 | 2037 | 1850 | 1780 | 1790 |
| Open-Meteo | `elevation` param, echoed back by the API | 1720 | 1700 | 1750 | 1650 | 1700 |
| YR.no | `altitude` param, echoed in `geometry.coordinates[2]` | 1720 | 1700 | 1750 | 1650 | 1700 |
| BOM | geohash gridpoint; API exposes no elevation — terrain height at the gridpoint centre (DEM) shown | ~1737 | ~1785 | ~1624 | ~1637 | ~1661 |
| Snowatch | human forecast, no stated reference elevation; text discusses snow levels per range, resort pages carry village/summit metadata only | resort-wide | resort-wide | resort-wide | resort-wide | resort-wide |
| Mountainwatch | page states "For _X_ m" above the table | 1830 | 1830 | 1850 | 1770 | 1710 |
| Jane's Weather | stated per snow location on janesweather.com/snow-forecast (see 2026-07-13 revision below) | 1800 | 1850 | 1850 | 1750 | 1700 |

Verification details:

- **Snow-Forecast**: `/6day/{bot,mid,top}` are distinct server-rendered
  tables; the switcher lists the metres for each band. "mid" is the band
  closest to `alt` at every resort (max deviation +147 m at Perisher), so it
  stays the canonical `snowforecast` series.
- **Open-Meteo**: honors `&elevation=` (response echoes it; downscaling is
  applied). Without the param it would use its own DEM at the grid cell
  (probed defaults: 1726/1728/1606/1619/1625) — close, but we pin `alt` for
  determinism. The collector already passes it. ✔
- **YR.no**: honors `&altitude=` (echoed in the response geometry). Without
  it, defaults would be 1764/**1477**/1610/1718/**1320** — Thredbo and
  Buller would drop to village level, so passing `alt` matters. The
  collector already passes it. ✔
- **BOM**: `api.weather.bom.gov.au/v1/locations/{geohash}` returns no
  elevation; the daily forecast is for the ~6 km ADFD grid cell containing
  the geohash. All five geohashes sit on high terrain (1620–1790 m DEM at
  the cell centre), i.e. broadly aligned. Note the collector doesn't use an
  elevation directly anyway — it derives snow from the 50%-chance (median) rainfall whenever
  tmax ≤ 2 °C at that gridpoint.
- **Snowatch**: no elevation is stated anywhere on the 15-day pages (checked
  all five). It's a human, resort-level outlook; its narrative quotes snow
  lines (e.g. "snow above 1650–1750 m in NSW"), implying the cm figures are
  for the resort's skiable terrain, i.e. roughly our reference band.
- **Mountainwatch**: each resort page states its reference ("For 1830 m —
  last updated…"), a near-summit point 60–130 m above `alt`.
- **Jane's Weather** (revised 2026-07-13): the forecast-edge API snaps the
  query to the nearest precalculated snow location. The original audit
  reported DEM terrain height at the snapped coordinates
  (~1721/**1367**/1710/1563/1597) and flagged Thredbo as materially low.
  Re-probed 2026-07-13: janesweather.com/snow-forecast embeds JW's official
  snow-location registry — Perisher 1800 m, Thredbo 1850 m, Hotham 1850 m,
  Falls Creek 1750 m, Buller 1700 m — and querying the API with JW's own
  published coordinates snaps to the *same* points our resorts.py
  coordinates already reach, returning identical forecasts. So the
  collector was always getting JW's canonical upper-mountain product; the
  Thredbo snapped point's coordinates sit at the village but JW nominates
  1850 m for it (the DEM at the snapped point is not what the model is
  calibrated to). No coordinate change is warranted or possible — there is
  no better JW point to query.

## Snow-Forecast elevation bands (added 2026-07-11)

`collectors/snowforecast.py` now collects all three bands per resort.
The mid band keeps the `snowforecast` source name (history and scoring
unbroken); bot/top are stored as `snowforecast_bot` / `snowforecast_top`.
The extras are:

- excluded from the ensemble (`store.NON_ENSEMBLE_SOURCES`),
- absent from the dashboard (its source list comes from
  `dashboard.PROVIDER_COLORS`, which doesn't include them),
- non-fatal: a bot/top fetch failure only warns; only a mid failure fails
  the collector.

## Normalization verdict: not warranted (yet)

Excluding the extra bands, every provider's reference sits within roughly
±150 m of `alt` — inside one Snow-Forecast band, and small relative to the
vertical spacing of their own bot/mid/top forecasts. The two caveats:

1. ~~**Jane's Weather × Thredbo (~1367 m)** is the one materially low
   cell.~~ Withdrawn 2026-07-13: JW nominates 1850 m for its Thredbo snow
   location (see the revised JW bullet above); the ~1367 m figure was the
   DEM at the snapped point's coordinates, not the forecast's reference.
2. **Mountainwatch** runs 60–130 m high — likely a slight over-read in
   marginal storms, again small.

## Day-window alignment (added 2026-07-13)

Reference *elevation* was the wrong suspect for Jane's Weather's early
accuracy numbers — the real mismatch was temporal. JW's `dailySnowTotal`
is a local **calendar-day** total (midnight→midnight, confirmed by summing
its own hourly series both ways), while the ground truth is the resort
report covering **7am→7am**, which score.py joins as target D ↔ report
D+1. The 2026-07-11/12 storm fell almost entirely between 11pm and 7am:
JW correctly painted it on the calendar day holding the 00:00–07:00 hours,
and the join scored that as a huge miss on one day and phantom snow the
next (night-before errors of −6…−13 then +11…+19; re-joined to the report
window its own hours actually overlap, the same forecasts were within
~±4 cm at four of five resorts).

Fix (2026-07-13): `collectors/janesweather.py` now sums the API's hourly
`snow` into 7am→7am windows, so the stored target-D value covers exactly
the window of the report dated D+1. The old calendar-day rows were renamed
to `janesweather_cal` (kept for reference; non-ensemble, undisplayed) and
the canonical `janesweather` series restarts clean — same accepted cost as
the BOM night-before purge.

The same midnight→midnight skew applies in principle to **Open-Meteo**
(`daily=snowfall_sum`), **YR.no** (hourly steps bucketed by calendar
date), and **BOM** daily rainfall. They are hourly-capable and could be
re-windowed the same way; that is a follow-up decision because it breaks
those series' meaning too (and Open-Meteo's backfilled history would need
re-deriving from its previous-runs archive). Snowatch, Mountainwatch and
Snow-Forecast publish ski-day tables without a stated clock window and
cannot be re-windowed.

A principled elevation adjustment of daily cm totals would need per-day
freezing-level data (the rain/snow split is a threshold effect, not a linear
lapse), which we don't collect. Rather than invent a scaling factor, the
`snowforecast_bot/top` bands are now being archived — once a season of them
exists, the empirical bot→mid→top gradient per storm can inform a real
normalization if the accuracy data shows reference elevation is actually
driving provider rankings.
