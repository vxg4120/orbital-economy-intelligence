"""GCAT (Jonathan McDowell's General Catalog) TSV loader. Raw landing only.

GCAT TSVs are the "second opinion" source: own object ids (JCAT), own status taxonomy, often
better ownership attribution than SATCAT. Headers are the first line, prefixed with `#`, and are
parsed dynamically by name — never by column position, since GCAT adds/reorders columns between
releases. `-` is GCAT's missing-value marker and is normalized to NULL. Columns not in the known
typed subset (see db/migrations/0003_raw.sql) are preserved verbatim in the `extra` JSONB blob so
no information is lost even as the source schema drifts.

Column-name mapping below is built from GCAT's documented satcat/psatcat field names; if a live
pull surfaces headers this mapping doesn't recognize, they still land safely in `extra` (nothing
is dropped) — only the mapping table needs updating, not the parser.
"""

import datetime as dt
import logging
from pathlib import Path

from psycopg.types.json import Jsonb

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "gcat"
SATCAT_ENDPOINT = "gcat_satcat"
SATCAT_URL = "https://planet4589.org/space/gcat/tsv/cat/satcat.tsv"
PSATCAT_ENDPOINT = "gcat_psatcat"
PSATCAT_URL = "https://planet4589.org/space/gcat/tsv/cat/psatcat.tsv"
# The US catalog ran out of five-digit numbers in July 2026 and continued at 100000. GCAT's S
# catalog followed in two files: part 1 (satcat.tsv) stops at S69999, and part 3
# (satcat100k.tsv, same columns) holds S100000 onward. Pulling part 1 alone landed exactly
# 69,999 rows from 2026-07-21 to 2026-11-14 while every object catalogued since went without a
# GCAT entry. Both parts land in one run because every reader takes the newest run as the whole
# snapshot (docs/specs/gcat-extended-catalog.md). The analyst-object files (satcat070k,
# satcat270k, satcat700M) are a different catalog that SATCAT does not carry; never pulled.
SATCAT_100K_URL = "https://planet4589.org/space/gcat/tsv/cat/satcat100k.tsv"
PSATCAT_100K_URL = "https://planet4589.org/space/gcat/tsv/cat/psatcat100k.tsv"
SATCAT_URLS = (SATCAT_URL, SATCAT_100K_URL)
PSATCAT_URLS = (PSATCAT_URL, PSATCAT_100K_URL)
ORGS_ENDPOINT = "gcat_orgs"
ORGS_URL = "https://planet4589.org/space/gcat/tsv/tables/orgs.tsv"
MIN_INTERVAL = runlog.DAILY
DATA_DIR = Path("data/gcat")

MISSING_MARKERS = {"", "-"}

# GCAT header name (normalized: lowercased, alnum-only) -> raw_gcat_satcat column
_SATCAT_FIELD_MAP = {
    "jcat": "jcat",
    "satcat": "norad_id",
    "piece": "piece",
    "type": "object_type",
    "name": "name",
    "plname": "pl_name",
    "ldate": "launch_date",
    "ddate": "decay_date",
    "status": "status",
    "dest": "dest",
    "owner": "owner",
    "state": "state",
    "manufacturer": "manufacturer",
    "bus": "bus",
    "mass": "mass",
    "perigee": "perigee_km",
    "apogee": "apogee_km",
    "inc": "inc_deg",
    "oporbit": "op_orbit",
    "altnames": "alt_names",
}

_SATCAT_COLUMNS = [
    "jcat",
    "norad_id",
    "piece",
    "object_type",
    "name",
    "pl_name",
    "launch_date",
    "decay_date",
    "status",
    "dest",
    "owner",
    "state",
    "manufacturer",
    "bus",
    "mass",
    "perigee_km",
    "apogee_km",
    "inc_deg",
    "op_orbit",
    "alt_names",
]

_PSATCAT_FIELD_MAP = {
    "jcat": "jcat",
    "piece": "piece",
    "name": "name",
}

_PSATCAT_COLUMNS = ["jcat", "piece", "name"]

# GCAT orgs.tsv header name (normalized: lowercased, alnum-only) -> raw_gcat_orgs column. Anything
# not listed here (Location, Longitude, Latitude, UName, ShortEName, TStartApprox, ...) falls
# through to `extra` untouched, so a source-schema change never drops a column.
_ORGS_FIELD_MAP = {
    "code": "code",
    "ucode": "ucode",
    "statecode": "state_code",
    "type": "org_type",
    "class": "org_class",
    "tstart": "t_start",
    "tstop": "t_stop",
    "shortname": "short_name",
    "name": "name",
    "ename": "e_name",
    "parent": "parent_code",
}

_ORGS_COLUMNS = [
    "code",
    "ucode",
    "state_code",
    "org_type",
    "org_class",
    "t_start",
    "t_stop",
    "short_name",
    "name",
    "e_name",
    "parent_code",
]


def _norm_key(header: str) -> str:
    return "".join(ch for ch in header.strip().lower() if ch.isalnum())


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in MISSING_MARKERS else value


