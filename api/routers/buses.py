"""Bus Benchmarks: manufacturer and bus-model leaderboards over satellite_bus attribution.

Read-only endpoints over the views in metrics/bus_benchmarks.sql:

* GET /api/buses                      leaderboard (group=manufacturer|bus, sortable, cohort floor)
* GET /api/buses/methodology          versioned, structured metric definitions and caveats
* GET /api/buses/history/{slug}       immutable monthly snapshot series for one group
* GET /api/buses/{slug}               detail: headline metrics, constituents, sample, provenance
* GET /api/buses/{slug}/provenance    the receipts: per-satellite rows behind one headline metric

Every leaderboard number is traceable to source rows: the provenance endpoint lists the
constituent satellites with their per-satellite values and the GCAT row (source_key = jcat,
ingest_run_id) that asserted the attribution. Definitions are versioned; see
docs/BUS_BENCHMARKS_METHODOLOGY.md.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db
from identity.bus import METHODOLOGY_UPDATED, METHODOLOGY_VERSION

router = APIRouter(prefix="/buses", tags=["buses"])

METHODOLOGY_DOC_URL = (
    "https://github.com/vxg4120/orbital-economy-intelligence"
    "/blob/main/docs/BUS_BENCHMARKS_METHODOLOGY.md"
)
CORRECTION_CHANNEL = (
    "Operators and manufacturers can confirm or dispute attribution: email "
    "vibhavgupta2@gmail.com with subject 'Bus attribution: {name}'. Adjudicated corrections "
    "enter the record as source_assertion rows with source 'operator_confirmed', which "
    "outranks catalog sources in precedence."
)

_GROUPS = {
    "manufacturer": {
        "view": "v_bus_benchmarks_manufacturer",
        "slug_col": "manufacturer_slug",
        "name_col": "manufacturer_name",
    },
    "bus": {
        "view": "v_bus_benchmarks_bus",
        "slug_col": "bus_slug",
        "name_col": "bus_model",
    },
}

# Whitelisted sort keys -> ORDER BY fragments (never interpolate user input directly).
_SORTS = {
    "fleet": "fleet_total DESC",
    "on_orbit": "fleet_on_orbit DESC",
    "active": "fleet_active DESC",
    "tto": "median_days_to_operational ASC NULLS LAST",
    "station_keeping": "p50_station_keeping_km ASC NULLS LAST",
    "sk_share": "station_keeping_share_pct DESC NULLS LAST",
    "decayed_share": "decayed_share_pct DESC NULLS LAST",
    "lifetime": "median_lifetime_years DESC NULLS LAST",
    "compliance": "disposal_compliance_pct DESC NULLS LAST",
    "coverage": "gp_coverage_pct DESC NULLS LAST",
    "name": "name ASC",
}

# Provenance metrics: per-satellite value column + cohort filter over v_bus_sat. The "of" note
# says which denominator the metric uses, so receipts are self-describing.
_PROVENANCE_METRICS = {
    "fleet": ("canonical_status", "TRUE", "all attributed payloads"),
    "on_orbit": ("canonical_status", "canonical_status <> 'DECAYED'", "latest status not DECAYED"),
    "active": ("canonical_status", "canonical_status = 'ACTIVE'", "latest status ACTIVE"),
    "decayed_share": ("canonical_status", "canonical_status = 'DECAYED'", "latest status DECAYED"),
    "lifetime": (
        "lifetime_days", "lifetime_days IS NOT NULL",
        "decayed payloads with launch and decay dates; value is lifetime in days",
    ),
    "tto": (
        "days_to_operational",
        "days_to_operational IS NOT NULL AND tto_shell_n >= 3",
        "converging LEO payloads in shells with >= 3 members; value is days from launch",
    ),
    "station_keeping": (
        "sk_median_stddev_km", "sk_median_stddev_km IS NOT NULL",
        "ACTIVE payloads with GP history; value is median 30-day rolling sma stddev (km)",
    ),
    "sk_share": (
        "sk_median_stddev_km", "sk_median_stddev_km IS NOT NULL",
        "ACTIVE payloads with GP history; station-keeping when value <= 0.100 km",
    ),
    "compliance": (
        "disposal_compliant", "disposal_compliant IS NOT NULL",
        "retired payloads with a decidable 5-year disposal verdict",
    ),
    "coverage": (
        "gp_days", "gp_days > 0",
        "payloads with at least one day in the sat_daily behavior aggregate",
    ),
}

METHODOLOGY = {
    "version": METHODOLOGY_VERSION,
    "updated_at": METHODOLOGY_UPDATED,
    "title": "Bus Benchmarks methodology",
    "doc_url": METHODOLOGY_DOC_URL,
    "purpose": (
        "An independent, provenance-tracked performance scoreboard for satellite buses "
        "(spacecraft platforms) by manufacturer and bus model, computed from public catalog "
        "and orbital element data."
    ),
    "data_sources": [
        {
            "name": "GCAT satcat (Jonathan McDowell)",
            "role": "bus model and manufacturer attribution per object",
            "url": "https://planet4589.org/space/gcat/",
        },
        {
            "name": "GCAT orgs",
            "role": "manufacturer org codes resolved to organizations, with parent hierarchy",
            "url": "https://planet4589.org/space/gcat/data/tables/orgs.html",
        },
        {
            "name": "GP element history (Space-Track / CelesTrak)",
            "role": "orbital behavior: station-keeping, orbit raising, decay",
            "url": "https://celestrak.org/NORAD/elements/",
        },
    ],
    "inclusion": (
        "Payload objects only (GCAT object type P*), attributed via the identity graph "
        "crosswalk. Rocket stages and debris are excluded even where GCAT records a platform."
    ),
    "cohort_minimum": 5,
    "attribution": [
        "Manufacturer codes resolve against the latest GCAT orgs snapshot.",
        "Co-manufactured objects (A/B) attribute to the first-listed (prime) org; the full "
        "code list is preserved.",
        "Parent rollup follows the GCAT org Parent chain upward only through business-class "
        "(Class B) orgs, so plant-level subsidiaries roll up to their corporate group while "
        "state design bureaus do not collapse into ministries or space agencies.",
        "One curated override: SPXS (SpaceX Seattle) rolls up to SPX (SpaceX); rows using it "
        "carry rollup_source 'gcat_orgs+override'.",
        "Bus strings are normalized: whitespace collapsed, GCAT's trailing '?' uncertainty "
        "marker stripped and recorded, placeholder values dropped, casing variants collapsed "
        "to the most common spelling. Distinct variants (Starlink V2M vs V2MO) stay distinct.",
    ],
    "metrics": [
        {
            "key": "fleet",
            "label": "Fleet size",
            "definition": "Cataloged payloads attributed to the group; on-orbit means latest "
                          "canonical status is not DECAYED; active means status ACTIVE.",
            "source": "v_bus_benchmarks_* (fleet_total, fleet_on_orbit, fleet_active)",
        },
        {
            "key": "tto",
            "label": "Median time to operational",
            "definition": "Days from launch to the first 7-consecutive-day streak within 15 km "
                          "of the constellation shell median sma, per v_time_to_operational; "
                          "cohort restricted to shells with at least 3 members.",
            "source": "v_time_to_operational (SPEC 7.2), rolled up per group",
        },
        {
            "key": "sk_share",
            "label": "Station-keeping share",
            "definition": "Share of behavior-observed ACTIVE payloads whose median 30-day "
                          "rolling sma stddev is at most 0.100 km, i.e. the orbit is actively "
                          "held rather than drifting.",
            "source": "v_station_keeping_30d (SPEC 7.1), per-satellite medians",
        },
        {
            "key": "station_keeping",
            "label": "Station-keeping tightness (p50)",
            "definition": "Cohort median of per-satellite median 30-day rolling sma stddev, in "
                          "km. Lower is tighter.",
            "source": "v_station_keeping_30d (SPEC 7.1)",
        },
        {
            "key": "decayed_share",
            "label": "Decayed share and median lifetime",
            "definition": "Share of the attributed fleet whose latest status is DECAYED, and "
                          "the median launch-to-decay lifetime (years) over decayed payloads "
                          "with both dates. Survivors are censored, not counted as zero.",
            "source": "satellite + satellite_status_history via v_bus_sat",
        },
        {
            "key": "compliance",
            "label": "Post-mission disposal (5-year rule)",
            "definition": "Share of retired payloads with a decidable verdict that re-entered "
                          "within 5 years of last observed ACTIVE status (the FCC-style rule "
                          "in v_deorbit_compliance). Sparse until decay-history backfill "
                          "deepens; n is always reported.",
            "source": "v_deorbit_compliance (SPEC 7.3)",
        },
        {
            "key": "coverage",
            "label": "Behavior coverage",
            "definition": "Share of the attributed fleet with at least one day of GP behavior "
                          "data in sat_daily. All behavior metrics are computed only over this "
                          "observed slice, which is LEO-biased.",
            "source": "sat_daily continuous aggregate",
        },
    ],
    "limitations": [
        "GP behavior observability is LEO-biased: the element history backfill covers LEO "
        "far better than GEO/MEO, so behavior metrics under-represent GEO manufacturers.",
        "GCAT bus and manufacturer attribution is itself incomplete and sometimes uncertain "
        "('?' markers); uncertainty flags are preserved per satellite.",
        "Survivorship effects: lifetime medians are computed over decayed objects only.",
        "Ambiguous bus variants exist; normalization collapses casing but never merges "
        "genuinely distinct variants.",
        "Generic form-factor entries (Cubesat 3U and similar) aggregate many unrelated "
        "vehicles and are labeled by their most common manufacturer.",
    ],
    "provenance_guarantee": (
        "Every number is traceable to source rows: each attribution carries (source, "
        "source_key = GCAT jcat, ingest_run_id) plus source_assertion records, and the "
        "per-metric provenance endpoint returns the exact constituent satellites behind any "
        "headline value."
    ),
    "correction_channel": CORRECTION_CHANNEL,
    "refresh": "Nightly, with the daily ingest cycle. Monthly leaderboard snapshots are "
               "frozen on the first refresh of each month and never rewritten.",
}


def _group_spec(group: str) -> dict:
    spec = _GROUPS.get(group)
    if spec is None:
        raise HTTPException(status_code=422, detail=f"group must be one of {sorted(_GROUPS)}")
    return spec


def leaderboard_rows(db, group: str, sort: str, min_n: int, limit: int, offset: int) -> dict:
    """Shared by the router and the MCP server. Returns {rows, total, group, sort, min_n}."""
    spec = _group_spec(group)
    order = _SORTS.get(sort)
    if order is None:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(_SORTS)}")
    base = (
        f"SELECT v.*, v.{spec['slug_col']} AS slug, v.{spec['name_col']} AS name "
        f"FROM {spec['view']} v WHERE v.fleet_total >= %(min_n)s"
    )
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM ({base}) t", {"min_n": min_n})
        total = cur.fetchone()["total"]
        cur.execute(
            f"{base} ORDER BY {order}, slug LIMIT %(limit)s OFFSET %(offset)s",
            {"min_n": min_n, "limit": limit, "offset": offset},
        )
        rows = cur.fetchall()
    return {"rows": rows, "total": total, "group": group, "sort": sort, "min_n": min_n}


def _find_group(db, slug: str, kind: str | None) -> tuple[str, dict] | None:
    """Resolve a slug to (kind, benchmark row); manufacturers win ties unless kind pins it."""
    kinds = [kind] if kind in _GROUPS else ["manufacturer", "bus"]
    with db.cursor() as cur:
        for k in kinds:
            spec = _GROUPS[k]
            cur.execute(
                f"SELECT v.*, v.{spec['slug_col']} AS slug, v.{spec['name_col']} AS name "
                f"FROM {spec['view']} v WHERE v.{spec['slug_col']} = %(slug)s",
                {"slug": slug},
            )
            row = cur.fetchone()
            if row is not None:
                return k, row
    return None


def detail_payload(db, slug: str, kind: str | None = None) -> dict:
    found = _find_group(db, slug, kind)
    if found is None:
        raise HTTPException(status_code=404, detail="no manufacturer or bus with that slug")
    k, benchmark = found
    slug_col = _GROUPS[k]["slug_col"]

    with db.cursor() as cur:
        if k == "manufacturer":
            # Constituents: this manufacturer's bus models, then its constituent GCAT orgs.
            cur.execute(
                "SELECT bus_slug AS slug, bus_model AS name, count(*) AS fleet_total, "
                "count(*) FILTER (WHERE canonical_status <> 'DECAYED') AS fleet_on_orbit "
                "FROM v_bus_sat WHERE manufacturer_slug = %(slug)s AND bus_slug IS NOT NULL "
                "GROUP BY 1, 2 ORDER BY fleet_total DESC, slug LIMIT 15",
                {"slug": slug},
            )
            constituents = cur.fetchall()
            cur.execute(
                "SELECT manufacturer_code AS code, manufacturer_org_name AS org_name, "
                "       rollup_source, count(*) AS fleet_total "
                "FROM v_bus_sat WHERE manufacturer_slug = %(slug)s "
                "GROUP BY 1, 2, 3 ORDER BY fleet_total DESC, code LIMIT 15",
                {"slug": slug},
            )
            orgs = cur.fetchall()
        else:
            cur.execute(
                "SELECT manufacturer_slug AS slug, manufacturer_name AS name, "
                "count(*) AS fleet_total, "
                "count(*) FILTER (WHERE canonical_status <> 'DECAYED') AS fleet_on_orbit "
                "FROM v_bus_sat WHERE bus_slug = %(slug)s AND manufacturer_slug IS NOT NULL "
                "GROUP BY 1, 2 ORDER BY fleet_total DESC, slug LIMIT 15",
                {"slug": slug},
            )
            constituents = cur.fetchall()
            orgs = []

        cur.execute(
            f"SELECT satellite_id, norad_id, cospar_id, canonical_name, canonical_status, "
            f"       launch_date, bus_model, manufacturer_name, gp_days, sk_median_stddev_km, "
            f"       days_to_operational, lifetime_days, "
            f"       source, source_key, ingest_run_id, rollup_source, "
            f"       bus_uncertain, manufacturer_uncertain "
            f"FROM v_bus_sat WHERE {slug_col} = %(slug)s "
            f"ORDER BY (canonical_status = 'ACTIVE') DESC, gp_days DESC, norad_id NULLS LAST "
            f"LIMIT 20",
            {"slug": slug},
        )
        satellites_sample = cur.fetchall()

        cur.execute(
            f"SELECT max(sb.ingest_run_id) AS ingest_run_id, max(sb.built_at) AS built_at, "
            f"       count(*) FILTER (WHERE sb.bus_uncertain OR sb.manufacturer_uncertain) "
            f"           AS uncertain_n "
            f"FROM satellite_bus sb JOIN v_bus_sat v USING (satellite_id) "
            f"WHERE v.{slug_col} = %(slug)s",
            {"slug": slug},
        )
        prov = cur.fetchone()

    total = benchmark["fleet_total"]
    provenance = {
        "source": "gcat",
        "ingest_run_id": prov["ingest_run_id"],
        "built_at": prov["built_at"],
        "uncertain_attributions": prov["uncertain_n"],
        "methodology_version": METHODOLOGY_VERSION,
        "metric_coverage": {
            "gp_behavior": {"n": benchmark["gp_n"], "of": total},
            "station_keeping": {"n": benchmark["sk_n"], "of": total},
            "time_to_operational": {"n": benchmark["tto_n"], "of": total},
            "lifetime": {"n": benchmark["lifetime_n"], "of": benchmark["decayed_count"]},
            "disposal": {"n": benchmark["disposal_n"], "of": benchmark["decayed_count"]},
        },
        "receipts": f"/api/buses/{slug}/provenance?metric=fleet",
    }
    return {
        "kind": k,
        "benchmark": benchmark,
        "constituents": constituents,
        "orgs": orgs,
        "satellites_sample": satellites_sample,
        "provenance": provenance,
        "correction_channel": CORRECTION_CHANNEL,
    }


def provenance_rows(db, slug: str, metric: str, kind: str | None,
                    limit: int, offset: int) -> dict:
    found = _find_group(db, slug, kind)
    if found is None:
        raise HTTPException(status_code=404, detail="no manufacturer or bus with that slug")
    k, benchmark = found
    spec = _PROVENANCE_METRICS.get(metric)
    if spec is None:
        raise HTTPException(
            status_code=422, detail=f"metric must be one of {sorted(_PROVENANCE_METRICS)}")
    value_col, row_filter, cohort_note = spec
    slug_col = _GROUPS[k]["slug_col"]

    base = (
        f"SELECT satellite_id, norad_id, cospar_id, canonical_name, canonical_status, "
        f"       {value_col} AS value, bus_model, manufacturer_name, "
        f"       source, source_key, ingest_run_id, rollup_source, "
        f"       bus_raw, manufacturer_raw, bus_uncertain, manufacturer_uncertain "
        f"FROM v_bus_sat WHERE {slug_col} = %(slug)s AND ({row_filter})"
    )
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM ({base}) t", {"slug": slug})
        total = cur.fetchone()["total"]
        cur.execute(
            f"{base} ORDER BY norad_id NULLS LAST, satellite_id "
            f"LIMIT %(limit)s OFFSET %(offset)s",
            {"slug": slug, "limit": limit, "offset": offset},
        )
        rows = cur.fetchall()
    return {
        "kind": k,
        "slug": slug,
        "name": benchmark["name"],
        "metric": metric,
        "cohort": cohort_note,
        "rows": rows,
        "total": total,
        "methodology_version": METHODOLOGY_VERSION,
    }


def history_rows(db, slug: str, kind: str | None = None) -> dict:
    kinds = [kind] if kind in _GROUPS else ["manufacturer", "bus"]
    with db.cursor() as cur:
        for k in kinds:
            cur.execute(
                "SELECT snapshot_month, display_name, metrics, methodology_version, created_at "
                "FROM bus_benchmark_snapshots WHERE kind = %(kind)s AND slug = %(slug)s "
                "ORDER BY snapshot_month",
                {"kind": k, "slug": slug},
            )
            rows = cur.fetchall()
            if rows:
                return {"kind": k, "slug": slug, "snapshots": rows}
    raise HTTPException(status_code=404, detail="no snapshots for that slug")


@router.get("")
def leaderboard(
    db=Depends(get_db),
    group: str = Query("manufacturer"),
    sort: str = Query("fleet"),
    min_n: int = Query(5, ge=1, le=100000),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return leaderboard_rows(db, group, sort, min_n, limit, offset)


# Static path routes must be declared before /{slug} so they win the route match.
@router.get("/methodology")
def methodology():
    return METHODOLOGY


@router.get("/history/{slug}")
def history(slug: str, db=Depends(get_db), kind: str | None = Query(None)):
    return history_rows(db, slug, kind)


@router.get("/{slug}")
def detail(slug: str, db=Depends(get_db), kind: str | None = Query(None)):
    return detail_payload(db, slug, kind)


@router.get("/{slug}/provenance")
def provenance(
    slug: str,
    db=Depends(get_db),
    metric: str = Query("fleet"),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return provenance_rows(db, slug, metric, kind, limit, offset)
