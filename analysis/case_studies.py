"""Behavioral-status oracle — exemplar time series (research scaffold).

Pulls ~12 hand-picked satellites from the ``sat_daily`` continuous aggregate (and,
for one panel, per-epoch ``gp_elements``) and renders one annotated small-multiple
PNG per exemplar into ``analysis/figs/``, plus a contact-sheet overview grid.

The point is not to *detect* anything — the repo owner will design the detector.
The point is to put the raw physical signatures in front of him with honest
numbers, so the taxonomy in ``analysis/BEHAVIORAL_STATUS.md`` is grounded in real
element-set behaviour rather than hand-waving.

Read-only. Run:  ``.venv/bin/python analysis/case_studies.py``

Every satellite here was chosen from live queries against the OEI database on the
2025-07 -> 2026-07 dense-coverage window. The ``teaches`` string on each exemplar
is the one sentence the panel is meant to make obvious.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")  # headless: write PNGs, never open a window
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import psycopg  # noqa: E402

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DSN = os.environ.get("OEI_DSN") or os.environ.get(
    "DATABASE_URL", "postgresql://oei:oei@localhost:5433/oei"
)
FIGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figs")

# Dense-coverage window. Before ~2025-07 only a handful of objects have history;
# the bulk of gp_elements is the Space-Track gp_history backfill of the benchmark
# fleet plus the current GP snapshot.
WIN_START = "2025-07-01"

ACCENT = "#c1440e"  # a single warm accent (rust); everything else is greyscale
INK = "#222222"
FAINT = "#d9d9d9"

# --------------------------------------------------------------------------- #
# Exemplar registry — the whole editorial argument lives in this list
# --------------------------------------------------------------------------- #


@dataclass
class Exemplar:
    norad: int
    group: str  # taxonomy bucket (for the overview grid ordering / captions)
    teaches: str  # the one-sentence lesson -> goes in the panel title
    caption: str  # numbers / context -> small print under the title
    kind: str = "arc"  # "arc" (daily sma) or "sawtooth" (per-epoch zoom)
    # optional manual event annotations: list of (iso_date, label)
    events: list[tuple[str, str]] = field(default_factory=list)
    # for the sawtooth zoom
    zoom_start: str = "2026-01-01"
    zoom_end: str = "2026-03-15"


EXEMPLARS: list[Exemplar] = [
    # -- (a) active station-keeping: low variance, held to a shell ---------- #
    Exemplar(
        42959,
        "station-keeper",
        "Active station-keeping is a ruler-flat line",
        "Iridium NEXT LEO. sma held to +/-2.5 m (1 sigma) over a full year at "
        "7155.8 km. Propulsion + control loop = near-zero drift.",
    ),
    Exemplar(
        40875,
        "station-keeper",
        "GEO station-keeping: flat, but at a different tolerance",
        "Eutelsat GEO comsat. sma 42164.8 km, held to ~0.12 km. Wider dead-band "
        "than LEO but still unmistakably controlled.",
    ),
    Exemplar(
        55290,
        "station-keeper",
        "A healthy operational Starlink holds its shell",
        "Starlink Group 2 (LEO ~572 km). sma 6950.2 km, 1 sigma ~17 m, range "
        "<0.16 km/yr. This is the 'alive' baseline to detect departures from.",
    ),
    # -- (b) passive-healthy: no propulsion, smooth drag decay (Planet) ----- #
    Exemplar(
        60483,
        "passive-decay",
        "Passive drag decay: smooth, monotonic, no fight",
        "Planet Dove (3U, no propulsion). Glides down ~0.65 km/day, sma "
        "6858 -> 6550 km, reentry 2026-04-25. The 'never station-kept' shape.",
        events=[("2026-04-25", "reentry")],
    ),
    Exemplar(
        62643,
        "passive-decay",
        "Every Dove traces the same drag curve",
        "Planet Dove. sma 6881 -> 6584 km, reentry 2026-06-19. Slope steepens as "
        "it drops into denser air — the classic decaying-exponential envelope.",
        events=[("2026-06-19", "reentry")],
    ),
    # -- (d) controlled deorbit: commanded rapid descent (SpaceX) ----------- #
    Exemplar(
        49131,
        "controlled-deorbit",
        "Controlled deorbit: plateau, then commanded descent",
        "Starlink Group 2-1. Held at 6950 km, then station-keeping ends and it is "
        "walked down ~420 km to reentry 2026-05-20 (GCAT TOp 2026-02-17).",
        events=[("2026-05-20", "reentry")],
    ),
    Exemplar(
        48880,
        "controlled-deorbit",
        "Retirement signature: end-of-ops months before reentry",
        "Starlink TSP2-02. Plateau -> decay onset -> reentry 2026-06-14. The "
        "operationally-dead date is NOT the reentry date; that gap is the product.",
        events=[("2026-06-14", "reentry")],
    ),
    Exemplar(
        56811,
        "controlled-deorbit",
        "Same operator, same disposal grammar",
        "Starlink Group 2-10. sma 6950 -> 6536 km, reentry 2026-05-26. A steady, "
        "shallow, *controlled* slope — distinguishable from a tumble.",
        events=[("2026-05-26", "reentry")],
    ),
    # -- (e) orbit-raise: should NOT read as an anomaly --------------------- #
    Exemplar(
        65777,
        "orbit-raise",
        "Orbit-raising climbs — do not flag it as anomalous",
        "Kuiper KA03-16. Rises +364 km from a low insertion (sma 6648 -> 7013 km) "
        "to its operational shell. A rising sma is birth, not death.",
        events=[("2025-10-07", "insertion")],
    ),
    # -- (c) death signature: station-keeping collapse -> decay onset ------- #
    Exemplar(
        52579,
        "death-in-progress",
        "MONEY: station-keeping stopped, catalog still says ACTIVE",
        "Starlink-3893. Ruler-flat at 6917.9 km for 10 months, then a clean break "
        "~May 2026 into monotonic decay (perigee 538 -> 396 km). Status: ACTIVE, "
        "decay_date: null. Physics knows before the catalog does.",
        events=[("2026-05-01", "plateau departure")],
    ),
    # -- the false-positive twin of the money example ----------------------- #
    Exemplar(
        54858,
        "maneuver-not-death",
        "The trap: a commanded move that RE-PLATEAUS",
        "Starlink Group 5-1-39. Flat at 6937 km, drops ~76 km in Mar 2026, then "
        "re-plateaus at 6861 km and holds. Same variance spike as death (#52579) "
        "but it is a shell change. A naive detector flags this as dead.",
        events=[("2026-03-05", "commanded lower")],
    ),
    # -- the re-boost sawtooth, only visible at meter scale ------------------ #
    Exemplar(
        42959,
        "reboost-sawtooth",
        "The re-boost sawtooth hides at ~15 m — zoom 1000x",
        "Same Iridium as panel 1, per-epoch, detrended. The drag-makeup sawtooth "
        "(drift down, small burn up) is real but sub-100 m — the daily cagg "
        "averages it flat. Peak-finding on dsma/dt needs per-epoch data.",
        kind="sawtooth",
        zoom_start="2026-01-01",
        zoom_end="2026-03-11",
    ),
]


# --------------------------------------------------------------------------- #
# Data access (read-only, parallel workers off to dodge server shm limits)
# --------------------------------------------------------------------------- #


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(DSN, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SET max_parallel_workers_per_gather = 0")
        cur.execute("SET statement_timeout = '60s'")
    return conn


def fetch_meta(conn: psycopg.Connection, norad: int) -> dict:
    """Canonical name, launch/decay dates and latest catalog status."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT satellite_id, canonical_name, launch_date, decay_date "
            "FROM satellite WHERE norad_id = %s",
            (norad,),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        sat_id, name, launch, decay = row
        cur.execute(
            "SELECT canonical_status FROM satellite_status_history "
            "WHERE satellite_id = %s ORDER BY observed_at DESC LIMIT 1",
            (sat_id,),
        )
        st = cur.fetchone()
    return {
        "name": name,
        "launch": launch,
        "decay": decay,
        "status": st[0] if st else "UNKNOWN",
    }


