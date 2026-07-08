"""Evening run (~6pm AEST): snapshot every provider's forecast.

Exits non-zero if any collector fails, so the scheduler flags it — a parse
failure must never be recorded as 0cm.
"""
from __future__ import annotations

import sys
import traceback

import store
from collectors import FORECASTERS
from collectors.common import today
from score import accuracy, weighted_ensemble


def main() -> int:
    con = store.connect()
    issued = today()
    results, failed = {}, []
    for mod in FORECASTERS:
        try:
            fc = mod.collect()
            store.save_forecasts(con, mod.SOURCE, issued, fc)
            results[mod.SOURCE] = fc
            print(f"[ok] {mod.SOURCE}: {len(fc)} days, "
                  f"tomorrow={fc.get(min(d for d in fc if d > issued), '?')}cm"
                  if any(d > issued for d in fc) else f"[ok] {mod.SOURCE}")
        except Exception:
            failed.append(mod.SOURCE)
            print(f"[FAIL] {mod.SOURCE}", file=sys.stderr)
            traceback.print_exc()

    if results:
        ens = weighted_ensemble(results, accuracy(con))
        store.save_forecasts(con, "ensemble", issued, ens)
        print(f"[ok] ensemble: {len(ens)} days")

    if failed:
        print(f"FAILED collectors: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
