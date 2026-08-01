# Tenancy: slots are not spacecraft

**Status:** accepted, ready to build
**Author:** OEI identity layer
**Date:** 2026-07-27
**Supersedes:** the ad-hoc jcat-to-COSPAR join switch shipped in `identity/bus.py` earlier today

---

## 1. The problem, starting with the one that is live

This morning I changed `identity/bus.py` to attach bus attribution by COSPAR piece instead of by
GCAT `jcat`, on the belief that the piece designation was the stable key and `jcat` was churning.
That change is in production and it fixed nothing, because the premise was wrong in a way that
matters.

Measured on the live database, comparing GCAT snapshot run 3123 (Jul 21) to run 3330 (Jul 27):

| Question | Answer |
|---|---|
| Pieces that changed which `jcat` they carry | **0** of 69,998 |
| `jcat`s that changed which piece they carry | **1** of 69,999 |
| `jcat`s that changed which NORAD id they carry (both non-null) | **0** |
| Payload rows that kept both keys and changed name, owner, manufacturer and bus together | **40** |
| Of those 40, how many are on launch 2026-156 (Transporter-17) | **40** |
| Of those 40, how many had a NORAD id at the previous run | **0** |

Neither key moved. The *occupant* moved. `jcat` and COSPAR piece are perfectly stable **addresses**
whose tenants get reassigned as GCAT works out which object is which. Switching between two stable
addresses could not possibly have helped, and the invariant the new test
`test_attribution_agrees_with_piece_crosswalk` encodes ("every attributed row must sit on the
satellite its COSPAR piece points to") is not a correctness property at all. It is a tautology
about a join that is now pointing at the wrong physical object with full confidence.

The last row of that table is the finding the whole design rests on, and no proposal found it:
**every churned row lacked a NORAD id at the previous snapshot, and not one NORAD-bearing row
churned.** Volatility is not a spectrum requiring a hand-tuned trust ladder. It is an observable
binary condition with a name: whether the row has yet acquired a permanent anchor.

The damage is real but small and precisely bounded, which is what makes it fixable this week:

- **70,196 satellites exist; exactly 73 lack a NORAD id, and all 73 are from launch 2026-156.**
  The proposals variously claimed 629 and 631 here. Those are counts of *GCAT rows*, not of
  *satellite entities*, and the distinction is the whole ballgame: the identifier we mint is
  written against satellite rows.
- **Launch 2026-156 is stored as 148 satellite rows for 73 payloads.** `identity/match.py::_cospar_pass`
  created a shell per COSPAR piece on Jul 8, then `_deterministic_satcat` created a second,
  NORAD-bearing row per piece on Jul 21 when Space-Track catalogued them. Nothing ever folded them
  together, because `identity/merge.py::merge` is dead code the pipeline has never called.
- **`merge_log` holds 420,048 rows of which 0 are real merges** (`surviving_id <> merged_id`), and
  **`satellite_identifier.valid_from` / `valid_to` are NULL on all 420,048 rows.** The identity
  layer can assert but has never once retracted or reconciled. That is why churn accretes instead
  of resolving: 190 COSPAR values and 86 `gcat_id` values now fan out to more than one satellite.
- Satellite 138949 is a slot, not an object. It was named `Posidonia`, then `Kostka`, now `Tyvak`,
  and it carries four different GCAT owner assertions (`ESIBG/UIB`, `BRNO`, `APEX`, `FLEET`). Its
  `gold_case` row, its status history and its assertions belong to at least three physical
  spacecraft.

Four further failure modes were established and remain in scope, but they are compounding costs
rather than live bugs, and the evidence resizes two of them substantially:

- **FM2, split org namespace.** Real, but 85% already solved by accident. **759 of the 1,126
  distinct manufacturer codes in `satellite_bus`, covering 23,801 of 27,843 rows, already resolve
  to an existing operator through `operator_alias`.** `APEX` is already `operator_id 1750,
  "Apex Space", commercial`. `SPXS` and `SPX` **both** already map to `operator_id 1`, which means
  `ROLLUP_OVERRIDES = {"SPXS": "SPX"}` is a hand-written duplicate of curation the seed already
  holds, and also that modelling it as a parent relationship edge would create a self-loop.
- **FM3, bus model identity.** Overstated by roughly 24x. Only **67 of 1,616 slugs have more than
  one raw spelling**, and `mode()` collapses them to **0** slugs with conflicting display names.
  The headline example does not exist: the catalog has one slug `aries`, one spelling `Aries`,
  2 rows. There is no `Aries-100` and no `ARIES SN`. The genuine gap is *cross-slug* reconciliation
  (`pocketqube-1p` at 22 rows versus `pocketqub-1p` at 3) and product-family rollup (`hs-702`,
  `bss-702`, `bss-702hp`, `702x`), which `mode()` structurally cannot do because they are different
  slugs.
- **FM4, spacecraft versus hosted payload.** Nothing we ingest distinguishes them. Verified:
  **0 `jcat`s in `raw_gcat_psatcat` fall outside `raw_gcat_satcat`**, so no source in the database
  contains a hosted payload as a distinct row and nothing is being miscounted today.
- **FM5, legitimate upstream absence.** GCAT documents that it records owner/operator and prime
  contractors, not subsystem contractors. This is a coverage limit to measure and disclose, not a
  bug to fix.

---

## 2. The architecture, and the honest reason it won

### The commitment

**A spacecraft's identity is anchored by a permanent identifier from the source that mints it.
Until that anchor exists, the catalog row is a provisional occupant of a slot, every attribute read
through that slot is a dated observation rather than an identity, and the binding is expirable.**

Three proposals were on the table. This one is Tenancy's spine, because Tenancy's central diagnosis
is the only one that survived measurement, with four corrections that change its shape
substantially and one inversion of its shipping order.

### Why Tenancy, stated as what the other two got wrong

**Keyring** (one entity namespace, typed keys, nightly restatement) is unbuildable as specified,
not merely expensive. Its Phase 2 turns `operator`, `operator_alias` and `operator_relationship`
into compatibility views, but `scripts/build_graph.py::seed_operators` and
`identity/enrich_operators.py` both write them with `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`,
and PostgreSQL supports neither `ON CONFLICT` on a view nor auto-update through a view with a
correlated subquery in its target list. Six production and test write sites already do the thing
the proposal files as a future risk. Its Phase 4 promotion pass calls `merge()`, which raises a
foreign key violation on all 73 of its intended targets, because `merge()` repoints four child
tables and there are six: `satellite_bus` (migration 0009) and `gold_case` (0007/0008) postdate it,
and **all 73 NORAD-less satellites have a row in each**. And its `entity_curated_name_uq` unique
index on `lower(canonical_name)` collides on day one with GCAT's own registry, which carries
6 name groups among the codes it would auto-create, including `('blue canyon', ['RAYBL','BLCAN'])`,
the proposal's own headline win.

**Late Binding** (evidence ledger, precedence as data, resolution as a cached function) has the most
attractive theory and the most decisive refutation. Its generic resolver is argmax over source rank.
Two of the five live resolvers are **folds over precedence with a per-attribute validity
predicate**: `identity/resolve.py::_resolve_status` skips any source whose value maps to canonical
`UNKNOWN` and keeps looking (that is what the `precedence.yml` comment "falls through for live ops"
means), and `_resolve_owner` falls through when the value misses the `operator_alias` map. A
rank-only engine cannot express either, and a critic measured **50.3% of satellites getting a
different status** under it. Separately its centerpiece statement does not compile:
`count(DISTINCT value) OVER (PARTITION BY ...)` raises `FeatureNotSupported: DISTINCT is not
implemented for window functions`, and `contested` is a schema column, an index, an API field and a
confidence multiplier. Its `object_anchor` also cannot do the job it was built for, because it
cannot bridge GCAT to SATCAT: **52 of the 75 SATCAT rows on launch 2026-156 are named
`TRANSPORTER-17 OBJECT <piece>`**, a re-encoding of the volatile piece letter carrying zero
independent identity signal.

Tenancy wins because it extends a mature, load-bearing layer instead of replacing it, and because
after correction its footprint is small enough to review honestly.

### What Tenancy got wrong, and what we changed

1. **Its migration does not apply.** `oei_launch_key` uses
   `substring(piece from '^([0-9]{4})[- ]?([0-9]{1,3})')`, and PostgreSQL's `substring(text FROM
   pattern)` returns only the **first** capture group when the pattern has capture groups. Verified
   on the target server: it returns `'2026'`, not `'2026-156'`. Every payload key would have
   collapsed to a calendar year. Separately, `ADD COLUMN join_rule TEXT NOT NULL DEFAULT 'gcat_id'`
   backfills a value that its own companion `CHECK` constraint does not permit, so the migration
   aborts and, because `scripts/migrate.py` sends the file in one `execute()`, rolls back entirely
   and never records itself. **Fixed** with `regexp_match` and zero-padding (verified:
   `2026-156BH -> 2026-156`, `2026-56A -> 2026-056`), and by removing the contradictory default.

2. **Its identity handle is minted onto the slot it is meant to escape.** `payload_key` was to
   attach to "whichever satellite the deterministic passes already resolved," and for a NORAD-less
   GCAT row the only pass that resolves it is `_cospar_pass`, which keys on piece. The names on
   2026-156 moved as a near-permutation across slots (`AB`: Tyvak to GRUS-3F; `AQ`: Posidonia to
   Tyvak; `AX`: FossaSat-2E to TOM-2). Keying by name while anchoring by slot under a permutation
   produces systematically wrong assignments minted at confidence 1.00. **Fixed** by dropping the
   derived key from the identity path entirely (see item 4).

3. **It ships the actual repair switched off.** Its step 4b merges the duplicate satellite rows and
   is `promote_provisional: false` by default. That is the disease; the rest is relabelling.
   **Fixed** by promoting reconciliation to Phase 1 and turning it on.

4. **Its confidence model is six hand-chosen constants.** Its own author conceded the 2026-156
   evidence supports the ordering and not the magnitudes. The measurement above dissolves the
   problem: **churn is perfectly predicted by absence of a permanent anchor** (40 of 40 churned rows
   had no NORAD; 0 NORAD-bearing rows churned). We therefore publish **no invented probability at
   all**. We publish a categorical, auditable `join_rule`, a boolean `key_churn_observed`, and a
   measured churn rate with its denominator. See section 4.5.

### What we took from the losing proposals, and why

- **From Keyring, the churn detector's framing:** absence is not the signal, referent change is.
  Verified: **0 `gcat_id`s vanish between snapshots**, because GCAT never deletes a `jcat`, it
  reassigns its meaning. Comparing the asserted, normalized name under a key across consecutive
  snapshots is the only signal that fires. This is the sharpest single idea in any of the three and
  we adopt it verbatim.
- **From Keyring, the promotion and reconciliation rules,** and the observation that
  `identity/merge.py` is correct-but-uncalled machinery. That is Phase 1.
- **From Keyring, the event ledger** (`entity_event`), reshaped into `identity_event` so that no
  retraction, expiry or merge is ever silent.
- **From Late Binding, trust as a measured property of a key rather than an assumed one,** and the
  insight that the correction channel should be one more `source_assertion` row at rank 0 with no
  code path of its own. We adopt both, but we scope stability **per (source, id_type)** as a critic
  correctly demanded, because the data insists on it: **SATCAT's `norad -> object_id` binding changed
  0 times in 70,006 objects across the full retained history, while GCAT's `jcat -> norad` binding
  changed 10 times over the same window.** A globally-typed key cannot express that.
- **From Late Binding, the coverage table** as the honest treatment of FM5.
- **From the critiques, four hard constraints** that shape the DDL: no capture groups in
  `substring`, no `DISTINCT` in a window function, no unique index on org display names, and no
  scalar `gcat_org_code` column (**1,322 operators already carry more than one GCAT code**, so a
  scalar column with a unique index would either violate on seed or fork those companies back apart).

---

## 3. Schema

Two additive migrations. Nothing is dropped, nothing is retyped, every new column either is
nullable or carries a default that satisfies its own constraints. Every existing view therefore
survives `CREATE OR REPLACE`, which in PostgreSQL permits appending columns only.

### 3.1 `db/migrations/0010_tenancy.sql`

```sql
-- ---------------------------------------------------------------------------
-- Shared key derivation, defined once in SQL so set-based joins can use it.
-- NOTE: substring(text FROM pattern) returns only the FIRST capture group when
-- the pattern has capture groups, which silently reduces a launch key to a
-- calendar year. regexp_match returns the whole array, so we use that and
-- zero-pad the launch number: '2026-156BH' -> '2026-156', '2026-56A' -> '2026-056'.
-- ---------------------------------------------------------------------------
CREATE FUNCTION oei_launch_key(piece text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
SELECT CASE
  WHEN m IS NULL THEN NULL
  ELSE m[1] || '-' || lpad(m[2], 3, '0')
END
FROM (SELECT regexp_match(btrim(COALESCE($1, '')), '^([0-9]{4})[- ]?([0-9]{1,3})') AS m) t
$f$;

-- Byte-identical to identity.normalize.norm_name over all 27,853 payload rows of
-- run 3330 (0 mismatches). tests/test_identity_keys.py re-runs that corpus
-- comparison on every CI pass so the two definitions cannot drift apart.
CREATE FUNCTION oei_name_key(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
SELECT NULLIF(regexp_replace(
  btrim(regexp_replace(regexp_replace(regexp_replace(
    regexp_replace(lower(COALESCE($1, '')), '[\(\[\{][^\)\]\}]*[\)\]\}]', ' ', 'g'),
    '[^[:alnum:][:space:]_]+', ' ', 'g'),
    '([[:alpha:]])([[:digit:]])', '\1 \2', 'g'),
    '([[:digit:]])([[:alpha:]])', '\1 \2', 'g')),
  '[[:space:]]+', ' ', 'g'), '')
$f$;

-- ---------------------------------------------------------------------------
-- Measured key stability, scoped per (source, id_type). NOT a hand-tuned ladder:
-- every column here is an observation with a denominator. Rebuilt set-based each
-- run from the retained snapshots.
--
-- Grounding at time of writing (GCAT runs 3123 -> 3330, SATCAT runs 3122 -> 3329):
--   satcat / norad    : 0 referent changes in 70,006 observations
--   gcat  / gcat_id   : 40 referent changes in 27,852 payload observations,
--                       ALL 40 on rows that had no NORAD id at the prior run
--   gcat  / cospar    : same 40 rows, same condition
-- ---------------------------------------------------------------------------
CREATE TABLE key_stability (
    source              TEXT   NOT NULL,
    id_type             TEXT   NOT NULL,
    observations        BIGINT NOT NULL,   -- keys present in both compared snapshots
    referent_changes    BIGINT NOT NULL,   -- of those, how many changed occupant
    changes_anchored    BIGINT NOT NULL,   -- of those, how many were on anchored rows
    prev_run_id         BIGINT NOT NULL REFERENCES ingest_run,
    curr_run_id         BIGINT NOT NULL REFERENCES ingest_run,
    measured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, id_type, prev_run_id, curr_run_id)
);

-- ---------------------------------------------------------------------------
-- The churn ledger. One row per observed reassignment. Absence is never the
-- signal (0 gcat_ids vanish between snapshots); referent change is.
-- ---------------------------------------------------------------------------
CREATE TABLE catalog_key_churn (
    source        TEXT   NOT NULL,
    id_type       TEXT   NOT NULL,          -- 'gcat_id' | 'cospar'
    id_value      TEXT   NOT NULL,
    prev_run_id   BIGINT NOT NULL REFERENCES ingest_run,
    curr_run_id   BIGINT NOT NULL REFERENCES ingest_run,
    prev_name     TEXT,
    curr_name     TEXT,
    prev_name_key TEXT,
    curr_name_key TEXT,
    prev_owner    TEXT,
    curr_owner    TEXT,
    prev_anchored BOOLEAN NOT NULL,         -- did the row carry a NORAD at prev_run?
    launch_key    TEXT,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, id_type, id_value, prev_run_id, curr_run_id)
);
CREATE INDEX catalog_key_churn_key_idx ON catalog_key_churn (id_type, id_value);
CREATE INDEX catalog_key_churn_launch_idx ON catalog_key_churn (launch_key);

