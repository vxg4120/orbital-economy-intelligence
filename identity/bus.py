"""Bus / manufacturer attribution build (the satellite_bus table).

Resolves each GCAT payload row's Bus and Manufacturer fields into normalized, org-resolved
attribution with explicit provenance, in one set-based rebuild:

* Bus strings are normalized: whitespace collapsed, GCAT's trailing '?' uncertainty marker
  stripped (and recorded in bus_uncertain), placeholder values (UNK, Unknown, ...) dropped,
  and casing variants of the same model collapsed to the most common spelling.
* Manufacturer codes are resolved against the latest raw_gcat_orgs snapshot. Co-manufactured
  objects ("NPOL/KOMET") attribute to the first-listed org (a convention of ours, NOT a GCAT
  semantic: GCAT never documents the slash, and 8 org pairs appear in both orders in the same
  snapshot, so position does not encode primacy); the
  full code list is preserved in manufacturer_codes.
* Parent rollup: the org's Parent chain is followed upward while the parent is a business-class
  org (GCAT Class 'B'), so plant-level subsidiaries roll up to their corporate group
  (BOES -> BOE "Boeing") while state design bureaus do NOT collapse into ministries or space
  agencies (NPO PM stays NPO PM rather than becoming MOM). A tiny curated override map covers
  parent links GCAT leaves blank (SPXS -> SPX: SpaceX's Seattle satellite works is SpaceX).
  The traversed code path and whether an override fired are stored per row.

Rows attach to canonical satellites by COSPAR piece designation first, falling back to the
jcat crosswalk only when the piece has no cospar identifier: GCAT reshuffles provisional jcat
slots between releases on fresh multi-payload launches, so jcat alone mis-joins those rows.

Provenance: every satellite_bus row carries (source='gcat', source_key=jcat, ingest_run_id),
and identity/assertions.py also extracts per-row 'bus' and 'manufacturer' source_assertion
records, so each resolved value is traceable to the raw catalog row that asserted it.

No commit here: the caller owns the transaction (same contract as the rest of identity/).
"""

from __future__ import annotations

# The benchmark methodology version. Bump whenever a metric definition, threshold, inclusion
# rule, or attribution rule changes, together with the Changelog in
# docs/BUS_BENCHMARKS_METHODOLOGY.md. Monthly snapshots record the version that produced them,
# and /api/buses/methodology reports it, so published numbers stay citable.
METHODOLOGY_VERSION = "1.5"
METHODOLOGY_UPDATED = "2026-07-31"

# Curated parent-rollup overrides for org edges GCAT leaves blank. Kept deliberately tiny and
# documented in docs/BUS_BENCHMARKS_METHODOLOGY.md; rows resolved through one of these carry
# rollup_source='gcat_orgs+override' so the curation is visible per satellite.
ROLLUP_OVERRIDES: dict[str, str] = {
    # SpaceX (Seattle) is SpaceX's Starlink manufacturing arm; GCAT has no Parent for it.
    "SPXS": "SPX",
}

# Bus strings that mean "no bus recorded", dropped rather than benchmarked as a model.
_BUS_PLACEHOLDERS = ("unk", "unknown", "tba", "none")

