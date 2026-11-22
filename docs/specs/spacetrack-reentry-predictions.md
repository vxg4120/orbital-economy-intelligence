# Spec: Space-Track reentry predictions ("reentering soon")

**Status:** active (phase 1 built 2026-11-22 at Vib's go; the first live pull is the Wednesday
2026-11-25 19:10 UTC nightly)
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-11-22

## Goal
Space-Track publishes, once a week, a 60-day decay prediction for every object it expects to
reenter within the next 60 days. OEI already pulls Space-Track's actual decay messages
(`MSG_TYPE` Historical) every night, and it deliberately left the predictions out
(docs/specs/spacetrack-topup.md, Open questions). This workstream lands the weekly 60-day
predictions in a table of their own and shows them on orbital.vibcreates.com as a
"Reentering soon" list: the objects predicted to come down in the next 60 days, the predicted
date, and the date of the prediction. A prediction never becomes a decay date: the resolver,
the claims feed, `satellite.decay_date` and the Conflicts page stay exactly as they are.

## What the data and the source say (2026-11-19)
- Space-Track's API use guidelines (https://www.space-track.org/documentation, the public
  part of the page) give each class its own frequency: DECAY "1 / day", TIP "1 / hour", and
  60-DAY DECAY "1 / week", with "Once per week, on Wednesdays after 1700 (UTC), for 60-Day
  Decay predication data" (sic). The site's example query for it is
  `/basicspacedata/query/class/decay/MSG_EPOCH/%3Enow-8/source/60day_msg/format/html/emptyresult/show`.
- The 60-day predictions are rows of the DECAY class with `source` `60day_msg`. TIP is a
  separate class (`/basicspacedata/query/class/tip`), checked hourly with
  `/INSERT_EPOCH/%3Enow-0.042/`. The per-class field reference is behind the login, so field
  names below come from the DECAY rows OEI already lands, not from the reference.
- Production, read-only: `raw_spacetrack_decay` holds 2,139 rows, all `msg_type` Historical
  (1,691 with `source` satcat and 448 with `source` decay_msg), from 34 OK nightly pulls between
  2026-10-16 and 2026-11-18. No prediction has ever been pulled.
- Every reader of `raw_spacetrack_decay` takes the newest OK run of endpoint `decay` as its
  snapshot (`identity/claims.py`, `v_spacetrack_decay_actual` in migration 0027) and filters
  to `msg_type = 'Historical'`.

## Architecture decisions
- 2026-11-19 (Claude): **Phase 1 is the 60-day predictions only, pulled weekly.** Rejected:
  TIP first. Because: TIP messages exist only for the last days before a reentry, so a weekly
  TIP pull would miss most of them, and a daily one is a second Space-Track class to run and
  test. The 60-day set is the natural weekly source for a 60-day list. TIP is phase 2, an open
  question for Vib.
- 2026-11-19 (Claude): **Own table and own ledger endpoint.** Predictions land in
  `raw_spacetrack_decay_prediction` under ingest_run endpoint `decay_60day`. Rejected: the
  existing `raw_spacetrack_decay` table and endpoint `decay`. Because: the readers take the
  newest OK `decay` run as the whole snapshot, so a predictions-only run would read as a night
  with no actual decays, and the claims pass would close every Space-Track decay claim.
- 2026-11-19 (Claude): **Predictions never feed identity.** No claim, no precedence slot, no
  write to `satellite.decay_date`. Because: a prediction is not a decay, and the Conflicts page
  counts disagreements about dates that have happened.
- 2026-11-19 (Claude): **A step inside the nightly, gated by the calendar and the ledger.** The
  step pulls only when no OK `decay_60day` run has started since the most recent Wednesday
  17:00 UTC. On a normal week that is the Wednesday 19:10 run; if that run fails, the next
  nightly retries. Rejected: a separate cron entry. Because: the nightly already holds the
  Space-Track credentials, the step log, the `!!` alert path and the publish order.
