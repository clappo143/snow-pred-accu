"""Render docs/index.html — a self-contained dashboard app — from the DB.

Single generated file, vanilla JS; the only external asset is the Google
Fonts stylesheet (Fraunces + Archivo), with system fallbacks so the page
still reads fine offline. Seven switchable palettes (each designed for light
and dark), stackable chart views (bars, range band, cumulative, lines,
table), a hero panel that reads the next snow event — consensus dot-strip,
outlier call-out — hover tooltips with per-day provider breakdowns, and a
manual-entry panel whose export feeds data/manual.json for season backfill.

The ensemble shown on the page is computed client-side as a weighted median
of whichever forecasters are toggled on (legend clicks), with per-forecaster
weights editable in the Weights panel. The server-side stored ensemble
(score.weighted_ensemble) is still written to the DB for scoring history but
the page ignores it for display.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

import store
from collectors.common import TZ
from resorts import RESORTS
from score import FLOOR_CM, HEADLINE, accuracy, accuracy_by_lead, pairs

import os

SITE = Path(__file__).parent / "docs"  # GitHub Pages only serves / or /docs
# GITHUB_REPOSITORY ("owner/repo") is set automatically in Actions, so a fork
# gets a correct link to its own Actions page without any edit.
_REPO = os.environ.get("GITHUB_REPOSITORY", "clappo143/snow-pred-accu")
ACTIONS_URL = f"https://github.com/{_REPO}/actions/workflows/daily.yml"

# Categorical series palette — hues spread across the wheel (blue, teal, red,
# violet, rose, gold, orange, green) for maximum series separation. Verified
# min pairwise ΔE76 ≈ 28, all mid-lightness so they read on light and dark.
PROVIDER_COLORS = {
    "yrno": "#3B79C4",
    "bom": "#12A2A2",
    "bom_meteye": "#586E75",  # muted slate-teal: BOM's sibling methodology
    "snowforecast": "#D2473E",
    "mountainwatch": "#9463C6",
    "janesweather": "#D65C9B",
    "snowatch": "#C4A028",
    "openmeteo": "#E36E24",
    "ensemble": "#3E9E62",
}
# Categorical colours for resort-vs-resort views (group mode) — same
# verified mid-lightness family as the provider palette.
RESORT_COLORS = {
    "perisher": "#3B79C4",
    "thredbo": "#12A2A2",
    "hotham": "#D2473E",
    "fallscreek": "#9463C6",
    "buller": "#E36E24",
}

PROVIDER_NAMES = {
    "yrno": "YR.no",
    "bom": "BOM",
    "bom_meteye": "BOM MetEye",
    "snowforecast": "Snow-Forecast",
    "mountainwatch": "Mountainwatch",
    "janesweather": "Jane's Weather",
    "snowatch": "Snowatch",
    "openmeteo": "Open-Meteo",
    "ensemble": "Ensemble",
}

# Brand marks (favicons/app-icons) embedded as data URIs so the page stays
# self-contained. Missing files just fall back to the coloured dot. The
# ensemble has no brand, so it keeps its coloured mark.
LOGO_DIR = Path(__file__).parent / "assets" / "logos"


def _logo_uri(source_id: str) -> str | None:
    p = LOGO_DIR / f"{source_id}.png"
    if not p.exists():
        return None
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# palette -> mode -> tokens. "clay" is the Anthropic-inspired scheme:
# ivory ground, book-cloth coral accent, warm near-black ink.
PALETTES = {
    "glacier": {
        "label": "Glacier",
        "light": dict(bg="#F2F5F9", card="#FFFFFF", ink="#1B2733", muted="#5D6C7B",
                      line="#DCE4EC", accent="#3E8FD8", chip="#E7EEF5"),
        "dark": dict(bg="#10161E", card="#18212C", ink="#E8EEF4", muted="#8DA0B3",
                     line="#26313E", accent="#5FA8E8", chip="#1F2A37"),
    },
    "clay": {
        "label": "Clay",
        "light": dict(bg="#F0EEE6", card="#FAF9F5", ink="#191919", muted="#6E6A5E",
                      line="#E0DCD1", accent="#CC785C", chip="#E8E4D9"),
        "dark": dict(bg="#1F1E1B", card="#282723", ink="#F0EEE6", muted="#A8A294",
                     line="#3A382F", accent="#D4A27F", chip="#31302A"),
    },
    "aurora": {
        "label": "Aurora",
        "light": dict(bg="#F1F6F5", card="#FFFFFF", ink="#122B29", muted="#527370",
                      line="#D8E5E3", accent="#0E9488", chip="#E2EEEC"),
        "dark": dict(bg="#0C1514", card="#132020", ink="#DCEDEA", muted="#7FA39E",
                     line="#1E3230", accent="#2DD4BF", chip="#162624"),
    },
    "corduroy": {
        "label": "Corduroy",
        "light": dict(bg="#FDF3E7", card="#FFFBF4", ink="#33261B", muted="#8A7360",
                      line="#EEDFC9", accent="#C2410C", chip="#F6E8D4"),
        "dark": dict(bg="#1C1410", card="#251B15", ink="#F3E9DD", muted="#B39A83",
                     line="#3A2C21", accent="#F97316", chip="#2E221A"),
    },
    "alpenglow": {
        "label": "Alpenglow",
        "light": dict(bg="#F6F0F1", card="#FFFDFD", ink="#2A2126", muted="#7C6A72",
                      line="#E9DBDE", accent="#B95A6C", chip="#F0E3E6"),
        "dark": dict(bg="#191216", card="#231A1F", ink="#F2E8EB", muted="#A98F98",
                     line="#382B32", accent="#E08D9B", chip="#2C2127"),
    },
    "wattle": {
        "label": "Wattle",
        "light": dict(bg="#F7F4E9", card="#FFFDF5", ink="#26231A", muted="#79715A",
                      line="#E7E0C9", accent="#96780A", chip="#EFE9D4"),
        "dark": dict(bg="#171509", card="#211E11", ink="#F1EDDE", muted="#A79F84",
                     line="#37331F", accent="#DCB53B", chip="#2A2716"),
    },
    "piste": {
        "label": "Piste",
        "light": dict(bg="#F4F4F2", card="#FFFFFF", ink="#17181A", muted="#61656B",
                      line="#E1E2DF", accent="#C13540", chip="#EBECE8"),
        "dark": dict(bg="#121314", card="#1B1D1F", ink="#ECEDEE", muted="#93989F",
                     line="#2B2E31", accent="#E05560", chip="#232629"),
    },
}


def _palette_css() -> str:
    def block(t: dict) -> str:
        return (f"--bg:{t['bg']};--card:{t['card']};--ink:{t['ink']};"
                f"--muted:{t['muted']};--line:{t['line']};--accent:{t['accent']};"
                f"--chip:{t['chip']};")
    css = ""
    for pid, p in PALETTES.items():
        sel = f':root[data-palette="{pid}"]'
        css += f"{sel}{{{block(p['light'])}}}\n"
        css += f"@media (prefers-color-scheme: dark){{{sel}{{{block(p['dark'])}}}}}\n"
        css += f'{sel}[data-theme="dark"]{{{block(p["dark"])}}}\n'
        css += f'{sel}[data-theme="light"]{{{block(p["light"])}}}\n'
    return css


CSS = """
:root { color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; } }
:root[data-theme="dark"] { color-scheme: dark; }
:root[data-theme="light"] { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 "Archivo", "Avenir Next", "Segoe UI", sans-serif;
  transition: background 0.3s, color 0.3s;
  -webkit-font-smoothing: antialiased; }
main { max-width: 1100px; margin: 0 auto; padding: 30px 24px 44px; }
header { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 16px; }
.masthead { flex: 1 1 320px; }
h1 { font-family: "Fraunces", Georgia, serif; font-weight: 600;
  font-size: clamp(27px, 4.5vw, 36px); line-height: 1.08; margin: 0;
  letter-spacing: -0.015em; }
h1 span { color: var(--accent); font-style: italic; }
h1 b { font-weight: 600; }
.resortbar { margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }
.resortbar .seg { flex-wrap: wrap; }
.resortbar .seg button { padding: 7px 14px; }
/* group mode (All / NSW / VIC) swaps the resort-specific cards for the
   aggregate ones */
body[data-mode="group"] .resort-only { display: none !important; }
body[data-mode="resort"] .group-only { display: none !important; }
.hero.group { grid-template-columns: 1fr; }
.ginsight { font-size: 13.5px; margin-bottom: 12px; }
.ginsight b { font-weight: 650; }
.rcards { display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.rcard { background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; padding: 12px 15px 13px; text-align: left;
  cursor: pointer; font: inherit; color: var(--ink); display: flex;
  flex-direction: column; gap: 3px; transition: border-color 0.18s;
  border-top-width: 3px; }
.rcard:hover { border-color: var(--accent); }
.rcard .rname { display: flex; align-items: baseline; gap: 7px;
  font-weight: 650; font-size: 14.5px; }
.rcard .rname small { color: var(--muted); font-size: 10px;
  text-transform: uppercase; letter-spacing: 0.08em; font-weight: 650; }
.rcard .evt { font-size: 18px; font-weight: 650; letter-spacing: -0.015em;
  font-variant-numeric: tabular-nums; }
.rcard .evt .cum { font-size: 13px; color: var(--accent); font-weight: 650; }
.rcard .meta { color: var(--muted); font-size: 11.5px;
  font-variant-numeric: tabular-nums; }
.swatchdot { display: inline-block; width: 9px; height: 9px;
  border-radius: 50%; margin-right: 8px; vertical-align: baseline; }
.sub { color: var(--muted); font-size: 13.5px; margin: 7px 0 0; max-width: 62ch; }
.headtools { display: flex; flex-direction: column; align-items: flex-end;
  gap: 10px; margin-left: auto; }
.stamp { color: var(--muted); font-size: 11px; margin: 0;
  text-transform: uppercase; letter-spacing: 0.09em; font-weight: 600;
  font-variant-numeric: tabular-nums; text-align: right; }
.spacer { flex: 1; }
.select-wrap { position: relative; display: inline-block; }
.select-wrap::after { content: "▾"; position: absolute; right: 13px; top: 50%;
  transform: translateY(-50%); pointer-events: none; color: var(--muted);
  font-size: 11px; }
.palette-select { appearance: none; -webkit-appearance: none;
  background: var(--chip); color: var(--ink); border: 1px solid var(--line);
  border-radius: 999px; padding: 7px 34px 7px 15px; cursor: pointer;
  font: 600 12px "Archivo", sans-serif; transition: border-color 0.18s; }
.palette-select:hover { border-color: var(--muted); }
.headctl { display: flex; gap: 8px; align-items: center; }
.iconbtn { display: inline-flex; align-items: center; justify-content: center;
  width: 31px; height: 31px; border-radius: 999px; background: var(--chip);
  border: 1px solid var(--line); color: var(--ink); cursor: pointer;
  transition: border-color 0.18s, color 0.18s; }
.iconbtn:hover { border-color: var(--muted); color: var(--accent); }
/* brand-mark badge (logo on a light tile) with a coloured-dot fallback */
.mark { display: inline-flex; width: 18px; height: 18px; border-radius: 5px;
  overflow: hidden; background: #fff; border: 1px solid var(--line);
  flex: none; vertical-align: middle; }
.mark img { width: 100%; height: 100%; object-fit: contain; padding: 1.5px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  flex: none; vertical-align: middle; }
.rule { height: 1px; background: var(--line); border: 0; margin: 18px 0 20px; }
h2 { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.11em;
  color: var(--muted); margin: 0; font-weight: 650; }
.hero { display: grid; gap: 12px; margin: 0 0 14px;
  grid-template-columns: minmax(320px, 1.3fr) 1fr; align-items: stretch; }
@media (max-width: 800px) { .hero { grid-template-columns: 1fr; } }
.herocard { border-radius: 14px; padding: 18px 20px;
  border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--line));
  background: var(--card);
  box-shadow: 0 1px 2px color-mix(in srgb, var(--ink) 4%, transparent),
    0 10px 28px -20px color-mix(in srgb, var(--ink) 22%, transparent);
  display: flex; flex-direction: column; gap: 5px; }
