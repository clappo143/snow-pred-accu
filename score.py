"""Scoring: forecasts vs the resort report, at any lead.

The window fix (2026-07-10): a resort's morning report covers the 24h to
~7am, so the report *published the morning of D+1* is the one that measures
calendar day D. v1 joined the forecast-for-D to the report dated D — a
window that mostly measured D−1 and, for an evening snapshot, hours that
were already in the past when the snapshot was taken. All scoring now joins
actual.date = target_date + 1 day.

Lead is the pair (run, lead_days), lead_days = target_date − issued_date:
    (am, 0) — "morning of": captured ~7:45, right after most providers'
              6-7am issuance, for the window that began at 7:00
    (pm, 1) — the classic night-before call (~13h before the window)
    (am, 1), (pm, 2), … — progressively earlier looks at the same day
(pm, 0) — an evening snapshot of a window already 11h in the past — is
hindsight, not a forecast, and is excluded everywhere.

Accuracy % per (source, run, lead) over the season:
    accuracy = 100 * max(0, 1 - MAE / mean(max(actual, FLOOR)))
The FLOOR stops long runs of 0cm days from making every source look perfect
or terrible; rankings only become meaningful once real snow has fallen,
matching the original project's caveat.
"""
from __future__ import annotations

import sqlite3

FLOOR_CM = 2.0
HEADLINE = ("pm", 1)  # the classic night-before call


def lead_hours(run: str, lead: int) -> int:
    """Nominal hours between the snapshot and the start of the scored
    window (7am on the target day). am snapshots run ~7:45, pm ~18:00."""
    return 24 * lead - 11 if run == "pm" else max(24 * lead - 1, 0)


def pairs(
    con: sqlite3.Connection, resort: str
) -> list[tuple[str, str, int, str, float, float]]:
    """(source, run, lead, target_date, forecast_cm, actual_cm) for every
    scoreable forecast row of one resort, at every lead."""
    return con.execute(
        """
        SELECT f.source, f.run,
               CAST(round(julianday(f.target_date) - julianday(f.issued_date))
                    AS INTEGER) AS lead,
               f.target_date, f.snow_cm, a.snow_cm
        FROM forecasts f
        JOIN actuals a ON a.resort = f.resort
                      AND a.date = date(f.target_date, '+1 day')
        WHERE f.resort = ?
          AND lead >= 0
          AND NOT (f.run = 'pm' AND lead = 0)
        ORDER BY f.target_date
        """,
        (resort,),
    ).fetchall()


def _skill(fc_actual: list[tuple[float, float]]) -> float:
    mae = sum(abs(fc - a) for fc, a in fc_actual) / len(fc_actual)
    norm = sum(max(a, FLOOR_CM) for _, a in fc_actual) / len(fc_actual)
    return 100 * max(0.0, 1 - mae / norm)


def accuracy(
    con: sqlite3.Connection,
    resort: str,
    run: str = HEADLINE[0],
    lead: int = HEADLINE[1],
) -> dict[str, float]:
    """Season accuracy % per source at one lead (default: night before)."""
    by_source: dict[str, list[tuple[float, float]]] = {}
    for source, r, l, _d, fc, actual in pairs(con, resort):
        if (r, l) == (run, lead):
            by_source.setdefault(source, []).append((fc, actual))
    return {s: _skill(p) for s, p in by_source.items()}


def accuracy_by_lead(
    con: sqlite3.Connection, resort: str
) -> dict[str, list[dict]]:
    """Per source: accuracy at every (run, lead) with data, ordered by how
    far ahead the snapshot was taken. Feeds the dashboard's lead controls."""
    grouped: dict[tuple[str, str, int], list[tuple[float, float]]] = {}
    for source, run, lead, _d, fc, actual in pairs(con, resort):
        grouped.setdefault((source, run, lead), []).append((fc, actual))
    out: dict[str, list[dict]] = {}
    for (source, run, lead), p in grouped.items():
        out.setdefault(source, []).append({
            "run": run, "lead": lead, "h": lead_hours(run, lead),
            "pct": round(_skill(p), 1), "n": len(p),
        })
    for rows in out.values():
        rows.sort(key=lambda r: r["h"])
    return out


def weighted_ensemble(
    forecasts: dict[str, dict], weights: dict[str, float]
) -> dict:
    """Accuracy-weighted mean across sources, per target date.

    `forecasts` maps source -> {date: cm}. Sources with no accuracy history
    yet get equal weight.
    """
    dates = {d for f in forecasts.values() for d in f}
    out = {}
    for d in sorted(dates):
        avail = [(s, f[d]) for s, f in forecasts.items() if d in f]
        if not avail:
            continue
        w = [max(weights.get(s, 50.0), 1.0) for s, _ in avail]
        out[d] = sum(wi * cm for wi, (_, cm) in zip(w, avail)) / sum(w)
    return out