_BUILD_SQL = """
WITH RECURSIVE
gcat_run AS (
    SELECT max(r.ingest_run_id) AS run
    FROM raw_gcat_satcat r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok'
),
orgs_run AS (
    SELECT max(r.ingest_run_id) AS run
    FROM raw_gcat_orgs r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok'
),
orgs AS (
    SELECT code,
           NULLIF(btrim(COALESCE(parent_code, '')), '') AS parent_code,
           org_class,
           NULLIF(btrim(COALESCE(state_code, '')), '') AS state_code,
           COALESCE(NULLIF(btrim(COALESCE(short_name, '')), ''),
                    NULLIF(btrim(COALESCE(e_name, '')), ''),
                    btrim(COALESCE(name, ''))) AS display_name
    FROM raw_gcat_orgs, orgs_run
    WHERE ingest_run_id = orgs_run.run
),
overrides AS (
    SELECT * FROM unnest(%(override_codes)s::text[], %(override_parents)s::text[])
        AS t(code, parent_code)
),
effective AS (
    -- Org edges with curated overrides patched in; overridden marks the patched edges.
    SELECT o.code,
           COALESCE(ov.parent_code, o.parent_code) AS parent_code,
           (ov.code IS NOT NULL) AS overridden
    FROM orgs o
    LEFT JOIN overrides ov ON ov.code = o.code
),
chain AS (
    -- Walk each org's parent chain upward, but only THROUGH business-class ('B') parents:
    -- corporate groups aggregate, ministries and space agencies do not.
    SELECT e.code AS leaf, e.code AS cur, e.parent_code AS next_code,
           e.overridden AS next_edge_override,
           ARRAY[e.code] AS path, FALSE AS used_override, 0 AS depth
    FROM effective e
    UNION ALL
    SELECT c.leaf, p.code, p.parent_code, p.overridden,
           c.path || p.code, c.used_override OR c.next_edge_override, c.depth + 1
    FROM chain c
    JOIN effective p ON p.code = c.next_code
    JOIN orgs po ON po.code = p.code
    WHERE c.next_code IS NOT NULL
      AND po.org_class = 'B'
      AND c.depth < 10
      AND p.code <> ALL(c.path)
),
rollup AS (
    SELECT DISTINCT ON (leaf) leaf, cur AS group_code, path, used_override
    FROM chain
    ORDER BY leaf, depth DESC
),
cleaned AS (
    -- GCAT payload rows from the latest OK snapshot; '-' and '' mean "no value".
    SELECT r.jcat, r.ingest_run_id, r.norad_id,
           NULLIF(btrim(COALESCE(r.piece, '')), '') AS piece,
           NULLIF(NULLIF(btrim(regexp_replace(COALESCE(r.bus, ''), '\\s+', ' ', 'g')),
                         ''), '-') AS bus_raw,
           NULLIF(NULLIF(btrim(regexp_replace(COALESCE(r.manufacturer, ''), '\\s+', ' ', 'g')),
                         ''), '-') AS manufacturer_raw
    FROM raw_gcat_satcat r, gcat_run
    WHERE r.ingest_run_id = gcat_run.run
      AND r.object_type LIKE 'P%%'
),
parsed AS (
    -- GCAT's trailing '?' marks uncertainty (stripped + flagged); a leading apostrophe is a
    -- GCAT name-formatting marker, not part of the model name.
    SELECT *,
           COALESCE(bus_raw LIKE '%%?', FALSE) AS bus_uncertain,
           CASE WHEN lower(NULLIF(ltrim(btrim(rtrim(bus_raw, '?')), ''''), ''))
                     = ANY(%(bus_placeholders)s)
                THEN NULL
                ELSE NULLIF(ltrim(btrim(rtrim(bus_raw, '?')), ''''), '')
           END AS bus_clean,
           -- GCAT marks an uncertain org with a trailing '?', and on a joint build it marks the
           -- individual code rather than the whole string ('RAYM?/GSFC'). Stripping only a
           -- trailing marker therefore leaves 'RAYM?' as a code, which resolves against nothing
           -- and then slugifies to 'raym', colliding with the real RAYM. Strip per code.
           COALESCE(manufacturer_raw LIKE '%%?%%', FALSE) AS manufacturer_uncertain,
           NULLIF(btrim(rtrim(btrim(split_part(manufacturer_raw, '/', 1)), '?')), '')
               AS primary_code,
           ARRAY(
               SELECT NULLIF(btrim(rtrim(btrim(c), '?')), '')
               FROM unnest(string_to_array(manufacturer_raw, '/')) AS c
           ) AS all_codes
    FROM cleaned
),
sluged AS (
    -- Slug key: '+' is load-bearing in bus names (BSS-702MP+ is a different variant from
    -- BSS-702MP), so it becomes '-plus' rather than vanishing with the other punctuation.
    SELECT *,
           NULLIF(btrim(regexp_replace(regexp_replace(lower(COALESCE(bus_clean, '')),
                                                      '\\+', '-plus', 'g'),
                                       '[^a-z0-9]+', '-', 'g'), '-'), '') AS bus_slug
    FROM parsed
),
bus_display AS (
    -- One display spelling per slug key (the most common), so slug <-> model is one-to-one.
    SELECT bus_slug AS bus_key,
           mode() WITHIN GROUP (ORDER BY bus_clean) AS bus_model
    FROM sluged
    WHERE bus_slug IS NOT NULL
    GROUP BY 1
),
resolved AS (
    SELECT p.jcat, p.ingest_run_id, p.norad_id, p.piece,
           p.bus_raw, p.bus_slug, bd.bus_model, p.bus_uncertain,
           p.manufacturer_raw, p.primary_code, p.all_codes, p.manufacturer_uncertain,
           leaf_org.display_name AS manufacturer_org_name,
           COALESCE(ru.group_code, p.primary_code) AS manufacturer_group_code,
           COALESCE(grp_org.display_name, leaf_org.display_name,
                    p.primary_code) AS manufacturer_name,
           grp_org.state_code AS manufacturer_country,
           ru.path AS rollup_path,
           CASE
               WHEN p.primary_code IS NULL THEN NULL
               WHEN leaf_org.code IS NULL THEN 'unresolved'
               WHEN ru.group_code = p.primary_code THEN 'leaf'
               WHEN ru.used_override THEN 'gcat_orgs+override'
               ELSE 'gcat_orgs'
           END AS rollup_source
    FROM sluged p
    LEFT JOIN bus_display bd ON bd.bus_key = p.bus_slug
    LEFT JOIN orgs leaf_org ON leaf_org.code = p.primary_code
    LEFT JOIN rollup ru ON ru.leaf = p.primary_code
    LEFT JOIN orgs grp_org ON grp_org.code = ru.group_code
    WHERE bd.bus_model IS NOT NULL OR p.primary_code IS NOT NULL
),
rule1 AS (
    -- anchored_norad: the raw catalog row itself carries the permanent anchor. The key that
    -- produces this join has never been observed to move (key_stability measures it nightly),
    -- so churn on the row's piece or jcat is irrelevant to the join's integrity.
    SELECT DISTINCT ON (r.jcat) s.satellite_id, r.*,
           'anchored_norad'::text AS join_rule
    FROM resolved r
    JOIN satellite s ON s.norad_id = r.norad_id
    WHERE r.norad_id IS NOT NULL
    ORDER BY r.jcat, s.satellite_id
),
rule2 AS (
    -- anchored_cospar: no anchor on the row yet, but the piece resolves through the live
    -- crosswalk (expired identifiers excluded) to a satellite that IS anchored, typically
    -- because promotion folded the provisional record into its Space-Track twin.
    SELECT DISTINCT ON (r.jcat) si.satellite_id, r.*,
           'anchored_cospar'::text AS join_rule
    FROM resolved r
    JOIN satellite_identifier si
      ON si.id_type = 'cospar' AND si.source = 'gcat'
     AND si.id_value = r.piece AND si.valid_to IS NULL
    JOIN satellite s ON s.satellite_id = si.satellite_id AND s.anchor_state = 'anchored'
    WHERE r.norad_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM rule1 WHERE rule1.jcat = r.jcat)
    ORDER BY r.jcat, si.satellite_id
),
rule3 AS (
    -- Whatever remains: piece first, then jcat, to whatever satellite the crosswalk still
    -- reaches. When that satellite is provisional the row is a dated occupancy observation
    -- (provisional_slot); in the rare jcat-only path to an anchored satellite the honest
    -- label is still anchored_cospar, decided per row from the satellite's anchor state.
    SELECT DISTINCT ON (r.jcat) m.satellite_id, r.*,
           CASE WHEN m.anchor_state = 'anchored'
                THEN 'anchored_cospar' ELSE 'provisional_slot' END::text AS join_rule
    FROM resolved r
    JOIN LATERAL (
        SELECT s.satellite_id, s.anchor_state, 0 AS tier
        FROM satellite_identifier si
        JOIN satellite s ON s.satellite_id = si.satellite_id
        WHERE si.id_type = 'cospar' AND si.source = 'gcat'
          AND si.id_value = r.piece AND si.valid_to IS NULL
        UNION ALL
        SELECT s.satellite_id, s.anchor_state, 1 AS tier
        FROM satellite_identifier si
        JOIN satellite s ON s.satellite_id = si.satellite_id
        WHERE si.id_type = 'gcat_id' AND si.source = 'gcat'
          AND si.id_value = r.jcat AND si.valid_to IS NULL
        ORDER BY tier, satellite_id
        LIMIT 1
    ) m ON TRUE
    WHERE NOT EXISTS (SELECT 1 FROM rule1 WHERE rule1.jcat = r.jcat)
      AND NOT EXISTS (SELECT 1 FROM rule2 WHERE rule2.jcat = r.jcat)
    ORDER BY r.jcat, m.satellite_id
),
matched AS (
    SELECT * FROM rule1 UNION ALL SELECT * FROM rule2 UNION ALL SELECT * FROM rule3
),
linked AS (
    -- A satellite occasionally carries two GCAT rows (merge artifacts): prefer the row with a
    -- bus model, then the certain one, then lowest jcat. Kept verbatim from the previous
    -- resolver because satellite_id is the primary key and any many-to-one join can violate it.
    SELECT DISTINCT ON (satellite_id) *
    FROM matched
    ORDER BY satellite_id, (bus_model IS NULL), manufacturer_uncertain,
             bus_uncertain, jcat
)
INSERT INTO satellite_bus (
    satellite_id, bus_raw, bus_model, bus_slug, bus_uncertain,
    manufacturer_raw, manufacturer_code, manufacturer_codes, manufacturer_uncertain,
    manufacturer_org_name, manufacturer_group_code, manufacturer_name, manufacturer_slug,
    manufacturer_country, rollup_path, rollup_source,
    source, source_key, ingest_run_id, join_rule, key_churn_observed
)
SELECT
    satellite_id, bus_raw, bus_model, bus_slug, bus_uncertain,
    manufacturer_raw, primary_code, all_codes, manufacturer_uncertain,
    manufacturer_org_name, manufacturer_group_code, manufacturer_name,
    NULLIF(btrim(regexp_replace(lower(COALESCE(manufacturer_group_code, '')),
                                '[^a-z0-9]+', '-', 'g'), '-'), '') AS manufacturer_slug,
    manufacturer_country, rollup_path, rollup_source,
    'gcat', jcat, ingest_run_id, join_rule,
    -- Churn is only meaningful for joins that rode a volatile key: an anchored_norad join's
    -- producing key has never been observed to move, so the flag stays false there even when
    -- the row's piece churned before the anchor arrived.
    CASE WHEN join_rule = 'anchored_norad' THEN FALSE
         ELSE EXISTS (SELECT 1 FROM catalog_key_churn c
                      WHERE (c.id_type = 'cospar' AND c.id_value = linked.piece)
                         OR (c.id_type = 'gcat_id' AND c.id_value = linked.jcat))
    END AS key_churn_observed
FROM linked
"""

