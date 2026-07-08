"""Render site/index.html — a self-contained dashboard — from the DB.

Design: cold-climate utilitarian. Ice-tinted grounds (light = overcast
snow day, dark = alpine night), one glacial-blue accent, provider colors
shared with charts.py. Pure HTML/CSS, no JS, no external assets.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import store
from score import FLOOR_CM, accuracy, daily_errors

SITE = Path(__file__).parent / "site"

PROVIDER_COLORS = {
    "yrno": "#5B9BD5",
    "bom": "#2E75B6",
    "snowforecast": "#C00000",
    "mountainwatch": "#44546A",
    "ensemble": "#2FA05A",
}
PROVIDER_NAMES = {
    "yrno": "YR.no",
    "bom": "BOM",
    "snowforecast": "Snow-Forecast",
    "mountainwatch": "Mountainwatch",
    "ensemble": "Ensemble",
}

CSS = """
:root {
  --bg: #F2F5F9; --card: #FFFFFF; --ink: #1B2733; --muted: #5D6C7B;
  --line: #DCE4EC; --accent: #3E8FD8; --actual: #1B2733;
}
@media (prefers-color-scheme: dark) { :root {
  --bg: #10161E; --card: #18212C; --ink: #E8EEF4; --muted: #8DA0B3;
  --line: #26313E; --accent: #5FA8E8; --actual: #E8EEF4;
} }
:root[data-theme="dark"] {
  --bg: #10161E; --card: #18212C; --ink: #E8EEF4; --muted: #8DA0B3;
  --line: #26313E; --accent: #5FA8E8; --actual: #E8EEF4;
}
:root[data-theme="light"] {
  --bg: #F2F5F9; --card: #FFFFFF; --ink: #1B2733; --muted: #5D6C7B;
  --line: #DCE4EC; --accent: #3E8FD8; --actual: #1B2733;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 1060px; margin: 0 auto; padding: 32px 20px 64px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px;
  border-bottom: 2px solid var(--ink); padding-bottom: 14px; }
h1 { font-size: 26px; margin: 0; letter-spacing: -0.02em; }
h1 span { color: var(--accent); }
.sub { color: var(--muted); font-size: 13px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); margin: 0 0 14px; font-weight: 600; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin: 24px 0; }
.tile { background: var(--card); border: 1px solid var(--line);
  border-radius: 6px; padding: 14px 16px; }
.tile b { display: block; font-size: 26px; font-weight: 650;
  font-variant-numeric: tabular-nums; }
.tile small { color: var(--muted); font-size: 12px; text-transform: uppercase;
  letter-spacing: 0.06em; }
.card { background: var(--card); border: 1px solid var(--line);
  border-radius: 6px; padding: 20px; margin: 0 0 20px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px;
  color: var(--muted); margin-bottom: 16px; }
.legend i { display: inline-block; width: 10px; height: 10px;
  border-radius: 2px; margin-right: 5px; }
.days { display: grid; grid-auto-flow: column; gap: 18px;
  align-items: end; height: 190px; padding-top: 8px; }
.day { display: flex; flex-direction: column; height: 100%; }
.bars { flex: 1; display: flex; align-items: flex-end; gap: 3px;
  border-bottom: 1px solid var(--line); }
.bar { flex: 1; border-radius: 2px 2px 0 0; min-height: 1px; position: relative; }
.bar em { position: absolute; top: -17px; left: 50%; transform: translateX(-50%);
  font: 600 10.5px/1 system-ui; font-style: normal;
  font-variant-numeric: tabular-nums; color: var(--muted); white-space: nowrap; }
.day > small { text-align: center; padding-top: 6px; color: var(--muted);
  font-size: 12px; }
.rank { display: grid; grid-template-columns: 130px 1fr 52px; gap: 10px;
  align-items: center; margin: 8px 0; font-size: 13.5px; }
.rank .track { background: var(--bg); border-radius: 4px; height: 18px;
  border: 1px solid var(--line); }
.rank .fill { height: 100%; border-radius: 3px; }
.rank b { text-align: right; font-variant-numeric: tabular-nums; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px;
  font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-size: 11.5px; text-transform: uppercase;
  letter-spacing: 0.06em; font-weight: 600; }
