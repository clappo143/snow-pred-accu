"""Chart generation (replaces the original project's manual Canva step)."""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CHART_DIR = Path(__file__).parent / "charts"

COLORS = {
    "yrno": "#5B9BD5",
    "bom": "#2E75B6",
    "snowforecast": "#C00000",
    "mountainwatch": "#44546A",
    "ensemble": "#00B050",
}


def _sources_dates(con: sqlite3.Connection, issued: dt.date):
    rows = con.execute(
        "SELECT source, target_date, snow_cm FROM forecasts WHERE issued_date=?",
        (issued.isoformat(),),
    ).fetchall()
    data: dict[str, dict[str, float]] = {}
    for s, d, cm in rows:
        data.setdefault(s, {})[d] = cm
    return data


def next_days_chart(con: sqlite3.Connection, issued: dt.date, ndays: int = 5) -> Path:
    data = _sources_dates(con, issued)
    dates = [(issued + dt.timedelta(days=i + 1)).isoformat() for i in range(ndays)]
    fig, ax = plt.subplots(figsize=(12, 5))
    n = max(len(data), 1)
    width = 0.8 / n
    for i, (source, fc) in enumerate(sorted(data.items())):
        vals = [fc.get(d, 0) for d in dates]
        pos = [j + i * width for j in range(len(dates))]
        ax.bar(pos, vals, width, label=source, color=COLORS.get(source))
    ax.set_xticks([j + 0.4 for j in range(len(dates))])
    ax.set_xticklabels([dt.date.fromisoformat(d).strftime("%a %d") for d in dates])
    ax.set_ylabel("forecast snow (cm)")
    ax.set_title(f"Perisher — next {ndays} days, forecasts snapshot {issued}")
    ax.legend()
    fig.tight_layout()
    out = CHART_DIR / "next_days.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def accuracy_chart(acc: dict[str, float]) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    items = sorted(acc.items(), key=lambda kv: -kv[1])
    ax.barh(
        [s for s, _ in items][::-1],
        [v for _, v in items][::-1],
        color=[COLORS.get(s, "#888") for s, _ in items][::-1],
    )
    ax.set_xlim(0, 100)
    ax.set_xlabel("accuracy % (24h-lead, season to date)")
    ax.set_title("Accuracy rankings")
    for i, (s, v) in enumerate(items[::-1]):
        ax.text(v + 1, i, f"{v:.0f}%", va="center")
    fig.tight_layout()
    out = CHART_DIR / "accuracy.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def history_chart(con: sqlite3.Connection) -> Path:
    """Daily lead-1 forecasts vs reported actual, season to date."""
    rows = con.execute(
        """
        SELECT f.target_date, f.source, f.snow_cm, a.snow_cm
        FROM forecasts f JOIN actuals a ON a.date = f.target_date
        WHERE date(f.issued_date) = date(f.target_date, '-1 day')
        ORDER BY f.target_date
        """
    ).fetchall()
    if not rows:
        raise ValueError("nothing scoreable yet")
    dates = sorted({r[0] for r in rows})
    fig, ax = plt.subplots(figsize=(12, 5))
    for source in sorted({r[1] for r in rows}):
        vals = {d: cm for d, s, cm, _ in rows if s == source}
        ax.plot(
            dates,
            [vals.get(d) for d in dates],
            marker="o", ms=3, lw=1, label=source, color=COLORS.get(source),
        )
    actual = {d: a for d, _, _, a in rows}
    ax.plot(
        dates, [actual[d] for d in dates],
        color="black", lw=2.5, marker="s", ms=4, label="actual (reported)",
    )
    ax.set_ylabel("snow (cm)")
    ax.set_title("24h-lead forecasts vs reported snowfall — Perisher")
    ax.legend()
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    out = CHART_DIR / "history.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out
