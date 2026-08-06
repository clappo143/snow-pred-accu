# Handoff prompt — Data Explorer tab

Paste everything below the line into a fresh Claude Code session at the repo
root. It is written to be self-contained; the agent should not need this
file's context to work.

---

Build a **Data Explorer** tab in the snow-pred-accu dashboard: a surface for
answering ad-hoc "was this forecaster actually wrong?" questions without
writing SQL by hand.

## Why

This repo scores eight forecasters against official resort reports. When
someone on the ski.com.au forums claims a forecaster blew a call, answering
it currently means hand-writing SQLite against `data/snow.db` and reasoning
carefully about windowing. That analysis is reproducible but slow, and the
windowing is easy to get wrong even for someone who knows the schema. This
tab should make the common questions a few clicks.

## Read these first, in order

1. `CLAUDE.md` — repo boundaries. Note especially that this repo is a
   *producer*; observed-operations work belongs in the Alpine Weather
   Dashboard repo. Do not build anything resembling an operations
   normalizer here.
2. `docs/reference-points.md` — what each source actually publishes, which
   series are retired and why, and the 7am→7am re-windowing history.
3. `score.py` — the whole scoring model is 163 lines. Read all of it.
4. `dashboard.py` — how the current tabs are built and how a resort blob is
   assembled (`_resort_blob`, `_group_blob`).

## The temporal model — the thing to get right

Three distinct instants hide behind one date. For a forecast row with
`target_date = D` at the headline lead:

| instant | when | where in the data |
|---|---|---|
| forecast issued | ~7:45am (`am` run) or ~6pm (`pm` run) on `issued_date` | `forecasts.issued_date`, `forecasts.run` |
| window covered | 7am D → 7am D+1 local | `forecasts.target_date = D` |
| report measuring it | published ~7am on D+1, covering the prior 24h | `actuals.date = D+1` |

So the join is `actuals.date = date(forecasts.target_date, '+1 day')`, and
**the actual for window D is stored under the date D+1**. Lead is
`target_date − issued_date`; the headline lead is `("pm", 1)`, the classic
night-before call. `("pm", 0)` is excluded from scoring because it is an
evening snapshot of a window already 11 hours in the past.

`dashboard.py` now has `windowLabel()` and `timeline()` helpers that render
these three instants. **Reuse them — do not invent a second date-formatting
convention.** Any date shown in the explorer must be unambiguous about which
of the three instants it refers to.

Accuracy is `100 * max(0, 1 - MAE / mean(max(actual, FLOOR)))` — read the
constants from `score.py` rather than copying the numbers.

## What to build

A new tab alongside the existing ones, backed by the same generated-blob
pattern (`python dashboard.py` regenerates `docs/index.html`; the page is
static and has no server). Controls:

- **resort** (one or pooled), **sources** (multi-select), **date range**
- **lead selector** — run × lead_days, defaulting to the headline `(pm, 1)`;
  this is what lets someone ask "how did the Sunday run do for Tuesday?"
- **filters**: minimum actual (isolate real snow events from the many dry
  days that flatter every forecaster), and event-only mode

Views:

1. **Window detail** — pick a window, see every source's call at every lead,
   with the actual. This is the "settle the argument" view.
2. **Forecast evolution** — one target window, x-axis = issue time, one line
   per source, horizontal rule at the actual. Shows who converged and who
   drifted.
3. **Source comparison** — MAE, bias and accuracy over the filtered set, with
   n always displayed next to any rate.
4. **Export** — CSV of the current filtered set, plus the equivalent SQL, so
   a result can be pasted into a forum reply and independently checked.

## Constraints

- **Single generated static file.** No server, no build step, no new runtime
  dependencies. Vanilla JS in `dashboard.py`'s template, matching existing
  style.
- **Respect `store.RETIRED_SOURCES`.** Retired series must never appear in
  scoring or comparison views. They may appear in an explicitly-labelled
  audit view only. Read the list from `store.py`; do not hardcode it.
- **Never show a rate without its n.** Small-n streaks are the main way this
  dashboard could mislead; `score.py` has `MIN_HEADLINE_N` for this reason.
- **Watch the payload size.** `docs/index.html` is ~256KB today. Full forecast
  history is ~27k rows and will not fit naively — either ship a filtered
  slice, or aggregate server-side and accept coarser interactivity. Decide
  deliberately and say which you chose and why.
- **Units.** Sources differ in what they publish. `yrno` stores precipitation
  mm gated to ≤1.0°C at 1mm→1cm; others publish cm directly. Do not present
  cross-source totals as if the derivations are identical without a caveat.
- `docs/index.html` is fully generated — never hand-edit it, and never
  hand-resolve a merge conflict in it. Regenerate with `python dashboard.py`.
- Run `git fetch origin main` and fast-forward before starting. Unattended
  runners push to `main` twice daily, so the clone drifts routinely.

## Definition of done

- `python -m pytest` passes, with new tests covering the lead/window maths —
  especially the `target_date + 1 day` join and month/year rollovers.
- `python dashboard.py` regenerates cleanly and the tab works from
  `file://`.
- A worked example in the PR description: reproduce the finding that on the
  Wed 5 → Thu 6 Aug 2026 window at Perisher, actual 10.0cm, yr.no's
  night-before call of 4.8cm was the closest of eleven series while still
  undercooking by 5.2cm — and that every source had ~0.0 for the following
  window.

## Ask before assuming

If the payload-size tradeoff forces a materially smaller feature than the
above, stop and confirm the cut rather than silently shipping a reduced
version.
