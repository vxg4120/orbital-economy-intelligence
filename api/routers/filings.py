"""FCC filings: the pre-launch pipeline.

GET /api/filings/pending    space-station applications filed and not yet decided

An FCC space-station authorization precedes launch by months to years (Starlink Gen1 by 14
months, Kuiper by 39), so the pending queue is a forward view of satellites that do not exist
in any tracking catalog yet. Rows come from v_fcc_pending_applications over the IBFS bulk dump:
pending is date-defined (filed, with no grant, denial, dismissal or surrender date), because
IBFS status codes are workflow states rather than dispositions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_db

router = APIRouter(prefix="/filings", tags=["filings"])


@router.get("/pending")
def pending(
    db=Depends(get_db),
    q: str | None = Query(None, max_length=80),
    applicant_slug: str | None = Query(None, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Pending applications, newest first. q searches call sign, file number, station name,
    applicant and description; applicant_slug filters to applications filed by one Bus
    Benchmarks manufacturer cohort's corporate group (the curated FRN link), and is the
    receipt set behind the cohort detail page's pending_applications count."""
    clauses = []
    params: dict = {"limit": limit, "offset": offset}
    if q and q.strip():
        clauses.append(
            "(callsign ILIKE %(q)s OR file_number ILIKE %(q)s "
            "OR satellite_name ILIKE %(q)s OR itu_name ILIKE %(q)s "
            "OR applicant_name ILIKE %(q)s OR description ILIKE %(q)s)"
        )
        params["q"] = "%" + q.strip().replace("%", "\\%").replace("_", "\\_") + "%"
    if applicant_slug and applicant_slug.strip():
        clauses.append(
            "applicant_frn IN (SELECT frn FROM fcc_applicant_link "
            "WHERE manufacturer_slug = %(applicant_slug)s)"
        )
        params["applicant_slug"] = applicant_slug.strip().lower()
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM v_fcc_pending_applications {where}", params)
        total = cur.fetchone()["total"]
        cur.execute(
            f"""
            SELECT p.*,
                   l.manufacturer_slug AS applicant_slug,
                   (SELECT count(*) FROM fcc_filing_document d
                    WHERE d.file_number = p.file_number) AS documents_n,
                   n.summary  AS note_summary,
                   n.key_points AS note_key_points,
                   n.source_doc AS note_source_doc,
                   n.source_pages AS note_source_pages,
                   s.orbit_type AS spec_orbit_type,
                   s.network_name AS spec_network_name,
                   s.total_satellites AS spec_total_satellites,
                   o.planes_n AS spec_planes_n,
                   o.alt_min_km AS spec_alt_min_km,
                   o.alt_max_km AS spec_alt_max_km,
                   o.incl_min_deg AS spec_incl_min_deg,
                   o.incl_max_deg AS spec_incl_max_deg,
                   o.implausible_n AS spec_implausible_n,
                   d.filings_pending AS docket_filings_pending,
                   d.filings_granted AS docket_filings_granted,
                   d.filings_total AS docket_filings_total,
                   d.pending_amendments AS docket_pending_amendments
            FROM (SELECT * FROM v_fcc_pending_applications {where}
                  ORDER BY date_filed DESC NULLS LAST, file_number
                  LIMIT %(limit)s OFFSET %(offset)s) p
            LEFT JOIN fcc_applicant_link l ON l.frn = p.applicant_frn
            LEFT JOIN fcc_filing_note n ON n.file_number = p.file_number
            -- Machine-derived Schedule S summary, validated rows only, so a row can show
            -- constellation shape without the client fetching every filing's spec separately.
            LEFT JOIN fcc_spec_filing s
                   ON s.file_number = p.file_number AND s.is_validated
            LEFT JOIN (
                SELECT file_number,
                       count(*) AS planes_n,
                       -- Altitude range uses mean altitude per plane, and EXCLUDES the lunar
                       -- sentinels: an applicant filing apogee 99999 because Schedule S cannot
                       -- express a translunar trajectory would otherwise set every summary range
                       -- to a meaningless span. The count of them travels alongside so the
                       -- exclusion is visible rather than silent.
                       round(min((apogee_km + perigee_km) / 2.0) FILTER (
                           WHERE apogee_km BETWEEN 150 AND 50000)) AS alt_min_km,
                       round(max((apogee_km + perigee_km) / 2.0) FILTER (
                           WHERE apogee_km BETWEEN 150 AND 50000)) AS alt_max_km,
                       -- The sentinel filter applies to the WHOLE row, not just altitude: a
                       -- lunar filing's inclination 0 is part of the same placeholder and must
                       -- not drag the inclination range down (verify pass, 2026-08-24).
                       min(inclination_deg) FILTER (
                           WHERE apogee_km IS NULL
                              OR apogee_km BETWEEN 150 AND 50000) AS incl_min_deg,
                       max(inclination_deg) FILTER (
                           WHERE apogee_km IS NULL
                              OR apogee_km BETWEEN 150 AND 50000) AS incl_max_deg,
                       count(*) FILTER (
                           WHERE apogee_km IS NOT NULL
                             AND apogee_km NOT BETWEEN 150 AND 50000) AS implausible_n
                FROM fcc_spec_orbital WHERE is_validated GROUP BY file_number
            ) o ON o.file_number = p.file_number
            -- Docket summary: how many filings share this callsign, granted and pending. Null on
            -- filings without a callsign, which is a fact about the filing, not a gap to fill.
            LEFT JOIN v_fcc_docket d ON d.callsign = p.callsign
            ORDER BY p.date_filed DESC NULLS LAST, p.file_number
            """,
            params,
        )
        rows = cur.fetchall()
    return {
        "rows": rows,
        "total": total,
        "note": (
            "Applications filed with the FCC and not yet decided: a forward view of satellites "
            "months to years before they reach any tracking catalog. Source: FCC IBFS bulk "
            "data, public domain. documents_n counts harvested ICFS attachments; "
            "/api/filings/{file_number}/documents lists them with direct FCC download links. "
            "spec_* fields are parsed deterministically from the filing's own Schedule S Tech "
            "Report and served only after each value was re-checked against the page it cites; "
            "/api/filings/{file_number}/spec carries the per-plane detail and those citations. "
            "docket_* fields summarize every filing sharing this callsign, granted and pending; "
            "/api/filings/docket/{callsign} serves that full dated timeline."
        ),
    }


