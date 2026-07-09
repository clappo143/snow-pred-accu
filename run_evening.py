"""Evening run (~6pm AEST): snapshot providers' forecasts.

Runs across two independent jobs/invocations (see .github/workflows/daily.yml):
most collectors run on GitHub's cloud runners; Snowatch runs separately on a
self-hosted runner because it blocks GitHub Actions' published IP ranges
(Cloudflare-level block, confirmed 2026-07-09). Because of that split, the
ensemble is always rebuilt from everything stored for today's snapshot in
the DB — not just what this particular invocation collected — so a
same-day Snowatch run (even hours later) still lands in the right ensemble.

Exits non-zero if any *attempted* collector fails, so the scheduler flags
it — a parse failure must never be recorded as 0cm.

Usage:
    python run_evening.py                  # every forecaster
    python run_evening.py --skip snowatch  # all except one or more sources
    python run_evening.py --only snowatch  # just one or more sources
"""
from __future__ import annotations

import argparse
import sys
import traceback

import store
from collectors import FORECASTERS
from collectors.common import today
from score import accuracy, weighted_ensemble


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated source names to run")
    parser.add_argument("--skip", help="comma-separated source names to skip")
    args = parser.parse_args(argv)

    only = set(args.only.split(",")) if args.only else None
    skip = set(args.skip.split(",")) if args.skip else set()
    mods = [
        m for m in FORECASTERS
        if m.SOURCE not in skip and (only is None or m.SOURCE in only)
    ]
    if not mods:
        print("nothing to do — filters excluded every collector", file=sys.stderr)
        return 1

    con = store.connect()
    issued = today()
    failed = []
    for mod in mods:
        try:
            fc = mod.collect()
            store.save_forecasts(con, mod.SOURCE, issued, fc)
            print(f"[ok] {mod.SOURCE}: {len(fc)} days, "
                  f"tomorrow={fc.get(min(d for d in fc if d > issued), '?')}cm"
                  if any(d > issued for d in fc) else f"[ok] {mod.SOURCE}")
        except Exception:
            failed.append(mod.SOURCE)
            print(f"[FAIL] {mod.SOURCE}", file=sys.stderr)
            traceback.print_exc()

    results = store.load_forecasts_for_issued(con, issued)
    if results:
        ens = weighted_ensemble(results, accuracy(con))
        store.save_forecasts(con, "ensemble", issued, ens)
        print(f"[ok] ensemble rebuilt from {len(results)} source(s): {len(ens)} days")

    if failed:
        print(f"FAILED collectors: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