# The one deterministic alias-resolution rule, shared by both annotation statements below.
# Sources are restricted to gcat_orgs/gcat/seed because GCAT org codes and SATCAT country codes
# share one namespace: without the restriction POL (Polyot, 95 satellites) resolves to Poland,
# COL to Colombia and LTU to Lithuania, unambiguously and wrongly. The ORDER BY makes ambiguous
# aliases deterministic (seed wins, then gcat_orgs, then lowest operator_id) instead of
# heap-ordered; today zero ambiguous aliases are used as group codes, so the tail terms are
# insurance that tests pin in place.
_ALIAS1_CTE = """
WITH alias1 AS (
    SELECT DISTINCT ON (a.alias) a.alias, a.operator_id
    FROM operator_alias a
    WHERE a.source IN ('gcat_orgs', 'gcat', 'seed')
    ORDER BY a.alias,
             (a.source <> 'seed'),
             (a.source <> 'gcat_orgs'),
             a.operator_id
)
"""

_ANNOTATE_LEAF_SQL = _ALIAS1_CTE + """
UPDATE satellite_bus sb
SET manufacturer_operator_id = alias1.operator_id
FROM alias1
WHERE alias1.alias = sb.manufacturer_code
"""

_ANNOTATE_GROUP_SQL = _ALIAS1_CTE + """
UPDATE satellite_bus sb
SET manufacturer_group_operator_id = alias1.operator_id
FROM alias1
WHERE alias1.alias = sb.manufacturer_group_code
"""

