# Spec: Space-Track nightly top-up (gp_history and actual decays)

**Status:** active. Spec drafted 2026-10-16, proceeding; Vib: yes to Space-Track top-up and
decay landing.
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-10-16

## Goal
The 12-month `gp_history` backfill was loaded once from the July seed (production's newest
`spacetrack_gp_history` element set was created 2026-07-08 22:56 UTC), and nothing in the
nightly has used the Space-Track credentials since, so the deep orbital history stopped in
July while CelesTrak kept adding two snapshots a day. This workstream adds one nightly step,
`scripts/spacetrack_topup.py`, that (1) keeps `gp_elements` current with every element set
Space-Track publishes for the benchmark fleet, and (2) lands Space-Track's actual-decay
messages in a raw table so that the resolver can later take `decay_date` from the source that
is authoritative for it. Both parts do nothing, cleanly, until Vib creates the account.

## Architecture decisions
- 2026-10-16 (Claude): **gp_history windows are incremental by CREATION_DATE and never
  overlap.** Each night asks for `CREATION_DATE/<cursor>--<now - 2 h>`, where the cursor
  (`spacetrack_cursor`, migration 0027) is the previous successful night's upper bound. The
  first night, with no cursor, starts 3 days back. Rejected: a rolling window such as
  `EPOCH > now-3d` (the first idea). Because: Space-Track's documentation limits `gp_history`
  to "1 / lifetime" and says "once you download an object's history, you need to store it on
  your own servers; do not download it again"; a rolling window downloads each element set
  three times. `CREATION_DATE` rather than `EPOCH`: it is the field the documentation names for
  "when the ephemerides was published", so a late-published element set with an old epoch is
  still picked up.
