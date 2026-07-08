# Snow Forecast Accuracy Tracker — Feasibility Report & Implementation Plan

*Reverse-engineered from Star_Hawk's "Daily forecast comparisons" threads on ski.com.au (2024–2025), with live probing of every data source on 2026-07-09.*

## 1. What the existing project actually does

- **Author/process:** Forum user **Star_Hawk**, fully **manual**. Each day he reads ~8 forecast websites, types the numbers in, and builds the graphics in **Canva** ("graphics are the time consuming part" — his own words, and the likely reason it fizzled out, along with a poor 2024 season).
- **Providers tracked (2025):** Snowatch, OpenSnow (replaced J2SKI from 2024), Snow-Forecast.com, Mountainwatch, Weatherzone, Jane's Weather, YR.no, BOM.
- **Ground truth:** the **Perisher resort snow report / snowstake — 24-hour snowfall to 7am** (matches the "24 hour Snowfall to 7am: 15cm" header in the accuracy graphic). He applies manual judgment when the stake is unreliable: *"Due to the wind, im going off perishers report of 12cm last night, as the snowstake is probably unreliable in this sort of high wind."*
- **Lead time:** your hunch is right — the scored forecast is the **short-lead (~24h) forecast for "today"**, compared against the 24h-to-7am observation. The "Next 5 Days" chart is tracked/displayed but the accuracy ranking is driven by the daily comparison.
- **Scoring:** cumulative accuracy % per provider (formula never disclosed; tweaked between seasons), plus a **"weighted average" ensemble forecast** where providers are weighted by their running accuracy ("they'll lose accuracy points and be weighted down as a result").
- **BOM caveat:** his BOM number isn't from a public page — he manually sums ADFD grid precipitation where temp ≤ 0°C. We'd replicate this differently (see below).

## 2. Source-by-source feasibility (all probed live)

### Tier 1 — easy, include in v1

| Source | Access | Verified today |
|---|---|---|
| **YR.no** | Official free API (`api.met.no/weatherapi/locationforecast/2.0`) | ✅ JSON for Perisher coords: hourly temp + precip. Derive snow (precip where T ≲ 1°C, ~1mm→1cm) exactly as yr's own site does. Rock solid, documented, ToS-friendly (needs a UA string). |
| **BOM** | Undocumented JSON API (`api.weather.bom.gov.au/v1/locations/r398cm/forecasts/daily`) | ✅ Returns daily rain min/max + temps for Perisher Valley. Snow derived same way. ⚠️ API metadata says "you must not use, copy or share" — for a personal project fine in practice, but the clean alternative is BOM's public FTP forecast products or Open-Meteo's ACCESS-G model feed. |
| **Snow-Forecast.com** | Server-rendered HTML (`/resorts/Perisher-Blue/6day/mid`) | ✅ Full forecast table in static HTML, snow cm per AM/PM/night block. Simple parse. |
| **Mountainwatch** | Server-rendered HTML (`/australia/perisher/weather/`) | ✅ Per-day snow cm to one decimal (e.g. "Sun: 17.8 cm") directly in HTML. Simple parse. |
| **Snowatch** | Server-rendered HTML | ✅ Accessible, but forecasts are published as **ranges over 5/10/15 days** (e.g. "4–13cm"), not clean per-day cm. Include using range midpoints from the per-resort 15-day page, or defer. |

### Tier 2 — tedious, defer from v1 (flagged per your instruction)

- **Weatherzone** — Next.js app; the `/snow` page's embedded JSON is just a shell and the data comes from a keyed client-side API. Doable with headless browser or key extraction from their JS bundle, but brittle. Phase 2.
- **Jane's Weather** — pure SPA (6KB HTML shell, zero data); the AI forecast is their paid product. Would need headless + likely an account. Phase 2 / skip.
- **OpenSnow** — returns 403/404 to non-browser clients, 10-day data is paywalled, no public API. Skip.

Losing these three still leaves 5 providers + our own weighted ensemble — enough for meaningful rankings, and Tier 2 can be added later without touching the core.

### Ground truth (the linchpin) — feasible ✅

- Perisher's own snow-report page is JS-rendered (old Joomla + XHR; their public "dashboard" was discontinued), so not the easiest path.
- **OnTheSnow** (`onthesnow.com/australia/perisher/skireport`) embeds the resort-reported figures in `__NEXT_DATA__` JSON — verified today: `last24`, `newSnow`, `snowDepth` fields present. This is the same resort-reported number Star_Hawk used.
- Cross-checks: Snowatch's resort reports (24h snowfall + natural depth), ski.com.au's own snow reports page. **Spencers Creek (Snowy Hydro)** is only measured ~weekly — useful as a seasonal sanity check, not for daily scoring.

## 3. Verdict

**Feasible, and genuinely automatable end-to-end** — collection, scoring, and chart generation can run unattended on a daily schedule, with the human role reduced to occasional calibration and fixing a scraper when a site redesigns. The manual Canva step that killed the original project is exactly the part that automates best.

## 4. Implementation plan (v1, code-light — roughly 500–800 lines of Python)

**Stack:** Python + `requests`/`beautifulsoup4` + SQLite + `matplotlib`/`plotly`. No framework, no server.

**Structure:**
```
collectors/          # one small module per source, all returning
                     #   (source, run_time, target_date, snow_cm)
  yrno.py bom.py snowforecast.py mountainwatch.py snowatch.py
  actuals_onthesnow.py
store.py             # SQLite: forecasts(source, issued_at, target_date, cm),
                     #         actuals(date, cm, source)
score.py             # scoring + weighted ensemble
charts.py            # the three graphics
run_evening.py       # captures forecasts
run_morning.py       # captures actuals, scores, renders
```

**Schedule (two runs, AEST):**
1. **~6pm daily** — snapshot every provider's forecast for tomorrow and the next 5 days. Snapshotting at a fixed time is what defines the "24h lead" being scored (the original never pinned this down; we make it explicit and can score longer leads for free since we store every horizon).
2. **~7:45am daily** — pull the reported 24h-to-7am snowfall, score yesterday-evening's forecasts, regenerate charts.

Runner: **GitHub Actions cron** (free, zero infra, DB + PNGs committed back to the repo, failures email you automatically) — or launchd/Claude Code scheduled task if you'd rather keep it local.

**Scoring (v1 proposal, since the original formula was never disclosed):**
- Daily error per provider: `|forecast − actual|`.
- Accuracy % as a decaying skill score, e.g. `100 × max(0, 1 − MAE_season / mean(max(actual, 2cm)))` — the 2cm floor stops 0cm days from dominating, mirroring the original's "rankings meaningless until notable snow".
- Ensemble = accuracy-weighted mean of provider forecasts, itself scored as a ninth "provider".

**Charts (matplotlib, replacing Canva):** daily comparison bars (today + next-7-days per provider), accuracy ranking bars, and the next-5-days grouped chart. Rendered to PNG on every morning run.

**Monitoring/calibration:** every collector returns `None` on parse failure rather than 0 → any `None` or a failed run triggers a notification; a weekly glance at the charts covers drift. Site redesigns (the main ongoing cost) show up as an alert, not silent bad data.

**Phase 2 (optional):** Weatherzone via Playwright; OpenSnow/Jane's Weather if paid access; Thredbo as a second ground-truth resort; publish charts to a small static page.
