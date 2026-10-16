"""GET /api/stories -- the facts behind the Overview's story panels.

The landing leads with what happened in orbit recently: launches and reentries, the newest
launch groups, the constellation build-outs, who flies the active fleet, and the geomagnetic
storms. This endpoint returns those facts as numbers, names and dates; the SPA phrases them,
so copy changes never need an API deploy (docs/specs/landing-stories.md).

Two honesty rules shape the payload:

* The current month is marked ``partial``.
* Since July 2026 almost every newly launched payload is owned in the graph by a country-level
  operator (``operator_class IS NULL``: "United States", "People's Republic of China") because
  GCAT attributes companies about three months late. Constellation series therefore stop at
  ``attribution.complete_through``, and the pending count is published rather than dropped.

Every query is a small aggregate (milliseconds on the box). They run in one psycopg pipeline so
a high-latency connection pays roughly one round trip instead of one per query. The payload
only moves when the nightly ingest runs, so it is served from the warm cache like /api/stats.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Sequence

import psycopg
from fastapi import APIRouter, Depends

from api import cache
from api.deps import get_db

router = APIRouter(tags=["stories"])

# Constellations are keyed by resolved operator, not by name pattern (spec: names mix cases and
# formats across SATCAT and GCAT). Display names live in the SPA.
CONSTELLATIONS: tuple[tuple[str, str], ...] = (
    ("starlink", "SpaceX"),
    ("oneweb", "Eutelsat"),
    ("kuiper", "Amazon"),
    ("qianfan", "Shanghai Yuanxin Weixing Keji YG"),
    ("guowang", "Zhongguo Weixing Wangluo Jituan YG"),
)

WINDOW_DAYS = 30  # the "recent" window for launches and reentries
MONTHS_SHOWN = 13  # launch tempo bars: this month plus the twelve before it
SCAN_MONTHS = 25  # months scanned for attribution completeness
SERIES_MONTHS = 24  # constellation month-ends returned, ending at complete_through
# month-ends computed for the series: the 24 returned plus a year of headroom for the lag
SERIES_SCAN_MONTHS = SERIES_MONTHS + 12
# Launch groups are looked for in this many trailing days. oei_launch_key is a SQL function
# evaluated per row (about 0.1 ms each), so the window stays near what ten groups need.
RECENT_LAUNCH_DAYS = 90
ATTRIBUTION_THRESHOLD = 0.80
RECENT_LAUNCHES = 10
NOTABLE_REENTRIES = 8
TOP_OPERATORS = 6
STORM_DAYS = 120

# One current owner per satellite (the live graph has exactly one; DISTINCT ON keeps it so if a
# second ever appears, preferring a company over a country placeholder). ``attributed`` is the
# honesty flag: false when the owner is only a country-level placeholder.
_OWNER_CTE = """
owner AS (
    SELECT DISTINCT ON (so.satellite_id)
        so.satellite_id,
        o.canonical_name AS owner,
        o.operator_class IS NOT NULL AS attributed
    FROM satellite_operator so
    JOIN operator o ON o.operator_id = so.operator_id
    WHERE so.role = 'owner' AND so.valid_to IS NULL
    ORDER BY so.satellite_id, (o.operator_class IS NULL), so.confidence DESC NULLS LAST,
             so.operator_id
)"""

_AS_OF_SQL = """
SELECT (max(finished_at) AT TIME ZONE 'UTC')::date AS as_of
FROM ingest_run
WHERE status = 'ok'
"""

# Payloads launched (and how many of those carry a company owner) and payloads reentered, per
# calendar month, for the SCAN_MONTHS months ending with the reference month.
_MONTHLY_SQL = f"""
WITH {_OWNER_CTE},
months AS (
    SELECT generate_series(
        date_trunc('month', %(ref)s::date) - make_interval(months => %(scan)s - 1),
        date_trunc('month', %(ref)s::date),
        interval '1 month'
    )::date AS m
),
launched AS (
    SELECT date_trunc('month', s.launch_date)::date AS m,
           count(*) AS n,
           count(*) FILTER (WHERE ow.attributed) AS attributed
    FROM satellite s
    LEFT JOIN owner ow ON ow.satellite_id = s.satellite_id
    WHERE s.object_type = 'PAYLOAD'
      AND s.launch_date >= (SELECT min(m) FROM months)
      AND s.launch_date <= %(ref)s
    GROUP BY 1
),
reentered AS (
    SELECT date_trunc('month', decay_date)::date AS m, count(*) AS n
    FROM satellite
    WHERE object_type = 'PAYLOAD'
      AND decay_date >= (SELECT min(m) FROM months)
      AND decay_date <= %(ref)s
    GROUP BY 1
)
SELECT to_char(months.m, 'YYYY-MM') AS month,
       COALESCE(l.n, 0) AS launched,
       COALESCE(l.attributed, 0) AS attributed,
       COALESCE(r.n, 0) AS reentered