- 2026-11-22 (Claude): **Retries stop 48 hours after Wednesday 17:00 UTC** (the Wednesday 19:10
  run and the three nightlies after it). Rejected: retrying every nightly until the next
  Wednesday. Because: an open-ended gate would make the first pull after a deploy happen on
  whatever weekday the deploy lands, and Space-Track asks for this query on Wednesdays. A week
  missed past the window is caught by the 9-day age alert.
- 2026-11-22 (Vib, "proceed however you think best is recommended"): the three open questions
  closed on Claude's recommendations. The weekly 60-day query is treated as its own allowance,
  because the public guidelines list it on a row of its own beside DECAY's one a day. The list
  is a panel on the Overview, below the latest reentries. TIP stays out of phase 1.
- 2026-11-22 (Claude): **The view finds the newest run in the ledger, not in the rows.** An OK
  run that landed nothing then empties the view, so last week's set is never shown as current.
  This avoids the limit recorded for `v_spacetrack_decay_actual` (migration 0027, review M-5).
- 2026-11-19 (Claude): **Snapshot semantics.** The newest OK `decay_60day` run is the whole
  prediction set, the same rule the other Space-Track readers follow. An object missing from it
  has no current prediction.
- 2026-11-19 (Claude): **Query.** `/basicspacedata/query/class/decay/MSG_EPOCH/%3Enow-8/source/
  60day_msg/orderby/NORAD_CAT_ID asc/format/json` (the site's example with JSON output and a
  stable order). Eight days covers one week of messages plus a day of slack.

## Constraints
- At most one 60-day query per week, after Wednesday 17:00 UTC. The nightly Historical pull
  keeps its query, its 23-hour gate and its endpoint unchanged.
- Unattended agents never set or change Space-Track credentials, never trigger a live pull and
  never move the top-up cursor. Development and tests use recorded fixtures only. The first
  live pull is the nightly's, or a manual run with Vib present.
- Predictions never write to identity: no claim rows, no precedence entry, no change to
  `satellite.decay_date`, `v_spacetrack_decay_actual` or the Conflicts counts.
