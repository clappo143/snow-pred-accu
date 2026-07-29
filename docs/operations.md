# Resort operations telemetry (Phase 3)

## Operations v2 foundation (not yet the production export)

Phase 1 of the Observed Operations expansion defines
`alpine.operations-export.v2` without changing the v1 collector or its default
artifact. The producer owns the editable asset registry at
`config/operations_assets_v1.json`; a validated snapshot is embedded in each
future v2 export. Consumers must not maintain a second editable registry.

The v2 export is normalized around captures, metric observations, dedicated
snowmaking observations, append-only asset-status observations, scoped
aggregates, narratives, and conflicts. It has no wide `latest` object. A future
latest view may select observation IDs, but must not overwrite evidence.

Semantic rules:

- source role and observation role are independent; a morning plan and a live
  actual can coexist without conflict;
- `observed`, `explicit_zero`, `blank`, `unavailable`, `not_applicable`, and
  `unknown` are distinct value states;
- capture, response, source-report, and metric-observed times are separate;
- raw field names and values are retained with interpretation confidence;
- snowmaking plant state, standby, gun/run counts, run flags, capability,
  machine-made cover/depth, area state, and narrative mentions are separate;
- a conflict applies only to the same semantic fact, subject, observation role,
  and overlapping effective time; no winner is required;
- an unmapped upstream asset remains an observation with `assetId=null`, its
  upstream identity, and a warning;
- aggregate numerators always retain denominator, denominator scope, and role.

The Hotham v2 fixture intentionally records `On Standby`, the raw
`RunsSnowmaking=5` count with unverified meaning, and each MountainOps `NO` run
flag with unverified meaning as independent signals. V1's legacy promotion is
untouched and must not be copied into a v2 parser.

### Registry maintenance

`tools/discover_operations_assets.py` deterministically creates candidates
from already-archived official structured payloads. It does not use the
network. Review names, upstream IDs, classes, and source scope before replacing
the canonical registry. Never infer physical grouping from similar names;
retired IDs remain immutable. Validate with `operations.registry` and the test
suite. Report-only parks, cross-country trails, and Selwyn assets should be
added only from explicit structured or human-reviewed source evidence.

`config/operations_sources_v2.json` is the independently validated source
semantics inventory. Adding a production parser or polling a report-only source
is explicitly Phase 2 work.

### Database migration

`operations.migrations_v2.migrate_v2(connection)` installs additive,
idempotent `operations_v2_*` tables and indexes. It is deliberately not called
by `connect()` and Phase 1 does not migrate `data/operations.sqlite`. Test it on
temporary empty and v1-shaped databases. Ambiguous v1 rows are not backfilled.

### Phase 1B contract hardening

`assert_valid_export()` applies the checked-in Draft 2020-12 schema with format
checking before producer semantic validation. The semantic pass checks registry
revisions, source/resort/layer/role agreement, observation-to-capture resorts,
asset identity/class, metric types and units, effective intervals, raw-payload
provenance, source mappings, observation references, and scoped conflicts.

Asset status contains only `snowmakingObservationIds`. Operational snowmaking
claims and flags live in dedicated snowmaking observations. Numeric
machine-made depth and snowmaking volume live only in metric observations.

The source registry assigns every known path one disposition: `normalized`,
`identity_provenance`, `raw_only`, or `ignored`. Repeating paths use `[*]`.
Unknown future paths remain in raw storage and must produce an
`unknownSourceFields` diagnostic. CMS layout, coordinates, and map drawing
fields are ignored rather than normalized merely to increase coverage.

The metric catalogue is `config/operations_metrics_v2.json`. Catalogue, source
inventory, and asset registry hashes are deterministic immutable revisions and
captures carry all three. Migration DDL stores append-only registry, source,
and metric snapshots and keys asset rows by registry revision.

Thredbo winter skiing services require an explicit winter classification and
denominator eligibility. Scenic services remain separate; MTB and summer lifts
are transports; Alpine Coaster is an activity. Only reviewed skiing records
enter `winter_skiing_service`. `unique_physical_assets` is not emitted until
physical groups are explicitly reviewed.

Identity resolution uses an explicit argument, then
`ALPINE_RESORT_IDENTITIES_PATH`, then the producer's repository-relative
contract copy. It has no user-specific dashboard path dependency.

### Phase 1C semantic integrity

Field coverage is generated from an exact `(sourceId, canonical path)` review
table. Substring classification is prohibited. Each normalized entry is an
executable specification containing its collection and field, metric key where
applicable, source and normalized types, unit rule, observation role, scope,
timestamp rule, value-state handling, confidence, and any aggregate, asset, or
snowmaking semantics. Registry validation rejects unknown catalogue metrics,
nonexistent schema destinations, duplicate paths, and contradictory entries.

