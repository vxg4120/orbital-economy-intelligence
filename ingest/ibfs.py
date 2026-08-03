"""FCC IBFS bulk-dump loader (IBFS.zip). Raw landing only, four tables of ~70.

IBFS is the FCC International Bureau's legacy filing system: every Part 25 space-station filing
ever made, including PENDING applications, which the Approved Space Station List by definition
omits. The bureau publishes the whole relational database as a pipe-delimited dump
(ftp://ftp.fcc.gov/pub/Bureaus/International/databases/IBFS.zip, ~49 MB, US Government work,
refreshed roughly weekly). We land MAIN (the filing docket), SPACE_STATION (name/orbit
reference), FREQUENCY (per-authorization frequency rows) and ADDRESS (applicant organization
names and FRNs, reached from MAIN's applicant address key); the DDL for everything is ibfs.txt
alongside the zip.

Format facts, verified against the live dump 2026-07-30 rather than trusted from the 1998 docs:

* Records terminate with '|^|' + CRLF exactly as ibfs.txt's header note says, and MAIN text
  fields really do contain embedded CRLFs (3,287 records), so records are split on the full
  five-character terminator, never on lines.
* Fields are pipe-separated in the documented 1998 column order, but live records carry MORE
  fields than documented (MAIN: 53 documented, ~71 live) because the schema grew after ibfs.txt
  was last updated; the extras are trailing and are ignored. A handful of records (16, all
  earth-station filings) contain embedded pipes inside free-text fields, which shifts their
  tail columns; typed-field coercion degrades that noise to NULL with a warning.
* Encoding is cp1252 (curly quotes 0x92-0x94, degree signs), decoded with errors="replace".
* File numbers are stored WITHOUT dashes: 'SATLOA2025061800152', not the web UI's
  'SAT-LOA-20250618-00152'.
* Dates are Sybase datetimes ('Jun 18 2025  4:50:24:023PM', double space before 1-digit
  values); the calendar date is kept and time of day dropped, the raw zip on disk retains it.

The fetch is FTP, so it cannot ride runlog.polite_get (HTTP-only), but it observes the exact
same ledger discipline by hand: fresh_within gate, start_run, finish_run with ok/error, raw
zip saved best-effort only after the run is already ok. ftp.fcc.gov's PASV replies advertise a
data port that never accepts connections from some networks (verified 2026-07-30: control
channel fine, PASV data connect times out, EPSV transfers instantly, which is also why curl
succeeds where urllib hangs), so the data channel is negotiated EPSV-first.
"""

import datetime as dt
import decimal
import ftplib
import io
import logging
import socket
import zipfile
from pathlib import Path

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "fcc"
ENDPOINT = "ibfs"
FTP_HOST = "ftp.fcc.gov"
FTP_PATH = "/pub/Bureaus/International/databases/IBFS.zip"
MIN_INTERVAL = dt.timedelta(hours=72)
DATA_DIR = Path("data/fcc")

RECORD_TERMINATOR = "|^|\r\n"
ENCODING = "cp1252"
_DATE_FMT = "%b %d %Y %I:%M:%S:%f%p"  # Sybase, e.g. 'Feb 28 1996 12:00:00:000AM'

# (our column, 0-based position in the documented layout, kind). Positions come from the
# create-table order in ibfs.txt and were re-verified against live records (signer at 37,
# description at 40, etc.); parsing is positional because the dump has no header row.
_FILING_SPEC = [
    ("filing_key", 0, "int"),
    ("callsign", 2, "text"),
    ("file_number", 3, "text"),
    ("subsystem_code", 4, "text"),
    ("status_code", 5, "text"),
    ("status_date", 6, "date"),
    ("last_action", 7, "text"),
    ("last_action_date", 8, "date"),
    ("date_filed", 10, "date"),
    ("date_grant", 12, "date"),
    ("date_deny", 13, "date"),
    ("date_dismiss", 14, "date"),
    ("date_surrender", 15, "date"),
    ("date_begin", 16, "date"),
    ("date_expire", 17, "date"),
    ("date_last_update", 18, "date"),
    ("app_type_code", 25, "text"),
    ("type_applicant_code", 33, "text"),
    ("class_of_station_code", 35, "text"),
    ("description", 40, "text"),
    # Applicant identity: column 41 keys into address.dat (the applicant's org name lives
    # there, not in main.dat); column 61 carries the applicant's FRN directly on the filing,
    # kept as a fallback for filings whose address row is missing from the dump.
    ("applicant_address_key", 41, "int"),
    ("frn", 61, "text"),
]

_SPACE_STATION_SPEC = [
    ("space_station_key", 0, "int"),
    ("us_name", 1, "text"),
    ("itu_name", 2, "text"),
    ("orbit_location", 3, "text"),
    ("verbose_name", 4, "text"),  # source column is 'verbose', a reserved word in Postgres
    ("inactive_date", 5, "date"),
]

_FREQUENCY_SPEC = [
    ("frequency_key", 0, "int"),
    ("antenna_key", 1, "int"),
    ("polarization_code", 2, "text"),
    ("eirp", 3, "float"),
    ("eirp_density", 4, "float"),
    ("emission", 5, "text"),
    ("frequency_lower", 6, "numeric"),
    ("frequency_upper", 7, "numeric"),
    ("trans_mode", 8, "text"),
    ("modulation", 9, "text"),
]

_ADDRESS_SPEC = [
    ("address_key", 0, "int"),
    ("name", 2, "text"),
    ("city", 6, "text"),
    ("state_code", 7, "text"),
    ("country", 9, "text"),
    ("frn", 12, "text"),
]

