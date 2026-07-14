"""GET /api/audit/summary -- the auditor's three headline numbers, computed live and read-only.

Everything here is derived on the fly from the fact layer + the Phase-2 ``sat_daily`` physics
aggregate; nothing is materialised. The three panels correspond to the three legs of the auditor
thesis:

  a. kuiper_milestone   -- Amazon's Kuiper deployment vs the FCC 50%-by-2026-07-30 obligation
                           (1,618 satellites). Each bird is bucketed by its current orbit into
                           at-shell / raising / deorbited so the "how close are they really" story is
                           legible, not just a raw launched count.
  b. lingering_leaderboard -- per benchmark operator, INACTIVE LEO payloads still loitering with a
                           perigee above 500 km (i.e. parked high, not deorbiting) -- the objects a
                           responsible operator would have lowered by now.
  c. active_but_decaying -- objects the catalog still calls ACTIVE whose physics says otherwise:
                           they have left their operational plateau and are still sinking. "Catalog
                           says active, physics says decaying."

All queries filter by NORAD / operator first and only touch the multi-million-row ``sat_daily`` for
(c) and the Kuiper settle-check, so the endpoint stays a few-seconds live read rather than a scan.
"""

from fastapi import APIRouter, Depends

from api.deps import get_db

router = APIRouter(prefix="/audit", tags=["audit"])

# Benchmark operators by canonical name (verbatim from scripts/backfill_gp_history.BENCHMARK_OPERATORS
# so the audit's fleet universe matches the gp_history backfill exactly). Each is rolled up together
# with its operator_relationship children -- this is how Eutelsat picks up the ex-OneWeb LEO fleet.
BENCHMARK_OPERATORS = [
    "SpaceX", "Eutelsat", "Planet Labs", "Spire", "Iridium", "ICEYE", "Capella Space", "Amazon",
]

# Kuiper (Amazon) milestone constants. The FCC authorisation requires 50% of the 3,236-satellite
# constellation -- 1,618 birds -- on orbit by 2026-07-30. Operational shell is a ~630 km circular
# orbit; a bird is "at shell" when its mean altitude sits within +/-15 km of that band AND has
# settled there (its trailing-14-day mean altitude is in-band too), mirroring the +/-15 km in-band
# test v_time_to_operational uses for time-to-operational.
KUIPER_OPERATOR = "Amazon"
KUIPER_REQUIRED = 1618
KUIPER_DEADLINE = "2026-07-30"
_KUIPER_SHELL_LO = 615.0  # 630 km - 15 km tolerance
_KUIPER_SHELL_HI = 645.0  # 630 km + 15 km tolerance

# Kuiper fleet bucketed by current orbit. Fleet = PAYLOAD sats (with a NORAD) owned by Amazon or an
# Amazon child operator. Altitude = mean of the latest element set (perigee+apogee)/2; the trailing
# 14-day mean from sat_daily gates the "settled" half of at-shell. deployed_last_30d feeds the FE's
# linear projection toward the deadline.
_KUIPER_SQL = """
WITH target AS (
    SELECT operator_id FROM operator WHERE canonical_name = %(op)s
    UNION
    SELECT r.child_id
    FROM operator_relationship r
    JOIN operator p ON p.operator_id = r.parent_id
    WHERE p.canonical_name = %(op)s
),
fleet AS (
    SELECT DISTINCT s.satellite_id, s.norad_id, s.launch_date
    FROM satellite s
    JOIN satellite_operator so ON so.satellite_id = s.satellite_id
    WHERE so.role = 'owner'
      AND so.operator_id IN (SELECT operator_id FROM target)
      AND s.norad_id IS NOT NULL
      AND s.object_type = 'PAYLOAD'
),
le AS (
    SELECT DISTINCT ON (norad_id) norad_id, (perigee_km + apogee_km) / 2.0 AS alt
    FROM gp_elements
    WHERE norad_id IN (SELECT norad_id FROM fleet)
    ORDER BY norad_id, epoch DESC
),
gmax AS (SELECT max(day) AS g FROM sat_daily),
trail AS (
    SELECT sd.norad_id, avg((sd.perigee_min + sd.apogee_max) / 2.0) AS trail_alt
    FROM sat_daily sd CROSS JOIN gmax
    WHERE sd.norad_id IN (SELECT norad_id FROM fleet)
      AND sd.day > gmax.g - interval '14 days'
    GROUP BY sd.norad_id
),
ls AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
    FROM satellite_status_history
    WHERE satellite_id IN (SELECT satellite_id FROM fleet)
    ORDER BY satellite_id, observed_at DESC
)
SELECT
    count(*) AS deployed_total,
    count(*) FILTER (WHERE COALESCE(ls.canonical_status, 'UNKNOWN') = 'DECAYED') AS deorbited,
    count(*) FILTER (
        WHERE COALESCE(ls.canonical_status, 'UNKNOWN') <> 'DECAYED'
          AND le.alt BETWEEN %(lo)s AND %(hi)s
          AND (t.trail_alt IS NULL OR t.trail_alt BETWEEN %(lo)s AND %(hi)s)
    ) AS at_shell,
    count(*) FILTER (
        WHERE COALESCE(ls.canonical_status, 'UNKNOWN') <> 'DECAYED'
          AND le.alt IS NOT NULL AND le.alt < %(lo)s
    ) AS raising,
    count(*) FILTER (WHERE f.launch_date > current_date - interval '30 days') AS deployed_last_30d
FROM fleet f
LEFT JOIN le ON le.norad_id = f.norad_id
LEFT JOIN trail t ON t.norad_id = f.norad_id
LEFT JOIN ls ON ls.satellite_id = f.satellite_id
"""