Counts are aggregate observations, not asset booleans. Queue minutes live only
on asset status and their upstream queue timestamp becomes `observedAt`.
Grooming counts are aggregates; last-groomed values supply observation timing.
Numeric and compass wind directions have separate metric keys. Forecast
maximum, daytime high, and overnight minimum temperatures remain distinct.

Partial embedded asset/source inventories must be exact canonical subsets for
their declared revisions. Complete inventories must exactly equal the full
canonical revision. Captures require their source entry, mapped observations
require their canonical asset entry, and raw descriptor URLs must equal the
source contract URL.

Asset-status snowmaking references are same-capture, same-resort, asset-scoped,
and same-asset (or same unmapped upstream identity). Cross-layer comparisons do
not use direct references. A conflict requires the same observation role and
overlapping effective time; morning plans and live actuals cannot be declared a
direct conflict merely because their values differ.

### Phase 1D semantic closure

Falls report fields containing comma-delimited names use a validated
`listExpansionSpec` and two explicit emissions. Splitting is comma-only,
whitespace is trimmed, empty elements (including trailing empties) are removed,
the original value is retained, and duplicate normalized names are retained
with a diagnostic. Lift, activity, and grooming lists emit one asset-status
record per name plus a same-capture aggregate. Snowmaking lists emit dedicated
run/area snowmaking evidence plus a derived count; their presence never proves
resort plant state. Blank strings are explicit empty lists for that field only.
Unmapped names retain their exact upstream identity and a warning.

Falls official morning lift status is `morning_plan`, afternoon status is
`expected`, and top-level report lists and other report asset summaries are
`report_summary`. MountainOps sources remain `live_actual`. The Falls
`AverageSnowMaking` field is a `machine_made_depth_cm` measurement using the
slope-maintenance update timestamp; `CurrentStatus` remains the separate plant
state. Cross-country totals are published kilometres via
`cross_country_groomed_km` and `cross_country_groomed_24h_km`. `TodaysLow` uses
the neutral `daytime_low_temperature_c` at medium confidence because the
surface does not establish forecast versus observed semantics; it is distinct
from `OvernightMin`.

Hotham `Wind` uses two emissions, `wind_speed_min_kmh` and
`wind_speed_max_kmh`. A range such as `Strong 61-80 km/h` becomes 61 and 80;
a single numeric speed sets equal minimum and maximum. Both observations retain
the original wording. Blank or malformed text produces null-valued blank or
unknown observations with raw evidence and diagnostics, never fabricated
speeds. Registry validation also
requires every normalized metric type to exactly match its catalogue type,
checks all multi-emission destinations, enforces same-capture list denominator
paths, and rejects `live_actual` evidence from daily-report sources.

### Phase 2B temporary-lane identity and rich contract

The temporary v2 capture identity is the SHA-256 digest of the source ID,
canonical UTC retrieval timestamp, and payload hash. Exact replay of one
retrieval envelope is idempotent, but two polls at different retrieval times
remain distinct even when their bytes are unchanged. Every observation ID
includes its capture ID, destination collection, canonical source path,
subject, and retained ordinal. A repeated deterministic key with different
content is a persistence error for captures, observations, catalogue snapshots,
versioned assets, v2 raw descriptors, and diagnostics.

Australian resort timestamps without an offset are interpreted in
`Australia/Melbourne`; aware timestamps retain their real offsets before UTC
conversion. Falls `LastUpdate` values explicitly labelled UTC remain UTC.
Perisher date-only report values establish an operational date but never a
fabricated source-reported instant. Negative freshness is warned and retained
as null. Rich 1970 operating-time placeholders normalize only to clock times.

The checked comparison fixture
`tests/fixtures/operations/v2/vail_rich_compact_comparison_2026-07-13.json`
is the promotion gate for rich lift mappings. IDs, trimmed names, and areas
matched 15/15 Falls, 14/14 Hotham, and 45/45 Perisher services. Status codes
2 and 3 have checked row support for closed and on hold. Code 1 maps to open
from an independently reviewed direct same-poll observation, but the checked
comparison rows do not contain that event and say so explicitly. Code 6 and
future codes remain unknown and raw-retained. The rich `isScheduled` field uses
`vail_rich_binary_schedule_flag_v1`: exact upstream integer 0 becomes false and
1 becomes true; every other value or type becomes null with a diagnostic. It
never supplies operational status, so code 6 plus schedule 1 remains unknown
and scheduled. `queueMinsEstimate` is raw-only because repeated all-zero samples
do not establish whether zero is an explicit no-wait observation, and no queue
timestamp is fabricated.