# zip member -> (destination table, column spec). Only these four of the dump's ~70 land.
MEMBERS = {
    "main.dat": ("raw_ibfs_filings", _FILING_SPEC),
    "space_sta.dat": ("raw_ibfs_space_stations", _SPACE_STATION_SPEC),
    "freq.dat": ("raw_ibfs_frequencies", _FREQUENCY_SPEC),
    "address.dat": ("raw_ibfs_addresses", _ADDRESS_SPEC),
}


class _EpsvFirstFTP(ftplib.FTP):
    """FTP that negotiates the data channel with EPSV before falling back to PASV.

    Over IPv4, ftplib only ever sends PASV, and ftp.fcc.gov's PASV data port hangs from some
    networks while EPSV works (see module docstring). If the server ever rejects EPSV, the
    inherited PASV path is used.
    """

    def makepasv(self):
        if self.af == socket.AF_INET:
            try:
                return ftplib.parse229(self.sendcmd("EPSV"), self.sock.getpeername())
            except ftplib.Error as exc:
                logger.warning("ibfs: EPSV rejected (%s), falling back to PASV", exc)
        return super().makepasv()


def _coerce(table: str, column: str, kind: str, value: str):
    """Coerce one stripped cell to its column type. Defensive: one malformed cell in a ~675k-row
    dump degrades to NULL with a warning, never aborts the load."""
    if value == "":
        return None
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
        if kind == "numeric":
            return decimal.Decimal(value)
        if kind == "date":
            return dt.datetime.strptime(value, _DATE_FMT).date()
    except (ValueError, decimal.InvalidOperation):
        logger.warning("ibfs %s: dropping unparseable %s=%r -> NULL", table, column, value)
        return None
    return value


def parse_records(text: str, spec: list[tuple[str, int, str]], table: str) -> list[dict]:
    """Split one .dat file's text into row dicts by documented column position.

    Records split on the full '|^|' + CRLF terminator (embedded CRLFs inside text fields make
    line-based splitting wrong), fields on '|'. Fixed-width char padding is stripped and empty
    cells become NULL. Positions beyond the documented layout (post-1998 additions) are
    ignored; a record too short for the spec degrades to NULLs for the missing tail, logged.
    """
    max_pos = max(pos for _, pos, _ in spec)
    rows = []
    for rec in text.split(RECORD_TERMINATOR):
        if not rec.strip():
            continue  # the empty tail after the final terminator
        fields = rec.split("|")
        if len(fields) <= max_pos:
            logger.warning(
                "ibfs %s: short record (%d fields, spec needs %d), missing tail -> NULL: %.80r",
                table, len(fields), max_pos + 1, rec,
            )
        row = {}
        for column, pos, kind in spec:
            value = fields[pos].strip() if pos < len(fields) else ""
            row[column] = _coerce(table, column, kind, value)
        rows.append(row)
    return rows


def parse_zip(content: bytes) -> dict[str, list[dict]]:
    """Table name -> parsed rows for the three members we land."""
    out = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for member, (table, spec) in MEMBERS.items():
            text = zf.read(member).decode(ENCODING, errors="replace")
            out[table] = parse_records(text, spec, table)
    return out


def _fetch() -> bytes:
    """Download IBFS.zip over FTP. The ledger gate belongs to the caller (run)."""
    buf = io.BytesIO()
    with _EpsvFirstFTP() as ftp:
        ftp.connect(FTP_HOST, timeout=runlog.TIMEOUT_S)
        ftp.login()  # anonymous, per the politeness posture: public file, ledger-gated
        ftp.voidcmd("TYPE I")
        ftp.retrbinary(f"RETR {FTP_PATH}", buf.write)
    return buf.getvalue()


def _land_rows(conn, tables: dict[str, list[dict]], run_id: int) -> int:
    """Insert every parsed row under one run id. executemany rather than the usual per-row
    execute loop: same statement shape, but ~675k rows per pull make the batched form matter."""
    total = 0
    with conn.cursor() as cur:
        for _, (table, spec) in MEMBERS.items():
            columns = [c for c, _, _ in spec]
            rows = tables[table]
            cur.executemany(
                "INSERT INTO {t} ({cols}, ingest_run_id) VALUES ({phs})".format(
                    t=table,
                    cols=", ".join(columns),
                    phs=", ".join(["%s"] * (len(columns) + 1)),
                ),
                [[row[c] for c in columns] + [run_id] for row in rows],
            )
            total += len(rows)
    conn.commit()
    return total


def run(conn) -> int:
    """Pull the dump and land all three tables under one run. Returns total rows landed."""
    if runlog.fresh_within(conn, SOURCE, ENDPOINT, MIN_INTERVAL):
        run_id = runlog.start_run(conn, SOURCE, ENDPOINT)
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="skipped_fresh")
        logger.info("fcc ibfs: skipped, fresh run within %s", MIN_INTERVAL)
        return 0
    run_id = runlog.start_run(conn, SOURCE, ENDPOINT)
    try:
        content = _fetch()
        tables = parse_zip(content)
        landed = _land_rows(conn, tables, run_id)
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="error", notes=str(exc)[:2000])
        raise
    notes = ", ".join(f"{table}={len(tables[table])}" for _, (table, _) in MEMBERS.items())
    runlog.finish_run(conn, run_id, rows=landed, bytes_dl=len(content), status="ok", notes=notes)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"ibfs-{dt.date.today().isoformat()}.zip").write_bytes(content)
    except OSError as exc:
        logger.warning("fcc ibfs: raw-file save failed (run already ok): %s", exc)
    logger.info("fcc ibfs: %d rows landed (%s)", landed, notes)
    return landed