FROM months
LEFT JOIN launched l ON l.m = months.m
LEFT JOIN reentered r ON r.m = months.m
ORDER BY months.m
"""

# The trailing window, (ref - WINDOW_DAYS, ref]. A launch is a distinct launch key among the
# payloads launched in the window, so it counts launches that put at least one payload up.
_WINDOW_SQL = """
SELECT
    count(DISTINCT oei_launch_key(cospar_id)) FILTER (WHERE in_launch) AS launches,
    count(*) FILTER (WHERE in_launch) AS payloads_launched,
    count(*) FILTER (WHERE in_decay) AS payloads_reentered,
    count(*) FILTER (WHERE in_decay AND canonical_name ILIKE 'starlink%%') AS starlink,
    count(*) FILTER (WHERE in_decay AND canonical_name ILIKE 'starlink v1.0%%') AS starlink_v1
FROM (
    SELECT cospar_id, canonical_name,
           launch_date > %(ref)s::date - %(days)s AND launch_date <= %(ref)s AS in_launch,
           decay_date > %(ref)s::date - %(days)s AND decay_date <= %(ref)s AS in_decay
    FROM satellite
    WHERE object_type = 'PAYLOAD'
) w
WHERE in_launch OR in_decay
"""

# The newest launch groups (oei_launch_key on the COSPAR id, migration 0013) that carried at
# least one payload. The lead payload is the lowest piece letter (A before B before AA),
# preferring a real name over a SATCAT placeholder ("OBJECT A", "GUOWANG 27 OBJECT A"); the
# owner is the most common current owner among the group's payloads.
_RECENT_LAUNCHES_SQL = f"""
WITH {_OWNER_CTE},
recent AS (
    SELECT s.satellite_id, s.canonical_name, s.cospar_id, s.object_type, s.launch_date,
           oei_launch_key(s.cospar_id) AS launch_key
    FROM satellite s
    WHERE s.launch_date > %(ref)s::date - %(days)s AND s.launch_date <= %(ref)s
),
groups AS (
    SELECT launch_key, min(launch_date) AS date,
           count(*) FILTER (WHERE object_type = 'PAYLOAD') AS payloads,
           count(*) AS objects
    FROM recent
    WHERE launch_key IS NOT NULL
    GROUP BY launch_key
    HAVING count(*) FILTER (WHERE object_type = 'PAYLOAD') > 0
    ORDER BY date DESC, launch_key DESC
    LIMIT %(n)s
),
lead AS (
    SELECT DISTINCT ON (r.launch_key) r.launch_key, r.satellite_id, r.canonical_name
    FROM recent r JOIN groups g ON g.launch_key = r.launch_key
    WHERE r.object_type = 'PAYLOAD'
    ORDER BY r.launch_key, (r.canonical_name ~* '(^| )OBJECT [A-Z]{{1,3}}$'),
             length(r.cospar_id), r.cospar_id
),
owners AS (
    SELECT r.launch_key, ow.owner, COALESCE(ow.attributed, false) AS attributed,
           count(*) AS n
    FROM recent r
    JOIN groups g ON g.launch_key = r.launch_key
    LEFT JOIN owner ow ON ow.satellite_id = r.satellite_id
    WHERE r.object_type = 'PAYLOAD'
    GROUP BY r.launch_key, ow.owner, ow.attributed
),
top_owner AS (
    SELECT DISTINCT ON (launch_key) launch_key, owner, attributed
    FROM owners
    ORDER BY launch_key, n DESC, owner
)
SELECT g.launch_key, g.date, l.canonical_name AS lead_name, l.satellite_id AS lead_satellite_id,
       g.payloads, g.objects, t.owner, t.attributed AS owner_attributed,
       (SELECT count(DISTINCT o.owner) FROM owners o WHERE o.launch_key = g.launch_key) AS owners