The compact lift feed remains the dynamically reported operational-status
source. The rich feed contributes verified identity, reviewed operating clock
fields, the binary schedule flag, partially known status codes, and a still
unresolved queue estimate. Identity promotion depends only on exact ID, trimmed
name, and area parity—not transient status agreement.

Temporary exports select captures first by an explicit inclusive UTC window,
then include every observation and v2 raw descriptor associated with those
captures. Independent collection limits are prohibited. The export fails if
selected captures span catalogue revisions that the top-level v2 contract
cannot represent truthfully.

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

## Phase 3 operations-v2 resolved view (non-production)

Phase 3 adds a deterministic derived view without changing any v2 evidence.
`snow-pred-accu` is the current **provisional incubation host** for the
operations-v2 evidence model and resolver; this is not a final repository
ownership decision. The resolver does not select a product-level canonical
daily snowfall value. Daily snowfall observations present in operations feeds
retain their source provenance, while the existing strict official-report
export remains authoritative for Alpine's resort-reported new-snowfall path.
Alpine's separate official-report polling is not reconciled or modified here.

The input is a bounded, schema-valid `alpine.operations-export.v2` document.
The output is a separate `alpine.operations-resolved-view.v1` document carrying
the complete input fingerprint, evidence window, catalogue revisions, policy
revision, as-of time, injectable generated time, resolved state, direct
conflicts, plan-versus-actual comparisons, input conflicts, and diagnostics.
The transformation never writes to an operations database and has no network,
scheduler, forecast, prediction, snowfall-scoring, or dashboard dependency.

Run it with explicit paths and time:

```bash
python3 -m operations.resolve_v2 \
  --in /tmp/operations-export-v2.json \
  --out /tmp/operations-resolved-v1.json \
  --as-of 2026-07-13T06:30:00Z \
  --generated-at 2026-07-13T06:31:00Z \
  --policy config/operations_resolution_policy_v1.json
```

The core API is pure over its supplied values:

```python
resolved = resolve_export(export_payload, as_of, policy, generated_at=clock_value)
```

Input validation is injectable for relocation. The default validates against
the producer's Phase 2 v2 contract. Exact input, as-of time, policy, and
generated time produce byte-identical canonical JSON. Semantic validation
reconstructs that expected document without recursively validating it and
compares every derived section. This binds selection values and known state,
provenance, timing and ages, freshness, resolution/conflict/comparison IDs,
alternates, reasons, preferred selections, diagnostics, and catalogue/evidence
metadata to the supplied evidence, policy, `asOf`, and `generatedAt`.

`generatedAt` is caller-supplied production metadata used during reconstruction;
it is not independently authenticated source evidence and is not a selection
input. Reconstruction proves that the document is internally reproducible for
the supplied `generatedAt`, not that this timestamp was the producer's original
wall-clock value. `asOf` and every as-of-derived selection remain deterministic
and validated. The shared schema's `unknown` freshness enum is reserved for
forward compatibility; the current resolver and policy reach only `fresh`,
`stale`, and `future_timestamp_anomaly`.

### Resolution policy and lanes

`config/operations_resolution_policy_v1.json` is schema-validated and its
revision is the canonical SHA-256 of the policy content excluding the revision
field. It explicitly maps roles into `actual`, `reported`, `plan`, `advisory`,
and `unknown` lanes. Every observation role is assigned exactly once. Direct
conflict compatibility requires the same role by default; sharing a broad lane
is not sufficient. Preferred display order is also explicit: fresh known
actual, fresh known reported, anomalous-but-available actual/reported, stale
known actual/reported, and only then plan evidence. The policy enumerates every
reachable `fresh`, `stale`, and `future_timestamp_anomaly` combination for all
five lanes; dead ordering tokens are rejected. Unknown evidence never
silently erases a known value. False, zero, and contract-permitted empty values
remain known.

Asset rows are never selected wholesale. `operationalStatus`, `scheduled`,
`expectedToOpen`, `openTime`, `closeTime`, `queueMinutes`, `groomed`,
`condition`, and `statusReason` are resolved independently. Reviewed Vail lift
status priority is compact MountainOps, then rich MountainOps only for known
mapped codes, then official report/list fallback in its own reported or plan
lane. Rich `isScheduled` is an independent scheduling fact. Code 6 is unknown,
and queue estimates remain unresolved because Phase 2 deliberately retained
them as raw-only. Compact and rich operating clocks are comparable only after
Phase 2 normalization; compact has deterministic precedence and differing
fresh same-lane values remain inspectable. MountainOps run status/grooming is
the preferred actual evidence while official-report facts remain reported or
fallback evidence.