-- ---------------------------------------------------------------------------
-- No silent identity write, ever. Generalizes merge_log's contract to expiry,
-- retraction and reconciliation. merge_log stays exactly as it is; this records
-- the events merge_log has no vocabulary for.
-- ---------------------------------------------------------------------------
CREATE TABLE identity_event (
    identity_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    satellite_id      BIGINT,                        -- deliberately no FK: survives a merge delete
    event             TEXT   NOT NULL,
    rule_fired        TEXT   NOT NULL,
    ingest_run_id     BIGINT REFERENCES ingest_run,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    details           JSONB,
    CONSTRAINT identity_event_event_ck CHECK (event IN (
        'key_churn_observed', 'identifier_expired', 'provisional_promoted',
        'promotion_declined', 'occupancy_recorded'))
);
CREATE INDEX identity_event_sat_idx ON identity_event (satellite_id, at DESC);
CREATE INDEX identity_event_event_idx ON identity_event (event, at DESC);

-- ---------------------------------------------------------------------------
-- Make satellite_identifier.valid_to load-bearing. The column has existed since
-- 0004 and nothing has ever written it, which is exactly why 86 gcat_id values
-- and 190 cospar values currently fan out to 2-3 satellites each.
-- ---------------------------------------------------------------------------
CREATE INDEX satellite_identifier_current_idx
    ON satellite_identifier (id_type, id_value, source)
    WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------------