FROM groups g
JOIN lead l ON l.launch_key = g.launch_key
JOIN top_owner t ON t.launch_key = g.launch_key
ORDER BY g.date DESC, g.launch_key DESC
"""

_NOTABLE_REENTRIES_SQL = f"""
WITH {_OWNER_CTE}
SELECT s.satellite_id, s.canonical_name AS name, s.decay_date AS date,
       s.launch_date AS launched, ow.owner
FROM satellite s
LEFT JOIN owner ow ON ow.satellite_id = s.satellite_id
WHERE s.object_type = 'PAYLOAD'
  AND s.decay_date > %(ref)s::date - %(days)s AND s.decay_date <= %(ref)s
  AND s.canonical_name NOT ILIKE 'starlink%%'
ORDER BY s.decay_date DESC, s.satellite_id DESC
LIMIT %(n)s
"""

# Per constellation operator and month: payloads in orbit at the month's last day, and payloads
# launched during the month, for the SERIES_SCAN_MONTHS month-ends ending with the reference
# month; the Python side trims to the SERIES_MONTHS ending at complete_through.
_CONSTELLATION_SERIES_SQL = f"""
WITH {_OWNER_CTE},
months AS (
    SELECT m::date AS m, (m + interval '1 month' - interval '1 day')::date AS month_end
    FROM generate_series(
        date_trunc('month', %(ref)s::date) - make_interval(months => %(scan)s - 1),
        date_trunc('month', %(ref)s::date),
        interval '1 month'
    ) m
),
fleet AS (
    SELECT ow.owner, s.launch_date, s.decay_date
    FROM satellite s
    JOIN owner ow ON ow.satellite_id = s.satellite_id
    WHERE s.object_type = 'PAYLOAD' AND ow.owner = ANY(%(ops)s)
)
SELECT f.owner, to_char(mo.m, 'YYYY-MM') AS month,
       count(*) FILTER (WHERE f.launch_date <= mo.month_end
                        AND (f.decay_date IS NULL OR f.decay_date > mo.month_end)) AS in_orbit,
       count(*) FILTER (WHERE f.launch_date >= mo.m AND f.launch_date <= mo.month_end)
           AS launched
FROM months mo CROSS JOIN fleet f
GROUP BY f.owner, mo.m
ORDER BY f.owner, mo.m
"""

_CONSTELLATION_NOW_SQL = f"""
WITH {_OWNER_CTE}
SELECT ow.owner, count(*) AS in_orbit_now
FROM satellite s
JOIN owner ow ON ow.satellite_id = s.satellite_id
WHERE s.object_type = 'PAYLOAD' AND ow.owner = ANY(%(ops)s)
  AND s.launch_date <= %(ref)s
  AND (s.decay_date IS NULL OR s.decay_date > %(ref)s)
GROUP BY ow.owner
"""

# ACTIVE payloads by current owner, from the latest-status materialization (migration 0025).
_ACTIVE_FLEET_SQL = f"""
WITH {_OWNER_CTE}
SELECT ow.owner, COALESCE(ow.attributed, false) AS attributed, count(*) AS n
FROM mv_latest_satellite_status ls
JOIN satellite s ON s.satellite_id = ls.satellite_id
LEFT JOIN owner ow ON ow.satellite_id = s.satellite_id
WHERE ls.canonical_status = 'ACTIVE' AND s.object_type = 'PAYLOAD'
GROUP BY ow.owner, ow.attributed
ORDER BY n DESC, ow.owner
"""

_STORMS_SQL = """
SELECT day, storm_level AS level, round(kp_max, 1) AS kp_max
FROM v_space_weather_daily
WHERE storm_level IS NOT NULL
  AND day > %(ref)s::date - %(days)s AND day <= %(ref)s
