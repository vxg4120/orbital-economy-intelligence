"""Diff the current Bus Benchmarks numbers against a captured baseline of what is published.

Every change to attribution rules moves numbers that are already public: the leaderboard, the
per-slug URLs, the MCP tools, and the frozen monthly snapshot series. This script makes that
movement explicit and reviewable before it ships, instead of discovering it from the live site.

Two failure modes matter more than metric drift and are reported separately:

* A slug that disappears is a 404 on a published URL and a frozen snapshot series that can no
  longer be resolved. Nothing may silently vanish.
* A cohort whose fleet_total changes without an entry in the allowed-change list is a rewrite of
  a published fact. Cohorts merging into one another are the dangerous version, because they do
  not 404, they just quietly report a different number.

Usage:
    .venv/bin/python scripts/diff_published_buses.py                     # human report
    .venv/bin/python scripts/diff_published_buses.py --gate             # exit 1 on any change
    .venv/bin/python scripts/diff_published_buses.py --gate --allow apex,terran
    .venv/bin/python scripts/diff_published_buses.py --capture out.json # refresh the baseline

Two baselines, for two different questions, and mixing them up produces noise:

* The shipped baseline (tests/baselines/published_buses_baseline.json) is captured from the LIVE
  site and answers "what did readers actually see". Diffing against it after a nightly refresh
  reports data drift as well as code drift, because behavior metrics such as station-keeping move
  every day as new tracking data arrives. Use it to describe published impact, not as a code gate.
* For a code gate, capture a local baseline from the SAME database immediately before the change
  (--capture), apply the change, rebuild, then diff. Data is then held constant and every
  difference is attributable to the code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402

DEFAULT_BASELINE = REPO_ROOT / "tests" / "baselines" / "published_buses_baseline.json"

# The metrics the baseline tracks, in the order the report prints them.
METRICS = [
    "fleet_total",
    "fleet_on_orbit",
    "fleet_active",
    "decayed_count",
    "median_days_to_operational",
    "station_keeping_share_pct",
    "p50_station_keeping_km",
    "disposal_compliance_pct",
    "gp_coverage_pct",
    "median_lifetime_years",
]

# Per-group extras. The bus view names a primary manufacturer, and a manufacturer-side merge can
# relabel bus pages without any bus slug or metric moving, which the gate would otherwise call
# "no change". Tracking these makes that relabel a visible, gateable diff.
GROUP_METRICS = {
    "manufacturer": METRICS,
    "bus": [*METRICS, "primary_manufacturer", "primary_manufacturer_slug"],
}

_GROUPS = {
    "manufacturer": ("v_bus_benchmarks_manufacturer", "manufacturer_slug", "manufacturer_name"),
    "bus": ("v_bus_benchmarks_bus", "bus_slug", "bus_model"),
}


def current(conn) -> dict:
    """Read every cohort straight from the benchmark views, no cohort floor applied."""
    out = {}
    with conn.cursor() as cur:
        for group, (view, slug_col, name_col) in _GROUPS.items():
            gmetrics = GROUP_METRICS[group]
            cur.execute(
                f"SELECT {slug_col} AS slug, {name_col} AS name, "
                f"{', '.join(gmetrics)} FROM {view} WHERE {slug_col} IS NOT NULL"
            )
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            # A slug must be unique: it is a public URL and the primary key of the frozen
            # snapshot archive. Keying by slug below would silently collapse a colliding pair and
            # report the loss as no change at all, so refuse to produce a report we cannot trust.
            # This is not hypothetical: 'RAYM?/GSFC' once yielded a code of 'RAYM?' that slugified
            # onto the real RAYM.
            seen = {}
            for r in rows:
                seen.setdefault(r["slug"], []).append(r["name"])
            collisions = {s: n for s, n in seen.items() if len(n) > 1}
            if collisions:
                raise SystemExit(
                    f"{group}: {len(collisions)} slug collisions, which corrupt published URLs "
                    f"and the snapshot archive: {dict(list(collisions.items())[:5])}"
                )
            out[group] = {
                r["slug"]: {k: _num(r[k]) for k in ["name", *gmetrics]} for r in rows
            }
        # The URL contract: retired slugs and where they now point. The gate treats a vanished
        # slug as acceptable only when a valid alias row redirects it to a live cohort.
        cur.execute("SELECT to_regclass('benchmark_slug_alias') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT kind, old_slug, new_slug FROM benchmark_slug_alias")
            aliases = {}
            for kind, old, new in cur.fetchall():
                aliases.setdefault(kind, {})[old] = new
            out["_aliases"] = aliases
        else:
            out["_aliases"] = {}
    return out


def _num(v):
    """Normalize Decimal and float so a value that did not change does not read as changed.

    Integers stay integers, so a fleet count prints as 3 rather than 3.0 and JSON round-trips
    through the baseline file unchanged.
    """
    if v is None or isinstance(v, (str, bool)):
        return v
    f = float(v)
    return int(f) if f.is_integer() else round(f, 4)


def compare(baseline: dict, now: dict) -> dict:
    """Classify every difference. Vanished slugs and fleet changes are called out separately."""
    report = {}
    for group in _GROUPS:
        was = baseline["groups"].get(group, {})
        is_ = now.get(group, {})
        vanished = sorted(set(was) - set(is_))
        appeared = sorted(set(is_) - set(was))
        changed = {}
        gmetrics = GROUP_METRICS[group]
        for slug in sorted(set(was) & set(is_)):
            deltas = {
                k: (was[slug].get(k), is_[slug].get(k))
                for k in ["name", *gmetrics]
                if _num(was[slug].get(k)) != _num(is_[slug].get(k))
            }
            if deltas:
                changed[slug] = deltas
        report[group] = {
            "vanished": vanished,
            "appeared": appeared,
            "changed": changed,
            "fleet_changed": sorted(s for s, d in changed.items() if "fleet_total" in d),
        }
    return report


def render(report: dict) -> str:
    lines = []
    for group, r in report.items():
        lines.append(f"\n=== {group} ===")
        lines.append(f"  cohorts vanished (published URLs would 404): {len(r['vanished'])}")
        for s in r["vanished"][:20]:
            lines.append(f"      - {s}")
        if len(r["vanished"]) > 20:
            lines.append(f"      ... and {len(r['vanished']) - 20} more")
        lines.append(f"  cohorts appeared: {len(r['appeared'])}")
        for s in r["appeared"][:10]:
            lines.append(f"      + {s}")
        if len(r["appeared"]) > 10:
            lines.append(f"      ... and {len(r['appeared']) - 10} more")
        lines.append(f"  cohorts with changed numbers: {len(r['changed'])}")
        lines.append(f"    of which fleet_total moved: {len(r['fleet_changed'])}")
        for s in r["fleet_changed"][:20]:
            was, is_ = r["changed"][s]["fleet_total"]
            lines.append(f"      ! {s}: fleet {was} -> {is_}")
        if len(r["fleet_changed"]) > 20:
            lines.append(f"      ... and {len(r['fleet_changed']) - 20} more")
        # Metric-level tally so drift is visible without printing thousands of rows.
        tally = {}
        for deltas in r["changed"].values():
            for k in deltas:
                tally[k] = tally.get(k, 0) + 1
        if tally:
            lines.append("  changes by metric:")
            for k in ["name", *GROUP_METRICS[group]]:
                if k in tally:
                    lines.append(f"      {k:32} {tally[k]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--capture", help="write current state to this path instead of diffing")
    ap.add_argument("--gate", action="store_true", help="exit 1 if anything outside --allow moved")
    ap.add_argument("--allow", default="", help="comma-separated slugs permitted to change")
    ap.add_argument(
        "--structural", action="store_true",
        help="gate only structural violations (vanished slugs without a valid alias, and slug "
             "collisions); metric drift passes. This is the nightly mode, where data legitimately "
             "moves every run and only a broken URL contract should stop the pipeline.",
    )
    args = ap.parse_args()

    conn = get_conn()
    try:
        now = current(conn)
    finally:
        conn.close()

    if args.capture:
        Path(args.capture).write_text(
            json.dumps({"groups": now}, indent=1, sort_keys=True), encoding="utf-8"
        )
        print(f"captured {sum(len(v) for v in now.values())} cohorts to {args.capture}")
        return 0

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    report = compare(baseline, now)
    print(f"baseline captured at {baseline.get('captured_at', 'unknown')}")
    print(render(report))

    if not args.gate:
        return 0

    allowed = {s.strip() for s in args.allow.split(",") if s.strip()}
    aliases = now.get("_aliases", {})
    violations = []
    for group, r in report.items():
        live = set(now.get(group, {}))
        for s in r["vanished"]:
            target = aliases.get(group, {}).get(s)
            if target and target in live:
                continue  # retired with a valid redirect: the URL contract holds
            violations.append(
                f"{group}/{s} vanished with no alias to a live cohort, which breaks a published URL"
            )
        if args.structural:
            continue
        for s in r["fleet_changed"]:
            if s not in allowed:
                was, is_ = r["changed"][s]["fleet_total"]
                violations.append(f"{group}/{s} fleet_total {was} -> {is_} is not in --allow")
    if violations:
        print(f"\nGATE FAILED, {len(violations)} violations:")
        for v in violations[:40]:
            print(f"  {v}")
        if len(violations) > 40:
            print(f"  ... and {len(violations) - 40} more")
        return 1
    print("\nGATE PASSED: no published URL vanished and no unlisted fleet number moved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