-- Anchor state on the satellite itself. This is the FM1 predicate: a satellite
-- is provisional exactly while it has no permanent identifier from a minting
-- authority. Backfilled in this migration; maintained by match.py thereafter.
--
-- Default 'provisional' is inside the CHECK list, unlike the proposal's
-- join_rule default which was not, and which aborted its own migration.
-- ---------------------------------------------------------------------------
ALTER TABLE satellite
    ADD COLUMN anchor_state TEXT NOT NULL DEFAULT 'provisional',
    ADD COLUMN anchor_source TEXT;
ALTER TABLE satellite ADD CONSTRAINT satellite_anchor_state_ck
    CHECK (anchor_state IN ('anchored', 'provisional'));

UPDATE satellite SET anchor_state = 'anchored', anchor_source = 'satcat'
WHERE norad_id IS NOT NULL;

CREATE INDEX satellite_provisional_idx ON satellite (anchor_state)
    WHERE anchor_state = 'provisional';
```

### 3.2 `db/migrations/0011_bus_attribution.sql`

```sql
-- ---------------------------------------------------------------------------
-- Bus model as an entity. Scoped honestly: mode() already collapses in-slug
-- spelling variance to 0 conflicts across all 1,616 slugs, so this table exists
-- for the two things mode() structurally CANNOT do:
--   (a) cross-slug aliasing  (pocketqube-1p 22 rows vs pocketqub-1p 3 rows)
--   (b) product-family rollup (hs-702, bss-702, bss-702hp, 702x, ...)
-- These are different mechanisms and the seed file must not conflate them:
-- an ALIAS asserts "same model", a FAMILY asserts "related models".
-- ---------------------------------------------------------------------------
CREATE TABLE bus_model (
    bus_model_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug                TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    family_slug         TEXT,
    builder_operator_id BIGINT REFERENCES operator,
    origin              TEXT NOT NULL DEFAULT 'discovered',
    notes               TEXT,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bus_model_origin_ck CHECK (origin IN ('seed', 'discovered', 'operator_confirmed'))
);
CREATE INDEX bus_model_family_idx ON bus_model (family_slug);

-- One raw slug resolves to exactly one model. This is the invariant mode()
-- enforces by accident within a slug; here it is a constraint across slugs, and
-- the seed loader fails loudly on a curated collision.
CREATE TABLE bus_model_alias (
    alias_slug   TEXT   PRIMARY KEY,
    bus_model_id BIGINT NOT NULL REFERENCES bus_model,
    alias_raw    TEXT,
    source       TEXT   NOT NULL,
    CONSTRAINT bus_model_alias_source_ck
        CHECK (source IN ('seed', 'gcat', 'operator_confirmed'))
);
CREATE INDEX bus_model_alias_model_idx ON bus_model_alias (bus_model_id);

-- ---------------------------------------------------------------------------
-- satellite_bus gains join provenance, org linkage and the FM4 ontology slot.
-- Every default satisfies its own CHECK.
-- ---------------------------------------------------------------------------
ALTER TABLE satellite_bus
    ADD COLUMN bus_model_id                   BIGINT REFERENCES bus_model,
    ADD COLUMN bus_family_slug                TEXT,
    ADD COLUMN manufacturer_operator_id       BIGINT REFERENCES operator,
    ADD COLUMN manufacturer_group_operator_id BIGINT REFERENCES operator,
    ADD COLUMN join_rule                      TEXT NOT NULL DEFAULT 'anchored_norad',
    ADD COLUMN key_churn_observed             BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN attribution_kind               TEXT NOT NULL DEFAULT 'spacecraft',
    ADD COLUMN hosted_on_satellite_id         BIGINT REFERENCES satellite,
    ADD COLUMN gcat_program                   TEXT;

ALTER TABLE satellite_bus
    ADD CONSTRAINT satellite_bus_join_rule_ck CHECK (join_rule IN (
        'anchored_norad',      -- satellite has a permanent anchor; key cannot move under it
        'anchored_cospar',     -- anchored satellite reached via COSPAR crosswalk
        'provisional_slot',    -- satellite has no anchor: occupancy is a dated observation
        'operator_confirmed')),
    ADD CONSTRAINT satellite_bus_attribution_kind_ck
        CHECK (attribution_kind IN ('spacecraft', 'hosted_payload'));

CREATE INDEX satellite_bus_model_idx ON satellite_bus (bus_model_id);
CREATE INDEX satellite_bus_mfr_op_idx ON satellite_bus (manufacturer_operator_id);
CREATE INDEX satellite_bus_join_rule_idx ON satellite_bus (join_rule);

-- ---------------------------------------------------------------------------
-- FM5 measured and published rather than hidden.
-- ---------------------------------------------------------------------------
CREATE TABLE attribution_coverage (
    measured_on               DATE NOT NULL,
    cohort                    TEXT NOT NULL,   -- 'all' | 'ex_spacex' | a manufacturer slug
    payloads                  INT  NOT NULL,
    builder_named             INT  NOT NULL,
    builder_is_owner          INT  NOT NULL,
    bus_named_builder_missing INT  NOT NULL,
    provisional_rows          INT  NOT NULL,
    methodology_version       TEXT NOT NULL,
    PRIMARY KEY (measured_on, cohort)
);

