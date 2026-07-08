"""Morning run (~7:45am AEST): record reported 24h snowfall, score, chart.

Ground truth is Perisher's own 24h-to-7am figure (accurate, unlagged). If
that scrape fails we fall back to OnTheSnow's newest day, which is a rougher
proxy — its per-day attribution disagreed with Perisher's official totals.
"""
from __future__ import annotations

import json
import sys
import traceback

import store
from charts import accuracy_chart, history_chart, next_days_chart
from collectors import actuals_onthesnow, actuals_perisher
from collectors.common import today
from score import accuracy
from store import DB_PATH


def _record_actual(con) -> bool:
    try:
        rep = actuals_perisher.collect()
        store.save_actual(con, rep["date"], rep["snow_24h"], actuals_perisher.SOURCE)
        (DB_PATH.parent / "resort_status.json").write_text(json.dumps({
            "date": rep["date"].isoformat(),
            "snow_24h": rep["snow_24h"],
            "snow_7day": rep["snow_7day"],
            "natural_depth": rep["natural_depth"],
        }, indent=1))
        print(f"[ok] Perisher official: {rep['date']} 24h={rep['snow_24h']}cm, "
              f"7d={rep['snow_7day']}cm, depth={rep['natural_depth']}cm")
        return True
    except Exception:
        print("[warn] Perisher scrape failed; trying OnTheSnow fallback",
              file=sys.stderr)
        traceback.print_exc()
    try:
        history = actuals_onthesnow.collect()
        newest = max(history)
        store.save_actual(con, newest, history[newest], actuals_onthesnow.SOURCE)
        print(f"[ok] fallback actual (OnTheSnow): {newest} = {history[newest]}cm")
        return True
    except Exception:
        print("[FAIL] no actual recorded", file=sys.stderr)
        traceback.print_exc()
        return False


def main() -> int:
    con = store.connect()
    date = today()
    if not _record_actual(con):
        return 1
    store.merge_manual(con)

    acc = accuracy(con)
    for s, v in sorted(acc.items(), key=lambda kv: -kv[1]):
        print(f"  {s:15s} {v:5.1f}%")

    try:
        if acc:
            print("chart:", accuracy_chart(acc))
            print("chart:", history_chart(con))
        print("chart:", next_days_chart(con, date))
    except ValueError as e:
        print(f"charts skipped: {e}")

    from dashboard import render
    print("dashboard:", render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