ORDER BY day DESC
"""


# ---- pure helpers (DB-free; tests/test_api_stories.py) ---------------------------------------


def month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_end(month: str) -> dt.date:
    """'2026-06' -> 2026-06-30."""
    year, mon = (int(x) for x in month.split("-"))
    return dt.date(year, mon, calendar.monthrange(year, mon)[1])


def is_partial(month: str, ref: dt.date) -> bool:
    """A month is partial while the reference date has not reached its last day; any month
    after the reference month is partial too (nothing in it has happened yet)."""
    return month_end(month) > ref


def complete_through(
    months: Sequence[dict], threshold: float = ATTRIBUTION_THRESHOLD
) -> str | None:
    """The newest month through which attribution is complete.

    Walks the months oldest first and stops at the first one where fewer than ``threshold`` of
    the payloads launched have a company owner; the answer is the month before it. A month with
    no launches has nothing unattributed and passes. Walking forward (rather than taking the
    newest passing month anywhere) is what keeps the in-orbit series honest: a month-end count
    is only complete when every earlier month's payloads are attributed, so a lone well-
    attributed month after a lagging one must not extend the series past the gap.

    Returns None when even the oldest month scanned falls short.
    """
    through = None
    for m in months:
        if m["launched"] and m["attributed"] / m["launched"] < threshold:
            break
        through = m["month"]
    return through


def next_month_start(month: str) -> dt.date:
    end = month_end(month)
    return end + dt.timedelta(days=1)


def pending_after(months: Sequence[dict], through: str | None) -> int:
    """Payloads launched after ``through`` that still have no company owner."""
    return sum(
        m["launched"] - m["attributed"]
        for m in months
        if through is None or m["month"] > through
    )


def trim_series(rows: Sequence[dict], through: str | None, n: int = SERIES_MONTHS) -> list[dict]:
    """The ``n`` newest month rows at or before ``through`` (none when through is None)."""
    if through is None:
        return []
    kept = [r for r in rows if r["month"] <= through]
    return kept[-n:]


def split_fleet(rows: Sequence[dict], top_n: int = TOP_OPERATORS) -> dict:
    """ACTIVE payloads by owner -> total, the top company operators, the payloads cataloged
    only by country (or with no owner at all), and everyone else.

    Country-level placeholders are kept out of the ranking: "United States" at number two would
    read as a fleet when it is mostly payloads still awaiting company attribution.
    """
    total = sum(r["n"] for r in rows)
    companies = sorted(
        (r for r in rows if r["attributed"] and r["owner"] is not None),
        key=lambda r: (-r["n"], r["owner"]),
    )
    top = [
        {"operator": r["owner"], "n": r["n"], "pct": round(100.0 * r["n"] / total, 1)}
        for r in companies[:top_n]
    ]
    country_only = sum(r["n"] for r in rows if not r["attributed"] or r["owner"] is None)
    other = total - sum(t["n"] for t in top) - country_only
    return {"total": total, "top": top, "country_only": country_only, "other": other}


# ---- compute ---------------------------------------------------------------------------------


def _run_all(db: psycopg.Connection, queries: Sequence[tuple[str, dict]]) -> list[list[dict]]:
    """Run independent queries in one pipeline (one network round trip rather than one each),
    falling back to sequential execution where libpq lacks pipeline support."""
    cursors = []
    if psycopg.Pipeline.is_supported():
        with db.pipeline():
            for sql, params in queries:
                cur = db.cursor()
                cur.execute(sql, params)
                cursors.append(cur)
    else:
        for sql, params in queries:
            cur = db.cursor()
            cur.execute(sql, params)
            cursors.append(cur)
    results = [cur.fetchall() for cur in cursors]
    for cur in cursors:
        cur.close()
    return results


def _build_stories(db: psycopg.Connection) -> dict:
    with db.cursor() as cur:
        cur.execute(_AS_OF_SQL)
        ref = cur.fetchone()["as_of"] or dt.datetime.now(dt.UTC).date()

    ops = [op for _, op in CONSTELLATIONS]
    monthly, window, recent, notable, series_rows, now_rows, fleet_rows, storms = _run_all(
        db,
        [
            (_MONTHLY_SQL, {"ref": ref, "scan": SCAN_MONTHS}),
            (_WINDOW_SQL, {"ref": ref, "days": WINDOW_DAYS}),
            (_RECENT_LAUNCHES_SQL, {"ref": ref, "days": RECENT_LAUNCH_DAYS, "n": RECENT_LAUNCHES}),
            (_NOTABLE_REENTRIES_SQL, {"ref": ref, "days": WINDOW_DAYS, "n": NOTABLE_REENTRIES}),
            (_CONSTELLATION_SERIES_SQL, {"ref": ref, "scan": SERIES_SCAN_MONTHS, "ops": ops}),
            (_CONSTELLATION_NOW_SQL, {"ref": ref, "ops": ops}),
            (_ACTIVE_FLEET_SQL, {}),
            (_STORMS_SQL, {"ref": ref, "days": STORM_DAYS}),
        ],
    )

    through = complete_through(monthly)
    w = window[0]

    by_op: dict[str, list[dict]] = {}
    for r in series_rows:
        by_op.setdefault(r["owner"], []).append(r)
    now_by_op = {r["owner"]: r["in_orbit_now"] for r in now_rows}
    constellations = []
    for key, op in CONSTELLATIONS:
        kept = trim_series(by_op.get(op, []), through)
        constellations.append(
            {
                "key": key,
                "operator": op,
                "in_orbit_now": now_by_op.get(op, 0),
                # launched in the twelve months ending at complete_through, so the lag cannot
                # make a build-out look like it slowed down
                "added_12m": sum(r["launched"] for r in kept[-12:]),
                "series": [{"month": r["month"], "in_orbit": r["in_orbit"]} for r in kept],
            }
        )

    return {
        "as_of": ref.isoformat(),
        "launches_monthly": [
            {
                "month": m["month"],
                "launched": m["launched"],
                "reentered": m["reentered"],
                "partial": is_partial(m["month"], ref),
            }
            for m in monthly[-MONTHS_SHOWN:]
        ],
        "last_30d": {
            "launches": w["launches"],
            "payloads_launched": w["payloads_launched"],
            "payloads_reentered": w["payloads_reentered"],
        },
        "recent_launches": [
            {
                "launch_key": r["launch_key"],
                "date": r["date"].isoformat(),
                "lead_name": r["lead_name"],
                "lead_satellite_id": r["lead_satellite_id"],
                "payloads": r["payloads"],
                "objects": r["objects"],
                "owner": r["owner"],
                "owner_attributed": bool(r["owner_attributed"]),
                "owners": r["owners"],
            }
            for r in recent
        ],
        "reentries": {
            "window_days": WINDOW_DAYS,
            "payloads": w["payloads_reentered"],
            "starlink": w["starlink"],
            "starlink_v1": w["starlink_v1"],
            "notable": [
                {
                    "satellite_id": r["satellite_id"],
                    "name": r["name"],
                    "date": r["date"].isoformat(),
                    "launched": r["launched"].isoformat() if r["launched"] else None,
                    "owner": r["owner"],
                }
                for r in notable
            ],
        },
        "constellations": constellations,
        "attribution": {
            "pending_payloads": pending_after(monthly, through),
            "since": next_month_start(through).isoformat() if through else None,
            "complete_through": through,
        },
        "active_fleet": split_fleet(fleet_rows),
        "storms": [
            {"day": r["day"].isoformat(), "level": r["level"], "kp_max": float(r["kp_max"])}
            for r in storms
        ],
    }


_cache = cache.register("stories", _build_stories)


@router.get("/stories")
def get_stories(db=Depends(get_db)):
    return _cache.get(db)