def fetch_daily(conn: psycopg.Connection, norad: int):
    """Daily sma/perigee/apogee series over the dense window."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day, sma_avg, perigee_min, apogee_max "
            "FROM sat_daily WHERE norad_id = %s AND day > %s "
            "AND sma_avg IS NOT NULL ORDER BY day",
            (norad, WIN_START),
        )
        rows = cur.fetchall()
    days = np.array([r[0] for r in rows])
    sma = np.array([float(r[1]) for r in rows])
    peri = np.array([float(r[2]) if r[2] is not None else np.nan for r in rows])
    return days, sma, peri


def fetch_epoch_sma(conn: psycopg.Connection, norad: int, start: str, end: str):
    """Per-epoch semi-major axis (metres) for the meter-scale sawtooth zoom."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT epoch, semi_major_axis_km * 1000.0 "
            "FROM gp_elements WHERE norad_id = %s "
            "AND epoch BETWEEN %s AND %s ORDER BY epoch",
            (norad, start, end),
        )
        rows = cur.fetchall()
    t = np.array([r[0] for r in rows])
    sma_m = np.array([float(r[1]) for r in rows])
    return t, sma_m


# --------------------------------------------------------------------------- #
# A deliberately-crude "plateau departure" heuristic, ONLY for annotation.
# This is not the detector — it is a pointer so the eye lands on the break.
# --------------------------------------------------------------------------- #


