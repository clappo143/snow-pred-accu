"""Morning run (~7:45am AEST): record reported 24h snowfall, score, chart."""
from __future__ import annotations

import sys
import traceback

import store
from charts import accuracy_chart, history_chart, next_days_chart
from collectors import actuals_onthesnow
from collectors.common import today
from score import accuracy


def main() -> int:
    con = store.connect()
    date = today()
    try:
        history = actuals_onthesnow.collect()
        for d, cm in sorted(history.items()):
            store.save_actual(con, d, cm, actuals_onthesnow.SOURCE)
        newest = max(history)
        print(f"[ok] actuals: {len(history)} days upserted, "
              f"newest {newest} = {history[newest]}cm")
    except Exception:
        print("[FAIL] actuals", file=sys.stderr)
        traceback.print_exc()
        return 1

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
