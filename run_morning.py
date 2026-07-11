"""Morning run (~7:45am AEST), per resort:

1. Record the reported 24h-to-7am snowfall. Ground truth is the resort's
   own report — every resort now has a scrapeable official source (see
   resorts.py) — with OnTheSnow's lagging resort-reported history as the
   gap-filling fallback. A third, mid-rank source (the snowatch.com.au
   homepage table) is collected separately on the self-hosted runner (see
   collectors/actuals_snowatch.py). store.save_actual enforces precedence
   (official > snowatch > onthesnow) regardless of collection order.
2. Snapshot every forecaster again (run='am'). Providers like Snowatch
   issue at 6-7am, so this captures the genuine "morning of" call for the
   24h window that just began at 7am — the evening snapshot alone was up to
   12h stale for them.
3. Score, chart (Perisher PNGs — the dashboard covers every resort),
   regenerate the dashboard.

Usage mirrors run_evening.py: --skip/--only filter the forecaster snapshot
(the cloud job passes --skip snowatch; the self-hosted runner captures
Snowatch itself via run_evening.py --only snowatch in the same slot).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

import store
from charts import accuracy_chart, history_chart, next_days_chart
from collectors import actuals_official, actuals_onthesnow
from collectors.common import run_now, today
from resorts import RESORTS, Resort
from run_evening import pick_collectors, snapshot_forecasts
from score import HEADLINE, accuracy
from store import DB_PATH


def _record_actual(con, resort: Resort) -> dict | None:
    """Store today's actual for one resort; returns its status blob."""
    if resort.official_kind:
        try:
            rep = actuals_official.collect(resort)
            store.save_actual(con, resort.id, rep["date"], rep["snow_24h"],
                              actuals_official.SOURCE,
                              reported_at=rep["reported_at"])
            print(f"[ok] {resort.id} official: {rep['date']} "
                  f"24h={rep['snow_24h']}cm, 7d={rep['snow_7day']}cm, "
                  f"depth={rep['natural_depth']}cm, "
                  f"reported_at={rep['reported_at']}")
            return {
                "date": rep["date"].isoformat(),
                "snow_24h": rep["snow_24h"],
                "snow_7day": rep["snow_7day"],
                "natural_depth": rep["natural_depth"],
                "reported_at": rep["reported_at"],
            }
        except Exception:
            print(f"[warn] {resort.id} official scrape failed; "
                  "trying OnTheSnow fallback", file=sys.stderr)
            traceback.print_exc()
    try:
        history = actuals_onthesnow.collect(resort)
        # lowest-rank fallback: save_actual only lets it fill gaps or
        # refresh its own rows — it never overwrites official/snowatch
        for date, cm in history.items():
            store.save_actual(con, resort.id, date, cm,
                              actuals_onthesnow.SOURCE)
        newest = max(history)
        print(f"[ok] {resort.id} actual (fallback OnTheSnow): "
              f"{newest} = {history[newest]}cm ({len(history)} days upserted)")
        return {"date": newest.isoformat(), "snow_24h": history[newest],
                "snow_7day": None, "natural_depth": None}
    except Exception:
        print(f"[FAIL] {resort.id}: no actual recorded", file=sys.stderr)
        traceback.print_exc()
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated source names to run")
    parser.add_argument("--skip", help="comma-separated source names to skip")
    args = parser.parse_args(argv)

    con = store.connect()
    date = today()

    status: dict[str, dict] = {}
    actual_failures = []
    for resort in RESORTS.values():
        blob = _record_actual(con, resort)
        if blob is None:
            actual_failures.append(resort.id)
        else:
            status[resort.id] = blob
    (DB_PATH.parent / "resort_status.json").write_text(
        json.dumps(status, indent=1))

    store.merge_manual(con)

    # morning forecast snapshot (run='am') — the "morning of" call
    mods = pick_collectors(args.only, args.skip)
    forecast_failures = snapshot_forecasts(con, mods, date, run_now())

    run, lead = HEADLINE
    for resort in RESORTS.values():
        acc = accuracy(con, resort.id, run, lead)
        if acc:
            print(f"accuracy ({resort.id}, night-before):")
            for s, v in sorted(acc.items(), key=lambda kv: -kv[1]):
                print(f"  {s:15s} {v:5.1f}%")

    try:
        perisher_acc = accuracy(con, "perisher", run, lead)
        if perisher_acc:
            print("chart:", accuracy_chart(perisher_acc))
            print("chart:", history_chart(con))
        print("chart:", next_days_chart(con, date))
    except ValueError as e:
        print(f"charts skipped: {e}")

    from dashboard import render
    print("dashboard:", render())

    if actual_failures or forecast_failures:
        print(f"FAILURES — actuals: {', '.join(actual_failures) or 'none'}; "
              f"forecasts: {', '.join(forecast_failures) or 'none'}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