# Per benchmark operator: how many of its PAYLOADs are INACTIVE yet still loitering in LEO with a
# perigee above 500 km (parked high, not lowering to deorbit). avg_alt_km is the mean altitude of
# that loitering set. Top 6 by count. Altitude from the latest element set per NORAD.
_LINGERING_SQL = """
WITH bench(name) AS (SELECT unnest(%(names)s::text[])),
target AS (
    SELECT b.name, o.operator_id
    FROM bench b JOIN operator o ON o.canonical_name = b.name
    UNION
    SELECT b.name, r.child_id
    FROM bench b
    JOIN operator p ON p.canonical_name = b.name
    JOIN operator_relationship r ON r.parent_id = p.operator_id
),
owned AS (
    SELECT DISTINCT t.name, s.satellite_id, s.norad_id
    FROM target t
    JOIN satellite_operator so ON so.operator_id = t.operator_id AND so.role = 'owner'
    JOIN satellite s ON s.satellite_id = so.satellite_id
    WHERE s.object_type = 'PAYLOAD' AND s.norad_id IS NOT NULL
)
-- Latest status + latest element set are fetched per fleet member via LATERAL index seeks
-- (satellite_status_history PK (satellite_id, observed_at); gp_elements PK (norad_id, epoch)) --
-- bounded to the benchmark fleet, never a catalog-wide DISTINCT ON scan.
SELECT
    o.name AS operator,
    count(*) AS count,
    round(avg(le.alt)::numeric, 1)::float8 AS avg_alt_km
FROM owned o
JOIN LATERAL (
    SELECT ssh.canonical_status
    FROM satellite_status_history ssh
    WHERE ssh.satellite_id = o.satellite_id
    ORDER BY ssh.observed_at DESC
    LIMIT 1
) ls ON TRUE
JOIN LATERAL (
    SELECT g.perigee_km, (g.perigee_km + g.apogee_km) / 2.0 AS alt
    FROM gp_elements g
    WHERE g.norad_id = o.norad_id
    ORDER BY g.epoch DESC
    LIMIT 1
) le ON TRUE
WHERE ls.canonical_status = 'INACTIVE'
  AND le.perigee_km > 500
  AND le.alt < 2000
GROUP BY o.name
ORDER BY count DESC, o.name
LIMIT 6
"""

# ACTIVE-status objects in post-plateau decay. Operationalised over sat_daily per NORAD:
#   * sma_first -- first-90-day mean sma -- the object's early / operational plateau baseline
#   * sma_last  -- trailing-14-day mean sma is at least 15 km BELOW that baseline: left the plateau
#   * still declining -- sma_last is below sma_prev, the immediately preceding 14-day window's mean,
#                        so an object that has bottomed out / already re-entered (gone flat) does NOT
#                        count.
# Restricted to objects whose latest canonical status is still ACTIVE: the catalog-vs-physics gap.
# One pass over sat_daily: a windowed min/max day per NORAD feeds the three conditional-average
# windows, so the multi-million-row aggregate is scanned once rather than per-window.
_DECAYING_SQL = """
WITH marked AS (
    SELECT
        norad_id, day, sma_avg,
        min(day) OVER (PARTITION BY norad_id) AS d0,
        max(day) OVER (PARTITION BY norad_id) AS dmax
    FROM sat_daily
),
agg AS (
    SELECT
        norad_id,
        avg(sma_avg) FILTER (WHERE day < d0 + interval '90 days') AS sma_first,
        avg(sma_avg) FILTER (WHERE day > dmax - interval '14 days') AS sma_last,
        avg(sma_avg) FILTER (
            WHERE day <= dmax - interval '14 days' AND day > dmax - interval '28 days'
        ) AS sma_prev
    FROM marked
    GROUP BY norad_id
),
ls AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
    FROM satellite_status_history
    ORDER BY satellite_id, observed_at DESC
)
SELECT count(*) AS n
FROM agg
JOIN satellite s ON s.norad_id = agg.norad_id
JOIN ls ON ls.satellite_id = s.satellite_id
WHERE ls.canonical_status = 'ACTIVE'
  AND agg.sma_last <= agg.sma_first - 15
  AND agg.sma_last < agg.sma_prev
"""


@router.get("/summary")
def audit_summary(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute(_KUIPER_SQL, {"op": KUIPER_OPERATOR, "lo": _KUIPER_SHELL_LO, "hi": _KUIPER_SHELL_HI})
        k = cur.fetchone()

        cur.execute(_LINGERING_SQL, {"names": BENCHMARK_OPERATORS})
        lingering = cur.fetchall()

        cur.execute(_DECAYING_SQL)
        decaying = cur.fetchone()

    return {
        "kuiper_milestone": {
            "at_shell": k["at_shell"],
            "raising": k["raising"],
            "deorbited": k["deorbited"],
            "deployed_total": k["deployed_total"],
            "deployed_last_30d": k["deployed_last_30d"],
            "required": KUIPER_REQUIRED,
            "deadline": KUIPER_DEADLINE,
        },
        "lingering_leaderboard": lingering,
        "active_but_decaying": decaying["n"],
    }