.empty { color: var(--muted); font-size: 13.5px; font-style: italic; }
.scroll { overflow-x: auto; }
footer { color: var(--muted); font-size: 12px; margin-top: 8px; }
footer a { color: var(--accent); }
"""


def _bar_group(day_label: str, values: list[tuple[str, float]], vmax: float) -> str:
    bars = ""
    for source, cm in values:
        h = 0 if vmax == 0 else round(100 * cm / vmax, 1)
        label = f"<em>{cm:.0f}</em>" if cm >= 0.5 else ""
        bars += (
            f'<div class="bar" title="{PROVIDER_NAMES.get(source, source)}: {cm:.1f}cm"'
            f' style="height:{max(h, 0.5)}%;background:'
            f'{PROVIDER_COLORS.get(source, "var(--actual)")}">{label}</div>'
        )
    return f'<div class="day"><div class="bars">{bars}</div><small>{day_label}</small></div>'


def render(out: Path | None = None) -> Path:
    con = store.connect()
    today = dt.date.today().isoformat()

    snapshot = con.execute("SELECT max(issued_date) FROM forecasts").fetchone()[0]
    forecasts: dict[str, dict[str, float]] = {}
    for s, d, cm in con.execute(
        "SELECT source, target_date, snow_cm FROM forecasts WHERE issued_date=?",
        (snapshot,),
    ):
        forecasts.setdefault(s, {})[d] = cm

    actuals = con.execute("SELECT date, snow_cm FROM actuals ORDER BY date").fetchall()
    acc = accuracy(con)
    scoreable = daily_errors(con)
    season_total = sum(cm for _, cm in actuals)
    newest_report = actuals[-1][0] if actuals else "—"

    sources = [s for s in PROVIDER_COLORS if s in forecasts]
    days = [
        (dt.date.fromisoformat(snapshot) + dt.timedelta(days=i)).isoformat()
        for i in range(1, 6)
    ]
    vmax = max(
        (forecasts[s].get(d, 0) for s in sources for d in days), default=0
    ) or 1

    legend = "".join(
        f'<span><i style="background:{PROVIDER_COLORS[s]}"></i>{PROVIDER_NAMES[s]}</span>'
        for s in sources
    )
    groups = "".join(
        _bar_group(
            dt.date.fromisoformat(d).strftime("%a %d"),
            [(s, forecasts[s].get(d, 0.0)) for s in sources],
            vmax,
        )
        for d in days
    )

    if acc:
        ranks = "".join(
            f'<div class="rank"><span>{PROVIDER_NAMES.get(s, s)}</span>'
            f'<div class="track"><div class="fill" style="width:{v:.0f}%;'
            f'background:{PROVIDER_COLORS.get(s, "var(--accent)")}"></div></div>'
            f"<b>{v:.0f}%</b></div>"
            for s, v in sorted(acc.items(), key=lambda kv: -kv[1])
        )
        acc_html = ranks + (
            f'<footer>Scored on {len({d for _, d, _, _ in scoreable})} day(s) of '
            f"24h-lead forecasts; accuracy = 100 × max(0, 1 − MAE / mean(max(actual, "
            f"{FLOOR_CM:.0f}cm))).</footer>"
        )
    else:
        acc_html = (
            '<p class="empty">No scoreable days yet — rankings appear once an '
            "evening snapshot has a reported actual to be judged against.</p>"
        )

    actual_rows = "".join(
        f"<tr><td>{d}</td><td>{cm:.0f}</td></tr>" for d, cm in actuals[::-1]
    )
    fc_head = "".join(f"<th>{dt.date.fromisoformat(d).strftime('%a %d')}</th>" for d in days)
    fc_rows = "".join(
        f"<tr><td>{PROVIDER_NAMES.get(s, s)}</td>"
        + "".join(
            f"<td>{forecasts[s][d]:.1f}</td>" if d in forecasts[s] else "<td>—</td>"
            for d in days
        )
        + "</tr>"
        for s in sources
    )

    html = f"""<title>Perisher forecast accuracy</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
<main>
<header><h1>Perisher <span>forecast accuracy</span></h1>
<span class="sub">snapshot {snapshot} · generated {today}</span></header>
<div class="tiles">
<div class="tile"><b>{season_total:.0f}cm</b><small>season snowfall</small></div>
<div class="tile"><b>{newest_report}</b><small>newest resort report</small></div>
<div class="tile"><b>{len(sources)}</b><small>forecasters tracked</small></div>
<div class="tile"><b>{len({d for _, d, _, _ in scoreable})}</b><small>days scored</small></div>
</div>
<div class="card"><h2>Next 5 days — forecast snowfall (cm)</h2>
<div class="legend">{legend}</div>
<div class="days">{groups}</div></div>
<div class="card"><h2>Accuracy rankings — 24h lead, season to date</h2>
{acc_html}</div>
<div class="card"><h2>Forecast table (cm)</h2>
<div class="scroll"><table><tr><th>Source</th>{fc_head}</tr>{fc_rows}</table></div></div>
<div class="card"><h2>Reported daily snowfall — Perisher resort report</h2>
<div class="scroll"><table><tr><th>Date</th><th>Snow (cm)</th></tr>{actual_rows}</table></div>
<footer>Ground truth: OnTheSnow <code>recent[]</code> per-date history
(resort-reported, includes 0cm days; backfilled as the feed updates).</footer></div>
</main>
"""
    out = out or SITE / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    return out


if __name__ == "__main__":
    print("wrote", render())
