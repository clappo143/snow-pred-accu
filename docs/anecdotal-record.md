# Anecdotal record: 11–13 July event, pre-restart forecasters

Context: `janesweather_cal` and `yrno_cal` (320 rows each) were retired from
the ensemble/dashboard on 2026-07-13 because the stored calendar-day totals
can't be honestly re-windowed into 7am→7am scoring — see the `f294337` and
`e047525` commit messages. Both providers restart clean rather than carry a
correctness-mangled history.

James asked a subagent to forage ski.com.au's "Predictions – July 11-16th"
thread for anything usable to backfill that gap. Nothing recovered is
resort/date/lead precise enough to become a scored `forecasts` row (multi-day
event totals, secondhand paraphrase, no fixed target date, or — for BOM — a
25%-chance precip tail rather than the median figure the collector scores).
Kept here as qualitative corroboration only, not data:

**yr.no**
- Donza, ski.com.au, 11 Jul 4:15pm, [post #6248131](https://www.ski.com.au/xf/threads/july-11-16th.97305/page-7#post-6248131):
  "bout 60 on yr.no" — inside the 11–13 Jul window, NSW/Perisher area per
  surrounding posts, but no fixed target date or per-day split.
- Donza, ski.com.au, 7 Jul 4:47pm, [post #6245186](https://www.ski.com.au/xf/threads/july-11-16th.97305/page-2#post-6245186):
  "Yr.no is showing 20cm plus for Perisher" — 4 days ahead of the window,
  may reflect an early run rather than the 10-12 event specifically.
- bizza, ski.com.au, 13 Jul (retrospective): "yr.no said it most of the way
  along and sadly they were right" — confirms the call held up, no number.

**Jane's Weather**
- howardyou, ski.com.au, 9 Jul 10:42am: "Jane Bunn's combo/AI model seemed
  to be onto this one pretty early" — no cm figure in-thread.
- murrumbidgee63, ski.com.au, 9 Jul 9:28am: called a rival forecaster's
  ("the Frog", likely Mountainwatch) big prediction "the polar opposite of a
  janes weather forecast" for Thredbo Village/Selwyn — implies a notably
  lower Jane's number, no figure quoted.

**BOM** (not part of the retirement — kept for context on the same event)
- [Weatherzone, 8 Jul](https://www.weatherzone.com.au/news/weekend-snowfalls-coming-after-sunny-week/1891447)
  (sourced from BOM data): "Totals in the vicinity of 10 to 15cm at the
  mid-level of most ski resorts... with the most consistent period of snow
  showers occurring on Sunday [12 July]." Regional/multi-day, not a
  per-resort forecast product figure.
- POW Hungry, ski.com.au, 11 Jul 5:05pm, [post #6248189](https://www.ski.com.au/xf/threads/july-11-16th.97305/page-7#post-6248189):
  "BOM siding with a 25% chance of 45mm tomorrow (Thredbo)" — the 25%-chance
  tail, not the 50%-chance/median figure the collector scores (`3aa2e4c`).

Net: this event is anecdotally well-attested as a good call for yr.no and
Jane's, which is useful context for why the clean restart isn't erasing a
record of failure — just a record of success that couldn't be scored
honestly.
