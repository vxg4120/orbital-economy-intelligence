"""Bus / manufacturer attribution build (the satellite_bus table).

Resolves each GCAT payload row's Bus and Manufacturer fields into normalized, org-resolved
attribution with explicit provenance, in one set-based rebuild:

* Bus strings are normalized: whitespace collapsed, GCAT's trailing '?' uncertainty marker
  stripped (and recorded in bus_uncertain), placeholder values (UNK, Unknown, ...) dropped,
  and casing variants of the same model collapsed to the most common spelling.
* Manufacturer codes are resolved against the latest raw_gcat_orgs snapshot. Co-manufactured
  objects ("NPOL/KOMET") attribute to the first-listed org (GCAT lists the prime first); the
  full code list is preserved in manufacturer_codes.
* Parent rollup: the org's Parent chain is followed upward while the parent is a business-class
  org (GCAT Class 'B'), so plant-level subsidiaries roll up to their corporate group
  (BOES -> BOE "Boeing") while state design bureaus do NOT collapse into ministries or space
  agencies (NPO PM stays NPO PM rather than becoming MOM). A tiny curated override map covers
  parent links GCAT leaves blank (SPXS -> SPX: SpaceX's Seattle satellite works is SpaceX).
  The traversed code path and whether an override fired are stored per row.

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
METHODOLOGY_VERSION = "1.0"
METHODOLOGY_UPDATED = "2026-07-23"

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
    SELECT r.jcat, r.ingest_run_id,
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
           COALESCE(manufacturer_raw LIKE '%%?', FALSE) AS manufacturer_uncertain,
           NULLIF(split_part(btrim(rtrim(manufacturer_raw, '?')), '/', 1), '') AS primary_code,
           string_to_array(btrim(rtrim(manufacturer_raw, '?')), '/') AS all_codes
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
    SELECT p.jcat, p.ingest_run_id,
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
linked AS (
    -- Attach through the identifier crosswalk. A satellite occasionally carries two GCAT rows
    -- (merge artifacts): prefer the row with a bus model, then the certain one, then lowest jcat.
    SELECT DISTINCT ON (si.satellite_id) si.satellite_id, r.*
    FROM resolved r
    JOIN satellite_identifier si
      ON si.id_type = 'gcat_id' AND si.source = 'gcat' AND si.id_value = r.jcat
    ORDER BY si.satellite_id, (r.bus_model IS NULL), r.manufacturer_uncertain,
             r.bus_uncertain, r.jcat
)
INSERT INTO satellite_bus (
    satellite_id, bus_raw, bus_model, bus_slug, bus_uncertain,
    manufacturer_raw, manufacturer_code, manufacturer_codes, manufacturer_uncertain,
    manufacturer_org_name, manufacturer_group_code, manufacturer_name, manufacturer_slug,
    manufacturer_country, rollup_path, rollup_source,
    source, source_key, ingest_run_id
)
SELECT
    satellite_id, bus_raw, bus_model, bus_slug, bus_uncertain,
    manufacturer_raw, primary_code, all_codes, manufacturer_uncertain,
    manufacturer_org_name, manufacturer_group_code, manufacturer_name,
    NULLIF(btrim(regexp_replace(lower(COALESCE(manufacturer_group_code, '')),
                                '[^a-z0-9]+', '-', 'g'), '-'), '') AS manufacturer_slug,
    manufacturer_country, rollup_path, rollup_source,
    'gcat', jcat, ingest_run_id
FROM linked
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
    """Rebuild satellite_bus from the latest OK GCAT snapshot. Returns summary stats."""
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
        cur.execute(_STATS_SQL)
        columns = [d.name for d in cur.description]
        stats = dict(zip(columns, cur.fetchone()))
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
    """Freeze the current month's leaderboards into bus_benchmark_snapshots, idempotently.

    Keyed on (snapshot_month, kind, slug) with DO NOTHING: the first run in a calendar month
    captures that month's numbers, every later run inserts zero rows. All cohorts are captured
    (no minimum n) so history stays complete; readers apply their own cohort floor.
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
                _SNAPSHOT_SQL.format(view=view, slug_col=slug_col, name_col=name_col),
                {"kind": kind, "version": METHODOLOGY_VERSION},
            )
            inserted[kind] = cur.rowcount
    return inserted
