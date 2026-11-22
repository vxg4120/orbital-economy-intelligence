"""Phase 1 identity pipeline CLI.

Seeds operators + status_mapping from the curated YAML (idempotent upserts), runs the matcher
(deterministic then probabilistic), records each feed's claims, resolves winners into the
dimension tables, and prints a summary. Seeding and every phase write are idempotent, so the
whole build is safe to re-run.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402
from identity import (  # noqa: E402
    churn,
    claims,
    enrich_operators,
    match,
    reconcile,
    resolve,
)

_OPERATOR_SEED = REPO_ROOT / "identity" / "operator_seed.yml"
_STATUS_MAP = REPO_ROOT / "identity" / "status_map.yml"
_REVIEW_CSV = REPO_ROOT / "data" / "review" / "match_review.csv"
_PROMOTION_REVIEW_CSV = REPO_ROOT / "data" / "review" / "promotion_review.csv"


# --- seeding ------------------------------------------------------------------


def seed_operators(conn, path=_OPERATOR_SEED) -> None:
    """Upsert operators, aliases (canonical/aliases/satcat_codes/gcat_codes) and relationships."""
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for op in doc.get("operators", []):
            cur.execute(
                """
                INSERT INTO operator (canonical_name, country, operator_class)
                VALUES (%s, %s, %s)
                ON CONFLICT (canonical_name)
                DO UPDATE SET country = EXCLUDED.country,
                             operator_class = EXCLUDED.operator_class
                RETURNING operator_id
                """,
                (op["name"], op.get("country"), op.get("class")),
            )
            oid = cur.fetchone()[0]
            ids[op["name"]] = oid
            aliases = [(a, "seed") for a in [op["name"], *op.get("aliases", [])]]
            aliases += [(c, "satcat") for c in op.get("satcat_codes", [])]
            aliases += [(c, "gcat") for c in op.get("gcat_codes", [])]
            for alias, src in aliases:
                cur.execute(
                    "INSERT INTO operator_alias (operator_id, alias, source) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (oid, alias, src),
                )
        for rel in doc.get("relationships", []):
            child, parent = ids.get(rel["child"]), ids.get(rel["parent"])
            if child is None or parent is None:
                raise ValueError(f"relationship references unknown operator: {rel}")
            cur.execute(
                """
                INSERT INTO operator_relationship
                    (child_id, parent_id, relationship, valid_from, valid_to, source)
                VALUES (%s, %s, %s, %s, NULL, 'seed')
                ON CONFLICT DO NOTHING
                """,
                (child, parent, rel["relationship"], rel["valid_from"]),
            )


def seed_status_map(conn, path=_STATUS_MAP) -> None:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    with conn.cursor() as cur:
        for source, codes in doc.items():
            for source_value, spec in codes.items():
                cur.execute(
                    """
                    INSERT INTO status_mapping (source, source_value, canonical_status, notes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (source, source_value)
                    DO UPDATE SET canonical_status = EXCLUDED.canonical_status,
                                 notes = EXCLUDED.notes
                    """,
                    (source, source_value, spec["canonical"], spec.get("notes")),
                )


# --- pipeline -----------------------------------------------------------------


def run_pipeline(conn, review_csv=_REVIEW_CSV) -> dict:
    """Seed + enrich + match + assert + resolve, without committing. Returns a summary dict."""
    seed_operators(conn)
    seed_status_map(conn)
    # Data-driven operator enrichment: turn GCAT/SATCAT owner CODES into operators + code aliases
    # (after the YAML seed so seed operators win, before resolve so the resolver's alias lookup sees
    # every code). This is what lifts operator coverage from ~4% to the large majority.
    enrich_stats = enrich_operators.enrich(conn)
    prob_stats = match.run_matchers(conn, review_csv=review_csv)
    # Fold provisional GCAT-only records into their NORAD-anchored Space-Track twins. Runs after
    # the matchers (which cannot link them: the NORAD pass has no NORAD id on the GCAT side, and
    # the name matcher scores 'MISR-D-1' against 'TRANSPORTER-17 OBJECT AJ' near zero) and before
    # assertion extraction, so the surviving record inherits both sides' claims and resolve() then
    # picks the GCAT name over Space-Track's placeholder per precedence.yml.
    promote_stats = reconcile.promote(conn, review_csv=_PROMOTION_REVIEW_CSV)
    # Churn ledger: observe catalog key reassignment between the last two snapshots, measure
    # per-key-type stability with denominators, refresh anchor states, and expire identifiers
    # that are volatile AND observed-churned AND pointing at unanchored satellites. Runs after
    # promotion so freshly promoted satellites are anchored before expiry considers them.
    churn_stats = churn.run_all(conn)
    # Claims, not copies (docs/specs/assertion-model.md): each feed's newest snapshot recorded
    # once, with a validity range; the resolver and every reader work from the claim table.
    claim_stats = claims.record_all(conn)
    # resolve() is a per-satellite write loop (one INSERT/UPDATE per object, no result read back);
    # psycopg pipeline mode batches those thousands of statements into far fewer round-trips,
    # turning the resolve phase from minutes into seconds on the full ~70k-object catalog. The SQL
    # is identical — this is purely a transport optimization.
    with conn.pipeline():
        resolve_stats = resolve.resolve(conn)
    summary = summarize(conn, prob_stats, resolve_stats, review_csv, enrich_stats)
    summary["promotion"] = promote_stats
    summary["churn"] = churn_stats
    summary["claims"] = claim_stats
    return summary


def summarize(conn, prob_stats, resolve_stats, review_csv, enrich_stats=None) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM satellite")
        satellites = cur.fetchone()[0]
        cur.execute("SELECT id_type, count(*) FROM satellite_identifier GROUP BY id_type")
        identifiers = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM merge_log")
        merge_rows = cur.fetchone()[0]
        # Current claims per source (claim table), not per-run copies of them.
        cur.execute("SELECT source, count(*) FROM claim WHERE closed_run IS NULL GROUP BY source")
        assertions_by_source = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM operator")
        operators = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM operator_alias")
        operator_aliases = cur.fetchone()[0]
        # The resolver names the decay messages it set aside by satellite_id; the log says NORAD.
        cur.execute(
            "SELECT norad_id FROM satellite WHERE satellite_id = ANY(%s) ORDER BY norad_id",
            (list(resolve_stats.get("decay_messages_outvoted", [])),),
        )
        outvoted_norad = [row[0] for row in cur.fetchall()]
        cur.execute(
            "SELECT norad_id FROM satellite WHERE satellite_id = ANY(%s) ORDER BY norad_id",
            (list(resolve_stats.get("decay_dates_tracked_after", [])),),
        )
        tracked_after_norad = [row[0] for row in cur.fetchall()]
        gcat_coverage = _gcat_coverage(cur)
        reentry_predictions = _reentry_predictions(cur)
    review_size = _review_size(review_csv)
    total = satellites or 1
    return {
        "satellites": satellites,
        "identifiers_by_type": identifiers,
        "merge_log_rows": merge_rows,
        "assertions_by_source": assertions_by_source,
        "operators": operators,
        "operator_aliases": operator_aliases,
        "enrich_stats": enrich_stats or {},
        "operator_resolved": resolve_stats.get("operator_resolved", 0),
        "status_resolved": resolve_stats.get("status_resolved", 0),
        "operator_coverage_pct": round(100 * resolve_stats.get("operator_resolved", 0) / total, 1),
        "status_coverage_pct": round(100 * resolve_stats.get("status_resolved", 0) / total, 1),
        "unmapped_status": resolve_stats.get("unmapped_status", []),
        "unmatched_owners": resolve_stats.get("unmatched_owners", []),
        "decay_messages_outvoted": outvoted_norad,
        "decay_dates_tracked_after": tracked_after_norad,
        "display_name_overrides": resolve_stats.get("display_name_overrides", 0),
        "gcat_coverage": gcat_coverage,
        "reentry_predictions": reentry_predictions,
        "review_queue_size": review_size,
        "auto_links": prob_stats.get("auto_links", 0),
        "ambiguous_cospar_links": prob_stats.get("ambiguous_cospar_links", 0),
    }


_GCAT_COVERAGE_SQL = """
WITH newest AS (
    -- The snapshot the claims pass and bus attribution read: the newest OK GCAT run.
    SELECT max(r.ingest_run_id) AS run
    FROM raw_gcat_satcat r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok'
),
g AS (
    SELECT DISTINCT i.satellite_id
    FROM satellite_identifier i
    JOIN raw_gcat_satcat r ON r.jcat = i.id_value
    JOIN newest ON r.ingest_run_id = newest.run
    WHERE i.id_type = 'gcat_id' AND i.valid_to IS NULL
)
SELECT count(g.satellite_id),
       count(*) - count(g.satellite_id),
       (array_agg(s.norad_id ORDER BY s.norad_id DESC)
            FILTER (WHERE g.satellite_id IS NULL AND s.norad_id IS NOT NULL))[1:%s]
FROM satellite s
LEFT JOIN g USING (satellite_id)
"""


def _gcat_coverage(cur, shown: int = 5) -> dict:
    """Satellites whose current GCAT link names a key in the newest GCAT snapshot, those without,
    and the newest NORAD numbers without one.

    From 2026-07-21 to 2026-11-14 GCAT published NORAD 100000 and above in a second file the
    loader did not pull, and 1,309 satellites had no GCAT entry while every run reported ok
    (docs/specs/gcat-extended-catalog.md). Counting links alone would miss the opposite failure,
    a later snapshot that lacks part of the catalog: links are never retired for a key's absence,
    but the claims pass closes that key's claims. The newest numbers are where a gap shows first.
    """
    cur.execute(_GCAT_COVERAGE_SQL, (shown,))
    with_gcat, without, newest = cur.fetchone()
    return {"with": with_gcat, "without": without, "newest_without": newest or []}


def _gcat_coverage_line(satellites: int, coverage: dict) -> str:
    """The summary line, starting with "!!" (which the nightly forwards to ALERT_URL) when more
    than 1% of satellites lack a GCAT entry. GCAT updates about weekly and a large rideshare adds
    about 120 objects, so its normal lag stays far below 1% (about 713 on 71,300)."""
    pct = round(100 * coverage["with"] / (satellites or 1), 1)
    line = f"{'gcat coverage:':<25}{pct}%  ({coverage['with']}; {coverage['without']} without"
    if coverage["without"] and coverage["newest_without"]:
        line += ", newest NORAD " + ", ".join(str(n) for n in coverage["newest_without"])
    line += ")"
    if coverage["without"] * 100 > satellites:
        line = f"!! {line}: more than 1% of satellites have no GCAT entry"
    return line


# Space-Track publishes the 60-day predictions weekly and the nightly retries a missed Wednesday
# for 48 hours (ingest/spacetrack_predictions.py), so a newest pull older than this means at
# least one week was missed.
PREDICTIONS_MAX_AGE = dt.timedelta(days=9)


def _reentry_predictions(cur) -> dict:
    """The newest OK weekly predictions pull (when it started and the rows it landed), the
    failed pulls since it, and the database clock to age it by."""
    cur.execute(
        "SELECT ingest_run_id, started_at, rows_ingested FROM ingest_run "
        "WHERE source = 'spacetrack' AND endpoint = 'decay_60day' AND status = 'ok' "
        "ORDER BY ingest_run_id DESC LIMIT 1"
    )
    newest = cur.fetchone()
    cur.execute(
        "SELECT count(*), now() FROM ingest_run "
        "WHERE source = 'spacetrack' AND endpoint = 'decay_60day' AND status = 'error' "
        "AND ingest_run_id > %s",
        (newest[0] if newest else 0,),
    )
    failed, now = cur.fetchone()
    return {"pulled_at": newest[1] if newest else None, "rows": newest[2] if newest else None,
            "failed_since": failed, "now": now}


def _reentry_predictions_line(p: dict) -> str:
    """The summary line for the weekly 60-day reentry predictions, starting with "!!" when no
    pull has landed although one was tried, when the newest pull landed nothing, or when it is
    older than PREDICTIONS_MAX_AGE."""
    label = f"{'reentry predictions:':<25}"
    failed = p["failed_since"]
    if p["pulled_at"] is None:
        if failed:
            return f"!! {label}none landed; {failed} pull(s) failed"
        return f"{label}none pulled yet (the first pull is a Wednesday nightly)"
    line = f"{label}{p['rows']} objects (as of {p['pulled_at']:%Y-%m-%d})"
    if failed:
        line += f"; {failed} pull(s) failed since"
    if not p["rows"]:
        return f"!! {line}: the newest pull landed no prediction"
    if p["now"] - p["pulled_at"] > PREDICTIONS_MAX_AGE:
        return f"!! {line}: the newest pull is more than {PREDICTIONS_MAX_AGE.days} days old"
    return line


def _review_size(review_csv) -> int:
    path = Path(review_csv)
    if not path.exists():
        return 0
    with path.open() as fh:
        return max(sum(1 for _ in fh) - 1, 0)  # minus header


def _norad_line(label: str, norad_ids: list[int], shown: int = 20) -> str:
    line = f"{label}: {len(norad_ids)}"
    if norad_ids:
        ids = ", ".join(str(n) for n in norad_ids[:shown])
        more = f" and {len(norad_ids) - shown} more" if len(norad_ids) > shown else ""
        line += f"  (NORAD {ids}{more})"
    return line


def _outvoted_line(norad_ids: list[int], shown: int = 20) -> str:
    """The summary line for the decay messages that both catalogs outvoted (identity/resolve.py),
    with their NORAD ids, so a second firing can be read in refresh.log after a nightly."""
    return _norad_line("decay messages outvoted", norad_ids, shown)


def _tracked_after_line(norad_ids: list[int], shown: int = 20) -> str:
    """The summary line for the objects whose decay date was set aside because an element set
    is dated more than thirty days after it (identity/decay_claims.py, tracked_after)."""
    return _norad_line("decay dates set aside, tracked after", norad_ids, shown)


def _print_summary(s: dict) -> None:
    print("=== identity graph build summary ===")
    print(f"satellites:              {s['satellites']}")
    print(f"merge_log rows:          {s['merge_log_rows']}")
    print(f"probabilistic auto-links:{s['auto_links']}")
    print(f"ambiguous COSPAR links:  {s['ambiguous_cospar_links']}")
    print(f"review-queue size:       {s['review_queue_size']}")
    print("identifiers by type:")
    for id_type, count in sorted(s["identifiers_by_type"].items()):
        print(f"  {id_type:<12} {count}")
    print("assertions by source:")
    for source, count in sorted(s["assertions_by_source"].items()):
        print(f"  {source:<12} {count}")
    print(f"operators:               {s.get('operators', 0)}  (aliases {s.get('operator_aliases', 0)})")
    es = s.get("enrich_stats") or {}
    if es:
        print(
            "operator enrichment:     "
            f"gcat codes used {es.get('gcat_codes_used', 0)} "
            f"(seed {es.get('gcat_seed_attached', 0)}, new {es.get('gcat_operators_created', 0)}, "
            f"no-org {es.get('gcat_codes_no_org', 0)}); "
            f"satcat codes {es.get('satcat_codes_used', 0)} "
            f"(new {es.get('satcat_operators_created', 0)}); "
            f"subsidiary edges {es.get('relationships_added', 0)}"
        )
    print(f"operator coverage:       {s['operator_coverage_pct']}%  ({s['operator_resolved']})")
    print(f"status coverage:         {s['status_coverage_pct']}%  ({s['status_resolved']})")
    if "gcat_coverage" in s:
        print(_gcat_coverage_line(s["satellites"], s["gcat_coverage"]))
    print(f"unmapped status values:  {len(s['unmapped_status'])}")
    for src, val in s["unmapped_status"][:20]:
        print(f"  {src}: {val!r}")
    print(f"unmatched owner values:  {len(s['unmatched_owners'])}")
    for val in s["unmatched_owners"][:20]:
        print(f"  {val!r}")
    # Printed at 0 too, because a drop to 0 is how the log would show that Space-Track corrected
    # or withdrew the bad message.
    print(_outvoted_line(s.get("decay_messages_outvoted", [])))
    # Twelve on 2026-11-20. A jump means a source published a batch
    # of decay dates for objects that are still tracked, or the element feed went wrong.
    print(_tracked_after_line(s.get("decay_dates_tracked_after", [])))
    if "reentry_predictions" in s:
        print(_reentry_predictions_line(s["reentry_predictions"]))
    print(f"display name overrides:  {s.get('display_name_overrides', 0)}")
    if "claims" in s:
        changed = ", ".join(f"{src} closed {closed} opened {opened}"
                            for src, (closed, opened) in s["claims"].items())
        print(f"claims:                  {changed}")


def main() -> None:
    conn = get_conn()
    try:
        summary = run_pipeline(conn)
        conn.commit()
        _print_summary(summary)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
