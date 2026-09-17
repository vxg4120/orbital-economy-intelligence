"""Pre-publish assertions over the aggregates the terminal serves.

Run by the nightly refresh after the benchmark build (scripts/assert_published.py) and by the
test suite (tests/test_publish_assertions.py). The 2026-09-16 audit's morning pass saw a bus
cohort with ON-ORBIT 507 against FLEET 505, which no single view row can produce and which
the evening pass could not reproduce; whatever served it, nothing here can rule it out after
the fact. These checks run on the views themselves, before a reader can, so a refresh that
would ship an impossible number says so in its log.

Every check is a pure function over plain row dicts, so it runs without a database; ``run``
gathers the rows from the served views and applies them. A violation is one string naming the
surface, the row and the rule broken.
"""

from __future__ import annotations

from collections.abc import Iterable

from psycopg.rows import dict_row

# Percentage columns every leaderboard view carries; each is NULL or within [0, 100].
PCT_COLUMNS = (
    "decayed_share_pct",
    "station_keeping_share_pct",
    "disposal_compliance_pct",
    "gp_coverage_pct",
)
# Per-metric observed counts; none can exceed the fleet it was computed over.
N_COLUMNS = ("gp_n", "sk_n", "tto_n", "lifetime_n", "disposal_n", "decayed_count")

# The bus leaderboard views, with the column that is their public URL key. The anchored
# variants are served on request (state=anchored) and must hold the same invariants.
BUS_VIEWS = {
    "v_bus_benchmarks_manufacturer": "manufacturer_slug",
    "v_bus_benchmarks_bus": "bus_slug",
    "v_bus_benchmarks_manufacturer_anchored": "manufacturer_slug",
    "v_bus_benchmarks_bus_anchored": "bus_slug",
}

# The API's default cohort floor; the leaderboard must never serve a smaller cohort under it.
COHORT_FLOOR = 5


def _label(surface: str, row: dict, key: str) -> str:
    return f"{surface}[{row.get(key, '?')}]"


def check_fleet_order(rows: Iterable[dict], surface: str, key: str) -> list[str]:
    """fleet_total >= fleet_on_orbit >= fleet_active >= 0 on every row.

    On-orbit is the fleet minus the decayed; active is the on-orbit subset with status ACTIVE.
    A row where a subset exceeds its superset is not a data question, it is a served lie."""
    out = []
    for r in rows:
        fleet, on_orbit, active = r["fleet_total"], r["fleet_on_orbit"], r["fleet_active"]
        if not (fleet >= on_orbit >= active >= 0):
            out.append(
                f"{_label(surface, r, key)}: fleet {fleet} >= on-orbit {on_orbit} "
                f">= active {active} >= 0 does not hold"
            )
    return out


def check_status_partition(rows: Iterable[dict], surface: str, key: str) -> list[str]:
    """fleet_on_orbit + decayed_count == fleet_total: latest status is DECAYED or it is not."""
    out = []
    for r in rows:
        if "decayed_count" not in r:
            continue
        if r["fleet_on_orbit"] + r["decayed_count"] != r["fleet_total"]:
            out.append(
                f"{_label(surface, r, key)}: on-orbit {r['fleet_on_orbit']} + decayed "
                f"{r['decayed_count']} != fleet {r['fleet_total']}"
            )
    return out


def check_metric_counts(rows: Iterable[dict], surface: str, key: str) -> list[str]:
    """Every per-metric n is a non-negative count no larger than the fleet it describes."""
    out = []
    for r in rows:
        for col in N_COLUMNS:
            n = r.get(col)
            if n is None:
                continue
            if n < 0 or n > r["fleet_total"]:
                out.append(f"{_label(surface, r, key)}: {col} {n} outside 0..fleet {r['fleet_total']}")
    return out


def check_percentages(rows: Iterable[dict], surface: str, key: str) -> list[str]:
    """Every percentage column is NULL or within [0, 100]."""
    out = []
    for r in rows:
        for col in PCT_COLUMNS:
            v = r.get(col)
            if v is None:
                continue
            if not (0 <= float(v) <= 100):
                out.append(f"{_label(surface, r, key)}: {col} {v} outside 0..100")
    return out