-- ---------------------------------------------------------------------------
-- Published slug continuity. Manufacturer slugs are currently slugified GCAT
-- group codes ('spx', 'yuzh', 'npopm') and 2,653 July snapshot rows plus every
-- /api/buses/{slug} URL are keyed on them. Routing manufacturers through the
-- operator graph moves some of those slugs, which would silently 404 the
-- published series. Any slug change MUST write a row here first.
-- ---------------------------------------------------------------------------
CREATE TABLE benchmark_slug_alias (
    kind       TEXT NOT NULL,          -- 'manufacturer' | 'bus'
    old_slug   TEXT NOT NULL,
    new_slug   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    retired_at DATE NOT NULL DEFAULT current_date,
    PRIMARY KEY (kind, old_slug)
);
CREATE INDEX benchmark_slug_alias_new_idx ON benchmark_slug_alias (kind, new_slug);
```

Note what is **not** here. There is no `payload_key` identifier type, no `entity` table, no
`operator.gcat_org_code` column, no `resolved_attribute` cache, no per-row confidence float, and no
compatibility views. Each was in one of the proposals and each was removed for a reason stated in
section 2 or section 8.

---

## 4. Resolution algorithm

Six phases. Every one is either a single set-based statement or a bounded loop over the 73-row
provisional cohort. Nothing loops per row over the 27,853-payload catalog or the 420,048-row
identifier table.

### 4.1 Phase R: reconciliation (`identity/reconcile.py::promote`)

This is the Phase 1 fix and it runs first, inside `scripts/build_graph.py`'s existing single
transaction, immediately after `match.deterministic`.

Candidate pairs are provisional satellites that share a COSPAR identifier and a launch date with
exactly one anchored satellite:

```sql
SELECT p.satellite_id AS provisional_id,
       min(n.satellite_id) AS anchored_id,
       count(DISTINCT n.satellite_id) AS candidates,
       max(p.canonical_name) AS prov_name,
       max(n.canonical_name) AS anch_name
FROM satellite p
JOIN satellite_identifier pi
  ON pi.satellite_id = p.satellite_id AND pi.id_type = 'cospar' AND pi.valid_to IS NULL
JOIN satellite_identifier ni
  ON ni.id_type = 'cospar' AND ni.id_value = pi.id_value AND ni.valid_to IS NULL
JOIN satellite n ON n.satellite_id = ni.satellite_id
WHERE p.anchor_state = 'provisional'
  AND n.anchor_state = 'anchored'
  AND p.launch_date IS NOT DISTINCT FROM n.launch_date
GROUP BY p.satellite_id
HAVING count(DISTINCT n.satellite_id) = 1;
```

Measured on the live database right now: **73 pairs, covering all 73 provisional satellites, with
0 provisional satellites having more than one anchored candidate.**

Each pair passes a name gate before merging. Space-Track names fresh rideshare objects
`TRANSPORTER-17 OBJECT <piece>`, which is the volatile piece letter re-encoded and carries no
identity signal, so those are not evidence of agreement or disagreement:

```
placeholder := anchored name matches '(?i)\yOBJECT\y'
gate passes  := placeholder OR oei_name_key(prov_name) = oei_name_key(anch_name)
```

Measured over the 95 candidate pair-rows: **50 placeholder, 45 exact name agreement, 0
disagreements.** A pair that fails the gate is not merged; it writes
`identity_event('promotion_declined')` and lands in `data/review/promotion_review.csv`. Automated
splitting of conflated objects turns a data quality problem into a data destruction problem, so we
never do the inverse operation automatically.

Passing pairs call `merge.merge(surviving=anchored_id, merged=provisional_id,
rule='promotion_cospar_launch', score=1.000)` and write `identity_event('provisional_promoted')`.
The anchored row survives because its identity is pinned by a key that has **never** moved
(SATCAT `norad -> object_id`: 0 changes in 70,006 objects across the full retained history).

**`merge()` must be completed first or every one of these raises a foreign key violation.** It
repoints four child tables; six reference `satellite`. `satellite_bus` and `gold_case` postdate it,
and all 73 targets have a row in each. Section 5 covers the change; section 7 covers the test that
stops this from rotting again.

### 4.2 Phase C: churn detection (`identity/churn.py::detect`)

One statement per key type comparing the two most recent OK GCAT snapshots. Absence is never the
signal, referent change is. The `prev_anchored` column records whether the row carried a NORAD id at
the earlier run, which is what makes `key_stability` a measurement rather than an assumption:

```sql
WITH runs AS (SELECT r.ingest_run_id FROM raw_gcat_satcat r
              JOIN ingest_run i USING (ingest_run_id)
              WHERE i.status = 'ok' GROUP BY 1 ORDER BY 1 DESC LIMIT 2),
     b AS (SELECT max(ingest_run_id) AS curr, min(ingest_run_id) AS prev FROM runs),
     a AS (SELECT jcat, piece, coalesce(pl_name, name) AS nm,
                  oei_name_key(coalesce(pl_name, name)) AS nk, owner,
                  (norad_id IS NOT NULL) AS anchored
           FROM raw_gcat_satcat, b
           WHERE ingest_run_id = b.prev AND object_type LIKE 'P%'),
     z AS (/* identical projection at b.curr */)
INSERT INTO catalog_key_churn (source, id_type, id_value, prev_run_id, curr_run_id,
                               prev_name, curr_name, prev_name_key, curr_name_key,
                               prev_owner, curr_owner, prev_anchored, launch_key)
SELECT 'gcat', 'gcat_id', a.jcat, b.prev, b.curr, a.nm, z.nm, a.nk, z.nk,
       a.owner, z.owner, a.anchored, oei_launch_key(a.piece)
FROM a JOIN z USING (jcat), b WHERE a.nk IS DISTINCT FROM z.nk
UNION ALL
SELECT 'gcat', 'cospar', a.piece, b.prev, b.curr, a.nm, z.nm, a.nk, z.nk,
       a.owner, z.owner, a.anchored, oei_launch_key(a.piece)
FROM a JOIN z USING (piece), b WHERE a.nk IS DISTINCT FROM z.nk
ON CONFLICT DO NOTHING;
```

Measured cost: **0.114 s**, writing 80 rows on today's data. `key_stability` is then one `GROUP BY`
over the same join, recording observations, referent changes and how many of those changes landed
on anchored rows.

### 4.3 Phase E: expire contested identifiers on unanchored satellites

```sql
UPDATE satellite_identifier si
SET valid_to = (SELECT max(started_at)::date FROM ingest_run WHERE status = 'ok')
WHERE si.valid_to IS NULL
  AND si.id_type IN ('gcat_id', 'cospar')
  AND EXISTS (SELECT 1 FROM catalog_key_churn c
              WHERE c.id_type = si.id_type AND c.id_value = si.id_value)
  AND EXISTS (SELECT 1 FROM satellite s
              WHERE s.satellite_id = si.satellite_id AND s.anchor_state = 'provisional');
