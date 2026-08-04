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
                   n.source_pages AS note_source_pages
            FROM (SELECT * FROM v_fcc_pending_applications {where}
                  ORDER BY date_filed DESC NULLS LAST, file_number
                  LIMIT %(limit)s OFFSET %(offset)s) p
            LEFT JOIN fcc_applicant_link l ON l.frn = p.applicant_frn
            LEFT JOIN fcc_filing_note n ON n.file_number = p.file_number
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
            "/api/filings/{file_number}/documents lists them with direct FCC download links."
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
