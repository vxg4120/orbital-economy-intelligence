"""The drag environment: space weather indices joined to the fleet's measured response.

GET /api/environment    daily series (indices + fleet drag) plus the latest observed state

Two layers, deliberately distinct in the payload because they make different claims: the
indices (planetary Kp/Ap, F10.7 solar flux; CelesTrak's consolidated file, public domain) say
what the sun and magnetosphere did; median_dsma_m says what OUR data shows the LEO fleet doing
about it, measured as the fleet-wide median one-day semi-major-axis change over consecutive-day
element pairs (maneuver-robust by construction: propulsion lifts a few satellites a lot, a
storm drags everyone a little, and the median follows everyone). Days with under 500 measured
pairs are withheld by the view, and the newest drag row is provisional until its day closes.

Rows whose f107_data_type is 'PRD' are CelesTrak's predictions, published here as the forward
view; they never carry drag numbers because the drag is a measurement.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_db

router = APIRouter(prefix="/environment", tags=["environment"])

ATTRIBUTION = (
    "Space weather indices: CelesTrak consolidated file (celestrak.org/SpaceData/), US "
    "Government work. Drag response: computed from this platform's GP element history."
)


def environment_rows(db, days: int, forward: int) -> dict:
    """Shared by the HTTP route and the MCP tool, so the two surfaces cannot drift."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT day, round(kp_max, 1) AS kp_max, ap_avg, ap_max,
                   f107_obs, f107_obs_center81, f107_data_type, storm_level,
                   sats_observed, median_dsma_m, p10_dsma_m, p90_dsma_m
            FROM v_drag_environment_daily
            WHERE day >= current_date - %(days)s
              AND day <= current_date + %(forward)s
            ORDER BY day
            """,
            {"days": days, "forward": forward},
        )
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT day, round(kp_max, 1) AS kp_max, ap_avg, ap_max, storm_level,
                   f107_obs, f107_obs_center81
            FROM v_space_weather_daily
            WHERE f107_data_type = 'OBS'
            ORDER BY day DESC LIMIT 1
            """
        )
        latest = cur.fetchone()
        cur.execute(
            """
            SELECT day, storm_level, round(kp_max, 1) AS kp_max, ap_avg,
                   sats_observed, median_dsma_m
            FROM v_drag_environment_daily
            WHERE median_dsma_m IS NOT NULL AND day >= current_date - %(days)s
            ORDER BY median_dsma_m ASC LIMIT 1
            """,
            {"days": days},
        )
        worst = cur.fetchone()
    return {
        "rows": rows,
        "latest_observed": latest,
        "worst_drag_day": worst,
        "note": (
            "median_dsma_m is the fleet-wide median one-day SMA change over LEO "
            "consecutive-day element pairs: maneuver-robust, so when it moves, the "
            "atmosphere moved everyone. Predicted rows (f107_data_type PRD) carry no drag "
            "numbers because drag here is a measurement, not a model."
        ),
        "attribution": ATTRIBUTION,
    }


@router.get("")
def environment(
    db=Depends(get_db),
    days: int = Query(60, ge=7, le=730),
    forward: int = Query(7, ge=0, le=45),
):
    """Daily drag-environment series: `days` of history plus `forward` predicted days."""
    return environment_rows(db, days, forward)