```

Only identifiers that are of a volatile type, have been **observed** to churn, and point at a
satellite with no permanent anchor are retired. Anchored satellites are never touched, which is the
guard against the false-positive mode a critic correctly identified: legitimate renames such as
`ICEYE-X73` becoming `CA-01` land on anchored objects and must not retract anything.

Expiry is not free unless the readers agree on it. `identity/match.py::_find_by_cospar` currently
queries without a `valid_to` filter, so an expired identifier would keep resolving in the matcher
while disappearing from `satellite_bus`. **Both sides move in the same commit**: `_find_by_cospar`
gains `AND valid_to IS NULL`, and `merge.link` gains a resurrection path that clears `valid_to` when
a live snapshot re-asserts an expired identifier for the same satellite. Every expiry writes
`identity_event('identifier_expired')`.

### 4.4 Phase A: attribution (`identity/bus.py`)

The `cospar_matched` / `jcat_matched` / `linked` CTE stack is replaced by a three-rule resolver.
Rules are first-match-wins, and both existing `DISTINCT ON` dedup layers are **kept verbatim**,
because `satellite_bus.satellite_id` is the primary key and any many-to-one join can violate it:

| # | Join | `join_rule` | Benchmark eligible |
|---|---|---|---|
| 1 | `id_type='norad'`, `valid_to IS NULL`, satellite `anchor_state='anchored'` | `anchored_norad` | yes |
| 2 | `id_type='cospar'`, `valid_to IS NULL`, satellite `anchor_state='anchored'` | `anchored_cospar` | yes |
| 3 | anything resolving only to a satellite with `anchor_state='provisional'` | `provisional_slot` | **yes, flagged** |

`key_churn_observed` is set when a `catalog_key_churn` row exists for the key that produced the
join. A fourth rule, `operator_confirmed`, overlays afterward from `source_assertion`.

**Provisional rows stay on the leaderboard.** This is a deliberate reversal of Tenancy's
`benchmark_eligible` filter and it is the single most consequential product decision in this
document. Under that filter, Apex Space would drop from 5 spacecraft to 2, and VUT Brne, Ethereal,
City Labs, Argo Space and GMV-RO would drop to zero fleet, because **4 of Apex's 5 GCAT rows have no
NORAD id and 3 sit on churned pieces.** Structurally the filter blinds the leaderboard to every
fleet younger than about 30 days (NORAD coverage at 30 days is 223 of 275, or 81.1%), which is the
newest and most commercially interesting cohort and the entire reason this product exists.

The evidence supports the softer treatment. The *name set* within launch 2026-156 is near-stable
(72 of 73 names common across the six-day window, the one delta being a `?` certainty marker that
`oei_name_key` strips anyway), and bus and manufacturer travel with the name. GCAT is uncertain
about **which slot** a spacecraft occupies, not about the fact that Apex built three buses on that
launch. So attribution is confident at the launch and fleet grain and unconfident only at the object
grain, and the honest presentation is to publish the fleet with a per-row flag rather than to
withhold it. `v_bus_sat` exposes `join_rule` and `key_churn_observed`; the leaderboards carry a
`provisional_n` column; `/api/buses` takes an opt-in `?state=anchored` filter that defaults to
including everything.

### 4.5 Confidence: what we publish and what we refuse to publish

All three proposals published a numeric confidence and all three were correctly attacked for it.
Late Binding multiplied three factors into a number with no probabilistic meaning; Keyring
double-counted the same evidence (an entity is provisional *because* its anchor is the 0.80 key,
then multiplied 0.80 by 0.80); Tenancy chose six constants from one launch and conceded the evidence
supported only their ordering. `gold_case` has 159 rows, nowhere near enough to calibrate against.

**We therefore publish no invented probability.** Three fields, each of which is a fact:

1. **`join_rule`**, categorical and auditable. A reader can check it by hand against the crosswalk.
2. **`key_churn_observed`**, boolean, backed by a `catalog_key_churn` row a reader can query.
3. **`key_stability`**, a rate always published with its denominator and its window, for example
   "GCAT `gcat_id`: 40 referent changes in 27,852 observations between runs 3123 and 3330, of which
   0 on rows carrying a NORAD id."

What makes a key trustworthy is therefore not a tuned constant. **A key is trustworthy when the
satellite it resolves to carries a permanent anchor from the source that mints that key type.**
That predicate is binary, observable, and was 100% predictive on the only churn event we have
measured: 40 of 40 churned rows were unanchored, and 0 anchored rows churned.

The existing `satellite_identifier.confidence` and the probabilistic matcher's 0.92 auto-link
threshold are untouched. They are pre-existing, documented, and out of scope; we are declining to
multiply them into new places.

### 4.6 Phase O: org resolution through the operator graph (FM2)

`primary_code` stops joining `raw_gcat_orgs` and joins `operator_alias` instead, resolving to an
`operator_id`. **759 of 1,126 manufacturer codes, covering 23,801 of 27,843 rows, already resolve
this way today.** `enrich_operators.py` is extended to scan the `manufacturer` column (splitting on
`/`) so the remaining 367 codes get entities, and to close the set upward over GCAT parent codes so
rollup targets exist.

Two guards the proposals lacked:

- **`operator_alias.alias` is not unique**: 55 alias strings map to more than one operator, including
  `Boeing` and `Rocket Lab`. The join takes an explicit deterministic tiebreak, `ORDER BY
  (source = 'seed') DESC, operator_id`, and every ambiguous code is written to
  `data/review/ambiguous_org_alias.csv`.
- **The class gate is not swapped blind.** Today's walk gates on GCAT `org_class = 'B'`.
  `operator.operator_class` is NULL on 114 operators and only a subset of B-class parents have
  operator rows, so switching the predicate outright would silently stop rollup for the majority and
  fail closed with no signal. We keep the GCAT `org_class = 'B'` gate as the authority for one
  release, run the `operator_class = 'commercial'` walk alongside it in shadow, and diff them in the
  DQ report before switching. `ROLLUP_OVERRIDES` is deleted in the same commit, because `SPXS` and
  `SPX` both already resolve to `operator_id 1`.

**Dated M&A rollup is explicitly out of scope for this design.** Section 8 explains why.

### 4.7 Phase B: bus model resolution (FM3)

`bus_slug` joins `bus_model_alias` to a `bus_model_id`. Unaliased slugs auto-create a `bus_model`
row with `origin='discovered'` and today's `mode()` display spelling, so all 1,616 slugs work on day
one and curation is purely additive and visible.

`identity/bus_seed.yml` has two strictly separated sections, because conflating them is a
regression. `aliases` asserts same-model and must never merge two slugs differing by a variant
suffix; `families` asserts related-models and drives a separate rollup view:

```yaml
aliases:
  pocketqube-1p:   [pocketqub-1p]        # spelling, same product
  pocketqube-1-5p: [pocketqub-1-5p]
  bss-702:         [hs-702, hs-702-model] # Hughes HS-702 was renamed BSS-702 post-acquisition
families:
  bss-702: [bss-702, bss-702hp, bss-702mp, bss-702mp-plus, bss-702sp, 702x]
```

Note what the family block does **not** do: `BSS-702HP`, `MP`, `SP` and `MP+` remain four distinct
models sharing a family. `identity/bus.py`'s own comment says `'+' is load-bearing in bus names
(BSS-702MP+ is a different variant from BSS-702MP)`, and aliasing those eight slugs into one model
would collapse 65 satellites and destroy a distinction the current code deliberately protects.

### 4.8 Phase X: the correction channel

`operator_confirmed` needs no code path. It is one insert into `source_assertion`, and it wins
because `precedence.yml` puts it first for the `bus` and `manufacturer` attributes:

```sql
INSERT INTO source_assertion (satellite_id, source_key, attribute, value, source,
                              observed_at, ingest_run_id)
