"""Compare this project's effective actuals with Alpine's archived reports."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALPINE = (Path.home() / "Projects" / "Alpine-Weather-Dashboard" /
                  "data" / "official_reports.json")


def instant(value: str | None):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def main(alpine_path: Path, date: str) -> int:
    ours = json.loads((ROOT / "data" / "alpine_export_v1.json").read_text())
    alpine = json.loads(alpine_path.read_text())
    left = {(x["resortId"], x["date"]): x for x in ours["actuals"]}
    right = {(x["resortKey"], x["reportDate"]): x for x in alpine
             if x.get("resortKey") in ours["forecasts"]}
    failed = False
    print("resort       snow  reportedAt                         reportTimeKind")
    for rid in sorted(ours["forecasts"]):
        a, b = left.get((rid, date)), right.get((rid, date))
        if not a or not b:
            print(f"{rid:12} MISSING ours={bool(a)} alpine={bool(b)}")
            failed = True
            continue
        alpine_snow = b.get("resortSnow24hCm")
        if alpine_snow is None:
            alpine_snow = b.get("snow24hCm")
        values = (a["snow24hCm"] == alpine_snow and
                  instant(a["reportedAt"]) == instant(b.get("reportedAt")) and
                  a["reportTimeKind"] == b.get("reportTimeKind"))
        print(f"{rid:12} {a['snow24hCm']:>4g}  {a['reportedAt'] or '-':34} "
              f"{a['reportTimeKind'] or '-':23} {'OK' if values else 'MISMATCH'}")
        if not values:
            print("  Alpine:", alpine_snow, b.get("reportedAt"),
                  b.get("reportTimeKind"))
            failed = True
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("date")
    parser.add_argument("--alpine", type=Path, default=DEFAULT_ALPINE)
    args = parser.parse_args()
    raise SystemExit(main(args.alpine, args.date))
