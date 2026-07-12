# Resort operations telemetry (Phase 3)

`snow-pred-accu` is the canonical scheduled collector for public snowmaking
operations telemetry. Alpine Weather Dashboard is read-only: it consumes the
versioned `data/operations_export_v1.json` artifact and never starts a second
poller in normal operation.

## Run it

```bash
cd /Users/jamesclapham/snow-pred-accu
python3 -m operations.collect --once
python3 -m operations.collect --resort hotham --source hotham_mountainops_runs --once
python3 -m operations.probe                 # live temporary probe; does not alter canonical data
python3 -m unittest discover -s tests -p 'test_operations.py'
```

`--resort <canonical-id|all>`, `--source <source|all>`, `--out <path>`,
`--raw-dir <path>`, and `--db <path>` make every run reproducible. A source
failure produces a visible `retrievalStatus=failed`/`snowmakingStatus=unknown`
capture and the command exits non-zero after continuing with the other sources.

## Sources and layers

| Canonical id | Report layer | Operational layer |
| --- | --- | --- |
| `falls` | Falls Creek JSON (`SlopeMaintenance.CurrentStatus`) | Vail MountainOps runs/lifts |
| `hotham` | Hotham XML (`Snowmaking`, `RunsSnowmaking`) | Vail MountainOps runs/lifts |
| `perisher` | Perisher XML (`snow_guns`, groomed/lift counts) | Vail MountainOps runs/lifts |
| `buller` | Buller weather widget (made/natural depth) | Buller public trails/lifts |
| `thredbo_top` | no defensible current metric | public trails remain `unavailable` for snowmaking |
| `bawbaw` | public snow/weather page narrative only | none currently configured |

The collector validates configured IDs against Phase 0's
`alpine-resort-identities.v1` where that contract is available. It preserves
run names and upstream IDs without inventing a run registry.

## Status semantics

- `active`: affirmative on/in-progress/guns/count/run flag only.
- `inactive`: affirmative off/stopped only.
- `mentioned`: narrative discusses snowmaking but does not establish it is on.
- `none_flagged`: a run/trail feed has no active flags. It never means off.
- `unavailable`: source has no snowmaking field (including Thredbo).
- `unknown`: a field or failed retrieval cannot be safely interpreted.

Modelled wet-bulb viability is deliberately not a status source. Operations
also depend on water, staffing, maintenance, terrain, wind, and economics.

## Storage, provenance, and cadence

The append-only SQLite tables are `operations_snapshots`, `operations_runs`,
and content-addressed `operations_raw_payloads` in `data/operations.sqlite`.
This dedicated database keeps the high-frequency operations archive isolated
from the Phase-1 collector. Successful
payloads are additionally archived at
`data/operations/raw/YYYY-MM-DD/<source>/<sha256>.json`; a repeated body uses
the same raw archive while each poll remains an append-only capture.

The export contains latest snapshots, a bounded history, raw payload metadata,
coverage, and explicit report/run disagreement records. Capture time and
source-reported time are separate. Coverage reports expected/actual captures,
first/last, max gap, status counts, parser failures, and a timing caveat:
activation and shutdown are interval-censored between polls.

## Production scheduler and Alpine delivery

The canonical scheduler is
[`operations.yml`](../.github/workflows/operations.yml) on the repository's
default branch. It runs every 30 minutes from approximately 3pm–10am and every
hour from approximately 10am–3pm in Australian alpine local time. The cron is
expressed as 05:00–23:59 and 00:00–04:59 UTC respectively (AEST); daylight
saving can shift the local labels by one hour. The archive's capture coverage,
not the cron expression, is the evidence of actual coverage.

The workflow is manually runnable from **Actions → resort operations telemetry
→ Run workflow**. Inspect failures in that workflow's `collect all independent
public sources` and `commit append-only telemetry archive` steps. A source
failure is committed as a visible failed/unknown snapshot after independent
sources continue; it never overwrites prior valid data or becomes off/zero.
`generatedAt` in `data/operations_export_v1.json`, the latest snapshot capture
times, and the archive commit are the last-successful-capture checks.

GitHub's remote checkout does not refresh a developer's local filesystem.
The current Alpine server deliberately reads the local canonical path
`/Users/jamesclapham/snow-pred-accu/data/operations_export_v1.json`. For this
development deployment, receive the scheduled export with:

```bash
cd /Users/jamesclapham/snow-pred-accu
git pull --ff-only origin main
```

Alpine reads the file on each API request, so a running local server sees the
new export without starting another collector. A separately deployed Alpine
service must copy this versioned file from the collector checkout into its
release artifact (and set `OPERATIONS_EXPORT_PATH` to that copy) as part of its
deployment; `OPERATIONS_EXPORT_PATH` is a filesystem path, not a remote URL.
There is intentionally no second long-lived Alpine poller.

For a temporary one-off development capture only:

```bash
cd /Users/jamesclapham/Projects/Alpine-Weather-Dashboard
npm run collect:operations
```

Do not run that command on a timer alongside the canonical GitHub scheduler.

## Adding a source

Add a `SourceSpec` and parser in `operations/core.py`, retain the exact public
URL and raw body, map only defensible semantics, add a fixture and a parser
test, and run the live probe. Never turn an absent flag into an inactive plant.