VALUES (138966, 'apex-2026-07-27', 'bus', 'Nova', 'operator_confirmed', now(), :run);
```

The overlay sets `join_rule='operator_confirmed'` and appears in receipts with its own provenance,
exactly like a catalog claim, because it is one.

---

## 5. File plan

### New files

| Path | Responsibility |
|---|---|
| `/Users/vgupta/Development/repos/space/db/migrations/0010_tenancy.sql` | Key functions, `key_stability`, `catalog_key_churn`, `identity_event`, `satellite.anchor_state`, the partial current-identifier index. Additive only. |
| `/Users/vgupta/Development/repos/space/db/migrations/0011_bus_attribution.sql` | `bus_model`, `bus_model_alias`, `satellite_bus` provenance columns, `attribution_coverage`, `benchmark_slug_alias`. Additive only. |
| `/Users/vgupta/Development/repos/space/identity/keys.py` | Pure, no DB, mirrors `normalize.py`'s contract. `launch_key(piece)`, `name_key(name)`. Python counterparts to the two SQL functions, used by tests and the corpus equivalence check. About 60 lines. |
| `/Users/vgupta/Development/repos/space/identity/reconcile.py` | `promote(conn, run_id) -> dict` (the Phase 1 fix: candidate pairs, name gate, merge, events, review CSV) and `candidates(conn)` for inspection without mutation. About 150 lines. |
| `/Users/vgupta/Development/repos/space/identity/churn.py` | `detect(conn) -> dict` writes `catalog_key_churn`; `measure_stability(conn) -> dict` writes `key_stability`; `expire_contested(conn, run_id) -> dict` runs Phase E; `churned_keys(conn, id_type) -> set[str]`. About 170 lines. |
| `/Users/vgupta/Development/repos/space/identity/bus_models.py` | `seed(conn, path)` upserts models, aliases and families from YAML, raising on a curated alias collision; `autofill(conn)` creates `origin='discovered'` models for unaliased slugs. About 140 lines. |
| `/Users/vgupta/Development/repos/space/identity/bus_seed.yml` | Curated `aliases` and `families`, strictly separated. Ships with roughly 15 families covering the highest-count slugs. |
| `/Users/vgupta/Development/repos/space/tests/test_identity_keys.py` | Pure unit tests plus the 27,853-row corpus equivalence check between `oei_name_key` and `normalize.norm_name`, and the `oei_launch_key` regression. |
| `/Users/vgupta/Development/repos/space/tests/test_identity_reconcile.py` | Pair selection, name gate, merge completeness, idempotence, the 148-to-75 assertion. |
| `/Users/vgupta/Development/repos/space/tests/test_identity_churn.py` | Detection against fixture snapshots, the absence-does-not-retract assertion, the anchored-rows-never-expire assertion. |
| `/Users/vgupta/Development/repos/space/tests/test_identity_bus_models.py` | Seed loading, alias collision raises, variant-suffix guard, autofill idempotence. |

### Modified files

| Path | Change |
|---|---|
| `identity/merge.py` | **Phase 1, load-bearing.** `merge()` gains `satellite_bus` (PK `satellite_id`, delete-then-repoint) and `gold_case` (delete merged-side rows that would collide, then repoint) handling. Without this every promotion raises a foreign key violation and rolls back the whole night. |
| `identity/match.py` | `_find_by_cospar` gains `AND valid_to IS NULL`; `merge.link` gains identifier resurrection; `_create_satellite` and `_upsert_norad` set `anchor_state` and `anchor_source`. About 40 lines, no restructuring. The probabilistic pass and its scoring are untouched. |
| `identity/bus.py` | Three-rule resolver replaces the `cospar_matched` / `jcat_matched` CTEs, **keeping both `DISTINCT ON` dedup layers**. `ROLLUP_OVERRIDES` deleted. `primary_code` joins `operator_alias` with an explicit tiebreak. `bus_display` feeds `bus_models.autofill`. `raw_gcat_psatcat` joined for `gcat_program`. `METHODOLOGY_VERSION` bumps per phase. |
| `identity/enrich_operators.py` | `_distinct_owner_codes` generalized to `_distinct_org_codes(conn, table, column, split=None)`, called for `owner` and for `manufacturer` split on `/`; parent closure over GCAT rollup targets; ambiguous-alias review CSV. About 90 lines added. |
| `identity/precedence.yml` | Adds `manufacturer: [operator_confirmed, gcat]`, `bus: [operator_confirmed, gcat]`. Flat shape preserved; the file is read as `prec['name']` at four call sites and must not gain a nesting level. |
| `identity/operator_seed.yml` | Manufacturer-side entries for builders with no operator row (Terran Orbital, Blue Canyon, Millennium, and the parent codes closure surfaces). No dated M&A edges: see section 8. |
| `scripts/build_graph.py` | Pipeline becomes `seed -> enrich -> match -> reconcile.promote -> churn.detect -> churn.measure_stability -> churn.expire_contested -> assertions -> resolve`. Summary gains promotion and churn counts. |
| `scripts/build_bus.py` | Calls `bus_models.seed` then `bus_models.autofill` before `bus.build`; writes `attribution_coverage`; prints the join-rule distribution. |
| `metrics/bus_benchmarks.sql` | `v_bus_sat` **appends** `join_rule`, `key_churn_observed`, `bus_model_id`, `bus_family_slug`, `attribution_kind`, `gcat_program`. Both leaderboards gain `provisional_n` and `builder_named_pct`. New `v_bus_benchmarks_family` and `v_bus_attribution_coverage`. Column order and names of existing columns are frozen, because `CREATE OR REPLACE VIEW` permits appending only. |
| `api/routers/buses.py` | `_find_group` consults `benchmark_slug_alias`; detail and provenance expose `join_rule` and `key_churn_observed`; `?state=anchored` opt-in filter; `group=family`; `GET /api/buses/coverage`. |
| `api/routers/conflicts.py` | `GET /api/conflicts/churn` over `catalog_key_churn` joined to `key_stability`. |
| `quality/report.py` | New sections: promotion results, key churn for the last run and trailing 30 days, identifier fan-out (the 190 COSPAR and 86 `gcat_id` values, which must trend to zero), join-rule distribution, `org_class` shadow diff. |
| `web/src/api/types.ts`, `web/src/views/Buses.tsx`, `web/src/api/client.ts`, and the three JSON fixtures | Provisional badge, `provisional_n` column, family grouping, coverage panel. **Explicitly in the diff**: no proposal listed the frontend, which would have shipped the entire FM1 payoff invisible to users. |
| `deploy/nightly-refresh.sh` | Adds `scripts/migrate.py` and `scripts/apply_metrics.py` (both idempotent) before `build_graph`, and replaces the `|| echo` swallowing with a non-zero exit so a failed build is visible instead of silently serving yesterday's `satellite_bus`. |
| `docs/BUS_BENCHMARKS_METHODOLOGY.md` | Version bumps and new sections per section 9. |

Realistic total: roughly 1,400 to 1,800 changed lines across 26 files, of which Phase 1 is
approximately 220 lines across 5 files.

---

## 6. Phased migration

Five phases. Each is independently shippable and leaves the live site serving and all 278 tests
green. Phase 1 is deliberately the smallest change that fixes the live FM1 bug, and it requires
**no new schema at all**.

### Phase 1: reconciliation (this week, no DDL)

Files: `identity/merge.py`, `identity/reconcile.py`, `scripts/build_graph.py`,
`tests/test_identity_reconcile.py`, `tests/test_identity_merge.py`.

1. Complete `merge()` for `satellite_bus` and `gold_case`. This is the prerequisite; without it the
   promotion raises a foreign key violation on all 73 targets, and because
   `scripts/build_graph.py::run_pipeline` has a single `conn.commit()` at the end, the first
   violation rolls back seeding, enrichment, matching, assertions and resolution for that entire
   night, with `deploy/nightly-refresh.sh` swallowing the failure into a log nobody reads.
2. Add `identity/reconcile.py::promote` and call it from `build_graph` after `match.deterministic`.
3. Ship the FK-completeness regression test described in section 7.

Verification, in order, before merge:

All five numbers below were confirmed by running the full phase against the live database inside a
rolled-back transaction, so they are expected values rather than hopes:

- 73 candidate pairs selected, **73 merged, 0 declined** by the name gate.
- `SELECT count(DISTINCT satellite_id) FROM satellite_identifier WHERE id_type='cospar' AND id_value
  LIKE '2026-156%'` goes **148 to 75**.
- `SELECT count(*) FROM satellite WHERE norad_id IS NULL` goes **73 to 0**.
- `SELECT count(*) FROM merge_log WHERE surviving_id <> merged_id` goes **0 to 73**.
- Ambiguous COSPAR values go **190 to 117**; ambiguous `gcat_id` values go **86 to 83**.
- `bus.build()` after the merge returns `attributed = 27843`, `with_bus = 27506`,
  `bus_models = 1616`, `manufacturers = 1037`, all identical to today.

One consequence is worth stating because it reframes the rest of the plan. After Phase 1 the Apex
fleet still reads 5, but its rows move off the provisional slots (138943, 138966, 138967, 138978)
and onto NORAD-anchored satellites (1871484, 1871507, 1871508, 1871519). **The provisional cohort
becomes empty.** Every current leaderboard row would resolve at `anchored_norad`, and the
`provisional_slot` path in Phase 3 exists for the next rideshare, not for today's data. That is the
correct order of operations: fix the accumulated damage first, then build the machinery that stops
it accumulating again.

This is the whole live fix. It uses machinery that already exists and is already correct, it needs
no new tables, and it reverts by reverting one commit.

### Phase 2: migration 0010 and the churn ledger

Apply `0010_tenancy.sql`, ship `identity/keys.py` and `identity/churn.py`, wire detection, stability
measurement and expiry into `build_graph`, add the DQ report sections. Published numbers do not
move, because `satellite_bus` still joins the old way. New tests: about 10, including the
27,853-row corpus equivalence check and the `oei_launch_key` regression.

The one thing to review hardest here is Phase E's expiry, because it is the first write to
`valid_to` in the project's history and the matcher's reader must move in the same commit.

### Phase 3: migration 0011 and the three-rule resolver

Apply `0011_bus_attribution.sql`, replace the join stack, expose `join_rule` and
`key_churn_observed` through `v_bus_sat`, the API and the SPA. **This is the first phase that moves
published numbers**, so before merging: run `bus.build()` on a scratch transaction, diff
`v_bus_benchmarks_manufacturer` and `v_bus_benchmarks_bus` against the committed state, and record
the delta in the changelog. Expected blast radius is bounded and knowable: at most the 40 churned
payload rows change `join_rule`, and roughly 115 satellites stop being ambiguously joined.

Take an explicit out-of-band snapshot first. `bus_benchmark_snapshots` currently holds 2,653 July
rows labelled `methodology_version = '1.0'` while `identity/bus.py` declares `'1.1'`, because the
v1.1 change moved July's numbers and `ON CONFLICT DO NOTHING` kept the frozen row at 1.0. The
published history is **already** mislabelled. Phase 3 fixes the mechanism by keying snapshots on
`(month, methodology_version)` so an intra-month methodology change gets its own frozen point,
and backfills the correct label for July.

### Phase 4: org unification

`enrich_operators` manufacturer scan and parent closure; `bus.py` manufacturer resolution moves to
`operator_alias` with the explicit tiebreak; `ROLLUP_OVERRIDES` deleted; the `operator_class` walk
runs in shadow and is diffed in the DQ report, not switched.

Any manufacturer slug that moves **must** get a `benchmark_slug_alias` row in the same commit, and
the test asserting every slug in `bus_benchmark_snapshots` still resolves through the live views is
the gate. Without it this phase is an unannounced break of every published `/buses/{slug}` URL and
every frozen monthly series.

The one pre-existing test that changes is
`tests/test_bus_build.py::test_manufacturer_rollup_rules_hold`, whose first assertion expects
`[("SPX", "gcat_orgs+override")]`. It becomes `[("SPX", "operator_graph")]` plus a companion
assertion that the `SPXS` to `SpaceX` link is provably sourced from `operator_alias`, which is a
strictly better test because it checks the curation rather than the patch. Its third assertion
filters `rollup_source LIKE 'gcat_orgs%'` and would go **vacuous** under the new vocabulary, passing
trivially with zero rows, so it is re-anchored rather than left to rot.

### Phase 5: bus models, families and coverage

`bus_seed.yml`, `bus_models.py`, family views, `group=family` in the API, `attribution_coverage`
written nightly, `GET /api/buses/coverage`, and the FM4 and FM5 disclosure sections.

Rollback for any phase is straightforward because `satellite_bus` is DELETE-and-rebuild and
`build_graph` is idempotent. Phase 1 is the only phase that deletes satellite rows, which is why it
ships alone, first, with the three verification queries above run by hand before the commit lands.

---

## 7. Test strategy

New tests are organized around the specific ways the critiques showed each proposal could fail
silently. Silence is the enemy: every one of these guards a failure mode that would otherwise
produce a plausible-looking wrong number.

**Phase 1, reconciliation.**

- `test_merge_handles_every_fk_referencing_satellite` queries `information_schema` for every foreign
  key referencing `satellite` and **fails on any table `merge()` does not repoint**. This is the
  test that matters most in the whole suite, because it is what stops `merge()` from silently
  rotting again the next time someone adds a table. It would fail today on `satellite_bus` and
  `gold_case`.
- `test_promotion_collapses_transporter17` asserts the 2026-156 satellite count goes to 75 and that
  no satellite is left with `anchor_state = 'provisional'` and an anchored COSPAR twin.
- `test_promotion_name_gate_declines_disagreement` seeds a pair whose names disagree and asserts no
  merge, one `identity_event('promotion_declined')`, and a review CSV row.
- `test_promotion_is_idempotent` runs `promote` twice and asserts the second run merges zero.
- `test_promotion_declines_ambiguous_candidates` seeds a provisional satellite with two anchored
  candidates and asserts no merge.

**Phase 2, keys and churn.**

- `test_launch_key_has_no_capture_group_bug` asserts `oei_launch_key('2026-156BH') = '2026-156'`,
  the exact bug that would have shipped green because the proposal's own guard only compared
  `oei_name_key`. Plus `'2026-56A' -> '2026-056'` for the padding contract.
- `test_name_key_matches_norm_name_over_corpus` compares `oei_name_key` to
  `normalize.norm_name` over every payload row of the latest snapshot, currently 27,853 rows and
  0 mismatches. This is the only defense against the two definitions drifting.
- `test_absence_alone_does_not_retract` asserts a key missing from the current snapshot writes no
  churn row, because absence never fires (0 `gcat_id`s vanish).
- `test_anchored_satellites_never_expire_identifiers` seeds a rename on a NORAD-bearing object
  (the real `ICEYE-X73` to `CA-01` case) and asserts nothing is expired.
- `test_expired_identifier_not_resolved_by_matcher` asserts `_find_by_cospar` and `bus.py` agree
  after an expiry, which is the reader/writer contract Phase E depends on.

**Phase 3, attribution.**

- `test_attribution_agrees_with_piece_crosswalk` is **deleted and replaced**, not tweaked. It
  asserts COSPAR primacy, which the three-rule resolver deliberately abandons, and its query does
  not filter `valid_to` so expiry does not exempt it. It is replaced by
  `test_join_rule_invariants_hold`, which asserts per rule that the resolved satellite actually
  carries a live identifier of the claimed type, and that `provisional_slot` rows are exactly those
  whose satellite has `anchor_state = 'provisional'`.
- `test_satellite_bus_pk_never_violated` runs a full rebuild and asserts no duplicate
  `satellite_id`, the guard against the many-to-one fan-out that removing the `DISTINCT ON` layers
  would cause.
- `test_apex_fleet_fully_attributed` is **kept and strengthened** from `>= 5` to `== 5` with an
  assertion that at least one row carries `join_rule = 'provisional_slot'`. Under Tenancy's
  `benchmark_eligible` filter this test would have failed at 2, which is how we know the filter was
  wrong.

**Phase 4, orgs.**

- `test_published_slugs_still_resolve` asserts every slug in `bus_benchmark_snapshots` resolves
  through the live views or through `benchmark_slug_alias`. This gates the phase.
- `test_ambiguous_alias_resolution_is_deterministic` runs the manufacturer join twice and asserts
  identical results, given 55 alias strings map to more than one operator.
- `test_rollup_provenance_is_not_vacuous` asserts the re-anchored assertion in
  `test_manufacturer_rollup_rules_hold` matches a non-zero number of rows.

**Phase 5, bus models.**

- `test_seed_never_aliases_variant_suffixes` asserts no seed entry aliases two slugs differing by a
  variant suffix, the guard against collapsing `BSS-702MP` into `BSS-702MP+`.
- `test_alias_collision_raises` asserts the loader fails loudly rather than picking a mode.
- `test_pocketqube_spellings_collapse` asserts `pocketqube-1p` and `pocketqub-1p` resolve to one
  model, and that their family rollup does not also merge `1.5P` into `1P`.

---

## 8. What we deliberately do not do

**We do not build a general entity namespace.** Keyring's `entity` / `entity_key` / `entity_link`
model subsumes all five failure modes uniformly and is probably the right three-month design. It is
the wrong one-week design, and its specific migration is unbuildable: compatibility views cannot
take the `ON CONFLICT DO UPDATE` that six existing write sites depend on, and its shared-surrogate
trick (`ALTER TABLE satellite ALTER COLUMN satellite_id DROP IDENTITY`) breaks eight test insert
sites its own plan does not count. If we ever build it, we build it with `entity` allocating its own
ids and `satellite.entity_id` as a unique foreign key, which is duller, safer, and costs one join.

**We do not make resolution generic.** Late Binding's single precedence engine is elegant and cannot
express what `resolve.py` actually does. `_resolve_status` and `_resolve_owner` are folds with
per-attribute validity predicates, not argmax over rank, and a rank-only engine changes the status
of roughly half the catalog. Making the engine generic requires first modelling validity predicates
as data, which is a real design problem we are not solving this week.

**We do not derive identity from names.** Neither `payload_key` nor `object_anchor` survives
contact with the data. A name-derived key cannot bridge GCAT to SATCAT, because **52 of the 75
SATCAT rows on the very launch that motivated this work are named `TRANSPORTER-17 OBJECT <piece>`**,
which is the volatile piece letter re-encoded. Minting such a key onto a slot-anchored satellite
also reproduces the derangement it was built to defeat, since the names on 2026-156 moved as a
near-permutation across slots. Names are used in exactly one place in this design: as a **gate** on
promotion, where a disagreement blocks a merge. That is a veto, not an anchor, and a veto that fires
wrongly costs us a review-queue row rather than a wrong merge.

**We do not publish a numeric confidence.** Section 4.5 covers this. We have 159 gold cases and no
held-out labelled set, so any composed float would order plausibly and mean nothing, and it would
sit on a public page next to rigorously computed station-keeping standard deviations. Operator
confirmations are the natural source of a labelled set and this design creates the hook; when there
are enough of them we can revisit with something calibrated.

**We do not date manufacturer M&A rollup.** Two independent reasons, either sufficient. First,
**there is no build date in the data.** `launch_date` is all we have, and slicing the Terran
Orbital family's attributed payloads (19 under the Terran code, 37 under its Tyvak subsidiary;
an earlier draft of this paragraph wrongly gave 37 as Terran's own count) on the October 2024
close reassigns roughly 25 to Lockheed, most of which
were built before the deal. Dating the edge does not make the attribution true, it makes a guess
look like a fact and gives it a `valid_from` to cite. Second, **the existing SCD2 resolver is the
wrong shape for it.** `resolve.py::_write_owner` emits two intervals, child then parent, which is
correct for ownership (a satellite really is owned by Eutelsat now) and a category error for
manufacture (nobody becomes the builder of an already-built spacecraft). We will model manufacture
as a point fact when we model it at all, and until then the leaderboard credits the org GCAT names,
with the corporate parent shown as a separate labelled column. `identity/operator_seed.yml` gains
manufacturer entities but no `acquired_by` edges for them.

**We do not fix co-manufacture.** 904 rows (3.2%) carry a `NPOL/KOMET`-style string and first-listed
still wins, exactly as today. The discarded half is precisely the FM5 population (`LMSSD/TERRAN` at
21 rows, `TYVAK/LMSSD` at 12, `KEPLER/UTIAS` at 20). We do not add an `integrator` role, because a
role with no rule that writes it is an empty slot that rots. `manufacturer_codes` already preserves
the full array, and `v_bus_attribution_coverage` will report the co-manufacture rate so the limit is
visible.

**We do not populate `attribution_kind = 'hosted_payload'`.** The column, its CHECK constraint, the
`hosted_on_satellite_id` foreign key and the leaderboard filter all ship, so a future hosted-payload
ingest cannot silently inflate a fleet count even if whoever adds it forgets. But **0 `jcat`s in
`raw_gcat_psatcat` fall outside `raw_gcat_satcat`**, so no source we ingest names a hosted payload
as a distinct row. Every satellite will read `spacecraft` for the foreseeable future. When someone
asks how many payloads Apex has flying, the honest answer stays "5 spacecraft; hosted payloads are
not counted, and here is why." This is a guardrail with no data behind it, and guardrails that never
fire tend to get defaulted around, so the test suite asserts the filter is actually applied.

### The FM5 coverage limit, and how we disclose it

GCAT's own org documentation states it records owner/operator and prime contractors, **not
subsystem level contractors**. When a prime integrates a merchant bus, GCAT's editorial rule puts
the prime in the Manufacturer field. That is not a bug in GCAT and not something we can fix; it is a
coverage limit of our upstream, and the only correct treatment is to measure it and publish it.

We measure four rates nightly into `attribution_coverage`, per manufacturer cohort and overall:
`builder_named` (GCAT names a builder distinct from the owner), `builder_is_owner`,
`bus_named_builder_missing` (the orphan rate: a bus model is recorded but no builder is credited),
and `provisional_rows`. Independent re-derivations put `builder_named` around 71 to 72% excluding
SpaceX, and the orphan rate at 0% across every peer bus builder checked, which says GCAT is in
practice a builder-crediting catalog and this limit bites rarely. Publishing them as a tracked
series rather than a one-off finding means a drift in GCAT's crediting behavior shows up as a trend
instead of a surprise.

The disclosure is a named section in the public methodology, quoting GCAT's statement verbatim
alongside our measured rates, and a `coverage` block in the `/api/buses/methodology` payload. Where
a merchant bus provider is known but not credited by GCAT, the `operator_confirmed` precedence entry
gives it a real path into the record. We make no attempt to infer subsystem attribution we cannot
source, because inventing it would be worse than the gap.

---

## 9. Methodology and public disclosure

`docs/BUS_BENCHMARKS_METHODOLOGY.md` is versioned and served by `GET /api/buses/methodology`, and
`bus_benchmark_snapshots` records the version that produced each frozen number. Three of the five
phases move published numbers, so three get version bumps and changelog entries. Phases 1 and 2 do
not, because reconciliation changes which satellite row an attribution sits on without changing any
attribution, and the churn ledger is observation only.

**Before anything else, fix the mechanism.** July's 2,653 snapshot rows are labelled
`methodology_version = '1.0'` while the code declares `'1.1'`, so the published history already
mislabels itself. Phase 3 re-keys snapshots on `(snapshot_month, kind, slug, methodology_version)`
and backfills July's correct label. A citable scoreboard whose version does not correspond to the
numbers it labels is the exact failure this table exists to prevent.

**v1.2 (Phase 3), "Provisional identity and key churn".** New sections: how a slot differs from a
spacecraft; what `join_rule` and `key_churn_observed` mean and how to check them; the measured
statement that all observed occupant churn to date has been on rows lacking a permanent anchor, with
its denominator; why provisional rows remain on the leaderboard rather than being withheld; and the
explicit refusal to publish a composed confidence score, with the reason. Changelog entry records
the leaderboard diff measured before merge.

**v1.3 (Phase 4), "Manufacturer resolution through the operator graph".** Records that manufacturer
codes now resolve through the same curated alias and relationship machinery as owners, that
`ROLLUP_OVERRIDES` is retired because the seed already asserted it, that ambiguous aliases resolve
by a stated deterministic tiebreak, and that corporate rollup remains **undated** with the reason
from section 8 stated plainly. Any slug change is listed with its `benchmark_slug_alias` mapping so
a reader holding an old URL can follow it.

**v1.4 (Phase 5), "Bus model families, ontology and coverage".** Records the alias-versus-family
distinction and why variant suffixes are never aliased; that `origin='discovered'` models are
uncurated and visibly so; the spacecraft-versus-hosted-payload definition and the honest statement
that no ingested source distinguishes them today; and the FM5 coverage section described above.

**New public surfaces.** `GET /api/buses/coverage` returns the `attribution_coverage` series.
`GET /api/conflicts/churn` returns the churn ledger joined to `key_stability`, which makes "the
catalog changed its mind about this object and here is when" a queryable public fact rather than an
invisible overwrite. The leaderboard carries a `provisional_n` column and a per-row badge. Every one
of these is a receipt, and the point of publishing them is that a number we withhold quietly is
worse than a number we publish with its caveat attached.