.herocard small, .stat small { display: block; color: var(--muted);
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.11em;
  font-weight: 600; margin-bottom: 4px; }
.herocard small { color: var(--accent); }
.heroline { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.heroline b { font-size: 24px; font-weight: 650; line-height: 1.15;
  letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.heroline .cum { font-size: 14.5px; font-weight: 650; color: var(--accent);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  padding: 3px 11px; border-radius: 999px; font-variant-numeric: tabular-nums; }
.herocard .range { font-size: 12.5px; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.insight { font-size: 13px; margin-top: 1px; }
.insight b { font-weight: 650; }
.strip { margin-top: 12px; }
.strip-medlabel { position: relative; height: 19px; margin-bottom: 2px; }
.strip-medlabel span { position: absolute; transform: translateX(-50%);
  white-space: nowrap; font-size: 11.5px; font-weight: 700; color: var(--accent);
  background: color-mix(in srgb, var(--accent) 15%, var(--card));
  padding: 2px 9px; border-radius: 999px; font-variant-numeric: tabular-nums; }
.strip-track { position: relative; height: 34px; overflow: visible; }
.strip-track::before { content: ""; position: absolute; left: 0; right: 0;
  top: 50%; height: 2px; border-radius: 2px; transform: translateY(-50%);
  background: var(--line); }
.strip-med { position: absolute; top: -3px; bottom: -3px; width: 2.5px;
  background: var(--accent); transform: translateX(-50%); border-radius: 2px; }
.strip-med::before { content: ""; position: absolute; top: -5px; left: 50%;
  transform: translateX(-50%); border: 5px solid transparent;
  border-top-color: var(--accent); }
.strip-badge { position: absolute; top: 50%; width: 21px; height: 21px;
  transform: translate(-50%, -50%); border-radius: 6px; overflow: hidden;
  background: #fff; border: 1px solid var(--line); padding: 2px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.22); }
.strip-badge img { width: 100%; height: 100%; object-fit: contain; display: block; }
.strip-scale { display: flex; justify-content: space-between; margin-top: 5px;
  font-size: 11.5px; font-weight: 700; color: var(--ink);
  font-variant-numeric: tabular-nums; }
.strip-scale .unit { color: var(--muted); font-weight: 500; }
.statgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.stat { background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; padding: 13px 15px; }
.stat:last-child:nth-child(odd) { grid-column: span 2; }
.stat b { font-size: 21px; font-weight: 650; line-height: 1.1;
  letter-spacing: -0.015em; font-variant-numeric: tabular-nums; }
.stat .range { font-size: 11.5px; color: var(--muted); margin-top: 4px;
  font-variant-numeric: tabular-nums; }
#tip { position: fixed; left: 0; top: 0; z-index: 10; pointer-events: none;
  background: var(--card); color: var(--ink);
  border: 1px solid color-mix(in srgb, var(--ink) 15%, var(--line));
  border-radius: 10px; padding: 9px 12px; font-size: 12px; max-width: 260px;
  box-shadow: 0 4px 12px -7px rgba(0, 0, 0, 0.4);
  opacity: 0; transition: opacity 0.12s; }
#tip.on { opacity: 1; }
#tip h4 { margin: 0 0 6px; font: 650 10.5px "Archivo", sans-serif;
  text-transform: uppercase; letter-spacing: 0.09em; color: var(--muted); }
#tip .trow { display: flex; align-items: center; gap: 7px; margin: 2px 0;
  min-width: 150px; font-variant-numeric: tabular-nums; }
#tip .trow .mark { width: 15px; height: 15px; border-radius: 4px; }
#tip .trow .dot { width: 9px; height: 9px; }
#tip .trow b { margin-left: auto; font-weight: 650; padding-left: 12px; }
.card { background: var(--card); border: 1px solid var(--line);
  border-radius: 14px; padding: 17px 20px; margin: 0 0 12px;
  box-shadow: 0 1px 2px color-mix(in srgb, var(--ink) 4%, transparent),
    0 10px 28px -20px color-mix(in srgb, var(--ink) 22%, transparent); }
.cardhead { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  margin-bottom: 13px; }
.seg { display: inline-flex; background: var(--chip); border: 1px solid var(--line);
  border-radius: 999px; padding: 3px; gap: 2px; }
.seg button { border: 0; background: transparent; color: var(--muted);
  font: 600 12px/1 "Archivo", sans-serif; padding: 6px 12px; cursor: pointer;
  border-radius: 999px; transition: background 0.18s, color 0.18s; }
.seg button:hover { color: var(--ink); }
.seg button.on { background: var(--card); color: var(--ink);
  box-shadow: 0 1px 3px color-mix(in srgb, var(--ink) 16%, transparent); }
.panel { margin-bottom: 16px; animation: fadeup 0.4s ease backwards; }
.panel:last-child { margin-bottom: 0; }
@keyframes fadeup { from { opacity: 0; transform: translateY(6px); } }
.panel-tag { display: flex; align-items: center; gap: 10px; font-size: 10.5px;
  text-transform: uppercase; letter-spacing: 0.11em; color: var(--muted);
  font-weight: 650; margin-bottom: 8px; white-space: nowrap; }
.panel-tag::after { content: ""; flex: 1; height: 1px; background: var(--line); }
.ens-note { font-size: 12px; color: var(--muted); margin-bottom: 11px; }
.ens-note b { color: var(--ink); font-weight: 650;
  font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.legend button.lgd { display: inline-flex; align-items: center; gap: 7px;
  border: 1px solid var(--line); background: var(--chip); color: var(--ink);
  font: 500 12px "Archivo", sans-serif; cursor: pointer; padding: 5px 12px;
  border-radius: 999px; transition: border-color 0.18s, opacity 0.18s; }
.legend button.lgd:hover { border-color: var(--muted); }
.legend i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
.lgd.off { opacity: 0.4; border-style: dashed; background: transparent; }
#advanced { background: var(--chip); border-radius: 10px;
  padding: 14px 16px; margin-bottom: 16px; }
.advhead { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  margin-bottom: 8px; }
.wrow { display: grid; grid-template-columns: 150px 1fr 36px; gap: 10px;
  align-items: center; font-size: 13px; margin: 6px 0; }
.wrow span i { display: inline-block; width: 9px; height: 9px;
  border-radius: 50%; margin-right: 7px; }
.wrow.off { opacity: 0.4; }
.wrow b { text-align: right; font-variant-numeric: tabular-nums; }
.wrow input[type="range"] { width: 100%; accent-color: var(--accent); margin: 0; }
.days { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 0;
  align-items: stretch; height: 200px; padding-top: 10px;
  border-bottom: 1px solid var(--line); }
/* extra headroom when brand caps sit above the bars */
.days.withmarks { height: 218px; padding-top: 34px; }
.day { display: flex; padding: 0 9px; }
.day + .day { border-left: 1px dashed var(--line); }
.bars { flex: 1; display: flex; align-items: flex-end; gap: 2px; }
.bar { flex: 1; border-radius: 2px 2px 0 0; min-height: 1px; position: relative;
  transition: filter 0.15s; }
.day:hover .bar { filter: saturate(1.15) brightness(1.08); }
.bar em { position: absolute; top: -16px; left: 50%; transform: translateX(-50%);
  font: 600 10px/1 "Archivo", sans-serif; font-style: normal; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.bar .barmark { position: absolute; top: -32px; left: 50%;
  transform: translateX(-50%); width: 15px; height: 15px; border-radius: 4px;
  overflow: hidden; background: #fff; border: 1px solid var(--line); padding: 1px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2); z-index: 1; }
.bar .barmark img { width: 100%; height: 100%; object-fit: contain; display: block; }
.bar .barmark i { display: block; width: 100%; height: 100%; border-radius: 50%; }
.daylabels { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; }
.daylabel { text-align: center; padding-top: 6px; color: var(--muted);
  font-size: 11.5px; white-space: nowrap; }
svg text { fill: var(--muted); font: 600 10.5px "Archivo", sans-serif; }
svg .grid { stroke: var(--line); stroke-width: 1; }
svg .vgrid { stroke: var(--muted); stroke-opacity: 0.4; stroke-width: 1;
  stroke-dasharray: 3 4; }
.rank { display: grid; grid-template-columns: 150px 1fr 56px; gap: 12px;
  align-items: center; margin: 10px 0; font-size: 13.5px; }
.rank .track { background: var(--chip); border-radius: 999px; height: 10px;
  overflow: hidden; }
.rank .fill { height: 100%; border-radius: 999px; }
.rank b { text-align: right; font-variant-numeric: tabular-nums;
  font-family: "Fraunces", Georgia, serif; font-size: 15.5px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px;
  font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--ink); font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.05em; font-weight: 700; }
td.tsrc { font-weight: 650; }
td.tsrc .mark, td.tsrc .dot { margin-right: 8px; }
tr:hover td { background: color-mix(in srgb, var(--chip) 55%, transparent); }
.empty { color: var(--muted); font-size: 13.5px; font-style: italic; }
.scroll { overflow-x: auto; }
footer { color: var(--muted); font-size: 12px; margin-top: 12px;
  line-height: 1.55; }
.colophon { text-align: center; margin-top: 32px; max-width: 70ch;
  margin-left: auto; margin-right: auto; }
.manual form { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.manual input, .manual select { background: var(--bg); border: 1px solid var(--line);
  color: var(--ink); border-radius: 8px; padding: 8px 12px; font: inherit;
  font-size: 13.5px; }
.manual input:focus, .manual select:focus { border-color: var(--accent);
  outline: none; }
.manual button[type="submit"] { background: var(--accent); color: var(--card);
  border: 0; border-radius: 999px; padding: 8px 16px;
  font: 600 13px "Archivo", sans-serif; cursor: pointer; }
.ghost { background: transparent; color: var(--muted);
  border: 1px solid var(--line); border-radius: 999px; padding: 6px 14px;
  font: 600 12px "Archivo", sans-serif; cursor: pointer;
  transition: color 0.18s, border-color 0.18s, background 0.18s; }
.ghost:hover { color: var(--accent); border-color: var(--accent); }
.ghost.on { background: var(--accent); border-color: var(--accent);
  color: var(--card); }
.chip { display: inline-flex; gap: 6px; align-items: center; background: var(--chip);
  border: 1px solid transparent; border-radius: 999px; padding: 4px 11px;
  font-size: 12px; margin: 2px; font-variant-numeric: tabular-nums; }
.chip button { border: 0; background: none; color: var(--muted); cursor: pointer;
  font-size: 13px; padding: 0; }
.chip i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.chip.stale { border-color: #E8820C; font-weight: 650; }
.freshlink { color: var(--accent); font-size: 12.5px; text-decoration: none;
  font-weight: 600; white-space: nowrap; }
.freshlink:hover { text-decoration: underline; }
/* accuracy headline — compact top-forecaster + runners-up */
.acc-headline { margin-bottom: 14px; }
.acc-top { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.acc-top .mark, .acc-top .dot { flex: none; }
.acc-top b { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.acc-top .aname { font-weight: 650; font-size: 14px; }
.acc-top .atag { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.09em;
  color: var(--accent); font-weight: 650; }
.acc-rest { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-left: 2px; }
.acc-rest span { display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.acc-rest .mark { width: 14px; height: 14px; border-radius: 3px; }
.acc-rest .dot { width: 7px; height: 7px; }
.acc-rest b { font-weight: 650; color: var(--ink); }
/* mini strip on group resort cards — badges stack onto lanes so close
   totals stay legible rather than piling up on one line */
.rcard .ministrip { margin-top: 9px; position: relative; }
.ministrip-scale { display: flex; justify-content: space-between; margin-top: 4px;
  font-size: 9.5px; font-weight: 700; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.ministrip-track { position: absolute; left: 0; right: 0; top: 0; height: 20px; }
.ministrip-track::before { content: ""; position: absolute; left: 0; right: 0;
  top: 10px; height: 1.5px; border-radius: 1px; transform: translateY(-50%);
  background: var(--line); }
.ministrip-med { position: absolute; top: 1px; height: 18px; width: 2px;
  background: var(--accent); transform: translateX(-50%); border-radius: 1px; }
.ministrip-med::before { content: ""; position: absolute; top: -4px; left: 50%;
  transform: translateX(-50%); border: 4px solid transparent;
  border-top-color: var(--accent); }
.ministrip-badge { position: absolute; width: 18px; height: 18px;
  transform: translate(-50%, -50%); border-radius: 4px; overflow: hidden;
  background: #fff; border: 1px solid var(--line); padding: 1.5px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2); }
.ministrip-badge img { width: 100%; height: 100%; object-fit: contain; display: block; }
.ministrip-badge i { display: block; width: 100%; height: 100%; border-radius: 50%; }
/* forecast vs actuals section */
.fva-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  margin-bottom: 12px; }
.fva-table td.hit { color: var(--accent); font-weight: 650; }
.fva-table td.miss { color: color-mix(in srgb, var(--ink) 60%, transparent); }
.fva-table td.actual-col { font-weight: 700; background:
  color-mix(in srgb, var(--accent) 6%, transparent); }
.fva-table td.err-col { font-variant-numeric: tabular-nums; }
.fva-summary { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; }
.fva-stat { text-align: center; }
.fva-stat b { display: block; font-size: 18px; font-weight: 700;
  font-variant-numeric: tabular-nums; }
.fva-stat small { font-size: 10.5px; text-transform: uppercase;
  letter-spacing: 0.09em; color: var(--muted); font-weight: 600; }
.spark { display: flex; align-items: flex-end; gap: 2px; height: 42px; }
.spark i { flex: 1; max-width: 14px; background: var(--accent);
  border-radius: 2px 2px 0 0; min-height: 2px; opacity: 0.85; }
.spark i.manual { opacity: 0.4; }
details.fold { background: var(--card); border: 1px solid var(--line);
  border-radius: 14px; margin: 0 0 16px; }
details.fold > summary { cursor: pointer; padding: 17px 22px; list-style: none;
  display: flex; align-items: baseline; gap: 12px; }
details.fold > summary::-webkit-details-marker { display: none; }
details.fold > summary::after { content: "+"; margin-left: auto;
  color: var(--muted); font: 400 17px/1 "Archivo", sans-serif; align-self: center;
  transition: transform 0.2s; }
details.fold[open] > summary::after { transform: rotate(45deg); }
details.fold .foldbody { padding: 0 22px 20px; }
.hint { color: var(--muted); font-size: 12px; }
details.fine { margin-top: 12px; font-size: 12px; color: var(--muted); }
details.fine summary { cursor: pointer; font-weight: 600; }
details.fine summary:hover { color: var(--accent); }
details.fine p { margin: 8px 0 0; line-height: 1.55; }
button:focus-visible, input:focus-visible, .swatch:focus-visible,
summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; } }
"""

JS = r"""
const $ = (s) => document.querySelector(s);

// --- shared tooltip: elements carry data-tip="<index into TIPS>" ---------
const TIPS = [];
const tipRef = (html) => { TIPS.push(html); return `data-tip="${TIPS.length - 1}"`; };
const tip = Object.assign(document.createElement("div"), { id: "tip" });
document.body.appendChild(tip);
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest ? e.target.closest("[data-tip]") : null;
  if (!t) { tip.classList.remove("on"); return; }
  tip.innerHTML = TIPS[+t.dataset.tip] || "";
  tip.classList.add("on");
  const pad = 14, r = tip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
});
document.addEventListener("scroll", () => tip.classList.remove("on"), true);

// per-day breakdown across visible sources, biggest call first
function dayTipHtml(d) {
  const rows = visible().map((s) => ({ s, v: F(s.id, d) }))
    .filter((r) => r.v != null).sort((a, b) => b.v - a.v)
    .map((r) => `<div class="trow">${badgeMark(r.s)}` +
      `${r.s.name}<b>${r.v.toFixed(1)}cm</b></div>`).join("");
  return `<h4>${fmtDay(d)}</h4>${rows || "no data"}`;
}
const PANEL_ORDER = ["bars", "range", "cumulative", "lines", "table"];
const PANEL_LABEL = { bars: "Daily bars", range: "Range band — provider spread with ensemble median",
  cumulative: "Cumulative", lines: "Daily lines", table: "Table" };
const state = {
  palette: localStorage.getItem("palette") || "glacier",
  theme: localStorage.getItem("theme") || "dark",   // dark by default
  resort: localStorage.getItem("resort") || "perisher",
  leadKey: localStorage.getItem("leadKey") || "pm:1", // night before
  panels: JSON.parse(localStorage.getItem("panels") || '["bars","cumulative"]'),
  horizon: +(localStorage.getItem("horizon") || 5),
  disabled: JSON.parse(localStorage.getItem("disabledSources") || "[]"),
  weights: JSON.parse(localStorage.getItem("sourceWeights") || "{}"),
  showAdv: localStorage.getItem("showAdv") === "1",
  barMarks: localStorage.getItem("barMarks") === "1",  // logos on daily bars
  manual: JSON.parse(localStorage.getItem("manualActuals") || "{}"),
  mforecasts: JSON.parse(localStorage.getItem("manualForecasts") || "[]"),
};
// views: a resort id, or a group id ("all" / "nsw" / "vic")
state.view = localStorage.getItem("view") || state.resort;
if (!(state.view in DATA.resorts) && !(state.view in DATA.groups))
  state.view = "perisher";
const activeGroup = () => DATA.groups[state.view] || null;
// the blob rankings read from: pooled group stats, or the resort's own
const activeAcc = () => activeGroup() || R;
// pre-multi-resort localStorage: flat {date: cm} becomes Perisher's
if (Object.values(state.manual).some((v) => typeof v === "number"))
  state.manual = { perisher: state.manual };
const fmtDay = (iso) => new Date(iso + "T12:00").toLocaleDateString("en-AU",
  { weekday: "short", day: "numeric" });
const plus1 = (iso) => {
  const t = new Date(iso + "T12:00"); t.setDate(t.getDate() + 1);
  return t.toISOString().slice(0, 10);
};
// R is the active resort's data blob; sources/providers follow it. In group
// mode R stays on the last resort (harmless — resort cards are hidden).
let R, Rid, sources, providers;
function bindResort() {
  if (DATA.resorts[state.view]) Rid = state.view;
  Rid = Rid || "perisher";
  R = DATA.resorts[Rid];
  sources = DATA.sources.filter((s) =>
    s.id === "ensemble" || s.id in R.forecasts);
  providers = sources.filter((s) => s.id !== "ensemble");
}
bindResort();
const isOn = (id) => !state.disabled.includes(id);
const weightOf = (id) => state.weights[id] ?? 50;
const visible = () => sources.filter((s) => isOn(s.id));

// a source's identifying mark: its brand logo on a light tile, or, lacking a
// logo (the ensemble), a coloured dot. `cls` lets callers size it in context.
function badgeMark(s, cls) {
  const k = cls ? " " + cls : "";
  return s.logo
    ? `<span class="mark${k}"><img src="${s.logo}" alt="" loading="lazy"></span>`
    : `<i class="dot${k}" style="background:${s.color}"></i>`;
}

// The ensemble is recomputed live in the browser: a weighted median of the
// providers that are toggled on, using the Advanced-panel weights (default 50
// each, i.e. a plain median). It ignores the server-side stored ensemble.
let ENS = {};

function wmedian(pairs) { // [value, weight], weights > 0
  const s = pairs.slice().sort((a, b) => a[0] - b[0]);
  const tot = s.reduce((t, p) => t + p[1], 0);
  let c = 0;
  for (let i = 0; i < s.length; i++) {
    c += s[i][1];
    if (c > tot / 2) return s[i][0];
    if (c === tot / 2) return (s[i][0] + s[Math.min(i + 1, s.length - 1)][0]) / 2;
  }
  return s[s.length - 1][0];
}

// weighted-median ensemble for any resort (group cards need them all)
function ensembleFor(rid) {
  const fc = DATA.resorts[rid].forecasts;
  const act = DATA.sources.filter((s) =>
    s.id !== "ensemble" && s.id in fc && isOn(s.id) && weightOf(s.id) > 0);
  const out = {};
  const dates = new Set();
  act.forEach((s) => Object.keys(fc[s.id]).forEach((d) => dates.add(d)));
  for (const d of dates) {
    const pairs = act.map((s) => [fc[s.id][d], weightOf(s.id)])
      .filter((p) => p[0] != null);
    if (pairs.length) out[d] = wmedian(pairs);
  }
  return out;
}

function computeEnsemble() {
  ENS = ensembleFor(Rid);
}

const F = (id, d) => (id === "ensemble" ? ENS[d] : R.forecasts[id][d]);

// per-day summary across the toggled-on providers (ensemble excluded)
function dayStats(d) {
  const vals = providers.filter((s) => isOn(s.id))
    .map((s) => R.forecasts[s.id][d]).filter((v) => v != null);
  if (!vals.length) return null;
  return { d, med: ENS[d] ?? median(vals),
    lo: Math.min(...vals), hi: Math.max(...vals) };
}

function setPalette(p) {
  state.palette = p;
  localStorage.setItem("palette", p);
  document.documentElement.dataset.palette = p;
  const sel = $("#paletteSelect");
  if (sel) sel.value = p;
}

const ICON_SUN = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>';

function setTheme(t) {
  state.theme = t;
  localStorage.setItem("theme", t);
  document.documentElement.dataset.theme = t;
  const b = $("#themeBtn");
  if (b) {  // show the icon for the mode you'd switch TO
    b.innerHTML = t === "dark" ? ICON_SUN : ICON_MOON;
    b.title = t === "dark" ? "Switch to light" : "Switch to dark";
  }
}

// The window opens on the snapshot day itself (today) — a forecast issued
// today is about today onwards, and "what's called for today" is exactly
// what a skier checks first. Runs from D through D+(horizon-1).
function horizonDates() {
  const out = [];
  for (let i = 0; i < state.horizon; i++) {
    const d = new Date(R.snapshot + "T12:00");
    d.setDate(d.getDate() + i);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function median(a) {
  const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// per-provider cumulative totals over a window of dates
function windowTotals(win) {
  return providers.filter((s) => isOn(s.id)).map((s) => {
    let t = 0, any = false;
    win.forEach((d) => {
      const v = R.forecasts[s.id][d];
      if (v != null) { t += v; any = true; }
    });
    return any ? { s, t } : null;
  }).filter(Boolean);
}

// spread meter: each forecaster's event total placed on a 0..max axis as its
// logo badge, with the ensemble median marked prominently. Badges that land
// close together are nudged onto stacked lanes so they never overlap.
function stripHtml(tot, med) {
  if (tot.length < 2) return "";
  const max = Math.max(1, med, ...tot.map((r) => r.t)) * 1.08;
  const xOf = (v) => 100 * v / max;
  const DY = [0, -18, 18, -36, 36], lanes = [];
  const placed = tot.slice().sort((a, b) => a.t - b.t).map((r) => {
    const x = xOf(r.t);
    let lane = 0;
    while (lane < lanes.length && x - lanes[lane] < 7) lane++;
    lanes[lane] = x;
    return { ...r, x, dy: DY[Math.min(lane, DY.length - 1)] };
  });
  const badges = placed.map((r) =>
    `<span class="strip-badge" style="left:${r.x.toFixed(1)}%;top:calc(50% + ${r.dy}px)"
      ${tipRef(`<h4>${r.s.name}</h4><div class="trow">event total<b>${r.t.toFixed(0)}cm</b></div>`)}>` +
    (r.s.logo ? `<img src="${r.s.logo}" alt="">`
      : `<i style="width:100%;height:100%;display:block;background:${r.s.color}"></i>`) +
    `</span>`).join("");
  const mx = xOf(med).toFixed(1);
  return `<div class="strip">
    <div class="strip-medlabel"><span style="left:${mx}%">median ${med.toFixed(0)}cm</span></div>
    <div class="strip-track">
      <span class="strip-med" style="left:${mx}%"
        ${tipRef(`<div class="trow">ensemble median<b>${med.toFixed(0)}cm</b></div>`)}></span>${badges}
    </div>
    <div class="strip-scale"><span>0<span class="unit">cm</span></span>
      <span>${max.toFixed(0)}<span class="unit">cm</span></span></div></div>`;
}

// one-line read on how much the forecasters agree, and who's the outlier
function insightText(tot, med) {
  if (tot.length < 2) return "";
  const lo = Math.min(...tot.map((r) => r.t)), hi = Math.max(...tot.map((r) => r.t));
  const out = tot.map((r) => ({ ...r, dev: r.t - med }))
    .sort((a, b) => Math.abs(b.dev) - Math.abs(a.dev))[0];
  if (Math.abs(out.dev) > Math.max(10, med * 0.75))
    return `Consensus sits near <b>${med.toFixed(0)}cm</b> — the outlier is
      <b>${out.s.name}</b> at ${out.t.toFixed(0)}cm
      (${out.dev > 0 ? "+" : "−"}${Math.abs(out.dev).toFixed(0)}cm vs the median).`;
  if (hi - lo <= Math.max(6, med * 0.5))
    return `Tight consensus — every forecaster lands within
      <b>${lo.toFixed(0)}–${hi.toFixed(0)}cm</b>.`;
  return `Moderate spread — calls run <b>${lo.toFixed(0)}–${hi.toFixed(0)}cm</b>
    around a ${med.toFixed(0)}cm median, with no single outlier.`;
}

// compact accuracy headline: top forecaster prominent, rest inline
function accHeadlineHtml(accObj) {
  const ranked = DATA.sources
    .map((s) => ({ s, pct: accObj[s.id] }))
    .filter((r) => r.pct != null && r.s.id !== "ensemble")
    .sort((a, b) => b.pct - a.pct);
  if (!ranked.length) return "";
  const top = ranked[0];
  const rest = ranked.slice(1);
  let html = `<div class="acc-headline">
    <div class="acc-top">
      ${badgeMark(top.s)}
      <span class="aname">${top.s.name}</span>
      <b>${top.pct.toFixed(0)}%</b>
      <span class="atag">most accurate</span>
    </div>`;
  if (rest.length)
    html += `<div class="acc-rest">${rest.map((r) =>
      `<span>${badgeMark(r.s)}${r.s.name} <b>${r.pct.toFixed(0)}%</b></span>`
    ).join("")}</div>`;
  return html + "</div>";
}

function renderBadges() {
  const days = horizonDates();
  const stats = days.map(dayStats);
  const first = stats.findIndex((s) => s && s.med >= 1);
  let peak = null;
  for (const s of stats) if (s && (!peak || s.med > peak.med)) peak = s;
  const st = R.status || {};

  let hero;
  if (first >= 0) {
    // the event is the run of consecutive snow days starting at the first one
    let end = first;
    while (end + 1 < stats.length && stats[end + 1] && stats[end + 1].med >= 1) end++;
    const win = days.slice(first, end + 1);
    const run = stats.slice(first, end + 1);
    const cum = run.reduce((t, s) =>
      ({ med: t.med + s.med, lo: t.lo + s.lo, hi: t.hi + s.hi }),
      { med: 0, lo: 0, hi: 0 });
    const span = run.length > 1
      ? `${fmtDay(win[0])} – ${fmtDay(win[win.length - 1])}` : fmtDay(win[0]);
    const tot = windowTotals(win);
    const tLo = tot.length ? Math.min(...tot.map((r) => r.t)) : cum.lo;
    const tHi = tot.length ? Math.max(...tot.map((r) => r.t)) : cum.hi;
    const peakIn = peak && win.includes(peak.d)
      ? `biggest day ${fmtDay(peak.d)} (median ${peak.med.toFixed(0)}cm) · ` : "";
    hero = `<small>Next snow event · ${run.length} day${run.length > 1 ? "s" : ""}</small>
      <div class="heroline"><b>${span}</b><span class="cum">~${cum.med.toFixed(0)}cm</span></div>
      <div class="range">${peakIn}forecaster totals ${tLo.toFixed(0)}–${tHi.toFixed(0)}cm</div>
      ${stripHtml(tot, cum.med)}
      <div class="insight">${insightText(tot, cum.med)}</div>`;
  } else {
    hero = `<small>Next snow event</small>
      <div class="heroline"><b>None sighted</b></div>
      <div class="range">no ensemble median ≥ 1cm in the next ${state.horizon} days</div>
      <div class="insight">A quiet spell — the hero panel wakes up when any
        consensus day reaches 1cm.</div>`;
  }

  const tiles = [];
  if (peak && peak.med >= 1)
    tiles.push(`<div class="stat"><small>Biggest day ahead</small><b>${fmtDay(peak.d)}</b>
      <div class="range">median ${peak.med.toFixed(1)}cm · range ${peak.lo.toFixed(0)}–${peak.hi.toFixed(0)}cm</div></div>`);
  if (st.natural_depth != null)
    tiles.push(`<div class="stat"><small>Natural snow depth</small><b>${st.natural_depth.toFixed(0)}cm</b>
      <div class="range">${R.label} report · ${st.date || ""}</div></div>`);
  if (st.snow_7day != null)
    tiles.push(`<div class="stat"><small>New snow, 7 days</small><b>${st.snow_7day.toFixed(0)}cm</b>
      <div class="range">${R.label} report, 24h-to-7am</div></div>`);
  tiles.push(`<div class="stat"><small>Days scored</small><b>${R.scored}</b>
    <div class="range">night-before comparisons</div></div>`);

  const accHl = accHeadlineHtml(R.accuracy);
  $("#hero").innerHTML = `<div class="herocard">${hero}</div>
    <div><div style="margin-bottom:10px">${accHl}</div>
    <div class="statgrid">${tiles.join("")}</div></div>`;
}

function daysAgo(iso) {
  const a = new Date(iso + "T00:00"), b = new Date(DATA.generated + "T00:00");
  return Math.round((b - a) / 86400000);
}

function renderFreshness() {
  const fresh = R.freshness || {};
  const rows = providers.map((s) => {
    const last = fresh[s.id];
    const age = last == null ? null : daysAgo(last);
    const stale = age == null || age >= 2;
    const label = age == null ? "no data yet"
      : age <= 0 ? "today" : age === 1 ? "1 day ago" : `${age} days ago`;
    return `<span class="chip${stale ? " stale" : ""}" title="${s.name}: last captured ${last || "never"}">
      ${badgeMark(s)}${s.name} · ${label}</span>`;
  }).join("");
  $("#freshness").innerHTML = rows +
    `<a class="freshlink" href="${DATA.actionsUrl}" target="_blank" rel="noopener"
      title="Opens the Actions history — triggering a run needs repo write access, so this is a plain link, not a public button">
      View pipeline runs →</a>`;
}

// ---- accuracy rankings: lead selector, per-lead ranks, lead-decay curve --
// A lead is (run, lead_days): 'am' snapshots are ~7:45 captures right after
// the providers' morning issuance, 'pm' the classic ~6pm evening snapshot.
function leadLabel(e) {
  if (e.run === "am" && e.lead === 0) return "Morning of";
  if (e.run === "pm" && e.lead === 1) return "Night before";
  const d = e.run === "pm" ? e.lead - 0.5 : e.lead;
  return `${d}d out`;
}

function renderRankings() {
  const A = activeAcc();  // pooled group stats, or the resort's own
  const byLead = A.accuracyByLead || {};
  const avail = new Map();
  for (const src in byLead) for (const e of byLead[src]) {
    const k = `${e.run}:${e.lead}`;
    if (!avail.has(k)) avail.set(k, { run: e.run, lead: e.lead, h: e.h, n: 0 });
    avail.get(k).n += e.n;
  }
  const opts = [...avail.values()].sort((a, b) => a.h - b.h);
  if (!opts.length) {
    $("#leadSeg").innerHTML = "";
    $("#rankings").innerHTML = `<p class="empty">No scoreable days yet for
      ${A.label} — rankings appear once a snapshot has a next-morning report
      to be judged against.</p>`;
    $("#leadcurve").innerHTML = "";
    return;
  }
  if (!avail.has(state.leadKey))
    state.leadKey = avail.has("pm:1") ? "pm:1" : `${opts[0].run}:${opts[0].lead}`;
  $("#leadSeg").innerHTML = opts.map((o) =>
    `<button data-leadk="${o.run}:${o.lead}"
      class="${state.leadKey === `${o.run}:${o.lead}` ? "on" : ""}"
      title="${o.run === "am" ? "~7:45am" : "~6pm"} snapshot, ${o.h}h before the scored 24h window begins">
      ${leadLabel(o)}</button>`).join("");
  document.querySelectorAll("[data-leadk]").forEach((b) => b.onclick = () => {
    state.leadKey = b.dataset.leadk;
    localStorage.setItem("leadKey", state.leadKey);
    renderRankings();
  });
  const [run, leadStr] = state.leadKey.split(":");
  const rows = DATA.sources
    .map((s) => ({ s, e: (byLead[s.id] || [])
      .find((x) => x.run === run && x.lead === +leadStr) }))
    .filter((r) => r.e)
    .sort((a, b) => b.e.pct - a.e.pct);
  $("#rankings").innerHTML = rows.map(({ s, e }) =>
    `<div class="rank" ${tipRef(`<h4>${s.name} — ${leadLabel(e)}</h4>
        <div class="trow">accuracy<b>${e.pct.toFixed(0)}%</b></div>
        <div class="trow">days scored<b>${e.n}</b></div>`)}>
      <span>${badgeMark(s)} ${s.name}</span>
      <div class="track"><div class="fill" style="width:${e.pct.toFixed(0)}%;background:${s.color}"></div></div>
      <b>${e.pct.toFixed(0)}%</b></div>`).join("") +
    `<footer>accuracy = 100 × max(0, 1 − MAE / mean(max(actual, ${DATA.floor}cm))),
     ${rows.length ? rows[0].e.n : 0} ${activeGroup() ? "resort-day" : "day"}(s)
     scored at this lead${activeGroup()
       ? ` — pooled across ${activeGroup().resorts.length} resorts, each
          resort-day one sample`
       : ""}. A forecast
     for day D is judged against the 24h-to-7am report published the morning
     of D+1 — the report that actually measures day D. Rankings are noise
     until real snow falls.</footer>`;
  renderLeadCurve(byLead);
}

// accuracy vs how far ahead the snapshot was taken — who sees events early
// vs who nails the amount at short range
function renderLeadCurve(byLead) {
  const series = Object.entries(byLead)
    .map(([id, es]) => ({ s: DATA.sources.find((x) => x.id === id), es }))
    .filter((r) => r.s && r.es.length >= 2 && isOn(r.s.id));
  if (!series.length) { $("#leadcurve").innerHTML = ""; return; }
  const W = 960, H = 230, PL = 42, PR = 26, PT = 14, PB = 30;
  const maxH = Math.max(...series.map((r) => r.es[r.es.length - 1].h), 24);
  const x = (h) => PL + (W - PL - PR) * h / maxH;
  const y = (p) => H - PB - (H - PT - PB) * p / 100;
  let g = "";
  for (let t = 0; t <= 4; t++) {
    const p = 25 * t;
    g += `<line class="grid" x1="${PL}" x2="${W - PR}" y1="${y(p)}" y2="${y(p)}"/>
      <text x="${PL - 6}" y="${y(p) + 3}" text-anchor="end">${p}%</text>`;
  }
  const ticks = new Map();
  series.forEach((r) => r.es.forEach((e) => ticks.set(e.h, leadLabel(e))));
  g += [...ticks.entries()].map(([h, lb]) =>
    `<line class="vgrid" x1="${x(h)}" x2="${x(h)}" y1="${PT}" y2="${H - PB}"/>
     <text x="${x(h)}" y="${H - PB + 16}" text-anchor="middle">${lb}</text>`).join("");
  const lines = series.map((r) => {
    const pts = r.es.map((e) => `${x(e.h)},${y(e.pct)}`);
    const dots = r.es.map((e) =>
      `<circle cx="${x(e.h)}" cy="${y(e.pct)}" r="3.6" fill="${r.s.color}"
        ${tipRef(`<h4>${r.s.name} — ${leadLabel(e)}</h4>
          <div class="trow">accuracy<b>${e.pct.toFixed(0)}%</b></div>
          <div class="trow">days scored<b>${e.n}</b></div>`)}/>`).join("");
    return `<path d="M${pts.join("L")}" fill="none" stroke="${r.s.color}"
      stroke-width="2" stroke-opacity="0.85"/>${dots}`;
  }).join("");
  $("#leadcurve").innerHTML =
    `<div class="panel-tag" style="margin-top:18px">Accuracy by lead time —
      does anyone see events coming early?</div>
     <svg viewBox="0 0 ${W} ${H}" style="width:100%">${g}${lines}</svg>`;
}

// ---- group (All / NSW / VIC) aggregate views -----------------------------

// next-event read for one resort over the coming week, from its live
// client-side ensemble — the same 1cm-median threshold the hero uses
function resortEvent(rid) {
  const blob = DATA.resorts[rid];
  const ens = ensembleFor(rid);
  const days = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date((blob.snapshot || DATA.generated) + "T12:00");
    d.setDate(d.getDate() + i);
    days.push(d.toISOString().slice(0, 10));
  }
  const first = days.findIndex((d) => (ens[d] ?? 0) >= 1);
  if (first < 0) return { blob, quiet: true, total: 0 };
  let end = first;
  while (end + 1 < days.length && (ens[days[end + 1]] ?? 0) >= 1) end++;
  const win = days.slice(first, end + 1);
  const total = win.reduce((t, d) => t + ens[d], 0);
  let peak = win[0];
  for (const d of win) if (ens[d] > ens[peak]) peak = d;
  return { blob, quiet: false, win, total, peak, peakCm: ens[peak] };
}

// mini consensus strip for a single resort — provider totals on a tiny axis
function miniStripHtml(rid) {
  const blob = DATA.resorts[rid];
  const ens = ensembleFor(rid);
  const days = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date((blob.snapshot || DATA.generated) + "T12:00");
    d.setDate(d.getDate() + i);
    days.push(d.toISOString().slice(0, 10));
  }
  const fc = blob.forecasts;
  const act = DATA.sources.filter((s) =>
    s.id !== "ensemble" && s.id in fc && isOn(s.id) && weightOf(s.id) > 0);
  if (act.length < 2) return "";
  const tot = act.map((s) => {
    let t = 0, any = false;
    days.forEach((d) => { const v = fc[s.id][d]; if (v != null) { t += v; any = true; } });
    return any ? { s, t } : null;
  }).filter(Boolean);
  if (tot.length < 2) return "";
  const med = ens ? days.reduce((t, d) => t + (ens[d] || 0), 0) : 0;
  const max = Math.max(1, med, ...tot.map((r) => r.t)) * 1.08;
  const xOf = (v) => 100 * v / max;
  // stack badges onto lanes (like the hero strip) — a badge whose x is within
  // ~9% of one already placed in a lane drops to the next lane down
  const lanes = [];
  const placed = tot.slice().sort((a, b) => a.t - b.t).map((r) => {
    const x = xOf(r.t);
    let lane = 0;
    while (lane < lanes.length && x - lanes[lane] < 9) lane++;
    lanes[lane] = x;
    return { ...r, x, lane };
  });
  const nLanes = Math.max(1, lanes.length);
  const badges = placed.map((r) => {
    const inner = r.s.logo
      ? `<img src="${r.s.logo}" alt="">`
      : `<i style="background:${r.s.color}"></i>`;
    return `<span class="ministrip-badge"
      style="left:${r.x.toFixed(1)}%;top:${10 + r.lane * 19}px"
      ${tipRef(`<h4>${r.s.name}</h4>
        <div class="trow">7-day total<b>${r.t.toFixed(0)}cm</b></div>`)}>${inner}</span>`;
  }).join("");
  const mx = xOf(med).toFixed(1);
  const badgesH = 20 + (nLanes - 1) * 19;
  const h = badgesH + 13;
  return `<div class="ministrip" style="height:${h}px">
    <div class="ministrip-track">
      <span class="ministrip-med" style="left:${mx}%"
        ${tipRef(`<div class="trow">ensemble median<b>${med.toFixed(0)}cm</b></div>`)}></span>
      ${badges}
    </div>
    <div class="ministrip-scale" style="position:absolute;left:0;right:0;top:${badgesH}px">
      <span>0cm</span>
      <span style="color:var(--accent)">median ${med.toFixed(0)}cm</span>
      <span>${max.toFixed(0)}cm</span></div></div>`;
}

function renderGroupHero(g) {
  const evts = g.resorts.map((rid) => ({ rid, ...resortEvent(rid) }));
  const best = evts.filter((e) => !e.quiet).sort((a, b) => b.total - a.total)[0];
  const insight = best
    ? `Biggest week ahead: <b>${best.blob.label}</b> — ensemble median
       ~<b>${best.total.toFixed(0)}cm</b> over ${fmtDay(best.win[0])}–${fmtDay(best.win[best.win.length - 1])}.`
    : `A quiet week — no resort's ensemble median reaches 1cm on any of the
       next 7 days.`;
  const cards = evts.map((e) => {
    const st = e.blob.status || {};
    const evt = e.quiet
      ? `<span class="evt" style="color:var(--muted)">quiet week</span>`
      : `<span class="evt">${fmtDay(e.win[0])}${e.win.length > 1
           ? "–" + fmtDay(e.win[e.win.length - 1]) : ""}
         <span class="cum">~${e.total.toFixed(0)}cm</span></span>`;
    const meta = [
      st.snow_24h != null ? `24h ${st.snow_24h.toFixed(0)}cm` : null,
      st.natural_depth != null ? `depth ${st.natural_depth.toFixed(0)}cm` : null,
      !e.quiet ? `peak ${fmtDay(e.peak)} ${e.peakCm.toFixed(0)}cm` : null,
    ].filter(Boolean).join(" · ");
    return `<button class="rcard" data-view="${e.rid}"
        style="border-top-color:${e.blob.color}"
        title="Open the ${e.blob.label} view">
      <span class="rname">${e.blob.label}<small>${e.blob.state}</small></span>
      ${evt}<span class="meta">${meta || "—"}</span>
      ${miniStripHtml(e.rid)}</button>`;
  }).join("");
  const accHl = accHeadlineHtml(g.accuracy);
  $("#hero").innerHTML =
    `<div><div class="ginsight">${insight}</div>
     ${accHl}
     <div class="rcards">${cards}</div></div>`;
  $("#hero").classList.add("group");
  document.querySelectorAll("#hero [data-view]").forEach((b) =>
    b.onclick = () => setView(b.dataset.view));
}

function renderGroupCompare(g) {
  const snapshot = g.snapshot || DATA.generated;
  const days = [];
  for (let i = 0; i <= 7; i++) {
    const d = new Date(snapshot + "T12:00");
    d.setDate(d.getDate() + i);
    days.push(d.toISOString().slice(0, 10));
  }
  const rows = g.resorts.map((rid) =>
    ({ rid, blob: DATA.resorts[rid], ens: ensembleFor(rid) }));
  const vmax = Math.max(1,
    ...rows.map((r) => days.map((d) => r.ens[d] || 0)).flat());
  const cols = days.map((d) => {
    const tipHtml = `<h4>${fmtDay(d)}</h4>` + rows
      .map((r) => ({ r, v: r.ens[d] }))
      .filter((x) => x.v != null)
      .sort((a, b) => b.v - a.v)
      .map((x) => `<div class="trow"><i class="dot"
        style="background:${x.r.blob.color}"></i>${x.r.blob.label}
        <b>${x.v.toFixed(1)}cm</b></div>`).join("");
    const bars = rows.map((r) => {
      const v = r.ens[d] ?? 0;
      const h = Math.max(0.5, 100 * v / vmax);
      const em = v >= 0.5 ? `<em>${v.toFixed(0)}</em>` : "";
      return `<div class="bar" style="height:${h}%;background:${r.blob.color}">${em}</div>`;
    }).join("");
    return `<div class="day" ${tipRef(tipHtml)}><div class="bars">${bars}</div></div>`;
  }).join("");
  const labels = days.map((d) => `<div class="daylabel">${fmtDay(d)}</div>`).join("");
  const trows = rows.map((r) => {
    const tint = `color-mix(in srgb, ${r.blob.color} 68%, var(--ink))`;
    const cells = days.map((d) =>
      `<td>${r.ens[d] == null ? "—" : r.ens[d].toFixed(1)}</td>`).join("");
    const tot = days.reduce((t, d) => t + (r.ens[d] || 0), 0);
    return `<tr><td class="tsrc" style="color:${tint}"><i class="swatchdot"
      style="background:${r.blob.color}"></i>${r.blob.label}</td>${cells}
      <td style="font-weight:650">${tot.toFixed(0)}</td></tr>`;
  }).join("");
  $("#gcompare").innerHTML =
    `<div class="days">${cols}</div><div class="daylabels">${labels}</div>
     <div class="scroll" style="margin-top:16px"><table>
       <tr><th>Resort</th>${days.map((d) => `<th>${fmtDay(d)}</th>`).join("")}<th>Σ</th></tr>
       ${trows}</table></div>`;
}

function setView(v) {
  state.view = v;
  localStorage.setItem("view", v);
  bindResort();
  const g = activeGroup();
  document.body.dataset.mode = g ? "group" : "resort";
  $("#hero").classList.toggle("group", !!g);
  $("#resortName").textContent = g ? g.label : R.label;
  $("#stamp").textContent =
    `snapshot ${(g || R).snapshot || "—"} · generated ${DATA.generated}`;
  document.querySelectorAll(".resortbar [data-view]").forEach((b) =>
    b.classList.toggle("on", b.dataset.view === v));
  if (g) {
    computeEnsemble();  // keeps hidden resort panels consistent
    renderGroupHero(g); renderGroupCompare(g);
  } else {
    const fr = $("#fResort"), mr = $("#mResort");
    if (fr) fr.value = v;
    if (mr) mr.value = v;
    refresh(); renderActuals(); renderForecasts(); renderFreshness(); renderFva();
  }
  renderRankings();
}

function chartBars(days) {
  const vis = visible();
  const marks = state.barMarks;
  const vmax = Math.max(1,
    ...vis.map((s) => days.map((d) => F(s.id, d) || 0)).flat());
  const cols = days.map((d) => {
    const bars = vis.map((s) => {
      const v = F(s.id, d) ?? 0;
      const h = Math.max(0.5, 100 * v / vmax);
      const em = v >= 0.5 ? `<em>${v.toFixed(0)}</em>` : "";
      // opt-in brand cap sitting above each bar (its numeric value stays below)
      const cap = (marks && v >= 0.5)
        ? `<span class="barmark">${s.logo
            ? `<img src="${s.logo}" alt="">`
            : `<i style="background:${s.color}"></i>`}</span>`
        : "";
      return `<div class="bar"
        style="height:${h}%;background:${s.color}">${cap}${em}</div>`;
    }).join("");
    return `<div class="day" ${tipRef(dayTipHtml(d))}><div class="bars">${bars}</div></div>`;
  }).join("");
  const labels = days.map((d) => `<div class="daylabel">${fmtDay(d)}</div>`).join("");
  return `<div class="days${marks ? " withmarks" : ""}">${cols}</div>
    <div class="daylabels">${labels}</div>`;
}

function chartSvg(days, cumulative) {
  const W = 960, H = 240, PL = 34, PT = 12, PB = 26;
  const PR = cumulative ? 132 : 34;  // room for end-labels on cumulative
  const series = visible().map((s) => {
    let run = 0;
    return { ...s, pts: days.map((d) => {
      const v = F(s.id, d) ?? 0;
      return cumulative ? (run += v) : v;
    }) };
  });
  const vmax = Math.max(1, ...series.map((s) => s.pts).flat());
  const x = (i) => PL + i * (W - PL - PR) / Math.max(1, days.length - 1);
  const y = (v) => H - PB - (H - PT - PB) * v / vmax;
  let g = "";
  for (let t = 0; t <= 4; t++) {
    const v = vmax * t / 4;
    g += `<line class="grid" x1="${PL}" x2="${W - PR}" y1="${y(v)}" y2="${y(v)}"/>
      <text x="${PL - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`;
  }
  // dashed verticals divide the days
  for (let i = 0; i < days.length; i++)
    g += `<line class="vgrid" x1="${x(i)}" x2="${x(i)}" y1="${PT}" y2="${H - PB}"/>`;
  const lastX = x(days.length - 1);
  const lines = series.map((s) => {
    const path = s.pts.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
    const end = s.pts[s.pts.length - 1];
    return `<path d="${path}" fill="none" stroke="${s.color}" stroke-width="2.2"/>
      <circle cx="${lastX}" cy="${y(end)}" r="3.4" fill="${s.color}"/>`;
  }).join("");
  // direct end-labels for cumulative: place at each line's endpoint, then
  // nudge apart vertically so they don't collide.
  let endLabels = "";
  if (cumulative) {
    const lab = series.map((s) => ({
      color: s.color, name: s.name,
      total: s.pts[s.pts.length - 1],
      yEnd: y(s.pts[s.pts.length - 1]),
    })).sort((a, b) => a.yEnd - b.yEnd);
    const GAP = 14;
    for (let i = 1; i < lab.length; i++)
      if (lab[i].yEnd - lab[i - 1].yEnd < GAP)
        lab[i].yEnd = lab[i - 1].yEnd + GAP;
    endLabels = lab.map((l) =>
      `<line class="grid" x1="${lastX}" y1="${l.yEnd}" x2="${lastX + 8}" y2="${l.yEnd}"
        stroke="${l.color}"/>
       <text x="${lastX + 12}" y="${l.yEnd + 3.5}" text-anchor="start"
        style="fill:${l.color};font-weight:700">${l.name} ${l.total.toFixed(0)}</text>`
    ).join("");
  }
  const labels = days.map((d, i) =>
    `<text x="${x(i)}" y="${H - PB + 16}" text-anchor="middle">${fmtDay(d)}</text>`).join("");
  const cw = (W - PL - PR) / Math.max(1, days.length - 1);
  const hov = days.map((d, i) =>
    `<rect x="${x(i) - cw / 2}" y="0" width="${cw}" height="${H}"
      fill="transparent" ${tipRef(dayTipHtml(d))}/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%">${g}${lines}${endLabels}${labels}${hov}</svg>`;
}

function chartRange(days) {
  const W = 960, H = 240, PL = 34, PR = 34, PT = 12, PB = 26;
  const stats = days.map((d) => dayStats(d) || { d, med: 0, lo: 0, hi: 0 });
  const vmax = Math.max(1, ...stats.map((s) => s.hi));
  const x = (i) => PL + i * (W - PL - PR) / Math.max(1, days.length - 1);
  const y = (v) => H - PB - (H - PT - PB) * v / vmax;
  let g = "";
  for (let t = 0; t <= 4; t++) {
    const v = vmax * t / 4;
    g += `<line class="grid" x1="${PL}" x2="${W - PR}" y1="${y(v)}" y2="${y(v)}"/>
      <text x="${PL - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}</text>`;
  }
  for (let i = 0; i < days.length; i++)
    g += `<line class="vgrid" x1="${x(i)}" x2="${x(i)}" y1="${PT}" y2="${H - PB}"/>`;
  const col = (sources.find((s) => s.id === "ensemble") || {}).color || "var(--accent)";
  const top = stats.map((s, i) => `${i ? "L" : "M"}${x(i)},${y(s.hi)}`).join("");
  const bot = stats.map((_, i) => {
    const s = stats[stats.length - 1 - i];
    return `L${x(stats.length - 1 - i)},${y(s.lo)}`;
  }).join("");
  const band = `<path d="${top}${bot}Z" fill="${col}" fill-opacity="0.14"
    stroke="${col}" stroke-opacity="0.35" stroke-width="1"/>`;
  const medPath = stats.map((s, i) => `${i ? "L" : "M"}${x(i)},${y(s.med)}`).join("");
  const medLine = `<path d="${medPath}" fill="none" stroke="${col}" stroke-width="2.4"/>`
    + stats.map((s, i) =>
      `<circle cx="${x(i)}" cy="${y(s.med)}" r="3.2" fill="${col}"/>`).join("");
  const labels = days.map((d, i) =>
    `<text x="${x(i)}" y="${H - PB + 16}" text-anchor="middle">${fmtDay(d)}</text>`).join("");
  const cw = (W - PL - PR) / Math.max(1, days.length - 1);
  const hov = stats.map((s, i) =>
    `<rect x="${x(i) - cw / 2}" y="0" width="${cw}" height="${H}" fill="transparent"
      ${tipRef(`<h4>${fmtDay(s.d)}</h4>
        <div class="trow">ensemble median<b>${s.med.toFixed(1)}cm</b></div>
        <div class="trow">provider range<b>${s.lo.toFixed(0)}–${s.hi.toFixed(0)}cm</b></div>`)}/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%">${g}${band}${medLine}${labels}${hov}</svg>`;
}

function chartTable(days) {
  const vis = visible();
  const head = days.map((d) => `<th>${fmtDay(d)}</th>`).join("");
  const maxByDay = days.map((d) => Math.max(
    ...vis.filter((s) => s.id !== "ensemble")
      .map((s) => F(s.id, d) ?? -1)));
  const rows = vis.map((s) => {
    // source name tinted with its chart colour, mixed toward the ink so it
    // stays legible on both light and dark grounds whatever the palette
    const tint = `color-mix(in srgb, ${s.color} 68%, var(--ink))`;
    const src = `<td class="tsrc" style="color:${tint}">${badgeMark(s)}${s.name}</td>`;
    return "<tr>" + src + days.map((d, i) => {
      const v = F(s.id, d);
      // the day's leading forecaster is emphasised in its own colour
      const isMax = v != null && s.id !== "ensemble" && v === maxByDay[i] && v > 0;
      const style = isMax ? ` style="color:${tint};font-weight:650"` : "";
      return `<td${style}>${v == null ? "—" : v.toFixed(1)}</td>`;
    }).join("") + "</tr>";
  }).join("");
  return `<div class="scroll"><table><tr><th>Source</th>${head}</tr>${rows}</table></div>`;
}

function panelBody(kind, days) {
  if (kind === "bars") return chartBars(days);
  if (kind === "range") return chartRange(days);
  if (kind === "table") return chartTable(days);
  return chartSvg(days, kind === "cumulative");
}

function toggleSource(id) {
  const i = state.disabled.indexOf(id);
  if (i < 0) {
    // never let the last provider be switched off
    if (id !== "ensemble" && providers.filter((s) => isOn(s.id)).length <= 1) return;
    state.disabled.push(id);
  } else state.disabled.splice(i, 1);
  localStorage.setItem("disabledSources", JSON.stringify(state.disabled));
  refresh();
}

function refresh() {
  computeEnsemble(); renderBadges(); renderMain(); renderWeights();
}

function renderMain() {
  const days = horizonDates();
  const nOn = providers.filter((s) => isOn(s.id)).length;
  $("#ensNote").innerHTML =
    `<b>${nOn}${nOn < providers.length ? " of " + providers.length : ""}</b> ` +
    `forecasters feeding the ensemble — click a name to toggle it in or out.`;
  $("#legend").innerHTML = sources.map((s) =>
    `<button class="lgd${isOn(s.id) ? "" : " off"}" data-src="${s.id}"
      title="Click to toggle ${s.name} ${s.id === "ensemble" ? "off the charts" : "in/out of charts and ensemble"}">
     ${badgeMark(s)}<span style="color:color-mix(in srgb, ${s.color} 68%, var(--ink))">${s.name}</span></button>`).join("");
  document.querySelectorAll("#legend .lgd").forEach((b) =>
    b.onclick = () => toggleSource(b.dataset.src));
  const active = PANEL_ORDER.filter((k) => state.panels.includes(k));
  $("#main").innerHTML = active.length
    ? active.map((k) =>
        `<div class="panel"><div class="panel-tag">${PANEL_LABEL[k]}</div>
         ${panelBody(k, days)}</div>`).join("")
    : `<p class="empty">Pick at least one view above.</p>`;
  document.querySelectorAll("[data-chart]").forEach((b) =>
    b.classList.toggle("on", state.panels.includes(b.dataset.chart)));
  document.querySelectorAll("[data-h]").forEach((b) =>
    b.classList.toggle("on", +b.dataset.h === state.horizon));
}

function saveWeights() {
  localStorage.setItem("sourceWeights", JSON.stringify(state.weights));
}

function renderWeights() {
  $("#advBtn").classList.toggle("on", state.showAdv);
  $("#advanced").style.display = state.showAdv ? "block" : "none";
  if (!state.showAdv) return;
  $("#wrows").innerHTML = providers.map((s) => `
    <div class="wrow${isOn(s.id) ? "" : " off"}">
      <span><i style="background:${s.color}"></i>${s.name}</span>
      <input type="range" min="0" max="100" step="5" value="${weightOf(s.id)}"
        data-w="${s.id}" aria-label="${s.name} ensemble weight"
        ${isOn(s.id) ? "" : "disabled"}>
      <b>${weightOf(s.id)}</b>
    </div>`).join("");
  document.querySelectorAll("[data-w]").forEach((r) => r.oninput = () => {
    state.weights[r.dataset.w] = +r.value;
    saveWeights();
    r.nextElementSibling.textContent = r.value;
    computeEnsemble(); renderBadges(); renderMain();
  });
}

const manualActuals = () => state.manual[Rid] || {};

function renderActuals() {
  const mine = manualActuals();
  const merged = { ...mine, ...R.actuals };
  const dates = Object.keys(merged).sort();
  $("#chips").innerHTML = Object.keys(mine).sort().map((d) =>
    `<span class="chip">${d} · ${mine[d]}cm
     <button aria-label="remove" onclick="removeManual('${d}')">×</button></span>`).join("");
  if (!dates.length) { $("#spark").innerHTML = ""; $("#actualRows").innerHTML = ""; return; }
  const vmax = Math.max(1, ...Object.values(merged));
  $("#spark").innerHTML = dates.map((d) =>
    `<i class="${d in R.actuals ? "" : "manual"}" title="${d}: ${merged[d]}cm"
     style="height:${Math.max(4, 100 * merged[d] / vmax)}%"></i>`).join("");
  // show forecaster calls alongside actuals for days that have scored pairs
  const sp = R.scoredPairs || {};
  const vis = providers.filter((s) => isOn(s.id));
  // the scored pair key is the target_date (day D); the actual report is D+1
  // so map report dates back to target dates
  const hasFc = (reportDate) => {
    const td = new Date(reportDate + "T12:00");
    td.setDate(td.getDate() - 1);
    return td.toISOString().slice(0, 10);
  };
  $("#actualRows").innerHTML = dates.slice().reverse().map((d) => {
    const targetD = hasFc(d);
    const row = sp[targetD];
    let fcCells = "";
    if (row) {
      fcCells = vis.slice(0, 4).map((s) => {
        const v = row[s.id];
        if (v == null) return "";
        const err = Math.abs(v - merged[d]);
        const col = err <= 2 ? "var(--accent)" : "var(--muted)";
        return `<span style="color:${col};margin-left:6px;font-size:12px"
          title="${s.name}: ${v.toFixed(1)}cm">${badgeMark(s)}${v.toFixed(0)}</span>`;
      }).join("");
    }
    return `<tr><td>${d}</td><td>${merged[d].toFixed(0)}</td>
     <td>${d in R.actuals ? "resort report" : "manual ✎"}${fcCells}</td></tr>`;
  }).join("");
}

function removeManual(d) {
  delete (state.manual[Rid] || {})[d];
  localStorage.setItem("manualActuals", JSON.stringify(state.manual));
  renderActuals(); renderBadges();
}

function saveForecasts() {
  localStorage.setItem("manualForecasts", JSON.stringify(state.mforecasts));
}
function removeForecast(i) {
  state.mforecasts.splice(i, 1); saveForecasts(); renderForecasts();
}

// The reported actual that scores a forecast for day D: the 24h-to-7am
// report published the morning of D+1 (see the colophon's methodology
// note), from the feed or a manual entry — for any resort.
function actualScoring(resortId, targetDate) {
  const rep = plus1(targetDate);
  const blob = DATA.resorts[resortId];
  if (blob && rep in blob.actuals) return blob.actuals[rep];
  const manual = state.manual[resortId] || {};
  if (rep in manual) return manual[rep];
  return null;
}

function renderForecasts() {
  const named = (id) => (DATA.sources.find((s) => s.id === id) || {}).name || id;
  const color = (id) => (DATA.sources.find((s) => s.id === id) || {}).color || "var(--accent)";
  const resortName = (rid) => (DATA.resorts[rid] || {}).label || rid;
  $("#fchips").innerHTML = state.mforecasts.map((f, i) =>
    `<span class="chip"><i style="width:8px;height:8px;border-radius:2px;
      display:inline-block;background:${color(f.source)}"></i>
     ${resortName(f.resort || "perisher")} · ${named(f.source)} · ${f.target_date} · ${f.cm}cm
     <button aria-label="remove" onclick="removeForecast(${i})">×</button></span>`).join("");
  // alignment preview: manual forecasts whose scoring report already exists
  const scored = state.mforecasts
    .map((f) => ({ ...f, actual: actualScoring(f.resort || "perisher", f.target_date) }))
    .filter((f) => f.actual != null)
    .sort((a, b) => a.target_date.localeCompare(b.target_date));
  if (!scored.length) {
    $("#alignBox").innerHTML =
      `<p class="empty">Add a forecast for a day whose next-morning report is
       already known to see the error here.</p>`;
    return;
  }
  const rows = scored.map((f) => {
    const err = f.cm - f.actual;
    return `<tr><td>${resortName(f.resort || "perisher")}</td><td>${named(f.source)}</td><td>${f.target_date}</td>
      <td>${f.cm.toFixed(1)}</td><td>${f.actual.toFixed(1)}</td>
      <td style="color:${Math.abs(err) < 2 ? "var(--accent)" : "var(--ink)"}">
        ${err >= 0 ? "+" : ""}${err.toFixed(1)}</td></tr>`;
  }).join("");
  const mae = (scored.reduce((t, f) => t + Math.abs(f.cm - f.actual), 0)
    / scored.length).toFixed(1);
  $("#alignBox").innerHTML =
    `<div class="scroll"><table><tr><th>Resort</th><th>Source</th><th>Target</th>
     <th>Forecast</th><th>Actual</th><th>Error</th></tr>${rows}</table></div>
     <footer>Mean absolute error across ${scored.length} manual call(s): ${mae}cm.
     Export and merge to fold these into the season rankings.</footer>`;
}

// forecast vs actuals review — scored days with per-forecaster predictions
function renderFva() {
  const sp = R.scoredPairs || {};
  const dates = Object.keys(sp).sort().reverse();
  if (!dates.length) {
    $("#fvaBody").innerHTML = `<p class="empty">No scored days yet for
      ${R.label} — this table fills in as actuals arrive.</p>`;
    return;
  }
  const vis = providers.filter((s) => isOn(s.id));
  const srcHead = vis.map((s) =>
    `<th title="${s.name}" style="color:color-mix(in srgb, ${s.color} 68%, var(--ink))">${badgeMark(s)}</th>`).join("");
  const rows = dates.map((d) => {
    const row = sp[d];
    const actual = row._actual;
    const cells = vis.map((s) => {
      const fc = row[s.id];
      if (fc == null) return `<td>—</td>`;
      const err = Math.abs(fc - actual);
      const cls = err <= Math.max(2, actual * 0.3) ? "hit" : "miss";
      return `<td class="${cls}" ${tipRef(
        `<h4>${s.name} — ${d}</h4>
        <div class="trow">forecast<b>${fc.toFixed(1)}cm</b></div>
        <div class="trow">actual<b>${actual.toFixed(1)}cm</b></div>
        <div class="trow">error<b>${(fc - actual) >= 0 ? "+" : ""}${(fc - actual).toFixed(1)}cm</b></div>`
      )}>${fc.toFixed(1)}</td>`;
    }).join("");
    return `<tr><td>${d}</td><td class="actual-col">${actual.toFixed(1)}</td>${cells}</tr>`;
  }).join("");
  // per-source MAE summary
  const maes = vis.map((s) => {
    const errs = dates.map((d) => sp[d][s.id] != null
      ? Math.abs(sp[d][s.id] - sp[d]._actual) : null).filter((e) => e != null);
    const mae = errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : null;
    return { s, mae, n: errs.length };
  }).filter((r) => r.mae != null);
  const maeRow = `<tr style="border-top:2px solid var(--line)"><td style="font-weight:700">MAE</td><td></td>${
    vis.map((s) => {
      const m = maes.find((r) => r.s.id === s.id);
      return m ? `<td style="font-weight:700">${m.mae.toFixed(1)}</td>` : `<td>—</td>`;
    }).join("")}</tr>`;
  $("#fvaBody").innerHTML =
    `<div class="scroll"><table class="fva-table">
      <tr><th>Date</th><th>Actual</th>${srcHead}</tr>
      ${rows}${maeRow}</table></div>
     <footer>${dates.length} day(s) scored at the night-before lead. Cells
     highlighted where forecast is within 2cm or 30% of actual.</footer>`;
}

function init() {
  document.documentElement.dataset.palette = state.palette;
  setTheme(state.theme);
  $("#themeBtn").onclick = () => setTheme(state.theme === "dark" ? "light" : "dark");
  const psel = $("#paletteSelect");
  psel.innerHTML = PALETTES.map((p) =>
    `<option value="${p.id}"${p.id === state.palette ? " selected" : ""}>${p.label}</option>`).join("");
  psel.onchange = () => setPalette(psel.value);
  // chart panels are toggles: click to add/remove a stacked view
  document.querySelectorAll("[data-chart]").forEach((b) => b.onclick = () => {
    const k = b.dataset.chart, i = state.panels.indexOf(k);
    if (i < 0) state.panels.push(k); else state.panels.splice(i, 1);
    localStorage.setItem("panels", JSON.stringify(state.panels));
    renderMain();
  });
  document.querySelectorAll("[data-h]").forEach((b) => b.onclick = () => {
    state.horizon = +b.dataset.h;
    localStorage.setItem("horizon", state.horizon);
    renderMain(); renderBadges();
  });
  $("#advBtn").onclick = () => {
    state.showAdv = !state.showAdv;
    localStorage.setItem("showAdv", state.showAdv ? "1" : "0");
    renderWeights();
  };
  $("#marksBtn").classList.toggle("on", state.barMarks);
  $("#marksBtn").onclick = () => {
    state.barMarks = !state.barMarks;
    localStorage.setItem("barMarks", state.barMarks ? "1" : "0");
    $("#marksBtn").classList.toggle("on", state.barMarks);
    renderMain();
  };
  $("#wEqual").onclick = () => { state.weights = {}; saveWeights(); refresh(); };
  $("#wAcc").onclick = () => {
    providers.forEach((s) =>
      state.weights[s.id] = Math.round(R.accuracy[s.id] ?? 50));
    saveWeights(); refresh();
  };
  // resort switcher
  document.querySelectorAll(".resortbar [data-view]").forEach((b) =>
    b.onclick = () => setView(b.dataset.view));
  // source + resort dropdowns for manual entry
  $("#fSource").innerHTML = DATA.sources.filter((s) => s.id !== "ensemble")
    .map((s) => `<option value="${s.id}">${s.name}</option>`).join("");
  const resortOpts = DATA.resortOrder.map((rid) =>
    `<option value="${rid}">${DATA.resorts[rid].label}</option>`).join("");
  $("#fResort").innerHTML = resortOpts;
  $("#mResort").innerHTML = resortOpts;
  // entry-type switch
  document.querySelectorAll("[data-entry]").forEach((b) => b.onclick = () => {
    const t = b.dataset.entry;
    document.querySelectorAll("[data-entry]").forEach((x) =>
      x.classList.toggle("on", x === b));
    $("#actualForm").style.display = t === "actual" ? "flex" : "none";
    $("#forecastForm").style.display = t === "forecast" ? "flex" : "none";
  });
  $("#actualForm").onsubmit = (e) => {
    e.preventDefault();
    const rid = $("#mResort").value, d = $("#mDate").value,
      v = parseFloat($("#mCm").value);
    if (!rid || !d || isNaN(v) || v < 0) return;
    if (d in (DATA.resorts[rid] || { actuals: {} }).actuals) {
      alert("That date already has a resort-reported value."); return;
    }
    (state.manual[rid] = state.manual[rid] || {})[d] = v;
    localStorage.setItem("manualActuals", JSON.stringify(state.manual));
    $("#mDate").value = ""; $("#mCm").value = "";
    renderActuals(); renderBadges(); renderForecasts();
  };
  $("#forecastForm").onsubmit = (e) => {
    e.preventDefault();
    const resort = $("#fResort").value, source = $("#fSource").value,
      d = $("#fDate").value, v = parseFloat($("#fCm").value);
    if (!resort || !source || !d || isNaN(v) || v < 0) return;
    state.mforecasts.push({ resort, source, target_date: d, cm: v });
    saveForecasts();
    $("#fDate").value = ""; $("#fCm").value = "";
    renderForecasts();
  };
  $("#exportBtn").onclick = () => {
    const forecasts = state.mforecasts.map((f) =>
      ({ resort: "perisher", ...f }));
    const bundle = { actuals: state.manual, forecasts };
    const blob = new Blob([JSON.stringify(bundle, null, 1)],
      { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "manual.json";
    a.click();
  };
  setView(state.view);
}
init();
"""


def _resort_blob(con, rid: str, label: str, status: dict) -> dict:
    """Everything the page needs for one resort."""
    snapshot = con.execute(
        "SELECT max(issued_date) FROM forecasts WHERE resort=?", (rid,)
    ).fetchone()[0]

    # per source, the newest snapshot ('pm' outranks a same-day 'am').
    # Only dashboard-known sources: extra series like Snow-Forecast's
    # bot/top elevation bands stay DB-only (docs/reference-points.md).
    forecasts: dict[str, dict[str, float]] = {}
    for (source,) in con.execute(
        "SELECT DISTINCT source FROM forecasts WHERE resort=?", (rid,)
    ):
        if source not in PROVIDER_COLORS:
            continue
        issued, run = con.execute(
            "SELECT issued_date, run FROM forecasts WHERE resort=? AND source=? "
            "ORDER BY issued_date DESC, run DESC LIMIT 1", (rid, source),
        ).fetchone()
        forecasts[source] = {
            d: round(cm, 2) for d, cm in con.execute(
                "SELECT target_date, snow_cm FROM forecasts "
                "WHERE resort=? AND source=? AND issued_date=? AND run=?",
                (rid, source, issued, run),
            )
        }

    actuals = dict(con.execute(
        "SELECT date, snow_cm FROM actuals WHERE resort=? ORDER BY date", (rid,)
    ).fetchall())
    acc = accuracy(con, rid)
    run, lead = HEADLINE
    scored = len({d for s, r, l, d, _f, _a in pairs(con, rid)
                  if (r, l) == (run, lead)})
    freshness = {s: d for s, d in con.execute(
        "SELECT source, max(issued_date) FROM forecasts "
        "WHERE resort=? AND source != 'ensemble' GROUP BY source", (rid,)
    ) if s in PROVIDER_COLORS}

    # scored pairs at the headline lead for the forecast-vs-actuals view
    scored_pairs_raw = [
        (s, r, l, d, round(fc, 2), round(a, 2))
        for s, r, l, d, fc, a in pairs(con, rid)
        if (r, l) == (run, lead)
    ]
    scored_pairs = {}
    for s, _r, _l, d, fc, a in scored_pairs_raw:
        scored_pairs.setdefault(d, {})[s] = fc
        scored_pairs.setdefault(d, {})["_actual"] = a

    return {
        "label": label,
        "state": RESORTS[rid].state,
        "color": RESORT_COLORS.get(rid, "var(--accent)"),
        "snapshot": snapshot,
        "forecasts": forecasts,
        "actuals": actuals,
        "accuracy": acc,
        "accuracyByLead": accuracy_by_lead(con, rid),
        "scored": scored,
        "status": status.get(rid) or {},
        "freshness": freshness,
        "scoredPairs": scored_pairs,
    }


def _group_blob(con, label: str, rids: list[str], resorts: dict) -> dict:
    """Aggregate view: the same skill formula over the pooled resort-day
    samples of several resorts (state level, or everything)."""
    run, lead = HEADLINE
    by_lead = accuracy_by_lead(con, rids)
    return {
        "label": label,
        "resorts": rids,
        "snapshot": max((resorts[r]["snapshot"] or "" for r in rids),
                        default="") or None,
        "accuracy": accuracy(con, rids),
        "accuracyByLead": by_lead,
        # pooled samples at the headline lead (resort-days, not days)
        "scored": max((e["n"] for es in by_lead.values() for e in es
                       if (e["run"], e["lead"]) == (run, lead)), default=0),
    }


def render(out: Path | None = None) -> Path:
    con = store.connect()
    today = dt.datetime.now(TZ).date().isoformat()

    status_path = store.DB_PATH.parent / "resort_status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    if "snow_24h" in status:  # pre-multi-resort flat file
        status = {"perisher": status}

    resorts = {rid: _resort_blob(con, rid, r.name, status)
               for rid, r in RESORTS.items()}
    group_defs = [
        ("all", "All resorts", list(RESORTS)),
        ("nsw", "New South Wales",
         [rid for rid, r in RESORTS.items() if r.state == "NSW"]),
        ("vic", "Victoria",
         [rid for rid, r in RESORTS.items() if r.state == "VIC"]),
    ]
    data = {
        "generated": today,
        "sources": [
            {"id": k, "name": PROVIDER_NAMES[k], "color": PROVIDER_COLORS[k],
             "logo": _logo_uri(k)}
            for k in PROVIDER_COLORS
        ],
        "resortOrder": list(RESORTS),
        "resorts": resorts,
        "groups": {gid: _group_blob(con, label, rids, resorts)
                   for gid, label, rids in group_defs},
        "floor": FLOOR_CM,
        "actionsUrl": ACTIONS_URL,
    }
    palettes_js = [
        {"id": pid, "label": p["label"], "bg": p["light"]["bg"],
         "accent": p["light"]["accent"]}
        for pid, p in PALETTES.items()
    ]

    group_seg = "".join(
        f'<button data-view="{gid}">{label}</button>'
        for gid, label, _rids in group_defs
    )
    resort_seg = "".join(
        f'<button data-view="{rid}">{r.name}</button>'
        for rid, r in RESORTS.items()
    )

    html = f"""<meta charset="utf-8">
<title>Snow forecast accuracy — Australian resorts</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400..700&family=Fraunces:ital,opsz,wght@0,9..144,400..700;1,9..144,400..700&display=swap">
<style>{_palette_css()}{CSS}</style>
<script>
/* set theme + palette before first paint so there's no flash */
(function () {{ var d = document.documentElement;
  d.dataset.palette = localStorage.getItem("palette") || "glacier";
  d.dataset.theme = localStorage.getItem("theme") || "dark"; }})();
</script>
<main>
<header>
  <div class="masthead">
    <h1><b id="resortName">Perisher</b> <span>forecast accuracy</span></h1>
    <p class="sub">Forecast snapshots from eight sources, morning and evening,
    scored against each resort's reported snowfall.</p>
  </div>
  <div class="headtools">
    <p class="stamp" id="stamp">generated {today}</p>
    <div class="headctl">
      <button id="themeBtn" class="iconbtn" type="button"
        aria-label="Toggle light or dark theme"></button>
      <div class="select-wrap">
        <select id="paletteSelect" aria-label="Colour palette"></select>
      </div>
    </div>
  </div>
</header>
<div class="resortbar">
  <span class="seg">{group_seg}</span>
  <span class="seg">{resort_seg}</span>
</div>
<hr class="rule">
<div class="hero" id="hero"></div>
<div class="card group-only">
  <div class="cardhead">
    <h2>Resort comparison — ensemble median (cm)</h2>
    <span class="spacer"></span>
    <span class="hint">weighted median of the providers, per resort · today + 7 days</span>
  </div>
  <div id="gcompare"></div>
</div>
<div class="card resort-only">
  <div class="cardhead">
    <h2>Forecast snowfall (cm)</h2>
    <span class="spacer"></span>
    <span class="seg" title="Toggle views — stack as many as you like">
      <button data-chart="bars">Bars</button>
      <button data-chart="range">Range</button>
      <button data-chart="cumulative">Cumulative</button>
      <button data-chart="lines">Lines</button>
      <button data-chart="table">Table</button>
    </span>
    <span class="seg">
      <button data-h="5">5d</button><button data-h="7">7d</button><button data-h="10">10d</button>
    </span>
    <button type="button" class="ghost" id="marksBtn"
      title="Cap each daily bar with the forecaster's logo">Logos</button>
    <button type="button" class="ghost" id="advBtn"
      title="Custom per-forecaster weights for the ensemble median">Weights</button>
  </div>
  <div class="ens-note" id="ensNote"></div>
  <div class="legend" id="legend"></div>
  <div id="advanced" style="display:none">
    <div class="advhead">
      <h2>Ensemble weights</h2>
      <span class="spacer"></span>
      <button type="button" class="ghost" id="wEqual">Equal</button>
      <button type="button" class="ghost" id="wAcc">By accuracy</button>
    </div>
    <div id="wrows"></div>
    <footer>The ensemble is the weighted median of the forecasters toggled on
    above — a weight of 0 (or clicking a name in the legend) drops that
    forecaster; "By accuracy" seeds weights from the season rankings.
    Settings live in this browser only.</footer>
  </div>
  <div id="main"></div>
</div>
<div class="card resort-only">
  <div class="cardhead"><h2>Data freshness</h2></div>
  <div id="freshness" style="line-height:2.4"></div>
  <details class="fine"><summary>Why might a source lag?</summary>
  <p>Snowatch runs on a self-hosted runner — Cloudflare blocks GitHub's
  cloud runner IPs. If it shows 2+ days old, the runner has likely been
  offline; it'll catch up automatically next time it's online. (The Actions
  link only lets the repo owner trigger a run early — GitHub requires write
  access for that, so it's not a public control.)</p></details>
</div>
<div class="card">
  <div class="cardhead">
    <h2>Accuracy rankings — season to date</h2>
    <span class="spacer"></span>
    <span class="seg" id="leadSeg"
      title="How far ahead of the scored 24h window the snapshot was taken"></span>
  </div>
  <div id="rankings"></div>
  <div id="leadcurve"></div>
</div>
<div class="card manual resort-only">
  <h2>Reported daily snowfall</h2>
  <div class="spark" id="spark" style="margin:12px 0"></div>
  <div class="scroll" style="max-height:220px;overflow-y:auto">
    <table><tr><th>Date</th><th>Snow (cm)</th><th>Source</th></tr>
    <tbody id="actualRows"></tbody></table>
  </div>
  <div id="chips" style="margin-top:8px"></div>
</div>
<div class="card resort-only">
  <div class="cardhead">
    <h2>Forecast vs actual — scored days</h2>
    <span class="spacer"></span>
    <span class="hint">night-before predictions vs next-morning reports</span>
  </div>
  <div id="fvaBody"></div>
</div>
<details class="fold manual resort-only">
  <summary><h2>Manual backfill</h2>
    <span class="hint">repo owner only — transcribe forecasts &amp; actuals from forum threads</span></summary>
  <div class="foldbody">
  <div class="cardhead">
    <span class="seg">
      <button data-entry="forecast" class="on">Forecast</button>
      <button data-entry="actual">Actual</button>
    </span>
    <span class="spacer"></span>
    <button type="button" class="ghost" id="exportBtn">Export manual.json</button>
  </div>
  <form id="forecastForm">
    <select id="fResort" required aria-label="resort"></select>
    <select id="fSource" required></select>
    <input type="date" id="fDate" required aria-label="target date">
    <input type="number" id="fCm" min="0" max="200" step="0.1" placeholder="cm"
      required style="width:80px">
    <button type="submit">Add prediction</button>
  </form>
  <form id="actualForm" style="display:none">
    <select id="mResort" required aria-label="resort"></select>
    <input type="date" id="mDate" required aria-label="report date"
      title="the morning the 24h-to-7am report was published">
    <input type="number" id="mCm" min="0" max="200" step="1" placeholder="cm" required
      style="width:80px">
    <button type="submit">Add actual</button>
  </form>
  <div id="fchips"></div>
  <div id="alignBox" style="margin-top:12px"></div>
  <footer>Transcribe historical predictions (e.g. from the ski.com.au thread) as
  night-before calls — a forecast for day D is scored against the report
  published the morning of D+1. Actuals are entered under the report date.
  Entries live in this browser until exported; drop the file at
  <code>data/manual.json</code> and the morning run merges it (feed data always
  wins on conflicts).</footer>
  </div>
</details>
<footer class="colophon">Ground truth: official resort snow reports for all
five resorts (24h to ~7am, unlagged), with the snowatch.com.au homepage table
and OnTheSnow's resort-reported history as gap-filling fallbacks. Forecasts are snapshotted
~7:45am and ~6pm AEST. A forecast for day D is scored against the report
published the morning of D+1 — the report whose 24h window actually measures
day D (snapshots taken the same evening, after most of the window had already
happened, are excluded as hindsight).<br>
An attempted automated reproduction of <b>Star_Hawk</b>'s daily forecast
comparisons on the ski.com.au forums. Built by Clappo, with Claude.</footer>
</main>
<script>
const DATA = {json.dumps(data)};
const PALETTES = {json.dumps(palettes_js)};
{JS}
</script>
"""
    out = out or SITE / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    print("wrote", render())