# Cohorts per (group operator, group code), with the display values that are functionally
# dependent on the group code. Used twice below on the SAME pre-merge state: first to record the
# URL contract for every slug the merge retires, then to perform the merge itself.
_COHORTS_CTE = """
cohorts AS (
    SELECT manufacturer_group_operator_id AS op,
           manufacturer_group_code AS gcode,
           count(*) AS fleet,
           min(manufacturer_name) AS gname,
           min(manufacturer_country) AS gcountry,
           min(manufacturer_slug) AS gslug
    FROM satellite_bus
    WHERE manufacturer_group_operator_id IS NOT NULL
      AND manufacturer_group_code IS NOT NULL
    GROUP BY 1, 2
),
multi AS (
    SELECT op FROM cohorts GROUP BY op HAVING count(*) >= 2
),
rep AS (
    -- The representative is the incumbent group code with the largest fleet, ties broken by
    -- code ascending. This is a published-URL decision, not an implementation detail: fleet-max
    -- keeps /buses/plan for the merged Planet family, where alphabetical would hand a
    -- 661-satellite cohort to /buses/cosmog, a two-satellite slug.
    SELECT DISTINCT ON (c.op) c.op, c.gcode, c.gname, c.gcountry, c.gslug
    FROM cohorts c
    JOIN multi m USING (op)
    ORDER BY c.op, c.fleet DESC, c.gcode ASC
)
"""

