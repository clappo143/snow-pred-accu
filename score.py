"""Scoring: compare lead-1 forecasts (snapshot taken the evening before the
target day) against reported actuals.

Accuracy % per source over the season:
    accuracy = 100 * max(0, 1 - MAE / mean(max(actual, FLOOR)))
The FLOOR stops long runs of 0cm days from making every source look perfect
or terrible; rankings only become meaningful once real snow has fallen,
matching the original project's caveat.
"""
from __future__ import annotations

import sqlite3

FLOOR_CM = 2.0


def daily_errors(con: sqlite3.Connection) -> list[tuple[str, str, float, float]]:
    """(source, date, forecast_cm, actual_cm) for all scoreable lead-1 days."""
    return con.execute(
        """
        SELECT f.source, f.target_date, f.snow_cm, a.snow_cm
        FROM forecasts f
        JOIN actuals a ON a.date = f.target_date
        WHERE date(f.issued_date) = date(f.target_date, '-1 day')
        ORDER BY f.target_date
        """
    ).fetchall()


def accuracy(con: sqlite3.Connection) -> dict[str, float]:
    rows = daily_errors(con)
    by_source: dict[str, list[tuple[float, float]]] = {}
    for source, _date, fc, actual in rows:
        by_source.setdefault(source, []).append((fc, actual))
    out = {}
    for source, pairs in by_source.items():
        mae = sum(abs(fc - actual) for fc, actual in pairs) / len(pairs)
        norm = sum(max(actual, FLOOR_CM) for _, actual in pairs) / len(pairs)
        out[source] = 100 * max(0.0, 1 - mae / norm)
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