def parse_tsv(text: str) -> list[dict]:
    """Split GCAT TSV text into header-keyed row dicts. The first line is the header and starts
    with `#`; short/ragged rows are padded so a dict is always fully keyed.

    GCAT files carry an extra comment line after the header (``# Updated 2026 Jul  7 1654:27``)
    and may sprinkle further ``#``-prefixed comments in the body. JCAT ids never start with ``#``,
    so any body line beginning with ``#`` is a comment and is skipped — otherwise the update
    banner would land as a bogus one-column row every pull."""
    lines = [line for line in text.splitlines() if line != ""]
    if not lines:
        return []
    header_line = lines[0]
    if header_line.startswith("#"):
        header_line = header_line[1:]
    headers = header_line.split("\t")

    rows = []
    for line in lines[1:]:
        if line.startswith("#"):
            continue  # comment line (e.g. "# Updated ..."), not data
        cells = line.split("\t")
        if len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))
        rows.append(dict(zip(headers, cells, strict=False)))
    return rows


def _split_row(raw_row: dict, field_map: dict) -> tuple[dict, dict]:
    typed: dict = {}
    extra: dict = {}
    for header, value in raw_row.items():
        cleaned = _clean(value)
        target = field_map.get(_norm_key(header))
        if target:
            typed[target] = cleaned
        else:
            extra[header] = cleaned
    return typed, extra


_SATCAT_NUMERIC = ("perigee_km", "apogee_km", "inc_deg")


def _coerce_satcat_types(typed: dict, extra: dict) -> dict:
    """Coerce the numeric subset defensively. GCAT is 40k+ rows; a single unexpected value
    (a merged flag, a range, a stray symbol) must not abort the whole landing transaction, so a
    value that won't parse is dropped from the typed column and preserved verbatim in ``extra``
    (nothing is lost, the row still lands)."""
    out = dict(typed)
    raw_norad = out.get("norad_id")
    if raw_norad is not None:
        try:
            out["norad_id"] = int(raw_norad)
        except (TypeError, ValueError):
            out["norad_id"] = None
            extra["_unparsed_satcat"] = raw_norad
    for field in _SATCAT_NUMERIC:
        raw = out.get(field)
        if raw is not None:
            try:
                out[field] = float(raw)
            except (TypeError, ValueError):
                out[field] = None
                extra[f"_unparsed_{field}"] = raw
    return out


def process_satcat_rows(raw_rows: list[dict]) -> list[tuple[dict, dict]]:
    processed = []
    for raw_row in raw_rows:
        typed, extra = _split_row(raw_row, _SATCAT_FIELD_MAP)
        processed.append((_coerce_satcat_types(typed, extra), extra))
    return processed


def process_psatcat_rows(raw_rows: list[dict]) -> list[tuple[dict, dict]]:
    return [_split_row(raw_row, _PSATCAT_FIELD_MAP) for raw_row in raw_rows]


def process_orgs_rows(raw_rows: list[dict]) -> list[tuple[dict, dict]]:
    """Split GCAT orgs rows into (typed, extra). Rows without a `Code` (the PK / join key) are
    dropped — a code-less org line carries nothing the identity layer can join on."""
    processed = []
    for raw_row in raw_rows:
        typed, extra = _split_row(raw_row, _ORGS_FIELD_MAP)
        if typed.get("code"):
            processed.append((typed, extra))
    return processed


def _land_satcat_rows(conn, processed_rows: list[tuple[dict, dict]], run_id: int) -> int:
    with conn.cursor() as cur:
        for typed, extra in processed_rows:
            values = [typed.get(col) for col in _SATCAT_COLUMNS] + [Jsonb(extra), run_id]
            cur.execute(
                "INSERT INTO raw_gcat_satcat ({cols}, extra, ingest_run_id) "
                "VALUES ({phs}, %s, %s)".format(
                    cols=", ".join(_SATCAT_COLUMNS),
                    phs=", ".join(["%s"] * len(_SATCAT_COLUMNS)),
                ),
                values,
            )
    conn.commit()
    return len(processed_rows)


def _land_psatcat_rows(conn, processed_rows: list[tuple[dict, dict]], run_id: int) -> int:
    with conn.cursor() as cur:
        for typed, extra in processed_rows:
            values = [typed.get(col) for col in _PSATCAT_COLUMNS] + [Jsonb(extra), run_id]
            cur.execute(
                "INSERT INTO raw_gcat_psatcat ({cols}, extra, ingest_run_id) "
                "VALUES ({phs}, %s, %s)".format(
                    cols=", ".join(_PSATCAT_COLUMNS),
                    phs=", ".join(["%s"] * len(_PSATCAT_COLUMNS)),
                ),
                values,
            )
    conn.commit()
    return len(processed_rows)