# Record the retired slug -> surviving slug mapping BEFORE rewriting anything. Aliases accumulate
# (ON CONFLICT DO NOTHING): once a slug has been published and retired, the redirect is a
# permanent contract even if a later catalog change would no longer produce it.
_ALIAS_UPSERT_SQL = "WITH " + _COHORTS_CTE + """
INSERT INTO benchmark_slug_alias (kind, old_slug, new_slug, reason)
SELECT 'manufacturer', c.gslug, r.gslug,
       'cohort merged into ' || r.gcode || ' via shared operator identity'
FROM cohorts c
JOIN rep r ON r.op = c.op AND c.gcode <> r.gcode
WHERE c.gslug IS NOT NULL AND r.gslug IS NOT NULL AND c.gslug <> r.gslug
ON CONFLICT (kind, old_slug) DO NOTHING
"""

# The merge itself: strictly merge-only, keyed on the GROUP code (never the leaf), so it can
# join cohorts but structurally cannot split one. Unresolved group codes (no operator match)
# keep their incumbent cohort untouched, which is the fallback for the ~317 codes the operator
# graph does not know. Merged rows record rollup_source='operator_merge' so the provenance of
# the rewrite is visible per satellite.
_OPERATOR_MERGE_SQL = "WITH " + _COHORTS_CTE + """
UPDATE satellite_bus sb
SET manufacturer_group_code = r.gcode,
    manufacturer_name = r.gname,
    manufacturer_country = r.gcountry,
    manufacturer_slug = r.gslug,
    rollup_source = 'operator_merge'
FROM rep r
WHERE sb.manufacturer_group_operator_id = r.op
  AND sb.manufacturer_group_code <> r.gcode
"""

_STATS_SQL = """
SELECT
    count(*) AS attributed,
    count(bus_model) AS with_bus,
    count(manufacturer_code) AS with_manufacturer,
    count(DISTINCT bus_slug) AS bus_models,
    count(DISTINCT manufacturer_slug) AS manufacturers,
    count(*) FILTER (WHERE rollup_source = 'gcat_orgs') AS rolled_up,
    count(*) FILTER (WHERE rollup_source = 'gcat_orgs+override') AS rolled_up_override,
    count(*) FILTER (WHERE rollup_source = 'unresolved') AS unresolved_codes
FROM satellite_bus
"""


