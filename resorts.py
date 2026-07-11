"""Resort registry — every collector is parameterized by one of these.

Slugs/geohashes were probed live on 2026-07-10; all seven forecasters cover
all five resorts. `alt` pins the model elevation for the lat/lon-based
sources (Open-Meteo, YR.no) to a mid/upper-mountain height so the derived
snowfall matches what skiers experience rather than the valley floor.

Ground truth ("official") per resort:
  - perisher: Vail-platform HTML snow report, parsed by
    collectors/actuals_official.py
  - hotham: the conditions/snow-reports page's "Natural snow fall and
    depth" section, which (unlike the Vail-style reports page) carries an
    "Issued:" timestamp for the snow report itself
  - fallscreek: the WordPress JSON feed behind fallscreek.com.au/snow-report
    (ski-patrol fresh-snow figure, stamped ~6:15am)
  - thredbo: thredbo.com.au/weather/snow-report/ serves a raw LivePass
    snowReport XML document (snow24Hours + full ISO `updated` stamp)
  - buller: api.mtbuller.com.au/api/weather/widget — the JSON feed behind
    the (otherwise JS-rendered) mtbuller.com.au snow report page
    (snow_report.snow_last_24_hours + ISO last_updated)

Every resort now has an official source (re-probed 2026-07-11). The
snowatch.com.au homepage 24hr table is the mid-rank proxy, and OnTheSnow's
resort-reported history remains the lowest-rank gap-filling fallback.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Resort:
    id: str
    name: str
    state: str
    lat: float
    lon: float
    alt: int
    bom_geohash: str
    snowatch_slug: str
    mountainwatch_slug: str
    snowforecast_slug: str
    onthesnow_slug: str
    official_kind: str | None = None  # "vail" | "hotham_html" | "falls_json" | "thredbo_xml" | "buller_json" | None
    official_url: str | None = None


RESORTS: dict[str, Resort] = {r.id: r for r in [
    Resort(
        id="perisher", name="Perisher", state="NSW",
        lat=-36.405, lon=148.410, alt=1720,
        bom_geohash="r398cm",  # kept from v1 for series continuity
        snowatch_slug="perisher", mountainwatch_slug="perisher",
        snowforecast_slug="Perisher-Blue", onthesnow_slug="perisher",
        official_kind="vail",
        official_url="https://www.perisher.com.au/reports-cams/reports/snow-report",
    ),
    Resort(
        id="thredbo", name="Thredbo", state="NSW",
        lat=-36.500, lon=148.290, alt=1700,
        bom_geohash="r392q7",
        snowatch_slug="thredbo", mountainwatch_slug="thredbo",
        snowforecast_slug="Thredbo", onthesnow_slug="thredbo-alpine-resort",
        official_kind="thredbo_xml",
        official_url="https://www.thredbo.com.au/weather/snow-report/",
    ),
    Resort(
        id="hotham", name="Mt Hotham", state="VIC",
        lat=-36.976, lon=147.146, alt=1750,
        bom_geohash="r32tsk",
        snowatch_slug="hotham", mountainwatch_slug="mt-hotham",
        snowforecast_slug="Mount-Hotham", onthesnow_slug="mt-hotham",
        official_kind="hotham_html",
        official_url="https://www.mthotham.com.au/mountain/conditions/snow-reports",
    ),
    Resort(
        id="fallscreek", name="Falls Creek", state="VIC",
        lat=-36.866, lon=147.278, alt=1650,
        bom_geohash="r32wr2",
        snowatch_slug="falls-creek", mountainwatch_slug="falls-creek",
        snowforecast_slug="Falls-Creek", onthesnow_slug="falls-creek-alpine-resort",
        official_kind="falls_json",
        official_url="https://www.fallscreek.com.au/wp-content/uploads/FCSnowReport_2021.json",
    ),
    Resort(
        id="buller", name="Mt Buller", state="VIC",
        lat=-37.146, lon=146.446, alt=1700,
        bom_geohash="r32hsm",
        snowatch_slug="mt-buller", mountainwatch_slug="mount-buller",
        snowforecast_slug="Mount-Buller", onthesnow_slug="mt-buller",
        official_kind="buller_json",
        official_url="https://api.mtbuller.com.au/api/weather/widget",
    ),
]}