def _land_orgs_rows(conn, processed_rows: list[tuple[dict, dict]], run_id: int) -> int:
    """Land orgs rows. `ON CONFLICT DO NOTHING` guards against a duplicate Code inside a single
    snapshot (GCAT's PK is Code, but a defensive guard keeps one stray dup from aborting the pull)."""
    landed = 0
    with conn.cursor() as cur:
        for typed, extra in processed_rows:
            values = [typed.get(col) for col in _ORGS_COLUMNS] + [Jsonb(extra), run_id]
            cur.execute(
                "INSERT INTO raw_gcat_orgs ({cols}, extra, ingest_run_id) "
                "VALUES ({phs}, %s, %s) ON CONFLICT (code, ingest_run_id) DO NOTHING".format(
                    cols=", ".join(_ORGS_COLUMNS),
                    phs=", ".join(["%s"] * len(_ORGS_COLUMNS)),
                ),
                values,
            )
            landed += 1
    conn.commit()
    return landed


def parse_parts(texts: list[str]) -> list[dict]:
    """parse_tsv over each file of a multi-file catalog, in order. A key in more than one file
    raises: the parts of GCAT's S catalog are disjoint number ranges, so a repeat means the
    split changed, and landing it would break the raw table's (jcat, run) key mid-transaction.
    A repeat within one file is left to the landing, as before the split (orgs.tsv's guard is
    ON CONFLICT DO NOTHING)."""
    rows, earlier, repeated = [], set(), []
    for text in texts:
        file_rows = parse_tsv(text)
        # The first column is the key: JCAT in the catalogs, Code in orgs.tsv.
        keys = {_clean(next(iter(row.values()), None)) for row in file_rows} - {None}
        repeated += sorted(keys & earlier)
        earlier |= keys
        rows += file_rows
    if repeated:
        raise ValueError(f"key in more than one catalog file: {', '.join(repeated[:10])}")
    return rows


def _save_raw_file(name: str, text: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / name
    out_path.write_text(text)
    return out_path


def _land_and_finish(conn, parts, land_fn) -> int:
    """Land rows, close the run 'ok', then best-effort save each part's raw file.

    `land_fn` parses and lands, so a parse failure closes the run as 'error' just like a landing
    failure (never an orphaned open row). The raw-file saves run only AFTER the run is finished
    'ok', so a disk/permission failure there cannot orphan a run whose data already committed.
    Each part is saved under its own file name (satcat-DATE.tsv, satcat100k-DATE.tsv)."""
    run_id = parts[0].oei_run_id
    bytes_dl = sum(part.oei_bytes for part in parts)
    try:
        n = land_fn()
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(
            conn, run_id, rows=0, bytes_dl=bytes_dl, status="error", notes=str(exc)[:2000],
        )
        raise
    runlog.finish_run(conn, run_id, rows=n, bytes_dl=bytes_dl, status="ok")
    today = dt.date.today().isoformat()
    for part in parts:
        try:
            _save_raw_file(f"{part.oei_name}-{today}.tsv", part.text)
        except OSError as exc:
            logger.warning("gcat: raw-file save failed (%s); rows already landed", exc)
    return n


def _pull(conn, endpoint: str, urls, land_rows) -> int | None:
    """Fetch every file of one catalog under one run and land them together. Returns the row
    count, or None when the endpoint is fresh."""
    parts = runlog.polite_get_parts(conn, SOURCE, endpoint, urls, MIN_INTERVAL)
    if parts is None:
        return None
    for part, url in zip(parts, urls, strict=True):
        part.oei_name = Path(url).stem  # satcat, satcat100k, psatcat, psatcat100k
    run_id = parts[0].oei_run_id
    return _land_and_finish(
        conn, parts, lambda: land_rows(conn, parse_parts([p.text for p in parts]), run_id)
    )


def run(conn) -> dict:
    counts = {"satcat": 0, "psatcat": 0}

    n = _pull(conn, SATCAT_ENDPOINT, SATCAT_URLS,
              lambda c, rows, run_id: _land_satcat_rows(c, process_satcat_rows(rows), run_id))
    if n is None:
        logger.info("gcat satcat: skipped, fresh run within %s", MIN_INTERVAL)
    else:
        counts["satcat"] = n

    n = _pull(conn, PSATCAT_ENDPOINT, PSATCAT_URLS,
              lambda c, rows, run_id: _land_psatcat_rows(c, process_psatcat_rows(rows), run_id))
    if n is None:
        logger.info("gcat psatcat: skipped, fresh run within %s", MIN_INTERVAL)
    else:
        counts["psatcat"] = n

    return counts


def run_orgs(conn) -> int:
    """Pull GCAT's organizations file (orgs.tsv) through the same polite ledger and land it into
    raw_gcat_orgs. Kept a separate entry point from run() so the operator-enrichment build can pull
    exactly this one endpoint without touching the satcat/psatcat freshness gates. Returns the row
    count (0 when a fresh run already exists within MIN_INTERVAL)."""
    n = _pull(conn, ORGS_ENDPOINT, (ORGS_URL,),
              lambda c, rows, run_id: _land_orgs_rows(c, process_orgs_rows(rows), run_id))
    if n is None:
        logger.info("gcat orgs: skipped, fresh run within %s", MIN_INTERVAL)
        return 0
    return n