@router.get("/{file_number}/documents")
def documents(file_number: str, db=Depends(get_db)):
    """The filing's harvested document inventory, with direct FCC gateway download URLs.

    Inventory only: the bytes stay on api-prod.fcc.gov (which occasionally answers 503;
    retrying is the caller's job). Filings outside the harvested scope return an empty list,
    not a 404, because absence of harvest is not absence of documents."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT file_number, doc_name, doc_date, download_url, fetched_at "
            "FROM fcc_filing_document WHERE file_number = %(fn)s "
            "ORDER BY doc_date NULLS LAST, doc_name",
            {"fn": file_number.strip().upper()},
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT summary, key_points, source_doc, source_pages, noted_at "
            "FROM fcc_filing_note WHERE file_number = %(fn)s",
            {"fn": file_number.strip().upper()},
        )
        note = cur.fetchone()
    return {
        "file_number": file_number.strip().upper(),
        "documents": rows,
        "analyst_note": note,
        "source": "FCC ICFS portal (public), harvested inventory; documents served by "
                  "api-prod.fcc.gov",
    }


# Structured methodology for the filings layer, mirroring /api/buses/methodology. The numbers are
# a dated snapshot (see "as_of"), fact-checked against production the day they were written; the
# living definitions are docs/FCC_FILINGS_METHODOLOGY.md, and changes bump the version there.
_METHODOLOGY = {
    "title": "FCC Pending Filings: Methodology",
    "version": "filings-methodology/1.0",
    "as_of": "2026-08-24",
    "purpose": (
        "Explain how the pending-filings pipeline turns FCC bulk data and filed attachments "
        "into served, page-cited technical specs, and state plainly what it covers and where it fails."
    ),
    "data_sources": [
        {
            "name": "FCC IBFS bulk data",
            "role": (
                "System of record for application status, callsigns, and dockets; defines the pending set "
                "(no grant, denial, dismissal, or surrender). It has no parent-filing key."
            ),
            "url": "ftp://ftp.fcc.gov/pub/Bureaus/International/databases/IBFS.zip",
        },
        {
            "name": "FCC ICFS portal",
            "role": "Per-filing document inventories are harvested here.",
            "url": "https://fccprod.servicenowservices.com",
        },
        {
            "name": "FCC attachment gateway",
            "role": (
                "Delivers the attachment PDFs, which are cached with sha256 content hashes. "
                "The truncation described in the caveats happens server-side on this gateway."
            ),
            "url": "https://api-prod.fcc.gov",
        },
    ],
    "pipeline": [
        "Harvest document inventories for pending filings from ICFS (136 of the 667 pending filings currently have harvested inventories).",
        "Fetch attachments from the gateway and record their sha256 content hashes and page counts. The bytes themselves are not retained, so a reissued attachment is detectable on refetch (the hash changes), but re-verification depends on the FCC continuing to serve the document.",
        "Candidate documents are selected by filename and then confirmed by content anchors ('OMB 3060-0678', '312 File Number:', 'Select Orbit Type', present in 3 of 3 sampled reports), so an absence claim is scoped to Schedule-S-named documents; content decides, names only nominate: 'Schedule S' and 'Form 312' appear in 0 of 3 generated reports, and the corpus has 558 distinct document names.",
        "Parse deterministically: regex over pypdf-extracted text of the fixed label-value layout that the FCC's own filing tool generates.",
        "Record the physical page each value was found on, per field, because a plane's values can straddle a page break (17 of 74 planes on the Boeing filing carry inclination on one page and apogee/perigee on the next).",
        "Validate independently: a validator that shares no code with the extractor re-opens each cited page and confirms the value's tokens are present.",
        "Serve validated rows only; rows that fail validation are stored but never served.",
    ],
    "coverage": {
        "pending_space_station_applications": 667,
        "filings_with_harvested_document_inventories": 136,
        "document_rows_harvested": 1313,
        "schedule_s_tech_report_pdfs": 95,
        "filings_holding_a_schedule_s_pdf": 64,
        "filings_holding_a_narrative_or_schedule_s_pdf": 103,
        "filings_serving_validated_extracted_specs": 60,
        "validated_orbital_planes": 285,
        "validated_frequency_bands": 316,
        "served_rows_that_passed_page_level_validation": "100%; failing rows are stored but never served",
        "proposed_satellites_across_validated_ngso_specs": 5246,
        "largest_single_system": "Boeing V-band NGSO: 2,956 satellites across 74 orbital planes",
        "documents_fetched_whole": 67,
        "documents_truncated_by_the_gateway": 28,
        "filed_to_launch_lead_time_examples": "Starlink Gen1 filed 14 months before first launch; Kuiper filed 39 months before.",
        "last_harvest_and_extraction_run": "2026-08-24, on weekly staleness gates inside the nightly refresh",
    },
    "docket_model": {
        "model": "Dockets are modeled as timelines keyed on callsign, never as supersession chains, and specs are never merged across filings.",
        "why": (
            "The bulk data has no parent-filing key, and concurrent pending modifications are the normal case: "
            "callsign S3069 (SpaceX Gen2) holds 28 filings since 2022-12-15, 19 granted and 9 pending, and four of "
            "the pending filings are concurrent MODs that each modify a different aspect of the same authorization. "
            "Asserting a chain would be inventing structure the data does not carry."
        ),
        "scale": "1,361 dockets across all SAT filings; 674 hold more than one filing; 347 pending filings sit in multi-filing dockets.",
    },
    "caveats": [
        "28 of the 95 cached documents are truncated, and all 28 belong to one 2016 filing (SATLOA2016062200058). The truncation is server-side on the FCC gateway, with 455 bytes delivered against a declared length of 87,684, identical under four different clients, so it cannot be fixed from this end.",
        "4 orbital-plane rows carry lunar sentinels: Schedule S has no field for a translunar trajectory, so Intuitive Machines and Lockheed Martin file apogee 1 km / perigee 1 km / inclination 0, and Astrobotic files apogee 99999 km. These rows are served exactly as filed, flagged as_filed_implausible, and excluded from summary altitude ranges with the exclusion counted visibly.",
        "Narratives were measured to contain no numeric orbital parameters; they carry intent and legal argument, so numeric specs come only from Schedule S Tech Reports.",
        "No supersession is asserted anywhere: concurrent filings under one callsign are presented side by side on a timeline, and no filing is claimed to replace another.",
        "Every served spec is machine-derived from the filed documents; it describes what an applicant filed, not what will fly.",
    ],
    "no_llm": (
        "The pipeline uses no language model anywhere: Schedule S Tech Reports are generated by the FCC's own "
        "filing tool in a fixed label-value layout, so deterministic regex over pypdf-extracted text is sufficient, "
        "and every served value stays reproducible and checkable against its cited page."
    ),
}


@router.get("/methodology")
def methodology():
    """Versioned, structured methodology: sources, pipeline, coverage, caveats, docket model."""
    return _METHODOLOGY


@router.get("/docket/{callsign}")
def docket(callsign: str, db=Depends(get_db)):
    """One callsign's full regulatory docket: every filing, granted and pending, dated.

    Deliberately a timeline and not a chain. Concurrent pending modifications to a single
    authorization are the normal case, not an anomaly (S3069 carries four at once, each touching
    a different aspect of the system); the bulk data has no parent-filing key; and which earlier
    filing an amendment amends lives in its prose. So nothing here asserts supersession, and
    specs are never merged across filings: each row reports only whether its own validated
    Schedule S extraction exists.

    An unknown callsign returns an empty docket with HTTP 200, because absence from the record
    is a fact about the record, not an error.
    """
    cs = callsign.strip().upper()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM v_fcc_docket WHERE callsign = %(cs)s", {"cs": cs})
        summary = cur.fetchone()
        cur.execute(
            """
            SELECT df.file_number, df.app_type_code, df.date_filed, df.status_code,
                   df.date_grant, df.date_deny, df.date_dismiss, df.date_surrender,
                   df.is_pending,
                   (s.file_number IS NOT NULL) AS spec_available
            FROM v_fcc_docket_filing df
            LEFT JOIN fcc_spec_filing s
                   ON s.file_number = df.file_number AND s.is_validated
            WHERE df.callsign = %(cs)s
            ORDER BY df.date_filed NULLS LAST, df.file_number
            """,
            {"cs": cs},
        )
        timeline = cur.fetchall()
    return {
        "callsign": cs,
        "summary": summary,
        "timeline": timeline,
        "note": (
            "A docket is a timeline, not a chain: concurrent pending modifications to one "
            "authorization are normal, the bulk data carries no parent-filing key, so no "
            "supersession is asserted and specs are never merged across filings. Source: FCC "
            "IBFS bulk data, latest ingest run."
        ),
    }


# Values a lunar or deep-space applicant enters because Schedule S has no field that describes a
# translunar trajectory: the form demands numbers, so they file sentinels. Observed across four
# planes (Intuitive Machines IM-3 Nova-C and Lockheed's LM Lunar file 1/1/0; Astrobotic's Griffin
# and Peregrine file apogee 99999). Reported as-filed and flagged, never silently corrected --
# the filing really does say this, and a reader checking the citation must find what we published.
_IMPLAUSIBLE_APOGEE_KM = (150, 50_000)


@router.get("/{file_number}/spec")
def filing_spec(file_number: str, db=Depends(get_db)):
    """Machine-derived Schedule S specs for one filing, every field carrying a page citation.

    Parsed deterministically from the FCC's own generated Tech Report, not inferred by a model.
    Only validated rows are served: a row whose cited page did not physically contain its value is
    kept in the table for debugging and never returned here.

    Orbital planes are returned raw, one per plane as Schedule S lists them. Grouping them into
    "shells" is a judgement with its own rule and is deliberately not done here.
    """
    fn = file_number.strip().upper()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM fcc_spec_filing WHERE file_number = %(fn)s AND is_validated",
            {"fn": fn},
        )
        summary = cur.fetchone()
        # One receipt identity: every row in this response comes from the SAME document the
        # summary was extracted from (file_number, sys_id, doc_sha256). A filing can carry more
        # than one Schedule S attachment, and unioning children across documents while showing
        # one blob hash would hand the reader citations into a document they are not looking at
        # (verify pass, 2026-08-24).
        sid = summary["sys_id"] if summary else None
        cur.execute(
            "SELECT plane_idx, apogee_km, apogee_page, perigee_km, perigee_page, "
            "inclination_deg, inclination_page, arg_perigee_deg, arg_perigee_page, source_page "
            "FROM fcc_spec_orbital WHERE file_number = %(fn)s AND sys_id = %(sid)s "
            "AND is_validated ORDER BY plane_idx",
            {"fn": fn, "sid": sid},
        )
        planes = cur.fetchall()
        cur.execute(
            "SELECT band_idx, service, freq_low_mhz, freq_high_mhz, direction, source_page "
            "FROM fcc_spec_band WHERE file_number = %(fn)s AND sys_id = %(sid)s "
            "AND is_validated ORDER BY band_idx",
            {"fn": fn, "sid": sid},
        )
        bands = cur.fetchall()
        cur.execute(
            "SELECT sys_id, sha256, byte_count, page_count, fetch_status, fetched_at "
            "FROM fcc_filing_blob WHERE file_number = %(fn)s AND sys_id = %(sid)s",
            {"fn": fn, "sid": sid},
        )
        blob = cur.fetchone()

    lo, hi = _IMPLAUSIBLE_APOGEE_KM
    for plane in planes:
        apogee = plane.get("apogee_km")
        plane["as_filed_implausible"] = apogee is not None and not (lo <= apogee <= hi)

    return {
        "file_number": fn,
        "summary": summary,
        "planes": planes,
        "bands": bands,
        "source_document": blob,
        "extraction": {
            "method": "deterministic parse of the FCC-generated Schedule S Tech Report",
            "validated": "every field re-checked against its cited page before being served",
            "caveat": "values are reported exactly as filed; planes flagged as_filed_implausible "
                      "carry sentinels the applicant entered because Schedule S cannot express a "
                      "translunar trajectory",
        },
    }