- 2026-10-16 (Claude): **Reuse the backfill rather than add a second path.** The top-up calls
  `scripts/backfill_gp_history.py`'s `BENCHMARK_OPERATORS`, `verify_operators`, `fleet_ids`,
  `make_plan`, `run_backfill` and `HourlyPacer`, and the client's `gp_history`, landing through
  `land_gp_history` (`gp_elements`, `source='spacetrack_gp_history'`, `ON CONFLICT (norad_id,
  epoch, source) DO NOTHING`, the backfill's key). The backfill has no datetime-window mode of
  its own (its windows are calendar months and its checkpoint is a file), so the top-up passes
  one datetime window and an in-memory checkpoint. `fleet_ids` gained an optional
  `decayed_after` (objects whose resolved decay date is before the window are skipped; the
  backfill passes nothing and is unchanged).
- 2026-10-16 (Claude): **The cursor advances only when every batch landed**, in the same
  commit as the ledger's 'ok'. A failed night leaves it put, and the next night asks for the
  same window again (rows already landed are skipped by the conflict key). That re-download is
  the one exception to "never twice", and only after a failure. An empty fleet, or a fleet of
  100+ ids with no rows at all, counts as a failure (review I-1, 2026-10-16).
- 2026-10-16 (Claude): **Fleet scope stays the benchmark fleet.** Measured on production
  2026-10-16: SpaceX 10,660, Eutelsat 708, Amazon 392, Planet Labs 130, Iridium 104, Spire
  57, ICEYE 35, Capella Space 9 NORAD ids, which is 126 requests of 100 ids. Rejected: a
  whole-catalog window query. Because: tens of thousands of objects the product does not read,
  and CelesTrak already lands the active catalog twice a day.
- 2026-10-16 (Claude): **Decays: one query a day for `MSG_TYPE` Historical, `MSG_EPOCH` in
  the last 30 days**, landed per run into `raw_spacetrack_decay` (a snapshot table on
  `SNAPSHOT_TABLES`, pruned like every raw_* table). Gate 23 hours, not `runlog.DAILY`'s 20,
  because the DECAY class is limited to 1 / day and a 20-hour gate can land two pulls within
  24 hours. Predictions are not pulled (Open questions).
- 2026-10-16 (Claude): **The resolver is not wired yet; the raw table and
  `v_spacetrack_decay_actual` are.** `identity/precedence.yml` already ranks
  `spacetrack_decay` first for `decay_date`, but the resolver reads claims through
  `v_current_claim`, and a new claim source needs (a) a feed in `identity/claims.py`, (b) a
  branch in `v_claim`'s `id_type` CASE and its `si.source = c.source` join, since Space-Track
  keys are NORAD ids linked under source 'satcat', and (c) one value per key (an object can
  have several Historical messages, and `claim_open_uq` allows one open claim). Injecting the
  view straight into `_resolve_decay_date` would be smaller, but the satellite page would then
  show a decay date that no listed claim makes. Rejected for this branch: both. Because:
  changing `v_claim` touches every claim reader and needs a production plan check, and the
  shortcut breaks "disagreements are data". See the next-step acceptance criterion.
- 2026-10-16 (Claude): **`status` stays as it is.** `status_map.yml` makes GCAT's reentry
  phases authoritative for DECAYED and the status precedence is `[gcat, satcat, ucs]`; SATCAT's
  'D' follows Space-Track within days. A Space-Track status source would also need the
  explicit tiebreak that migration 0025 warns about (`mv_latest_satellite_status` orders by
  source name). Revisit only if the decay_date wiring shows status lagging.

## Constraints
- **Never exceed Space-Track's limits:** under 30 requests a minute and 300 an hour, account
  wide; `gp_history` once per element set; DECAY once a day. The client spaces requests 3 s
  apart (at most 20 a minute), the pacer caps a run at 250 an hour with retries counted, and a
  plan over 200 requests is refused before any request.
- **The gp_history window ends 2 hours before now** (`PUBLICATION_MARGIN`). An element set
  can become queryable after its CREATION_DATE; a window ending at "now" would move the cursor
  past such a set before it was visible, and it would never be fetched. Never shrink the margin
  to zero.
- **A window is only "done" when it could have returned data.** A fleet of zero ids, or a fleet
  of at least 100 ids (`EMPTY_WINDOW_MIN_IDS`) that returns no element set across the whole
  window, is an 'error' ledger row, the cursor stays put and the part exits non-zero.
- **One top-up at a time.** `main()` takes `pg_try_advisory_lock` on a fixed key when the
  credentials are set; a second run prints "another top-up is running; skipping" and exits 0.
- **No credentials, no requests.** Blank or unset `SPACETRACK_IDENTITY` or
  `SPACETRACK_PASSWORD`: one log line, exit 0, no database connection.
- **Never delete `gp_elements` rows.** The top-up only inserts, with `ON CONFLICT DO NOTHING`.
- **`raw_spacetrack_decay` follows docs/specs/raw-retention.md:** it is on `SNAPSHOT_TABLES`
  and its readers take the newest OK run.
- Do not run a manual `backfill_gp_history.py` while the nightly's `oei_spacetrack` step runs:
  the pacers are per process and the limits are per account.
- Unattended agents never set or change the production credentials, never trigger a live
  pull, and never move the cursor on production.

## Interfaces & dependencies
- `scripts/spacetrack_topup.py [--only gp|decay] [--dry-run]`: exit 0 on success or no
  credentials, 1 when either part failed (the other still runs). `--dry-run` prints tonight's
  window and request count and makes no request.
- `deploy/nightly-refresh.sh`: `step oei_spacetrack` runs between `oei_ingest_all` and
  `oei_build_graph`, passing the two variables from `deploy/.env`; failure prints
  `!! oei spacetrack top-up failed`, which ALERT_URL forwards.
- Migration 0027: `spacetrack_cursor`, `raw_spacetrack_decay`, `v_spacetrack_decay_actual`.
- `ingest/spacetrack_client.py`: new `query_ledgered(endpoint, query)`; throttle now runs
  before the pre-request hook (see the decision log).
- `ingest/spacetrack_decay.py`: `run(conn, client)`, `land_rows`, `parse_row`.
- Ledger rows (`ingest_run`, source 'spacetrack'): `gp_history_topup` (one per night, notes
  hold the window and request count), `gp_history` (one per batch, notes hold the URL, as the
  backfill writes), `decay` (one per pull, notes hold the query).
- Deploy: `scripts/` and `ingest/` are baked into the image; `deploy/` is bind-mounted. A
  rebuild (`docker compose up -d --build oei-api`) and `docker compose exec -T oei-api python
  scripts/migrate.py` must come before the first nightly with the new step, or the step
  soft-fails until they do.
- The exact queries (after `https://www.space-track.org`):
  - `/basicspacedata/query/class/gp_history/NORAD_CAT_ID/<100 ids>/CREATION_DATE/<since>--<through>/orderby/CREATION_DATE asc/format/json`,
    with `since` and `through` as `YYYY-MM-DDTHH:MM:SS` (UTC).
  - `/basicspacedata/query/class/decay/MSG_TYPE/Historical/MSG_EPOCH/%3Enow-30/orderby/NORAD_CAT_ID asc/format/json`.

## Edge cases
- **The July-to-October gap.** The first night starts 3 days back, so element sets created
  between 2026-07-08 22:56 and that start are not fetched by the nightly. Filling them is a
  deliberate one-off, outside the nightly windows, before or after the first night:
  `python scripts/backfill_gp_history.py --since 2026-07-08 --until <first night's start date>`
  (4 monthly windows x 126 batches, about 500 requests, about 2 hours at its 260-an-hour
  pace). Day-granular, so its last day overlaps the first nightly window by hours.
- **A gap over 30 days** (the nightly broken for a month) is refused with the backfill command
  and the cursor UPDATE that resumes it, rather than one oversized query.
- **Objects that join the fleet late.** A satellite that enters the graph after its first
  element sets were created (identity resolution lags a launch by a night or two) misses those
  element sets, since the window starts at the cursor. CelesTrak's twice-daily snapshots cover
  them; a periodic backfill run can fill them if it matters.
- **Datetime format in a CREATION_DATE range.** The backfill only ever sent dates. Space-Track
  returns CREATION_DATE as `YYYY-MM-DDTHH:MM:SS`, and the top-up sends the same form; this has
  not been tried live (no account). If the first live night returns an error stub or rejects
  the range, the ledger row is 'error' and the cursor stays put, so nothing is lost.
- **A decay window that excludes an old decay.** Objects that decayed more than 30 days before
  the newest run fall out of `v_spacetrack_decay_actual`. Once the resolver reads it, their
  `decay_date` falls back to SATCAT, which by then carries the date (CelesTrak's SATCAT takes
  decays from the same 18 SDS catalog). The next-step wiring must not treat leaving the window
  as a retraction (see Open questions).
- **Several Historical messages per object.** The view keeps the lowest PRECEDENCE ("lower
  PRECEDENCE values indicate more recent data") and then the newest MSG_EPOCH.
- **HTTP 200 throttle stubs** are already retried and then raised by the client, so a
  throttled night is an error, never a silent empty window.

## Acceptance criteria
- [x] `DATABASE_URL=<migrated scratch db> pytest -q tests/test_spacetrack_topup.py` passes:
  no-op without credentials (four blank/partial combinations plus unset, no connection, no
  request), first window 3 days back then from the cursor, a 30-day gap refused, batching
  150+1 ids into 3 requests carrying the exact CREATION_DATE window, running twice adds no
  rows and the second window starts at the first's end, a second run within the day makes no
  request, a failed batch leaves the cursor put with an 'error' ledger row, an over-budget plan
  makes no request, decay rows land and the view keeps one per object, decay at most once a
  day, and on a fake clock 400 requests never exceed 20 in any minute or 250 in any hour.
- [x] `pytest -q tests/test_ingest_spacetrack.py tests/test_backfill_gp_history.py
  tests/test_prune_snapshots.py tests/test_ingest_gp.py` passes unchanged.
- [x] `bash -n deploy/nightly-refresh.sh` exits 0, and `grep -n "step oei_" deploy/nightly-refresh.sh`
  lists `oei_spacetrack` after `oei_ingest_all` and before `oei_build_graph`.
- [x] `SPACETRACK_IDENTITY= SPACETRACK_PASSWORD= python scripts/spacetrack_topup.py; echo $?`
  prints one line and `0`.
- [ ] Deployed (orchestrator): after the rebuild and migration, with blank credentials,
  `grep -A1 "step oei_spacetrack\|spacetrack_topup" deploy/refresh.log | tail -3` shows the
  "not set; skipping" line and `exit 0`.
- [ ] First live night (after Vib pastes the credentials): `SELECT status, rows_ingested,
  notes FROM ingest_run WHERE source='spacetrack' AND endpoint IN ('gp_history_topup','decay')
  ORDER BY ingest_run_id DESC LIMIT 2` shows two 'ok' rows with rows_ingested > 0, and
  `SELECT through FROM spacetrack_cursor` is within an hour of that night's step.
- [ ] Rate check on the first live night: `SELECT count(*), max(finished_at) - min(started_at)
  FROM ingest_run WHERE source='spacetrack' AND started_at > now() - interval '1 day'` is at
  most 250 requests over at least 3 s each.
- [ ] **Next step, resolver wiring:** `spacetrack_decay` is a claims feed (one value per NORAD
  id from the newest run, read through `v_claim` via the 'satcat' NORAD link), and a db test
  shows a satellite with SATCAT decay 2024-01-01 and a Space-Track actual decay 2024-01-02
  resolving to 2024-01-02, with the Space-Track claim listed on `/api/satellites/<id>`; an
  `EXPLAIN (ANALYZE, BUFFERS)` of `v_current_claim` on production is no slower than before by
  more than 10%.

## Open questions
- (Vib) **Predictions.** TIP messages (1 / hour) and 60-day predictions (1 / week) are in the
  same DECAY class. Worth a separate weekly pull for a "reentering soon" view?
- (Vib) **The July gap backfill** (Edge cases): run it once the account exists?
- (Claude) **Window vs. snapshot for the decay feed.** A 30-day window makes the claims feed
  close a claim when its object ages out. Either the next step pulls every Historical message
  (one daily query of the whole decay history, still within 1 / day) so the feed is a real
  snapshot, or the claims writer learns that a windowed feed's absences are not retractions.
  The first is simpler; it needs one live measurement of the response size.
- (Claude) Space-Track response time for a 1-day window of 100 ids is unknown (the July
  backfill saw 120 s timeouts on month windows). The step log records the step's time on the
  first live night; if it is long, raise the batch size or run the step after the publish
  steps instead.

## Decision log & lessons learned
- 2026-10-16 (Claude): the brief proposed a rolling `EPOCH > now-3d` window; Space-Track's
  documentation (fetched 2026-10-16) limits gp_history to once per element set and DECAY to
  once a day, so the windows became cursor-based and the decay gate 23 hours. Lesson: read the
  provider's per-class limits before choosing a window, not only the global request caps.
- 2026-10-16 (Claude): the rate-limit test on a fake clock found 251 requests in one trailing
  hour under a 250 cap: the client called the pacer's hook before its own 3 s throttle, so a
  send was delayed after the pacer had timestamped it. The throttle now runs first. The July
  backfill ran at 260 an hour, so it never reached Space-Track's 300.
- 2026-10-16 (Claude): production's `spacetrack` ledger rows are all from the July backfill,
  with endpoint `gp_history` or (before the ledger-hygiene change) the full query URL; none use
  `gp_history_topup` or `decay`, so no old row can satisfy the new gates.
- 2026-10-16 (Claude): the Codex verify pass could not run: `codex exec` rejects both the
  configured model and `gpt-5.5` on the ChatGPT login ("model is not supported when using
  Codex with a ChatGPT account"). The branch has had a Claude self-review only; run the verify
  pass after `codex login` is fixed, before merge. The db tests ran on a RAM-backed scratch
  TimescaleDB (127.0.0.1:5446), because the shared scratch on 5439 had crashed when the
  Docker VM's disk filled (58 GB, 100%).
