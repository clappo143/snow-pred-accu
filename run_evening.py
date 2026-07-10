"""Forecast snapshot run: capture every provider's forecast for every resort.

Scheduled twice daily (see .github/workflows/daily.yml): ~6pm AEST (the
classic evening snapshot, run='pm') and — via run_morning.py, which reuses
snapshot_forecasts() — ~7:45am AEST (run='am', right after most providers'
6-7am morning issuance). The run slot is inferred from the clock, so the
self-hosted Snowatch job can invoke this same script in both slots.

Runs across two independent jobs/invocations: most collectors run on
GitHub's cloud runners; Snowatch runs separately on a self-hosted runner
because it blocks GitHub Actions' published IP ranges (Cloudflare-level
block, confirmed 2026-07-09). Because of that split, each resort's ensemble
is always rebuilt from everything stored for this snapshot in the DB — not
just what this particular invocation collected — so a same-day Snowatch run
(even hours later) still lands in the right ensemble.

Exits non-zero if any *attempted* collector fails, so the scheduler flags
it — a parse failure must never be recorded as 0cm.

Usage:
    python run_evening.py                  # every forecaster, every resort
    python run_evening.py --skip snowatch  # all except one or more sources
    python run_evening.py --only snowatch  # just one or more sources
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback

import store
from collectors import FORECASTERS
from collectors.common import run_now, today
from resorts import RESORTS
from score import accuracy, weighted_ensemble


def snapshot_forecasts(con, mods, issued: dt.date, run: str) -> list[str]:
    """Collect every (resort, source) pair, save, and rebuild each resort's
    ensemble. Returns the list of 'resort/source' failures."""
    failed = []
    for resort in RESORTS.values():
        for mod in mods:
            try:
                fc = mod.collect(resort)
                store.save_forecasts(con, resort.id, mod.SOURCE, issued, run, fc)
                nxt = min((d for d in fc if d > issued), default=None)
                print(f"[ok] {resort.id}/{mod.SOURCE}: {len(fc)} days"
                      + (f", tomorrow={fc[nxt]}cm" if nxt else ""))
            except Exception:
                failed.append(f"{resort.id}/{mod.SOURCE}")
                print(f"[FAIL] {resort.id}/{mod.SOURCE}", file=sys.stderr)
                traceback.print_exc()

        results = store.load_forecasts_for_issued(con, resort.id, issued, run)
        if results:
            ens = weighted_ensemble(results, accuracy(con, resort.id))
            store.save_forecasts(con, resort.id, "ensemble", issued, run, ens)
            print(f"[ok] {resort.id}/ensemble rebuilt from "
                  f"{len(results)} source(s): {len(ens)} days")
    return failed


def pick_collectors(only: str | None, skip: str | None) -> list:
    only_set = set(only.split(",")) if only else None
    skip_set = set(skip.split(",")) if skip else set()
    return [
        m for m in FORECASTERS
        if m.SOURCE not in skip_set and (only_set is None or m.SOURCE in only_set)
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated source names to run")
    parser.add_argument("--skip", help="comma-separated source names to skip")
    args = parser.parse_args(argv)

    mods = pick_collectors(args.only, args.skip)
    if not mods:
        print("nothing to do — filters excluded every collector", file=sys.stderr)
        return 1

    con = store.connect()
    failed = snapshot_forecasts(con, mods, today(), run_now())
    if failed:
        print(f"FAILED collectors: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