def plateau_departure(days, sma, plateau_n: int = 120, k: float = 8.0):
    """First day sma leaves its early-window plateau by k*sigma, held 5+ days.

    Returns a numpy datetime or None. Intentionally simple: the owner will
    replace this with a real change-point method (CUSUM / BOCPD / PELT).
    """
    if len(sma) < plateau_n + 20:
        return None
    base = sma[:plateau_n]
    med = np.median(base)
    sigma = max(np.std(base), 0.02)  # floor at 20 m so ruler-flat objects work
    thresh = med - k * sigma
    below = sma < thresh
    # require 5 of 7 consecutive samples below threshold to debounce
    for i in range(plateau_n, len(sma) - 6):
        if below[i] and below[i : i + 7].sum() >= 5:
            return days[i]
    return None


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _style_axes(ax):
    ax.grid(axis="y", color=FAINT, linewidth=0.6, alpha=0.7)
    ax.grid(axis="x", visible=False)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#999999")
        ax.spines[spine].set_linewidth(0.8)
    ax.tick_params(colors="#666666", labelsize=8, length=3)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b'%y"))


def _iso(d: str) -> datetime:
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


def render_arc(conn, ex: Exemplar, meta: dict, ax=None, standalone=True):
    days, sma, _ = fetch_daily(conn, ex.norad)
    if len(days) == 0:
        return False
    if ax is None:
        fig, ax = plt.subplots(figsize=(7.4, 4.4))
    else:
        fig = None

    ax.plot(days, sma, color=ACCENT, linewidth=1.1)

    # faint plateau reference (early-window median) where a plateau exists
    if ex.group in {"station-keeper", "death-in-progress", "maneuver-not-death",
                    "controlled-deorbit"}:
        med = float(np.median(sma[: min(120, len(sma))]))
        ax.axhline(med, color=INK, linewidth=0.6, linestyle=(0, (1, 3)), alpha=0.4)

    # auto plateau-departure marker (annotation only)
    dep = plateau_departure(days, sma)
    if dep is not None:
        ax.axvline(dep, color=INK, linewidth=0.7, linestyle=":", alpha=0.55)

    # manual event markers from the registry
    ymin, ymax = float(np.nanmin(sma)), float(np.nanmax(sma))
    span = max(ymax - ymin, 0.05)
    for iso, label in ex.events:
        x = _iso(iso)
        ax.axvline(x, color=INK, linewidth=0.7, linestyle="--", alpha=0.5)
        ax.annotate(
            label,
            xy=(x, ymax),
            xytext=(4, -2),
            textcoords="offset points",
            fontsize=7,
            color="#555555",
            rotation=90,
            va="top",
            ha="left",
        )

    ax.set_ylim(ymin - 0.06 * span, ymax + 0.10 * span)
    _style_axes(ax)

    if standalone:
        ax.set_ylabel("semi-major axis (km)", fontsize=9, color="#444444")
        _titlebox(ax, ex, meta)
        fig.tight_layout()
    return fig