def check_unique_keys(rows: Iterable[dict], surface: str, key: str) -> list[str]:
    """The public key (a slug, an operator id) is present and unique: one URL, one cohort."""
    seen: dict = {}
    out = []
    for r in rows:
        k = r.get(key)
        if k in (None, ""):
            out.append(f"{surface}: a row has no {key}")
            continue
        seen[k] = seen.get(k, 0) + 1
    out += [f"{surface}[{k}]: {key} appears {n} times" for k, n in seen.items() if n > 1]
    return out


def check_cohort_floor(rows: Iterable[dict], surface: str, key: str, floor: int) -> list[str]:
    """Rows served under a cohort floor all meet it."""
    return [
        f"{_label(surface, r, key)}: fleet {r['fleet_total']} served under floor {floor}"
        for r in rows
        if r["fleet_total"] < floor
    ]


def check_leaderboard(rows: list[dict], surface: str, key: str) -> list[str]:
    """Every row-level invariant a leaderboard view must hold."""
    return (
        check_fleet_order(rows, surface, key)
        + check_status_partition(rows, surface, key)
        + check_metric_counts(rows, surface, key)
        + check_percentages(rows, surface, key)
        + check_unique_keys(rows, surface, key)
    )


def check_header_counters(stats: dict, league_total: int) -> list[str]:
    """The topbar counters agree with the tables they summarise.

    OPERATORS is the operator count and the league's total; CONFLICTS is the sum of the three
    conflict tabs, so each part must be a count. The audit found the first pair disagreeing
    by 53 (major 4)."""
    out = []
    if stats["operators"] != league_total:
        out.append(f"header operators {stats['operators']} != league total {league_total}")
    for part, n in stats["conflicts"].items():
        if not isinstance(n, int) or n < 0:
            out.append(f"conflict tally {part} is not a count: {n!r}")
    for name in ("satellites", "operators", "gp_elements", "on_orbit_payloads"):
        if stats[name] < 0:
            out.append(f"header {name} is negative: {stats[name]}")
    for name, pct in stats["coverage"].items():
        if not (0 <= pct <= 100):
            out.append(f"coverage {name} {pct} outside 0..100")
    return out


def run(conn) -> list[str]:
    """Every assertion against the live views. Returns the violations; empty means publishable."""
    from api.routers import stats as stats_router
    from api.routers.buses import leaderboard_rows
    from api.routers.operators import _LEAGUE_COUNT_SQL, _LEAGUE_CTE

    # The API builders read columns by name, exactly as api.deps.get_db sets a request up.
    conn.row_factory = dict_row
    violations: list[str] = []
    with conn.cursor() as cur:
        for view, key in BUS_VIEWS.items():
            cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (view,))
            if not cur.fetchone()["present"]:
                continue  # metrics layer not applied in this database
            cur.execute(f"SELECT * FROM {view}")
            violations += check_leaderboard(cur.fetchall(), view, key)

        cur.execute(_LEAGUE_CTE + "SELECT * FROM agg")
        league = cur.fetchall()
        violations += check_fleet_order(league, "operators", "operator_id")
        violations += check_unique_keys(league, "operators", "operator_id")
        cur.execute(_LEAGUE_COUNT_SQL)
        league_total = cur.fetchone()["total"]

    # The served leaderboard under its default floor, both groupings, every page.
    for group in ("manufacturer", "bus"):
        offset = 0
        while True:
            page = leaderboard_rows(conn, group, "fleet", COHORT_FLOOR, 200, offset)
            violations += check_cohort_floor(page["rows"], f"/api/buses?group={group}", "slug",
                                             COHORT_FLOOR)
            offset += 200
            if offset >= page["total"] or not page["rows"]:
                break

    # The Overview payload, computed the way the endpoint computes it.
    stats = stats_router._build_stats(conn)
    violations += check_header_counters(stats, league_total)
    return violations
