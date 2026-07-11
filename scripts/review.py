"""The gold-set arbitration CLI: stdlib-only, resumable, crash-safe.

Loops over unlabeled gold_case rows and, for each, renders a clean side-by-side evidence block
(sources as aligned columns, source names dimmed, conflicting values highlighted) plus research
deep-links, then takes a single-key verdict. Every verdict is written to the DB immediately (so a
crash mid-session loses nothing) AND appended to docs/gold/verdicts.jsonl -- the committed file that
IS the gold set. data/gold/gold_cases.jsonl (gitignored) is a full-fidelity export for resilience.

No third-party deps: argparse + termios for single-key input + ANSI escapes for color. The DB write
path is factored into record_verdict()/verdict_record() so it is unit-testable without the loop.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_conn

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VERDICTS_PATH = REPO_ROOT / "docs" / "gold" / "verdicts.jsonl"       # committed: the gold set
EXPORT_DEFAULT = REPO_ROOT / "data" / "gold" / "gold_cases.jsonl"    # gitignored: resilience dump

TERMINAL_BASE = "http://localhost:8600"

VERDICT_KEYS = {"c": "correct", "i": "incorrect", "p": "partial", "u": "unresolvable"}
NEEDS_CORRECTION = {"incorrect", "partial"}

SOURCE_ORDER = ["satcat", "gcat", "ucs", "operator_seed", "spacetrack_decay"]
ATTR_ORDER = ["name", "owner", "status", "object_type", "decay_date"]


# --------------------------------------------------------------------------------------------
# ANSI color (auto-disabled when not a tty or NO_COLOR is set)
# --------------------------------------------------------------------------------------------


class _Ansi:
    def __init__(self, enabled):
        self.enabled = enabled

    def _wrap(self, code, s):
        return f"\x1b[{code}m{s}\x1b[0m" if self.enabled else s

    def dim(self, s):
        return self._wrap("2", s)

    def bold(self, s):
        return self._wrap("1", s)

    def conflict(self, s):
        return self._wrap("1;33", s)  # bold yellow

    def good(self, s):
        return self._wrap("32", s)

    def head(self, s):
        return self._wrap("1;36", s)  # bold cyan


def _color_enabled():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


# --------------------------------------------------------------------------------------------
# DB access + the factored write path
# --------------------------------------------------------------------------------------------


def fetch_unlabeled(conn, only_type=None, limit=None):
    sql = (
        "SELECT case_id, case_type, satellite_id, subject_ref, question, system_answer, evidence "
        "FROM gold_case WHERE verdict IS NULL"
    )
    params = []
    if only_type:
        sql += " AND case_type = %s"
        params.append(only_type)
    sql += " ORDER BY case_type, case_id"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def record_verdict(conn, case_id, verdict, corrected_answer=None, notes=None) -> dict:
    """Write a verdict + labeled_at to gold_case. Transaction-agnostic: the caller commits (the
    review loop commits immediately for crash-safety). Returns the verdict record for the jsonl."""
    if verdict not in VERDICT_KEYS.values():
        raise ValueError(f"invalid verdict: {verdict!r}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gold_case SET verdict = %s, corrected_answer = %s, verdict_notes = %s, "
            "labeled_at = now() WHERE case_id = %s "
            "RETURNING case_type, subject_ref, verdict, corrected_answer, verdict_notes, labeled_at",
            (verdict, corrected_answer, notes, case_id),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"case_id {case_id} not found")
    return verdict_record(*row)


def verdict_record(case_type, subject_ref, verdict, corrected_answer, verdict_notes, labeled_at):
    """The canonical dict shape written to verdicts.jsonl (the committed gold set)."""
    return {
        "case_type": case_type,
        "subject_ref": subject_ref,
        "verdict": verdict,
        "corrected_answer": corrected_answer,
        "verdict_notes": verdict_notes,
        "labeled_at": labeled_at.isoformat() if isinstance(labeled_at, dt.datetime) else labeled_at,
    }


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def export_cases(conn, path: pathlib.Path) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT case_id, case_type, satellite_id, subject_ref, question, system_answer, "
            "evidence, verdict, corrected_answer, verdict_notes, labeled_at "
            "FROM gold_case ORDER BY case_type, case_id"
        )
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(dict(zip(cols, row)), default=str) + "\n")
    return len(rows)


def import_verdicts(conn, path: pathlib.Path) -> int:
    """Restore verdicts from a jsonl (verdicts.jsonl or an export). Matches on (case_type,
    subject_ref); overwrites so a re-import is a faithful restore. Caller need not pre-clear."""
    if not path.exists():
        print(f"no such file: {path}")
        return 0
    n = 0
    with conn.cursor() as cur, path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not rec.get("verdict"):
                continue
            cur.execute(
                "UPDATE gold_case SET verdict = %s, corrected_answer = %s, verdict_notes = %s, "
                "labeled_at = COALESCE(%s, now()) WHERE case_type = %s AND subject_ref = %s",
                (
                    rec["verdict"],
                    rec.get("corrected_answer"),
                    rec.get("verdict_notes"),
                    rec.get("labeled_at"),
                    rec["case_type"],
                    rec["subject_ref"],
                ),
            )
            n += cur.rowcount
    conn.commit()
    return n


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def _assertion_table(assertions, c: _Ansi) -> str:
    """Attributes as rows, sources as aligned columns; cells that disagree are highlighted."""
    by_attr: dict[str, dict[str, str]] = {}
    for a in assertions:
        by_attr.setdefault(a["attribute"], {})[a["source"]] = a["value"]
    sources = [s for s in SOURCE_ORDER if any(s in v for v in by_attr.values())]
    sources += sorted({s for v in by_attr.values() for s in v} - set(sources))
    if not sources:
        return "  (no assertions)\n"
    attrs = [a for a in ATTR_ORDER if a in by_attr] + sorted(set(by_attr) - set(ATTR_ORDER))

    label_w = max(len("attribute"), *(len(a) for a in attrs))
    raw = {(a, s): (by_attr.get(a, {}).get(s) or "") for a in attrs for s in sources}
    col_w = {
        s: max(len(s), *(len(raw[(a, s)]) for a in attrs)) for s in sources
    }

    header = "  " + "attribute".ljust(label_w) + "  " + "  ".join(
        c.dim(s.ljust(col_w[s])) for s in sources
    )
    lines = [header]
    for a in attrs:
        present = [raw[(a, s)] for s in sources if raw[(a, s)]]
        conflict = len(set(present)) > 1
        cells = []
        for s in sources:
            val = raw[(a, s)]
            padded = val.ljust(col_w[s])
            cells.append(c.conflict(padded) if (conflict and val) else padded)
        marker = c.conflict(" *") if conflict else "  "
        lines.append("  " + a.ljust(label_w) + marker + "  ".join(cells))
    return "\n".join(lines) + "\n"


def _identity_line(ev, c: _Ansi) -> str:
    bits = [
        f"NORAD {ev.get('norad_id')}",
        f"JCAT {ev.get('jcat')}",
        f"COSPAR {ev.get('cospar_id')}",
        f"type {ev.get('object_type')}",
        f"regime {ev.get('orbital_regime')}",
        f"launch {ev.get('launch_date')}",
        f"decay {ev.get('decay_date')}",
    ]
    return "  " + c.dim(" | ".join(str(b) for b in bits))


def _resolved_line(ev, c: _Ansi) -> str:
    r = ev.get("resolved", {})
    return "  resolved -> owner: " + c.bold(str(r.get("owner"))) + ", status: " + c.bold(
        str(r.get("status"))
    )


def render_case(case, c: _Ansi) -> str:
    ev = case["evidence"]
    out = [c.head(f"[{case['case_type']}]  {case['subject_ref']}")]

    if "satellites" in ev:  # ambiguous_cospar: several satellites share one designator
        out.append(f"  COSPAR {ev.get('cospar')} shared by {ev.get('n_satellites')} satellites:")
        for s in ev["satellites"]:
            out.append("  " + c.bold(str(s.get("canonical_name"))))
            out.append(_identity_line(s, c))
            out.append(_resolved_line(s, c))
            out.append(_assertion_table(s.get("assertions", []), c))
    else:
        out.append("  " + c.bold(str(ev.get("canonical_name"))))
        out.append(_identity_line(ev, c))
        out.append(_resolved_line(ev, c))
        out.append("")
        out.append(_assertion_table(ev.get("assertions", []), c))

    # Stratum-specific conflict block, if present.
    for block_key in (
        "owner_dispute", "status_conflict", "decay_conflict", "type_conflict",
        "stale_owner", "missed_join",
    ):
        if block_key in ev:
            out.append("  " + c.dim(block_key + ": ") + json.dumps(_shallow(ev[block_key])))

    out.append("")
    out.append(c.bold("  Q: ") + case["question"])
    out.append("  system answer: " + case["system_answer"])
    out.append("")
    out.append(render_links(case))
    return "\n".join(out)


def _shallow(block):
    """Drop the nested 'candidate' satellite packet so the one-line block stays readable."""
    if isinstance(block, dict):
        return {k: v for k, v in block.items() if not isinstance(v, (dict, list))}
    return block


def render_links(case) -> str:
    ev = case["evidence"]
    links = []
    sat_id = case.get("satellite_id")
    if sat_id is not None:
        links.append(f"  terminal:  {TERMINAL_BASE}/resolver/{sat_id}")
    name = ev.get("canonical_name")
    if name:
        q = name.replace(" ", "+")
        links.append(f"  google:    https://www.google.com/search?q={q}+satellite")
    jcat = ev.get("jcat")
    if jcat:
        links.append(f"  gcat:      https://planet4589.org/space/gcat/web/cat/  (object {jcat})")
    norad = ev.get("norad_id")
    if norad is not None:
        links.append(f"  celestrak: https://celestrak.org/satcat/records.php?CATNR={norad}")
    return "\n".join(links) if links else "  (no external links)"


# --------------------------------------------------------------------------------------------
# Single-key input
# --------------------------------------------------------------------------------------------


def read_key(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return (line.strip()[:1].lower() if line else "q")
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write("\n")
    if ch in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D -> quit
        return "q"
    return ch.lower()


def read_line(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return sys.stdin.readline().strip()
    except EOFError:
        return ""


# --------------------------------------------------------------------------------------------
# Progress + loop
# --------------------------------------------------------------------------------------------


def progress_summary(conn, c: _Ansi) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT case_type, count(*) AS total, count(verdict) AS labeled "
            "FROM gold_case GROUP BY case_type ORDER BY case_type"
        )
        rows = cur.fetchall()
    total = sum(r[1] for r in rows)
    labeled = sum(r[2] for r in rows)
    lines = [c.head(f"Progress: {labeled}/{total} labeled")]
    for case_type, t, lab in rows:
        bar = c.good("done") if lab >= t else f"{lab}/{t}"
        lines.append(f"  {case_type:<24} {bar}")
    return "\n".join(lines)


def review_loop(conn, only_type=None, limit=None) -> None:
    c = _Ansi(_color_enabled())
    cases = fetch_unlabeled(conn, only_type=only_type, limit=limit)
    if not cases:
        print(progress_summary(conn, c))
        print("\nNo unlabeled cases match. Nothing to do.")
        return

    menu = (
        "\n  [c]orrect  [i]ncorrect  [p]artial  [u]nresolvable  "
        "[s]kip  [n]ote  [q]uit > "
    )
    for i, case in enumerate(cases):
        print("\n" + "=" * 100)
        print(progress_summary(conn, c))
        print(f"\ncase {i + 1}/{len(cases)} in this session\n")
        print(render_case(case, c))

        note = None
        while True:
            key = read_key(menu)
            if key == "q":
                print("\nQuitting. Progress saved.")
                return
            if key == "s":
                print("  skipped.")
                break
            if key == "n":
                note = read_line("  note: ") or note
                continue
            if key in VERDICT_KEYS:
                verdict = VERDICT_KEYS[key]
                corrected = None
                if verdict in NEEDS_CORRECTION:
                    corrected = read_line("  corrected answer: ") or None
                if note is None:
                    note = read_line("  note (optional, Enter to skip): ") or None
                rec = record_verdict(conn, case["case_id"], verdict, corrected, note)
                conn.commit()  # crash-safe: durable before the next case is shown
                append_jsonl(VERDICTS_PATH, rec)
                print("  " + c.good(f"recorded: {verdict}"))
                break
            print("  (unrecognized key)")

    print("\n" + "=" * 100)
    print(progress_summary(conn, c))
    print("\nReached end of this session's cases.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Arbitrate gold_case cases (resumable).")
    parser.add_argument("--type", dest="only_type", help="review only this stratum")
    parser.add_argument("--limit", type=int, help="review at most N cases this session")
    parser.add_argument("--export", metavar="PATH", nargs="?", const=str(EXPORT_DEFAULT),
                        help="export all cases to jsonl (default data/gold/gold_cases.jsonl) and exit")
    parser.add_argument("--import-verdicts", metavar="PATH", dest="import_verdicts",
                        help="restore verdicts from a jsonl and exit")
    args = parser.parse_args()

    conn = get_conn()
    try:
        if args.export is not None:
            n = export_cases(conn, pathlib.Path(args.export))
            print(f"exported {n} cases to {args.export}")
            return
        if args.import_verdicts:
            n = import_verdicts(conn, pathlib.Path(args.import_verdicts))
            print(f"imported {n} verdicts from {args.import_verdicts}")
            return
        review_loop(conn, only_type=args.only_type, limit=args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