def render_sawtooth(conn, ex: Exemplar, meta: dict, ax=None, standalone=True):
    t, sma_m = fetch_epoch_sma(conn, ex.norad, ex.zoom_start, ex.zoom_end)
    if len(t) < 10:
        return False
    if ax is None:
        fig, ax = plt.subplots(figsize=(7.4, 4.4))
    else:
        fig = None

    # detrend: subtract a linear fit so the ~15 m dead-band sawtooth is visible
    tnum = mdates.date2num(t)
    coeff = np.polyfit(tnum, sma_m, 1)
    resid = sma_m - np.polyval(coeff, tnum)

    ax.plot(t, resid, color=ACCENT, linewidth=0.9, marker="o", markersize=2.2,
            markerfacecolor=ACCENT, markeredgecolor="none")
    ax.axhline(0, color=INK, linewidth=0.6, linestyle=(0, (1, 3)), alpha=0.4)
    _style_axes(ax)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))

    if standalone:
        ax.set_ylabel("sma residual, detrended (m)", fontsize=9, color="#444444")
        _titlebox(ax, ex, meta)
        fig.tight_layout()
    return fig


def _titlebox(ax, ex: Exemplar, meta: dict):
    name = meta.get("name", f"NORAD {ex.norad}")
    status = meta.get("status", "?")
    ax.set_title(
        f"{name}  ({ex.norad})  ·  status={status}\n{ex.teaches}",
        fontsize=10.5,
        color=INK,
        loc="left",
        pad=10,
        fontweight="normal",
    )
    ax.text(
        0.0,
        -0.20,
        ex.caption,
        transform=ax.transAxes,
        fontsize=7.6,
        color="#666666",
        va="top",
        ha="left",
        wrap=True,
    )


def render_panel(conn, ex: Exemplar, idx: int) -> str | None:
    meta = fetch_meta(conn, ex.norad)
    render = render_sawtooth if ex.kind == "sawtooth" else render_arc
    fig = render(conn, ex, meta)
    if fig is False or fig is None:
        print(f"  ! {ex.norad}: no data, skipped")
        return None
    fig.subplots_adjust(bottom=0.24, top=0.84)
    fname = f"{idx:02d}_{ex.group}_{ex.norad}.png"
    path = os.path.join(FIGDIR, fname)
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    kb = os.path.getsize(path) / 1024
    print(f"  ok {fname}  ({kb:.0f} KB)")
    return path


def render_overview(conn):
    """Contact sheet: all exemplars as small multiples in one grid."""
    n = len(EXEMPLARS)
    ncol, nrow = 3, (n + 2) // 3
    fig, axes = plt.subplots(nrow, ncol, figsize=(13.5, 3.0 * nrow))
    axes = axes.ravel()
    for i, ex in enumerate(EXEMPLARS):
        ax = axes[i]
        meta = fetch_meta(conn, ex.norad)
        if ex.kind == "sawtooth":
            render_sawtooth(conn, ex, meta, ax=ax, standalone=False)
        else:
            render_arc(conn, ex, meta, ax=ax, standalone=False)
        # tight-range panels otherwise emit a y-offset label ("+7.155e3") at the
        # top-left that collides with the title in a small cell. Format the ticks
        # plainly so no offset text is generated; the absolute baseline is carried
        # by the standalone panel. (sawtooth panel keeps its auto formatting.)
        if ex.kind != "sawtooth":
            ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        ax.set_title(
            f"{i + 1}. {meta.get('name', ex.norad)} ({ex.norad})",
            fontsize=8.5, color=INK, loc="left", pad=14,
        )
        ax.text(1.0, 1.02, ex.group, transform=ax.transAxes, fontsize=7,
                color=ACCENT, va="bottom", ha="right")
    for j in range(n, len(axes)):
        axes[j].set_visible(False)
    fig.suptitle(
        "Behavioral status oracle — element-set signatures (2025-07 → 2026-07)",
        fontsize=13, color=INK, x=0.01, ha="left", fontweight="normal",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    path = os.path.join(FIGDIR, "00_overview_grid.png")
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    print(f"  ok 00_overview_grid.png  ({os.path.getsize(path) / 1024:.0f} KB)")


# --------------------------------------------------------------------------- #


def main() -> None:
    os.makedirs(FIGDIR, exist_ok=True)
    print(f"connecting: {DSN}")
    with _connect() as conn:
        print(f"rendering {len(EXEMPLARS)} exemplar panels -> {FIGDIR}")
        made = 0
        for i, ex in enumerate(EXEMPLARS, start=1):
            if render_panel(conn, ex, i):
                made += 1
        print("rendering overview grid")
        render_overview(conn)
    print(f"done: {made}/{len(EXEMPLARS)} panels + 1 overview grid")


if __name__ == "__main__":
    main()