def build(conn) -> dict:
    """Rebuild satellite_bus from the latest OK GCAT snapshot. Returns summary stats.

    After the GCAT rollup, the operator graph is applied as a strictly merge-only identity
    layer: group codes that resolve to the same operator collapse into one cohort under the
    fleet-max incumbent's slug, and every slug that retires gets a permanent redirect row in
    benchmark_slug_alias. The GCAT parent walk and ROLLUP_OVERRIDES stay authoritative for the
    rollup itself; the operator graph only decides which already-rolled-up cohorts are the same
    real-world company. See docs/design/0002-phase4-brief.md for why this shape and no other.
    """
    override_codes = list(ROLLUP_OVERRIDES)
    override_parents = [ROLLUP_OVERRIDES[c] for c in override_codes]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM satellite_bus")
        cur.execute(
            _BUILD_SQL,
            {
                "override_codes": override_codes,
                "override_parents": override_parents,
                "bus_placeholders": list(_BUS_PLACEHOLDERS),
            },
        )
        cur.execute(_ANNOTATE_LEAF_SQL)
        cur.execute(_ANNOTATE_GROUP_SQL)
        cur.execute(_ALIAS_UPSERT_SQL)
        aliases_recorded = cur.rowcount
        cur.execute(_OPERATOR_MERGE_SQL)
        merged_rows = cur.rowcount
        cur.execute(_STATS_SQL)
        columns = [d.name for d in cur.description]
        stats = dict(zip(columns, cur.fetchone()))
    stats["operator_merged_rows"] = merged_rows
    stats["slug_aliases_recorded"] = aliases_recorded
    return stats


def refresh_behavior_matview(conn) -> bool:
    """Refresh mv_bus_behavior_sat when the metrics layer has created it. Returns whether it ran."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('mv_bus_behavior_sat') IS NOT NULL")
        exists = cur.fetchone()[0]
        if exists:
            cur.execute("REFRESH MATERIALIZED VIEW mv_bus_behavior_sat")
    return bool(exists)


_SNAPSHOT_SQL = """
INSERT INTO bus_benchmark_snapshots
    (snapshot_month, kind, slug, display_name, metrics, methodology_version)
SELECT date_trunc('month', current_date)::date, %(kind)s, v.{slug_col}, v.{name_col},
       to_jsonb(v), %(version)s
FROM {view} v
WHERE v.{slug_col} IS NOT NULL
ON CONFLICT (snapshot_month, kind, slug) DO NOTHING
"""


def snapshot_benchmarks(conn) -> dict:
    """Freeze the current month's leaderboards into bus_benchmark_snapshots, once per month.

    A month is captured in full by the FIRST run of that calendar month and never touched again:
    if the month already holds any rows for a kind, the run inserts nothing at all. The earlier
    per-slug ON CONFLICT DO NOTHING allowed a cohort first seen mid-month to append itself into
    an already-frozen month with that day's values (observed in July 2026: 2,650 rows on the
    23rd, 3 more on the 27th), which made "immutable monthly" quietly false. All cohorts are
    captured (no minimum n) so history stays complete; readers apply their own cohort floor.
    """
    inserted = {}
    specs = [
        ("manufacturer", "v_bus_benchmarks_manufacturer", "manufacturer_slug", "manufacturer_name"),
        ("bus", "v_bus_benchmarks_bus", "bus_slug", "bus_model"),
    ]
    with conn.cursor() as cur:
        for kind, view, slug_col, name_col in specs:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (view,))
            if not cur.fetchone()[0]:
                inserted[kind] = None  # metrics views not applied yet
                continue
            cur.execute(
                "SELECT count(*) FROM bus_benchmark_snapshots "
                "WHERE snapshot_month = date_trunc('month', current_date)::date AND kind = %s",
                (kind,),
            )
            if cur.fetchone()[0] > 0:
                inserted[kind] = 0  # month already frozen for this kind
                continue
            cur.execute(
                _SNAPSHOT_SQL.format(view=view, slug_col=slug_col, name_col=name_col),
                {"kind": kind, "version": METHODOLOGY_VERSION},
            )
            inserted[kind] = cur.rowcount
    return inserted
