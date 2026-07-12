"""Proxy ground truth: snowatch.com.au's homepage "Resort Watch" table.

The homepage lists a "24hr Snow" figure (plus temp and depth) for every
major Australian resort, mirroring the resorts' own morning snow reports —
its depths match the official pages to the decimal on the same morning
(verified 2026-07-11), so unlike OnTheSnow it doesn't lag by days. It
carries no explicit report timestamp, so reported_at is left NULL.

Rows are keyed by the same slug as the 15-day forecast pages
(data-href='/15-day-forecasts/<slug>/'), i.e. resort.snowatch_slug.

Date convention: the figure mirrors the morning 24h-to-7am report, so a
row collected on date D is stored with date=D — same as the official
collectors. Collect this in the MORNING slot only; the table's semantics
later in the day are less certain.

Ranked between official and OnTheSnow (see store.SOURCE_RANK): it fills
in whenever an official scrape breaks, and can never overwrite official
history.

Like collectors/snowatch.py, Cloudflare blocks GitHub's cloud-runner IPs
for this site, so this runs on the self-hosted runner (see
.github/workflows/daily.yml) — invoke as:

    python3 -m collectors.actuals_snowatch
"""
from __future__ import annotations

import re

from resorts import Resort, RESORTS

from .common import get, today

SOURCE = "snowatch"
URL = "https://www.snowatch.com.au/"

_CELL = re.compile(r"<td[^>]*>\s*(?:<[^>]*>)*([^<]*)", re.S)


def parse(html: str, resort: Resort) -> float:
    """The 24hr-snow cm figure from the resort's Resort Watch row."""
    m = re.search(
        rf"data-href='/15-day-forecasts/{re.escape(resort.snowatch_slug)}/'"
        r".*?</tr>",
        html, re.S)
    if not m:
        raise ValueError(f"no Resort Watch row for {resort.snowatch_slug}")
    cells = [c.strip() for c in _CELL.findall(m.group(0))]
    # cells: [resort name, temp, 24hr snow, depth]
    if len(cells) < 4:
        raise ValueError(f"unexpected row shape for {resort.snowatch_slug}: {cells}")
    v = re.fullmatch(r"([\d.]+)\s*cm|([\d.]+)", cells[2])
    if not v:
        raise ValueError(f"unparseable 24hr figure {cells[2]!r} "
                         f"for {resort.snowatch_slug}")
    return float(v.group(1) or v.group(2))


def collect_all() -> dict[str, float]:
    """resort id -> 24hr snow (cm) for every registered resort with a row."""
    html = get(URL).text
    return {r.id: parse(html, r) for r in RESORTS.values()}


def main() -> int:
    import sys
    import traceback

    import store

    con = store.connect()
    date = today()
    failed = []
    html = get(URL).text
    for resort in RESORTS.values():
        try:
            cm = parse(html, resort)
            store.save_actual(
                con, resort.id, date, cm, SOURCE, source_url=URL,
                raw={"date": date.isoformat(), "snow_24h": cm})
            print(f"[ok] {resort.id} snowatch proxy: {date} 24h={cm}cm")
        except Exception:
            failed.append(resort.id)
            print(f"[FAIL] {resort.id} snowatch proxy", file=sys.stderr)
            traceback.print_exc()
    if failed:
        print(f"FAILED snowatch actuals: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