Phase 2 currently emits structural `statusReason: []` on every asset row. Phase
3B treats both `null` and `[]` as unresolved because an empty array does not
prove that the source explicitly reported no reason. A non-empty reviewed
reason list remains known. Distinguishing an explicit source-reported empty
reason would require additional Phase 2 field-level provenance. This exception
does not change false or numeric-zero semantics for fields where those are real
evidence.

Freshness comes exclusively from each source entry embedded in the input
inventory. `capture.retrievedAt` is the availability boundary; captures after
`asOf` are excluded. Effective intervals are respected. Capture and observation
age plus the embedded threshold are emitted for every selection. An implausibly
future `observedAt` on an already-available capture is labelled
`future_timestamp_anomaly`, not treated as future knowledge. Stale last-known
values remain selectable and visibly stale. Failed captures remain excluded
from selection but are listed with capture, resort, source, retrieval status,
retrieval time, HTTP status, and warnings. Warnings from usable partial captures
retain the same capture/source identity. Future captures are likewise listed
as excluded without exposing their observations as available state.

Metrics retain the full semantic key: resort, metric, scope, subject, location,
unit, and lane. Forecast/expected and observed/reported values are not collapsed.
Successive values from one source are transitions. Falls location depths stay
separate. Natural depth, machine-made depth, and snowmaking volume are distinct.
Aggregates additionally retain asset class, area, status, denominator scope,
and lane. Fractions exist only for a positive, same-observation denominator;
unknown denominators remain useful numerators. Invalid negatives, numerator
overflow, and shared-denominator status partition overflow are diagnosed.

Snowmaking is deliberately multi-signal. Plant state, machine-made depth,
volume, reported runs count, guns count, per-asset flags, report-listed active
assets, feed-level `none_flagged`, and narrative mentions retain independent
semantic keys. Thus Hotham plant `standby`, `RunsSnowmaking=5`, unknown per-run
`NO` flags, and feed-level `none_flagged` coexist without a fabricated plant
verdict or direct conflict.

Direct conflicts are conservative: known differing values, identical semantic
field/subject, the same observation role, comparable units/scopes/locations, and an
overlapping interval or configured comparison window. Later values from the
same source are transitions. Unknown-versus-known, plan-versus-actual,
scheduled-versus-operational, different snowmaking signal types, different
metric locations, and incompatible aggregate denominators are not direct
conflicts. A policy-selected display candidate does not resolve or remove a
conflict. Plan-versus-actual differences are emitted separately with provenance
and freshness on both sides. An input evidence conflict appears in
`inputEvidenceConflicts` only when every referenced observation exists, is
effective at `asOf`, and belongs to a usable capture retrieved no later than
`asOf`. Otherwise it is listed in `excludedInputEvidenceConflicts` with exact
exclusion reasons. The fingerprint still binds the resolved view to the
complete input document, including excluded future facts.

Unmapped assets use a stable source-scoped identity containing resort, exact
source ID, asset class, and upstream ID or normalized upstream name. Similar
text from different sources is not merged. No physical-lift grouping is added.

### Relocation inventory for Phase 3.5 review

- Portable contracts/configuration: the export v2 contract, resolved-view v1
  contract, resolution-policy v1 contract, revisioned policy, asset/source/
  metric catalogues, and resort identity contract.
- Portable code: v2 normalization, temporary append-only storage/export,
  resolver, and structural/semantic validators. The resolver itself accepts
  supplied evidence, policy, times, and an injectable input validator.
- Repository-specific elements: current `snow-pred-accu` producer labels,
  default CLI paths, raw archive layout, registry discovery/finalization tools,
  and existing strict official-report snowfall output.
- Workflow/delivery dependencies: the current operations scheduler is v1 only;
  no v2 activation or default-selection change exists. Alpine currently has
  its own read-only paths/polling and receives no resolved-view artifact in this
  phase.
- Migration considerations: preserve observation/capture IDs, immutable raw
  references, registry snapshots, catalogue revisions, evidence fingerprints,
  archive retention, and byte-identical contracts. Decide ownership, delivery,
  duplicate-poll retirement, and cross-contract snowfall selection before any
  production cutover.

Phase 3 stops at validated resolver behavior. Scheduling, default-v2 export,
dashboard integration, duplicate polling retirement, and final ownership are
Phase 3.5 decisions.

The demonstration tool reports source and resolved byte sizes, gzip sizes,
record counts by collection, and known versus unresolved selection counts. The
complete analytical resolved view is not automatically a suitable dashboard
delivery artifact. Phase 3.5 must decide on a separately bounded, compact
consumer projection rather than treating the full provenance-rich view as the
default delivery payload.

## Adding a source

Add a `SourceSpec` and parser in `operations/core.py`, retain the exact public
URL and raw body, map only defensible semantics, add a fixture and a parser
test, and run the live probe. Never turn an absent flag into an inactive plant.