- Store what is downloaded and never re-download it (Space-Track's guidance): every run's rows
  stay in the raw table, under the raw-retention rules that already apply to raw tables.
- No box work from 07:05 to 07:30 or from 19:05 to 19:30 UTC; never print credential values.
- Public copy in full sentences with no en or em dashes, and it says that the dates are
  predictions, whose source is Space-Track, and how old the prediction is.

## Interfaces & dependencies
- Migration (next free number): `raw_spacetrack_decay_prediction`, the same columns as
  `raw_spacetrack_decay` (norad_cat_id, object_name, intldes, country, msg_epoch, decay_epoch,
  decay_date, source, msg_type, precedence, payload, ingest_run_id, loaded_at), with indexes on
  ingest_run_id and norad_cat_id. A view `v_reentry_prediction_latest`: from the newest OK
  `decay_60day` run, one row per NORAD (lowest PRECEDENCE, then newest MSG_EPOCH; the glossary
  says lower PRECEDENCE is more recent data).
- `ingest/spacetrack_predictions.py`: `due(conn, now)` (the Wednesday gate), `parse_row`
  (reuses `ingest/spacetrack_decay.parse_row`), `run(conn, client)` through
  `client.query_ledgered("decay_60day", QUERY)`.
- The nightly: the existing `oei_spacetrack` step calls the predictions pull after the
  Historical pull, so a missing credential stays one clean exit 0 with no request.
- API (`api/routers/reentries.py`, warm-cached): `GET /api/reentries/upcoming` returns
  `{as_of, pulled_at, window_days, predicted_through, rows: [{norad_id, satellite_id|null, name,
  object_type, owner|null, predicted_date, msg_epoch}], counts: {payload, rocket_body, debris,
  unknown, starlink}}`, with rows from today to 60 days ahead, payloads first and then by
  predicted date, and objects whose satellite already has a decay date left out. `as_of` is the
  newest MSG_EPOCH in the set and `pulled_at` the start of the newest OK pull; both are null
  before the first pull.
- Web: a "Reentering soon" panel on the Overview below the latest reentries, listing up to 8
  payloads other than Starlink (every object when there is none), each row linking to its
  resolver page when the satellite is in the graph.
- Build summary line: `reentry predictions: N objects (as of YYYY-MM-DD)`, which starts with
  `!!` when the newest run is more than 9 days old or landed 0 rows. Before any OK run it reads
  "none pulled yet" without an alert, and alerts once a pull has failed.

## Edge cases
- **An empty answer on a Wednesday.** The set normally holds many objects, so 0 rows is landed
  as an OK run (the snapshot is honest) but the summary line alerts.
- **A throttle stub or an HTTP error.** The client already retries and then raises, so the run
  closes `error`; the previous snapshot keeps serving, and the age check alerts after 9 days.
- **A prediction for an object that has since reentered.** The API drops objects whose
  `satellite.decay_date` is set and rows whose predicted date is before today.
- **An object not in the graph yet** (a new NORAD number): shown with Space-Track's name and no
  resolver link.
- **Several messages for one NORAD in a run:** the view keeps one (see Interfaces).
- **DECAY_EPOCH format.** The Historical rows send a date or a date and time;
  `_decay_date` degrades anything else to NULL and keeps the text. A row with a NULL predicted
  date is kept in the table and left off the list.
- **Starlink.** SpaceX deorbits Starlinks on purpose, so they may dominate the list. The panel
  groups them into one line with a count, the same treatment the notable reentries give.

## Acceptance criteria
- [ ] The migration applies on a fresh database and on a copy of production's schema, and adds
  no row to any identity table. `python scripts/migrate.py` exits 0 twice in a row.
- [ ] `pytest tests/test_ingest_spacetrack_predictions.py` passes, covering: the Wednesday
  gate (Tuesday no, Wednesday 16:59 no, Wednesday 19:10 yes, Thursday 07:10 yes when Wednesday
  failed, no when it succeeded), the exact query string, parsing from a recorded fixture, the
  endpoint name `decay_60day`, and a 0-row answer landing OK.
- [ ] A test proves that a `decay_60day` run leaves the claims feed and
  `v_spacetrack_decay_actual` byte-identical (rows before and after compared).
- [ ] `pytest tests/test_api_reentries.py` passes: the 60-day window, the drop of decayed
  satellites and past dates, payloads first, and graph-less objects without a satellite id.
- [ ] The build summary prints `reentry predictions:` and starts it with `!!` for a run older
  than 9 days or with 0 rows (`pytest tests/test_build_graph_summary.py -k reentry`).
- [ ] Full suite, ruff, the web build and the dash check are clean; CI is green on the merge.
- [ ] On production after the first Wednesday 19:10 UTC run: one OK `decay_60day` run, the
  summary line without `!!`, the Historical pull and the claims counts unchanged from the night
  before, and `/api/reentries/upcoming` answering 200 in under 1 s.

## Open questions
- CLOSED 2026-11-22 (see the decision log): the shared-allowance question, the placement and
  TIP. If Space-Track ever says the 60-day query does count against DECAY's one a day, the
  Wednesday predictions pull moves to Thursday 07:10 and the Historical pull of that morning
  is skipped, so no UTC day holds two DECAY queries.
- (Vib) **Phase 2, TIP:** a daily TIP pull (`INSERT_EPOCH>now-1`, well inside one an hour)
  would give the final reentry windows and locations for the objects coming down within days.
- (Claude) The size of the 60-day set and the exact DECAY_EPOCH format, from the first live
  pull; the estimates above are recorded as such until then.

## Decision log & lessons learned
- 2026-11-19 (Claude): written after Vib's go. The July `gp_history` gap backfill stays undone
  at Vib's OK: nothing on the sites reads that gap, and it would be about 500 live requests.
- 2026-11-19 (Claude): the top-up spec said TIP messages are "in the same DECAY class". The
  public documentation shows a separate TIP class with its own hourly limit; the 60-day
  predictions are the ones inside DECAY.
