"""Filing document harvest: number reconstruction, widget-tree extraction, API surface.

The harvest rides two undocumented-but-public interfaces (the ICFS portal's anonymous page
API and the api-prod.fcc.gov attachment gateway), so the tests pin OUR side of the contract:
the dashed-number reconstruction that selects which filing gets harvested (a silent mangle
here harvests the wrong filing's documents), and the field-keyed tree walk that must survive
ServiceNow reshuffling its widget layout.
"""

import warnings

import pytest

from ingest.icfs_documents import dashed_file_number, extract_documents


def _client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def test_dashed_reconstruction_is_a_fixed_slice():
    assert dashed_file_number("SATMOD2025061100144") == "SAT-MOD-20250611-00144"
    assert dashed_file_number("SATLOA2025061800152") == "SAT-LOA-20250618-00152"
    # The slash-bearing type codes are 3 characters like every other: T/C, A/O.
    assert dashed_file_number("SATT/C2024010100001") == "SAT-T/C-20240101-00001"
    assert dashed_file_number("SATA/O2023123199999") == "SAT-A/O-20231231-99999"
    assert dashed_file_number(" satmod2025061100144 ") == "SAT-MOD-20250611-00144"


def test_dashed_reconstruction_refuses_unexpected_shapes():
    for bad in ("SATMOD202506110014", "SESMOD2025061100144", "SATMOD20250611001445", ""):
        with pytest.raises(ValueError):
            dashed_file_number(bad)


def test_extract_documents_walks_both_observed_node_shapes():
    """The attachment node appears both as actions{attachment_name,url} directly and as
    actions.links[{attachment_name,url}]; the date lives on the enclosing row as
    sys_created_on{display_value}. Field-keyed walking must find all, dedup on the download
    id, and strip query strings."""
    page = {"result": {"containers": [{"rows": [{"widgets": [{"data": {"all_data": [
        {
            "sys_created_on": {"display_value": "2025-06-25", "value": "2025-06-25"},
            "actions": {
                "display_value": "Download",
                "attachment_name": "Narrative.pdf",
                "url": "https://api-prod.fcc.gov/icfs-attachment/exp/api/v1/aaaa1111?x=1",
            },
        },
        {
            "sys_created_on": {"display_value": "2025-06-26"},
            "actions": {
                "isLink": True,
                "links": [
                    {"attachment_name": "Technical Attachment.pdf",
                     "url": "https://api-prod.fcc.gov/icfs-attachment/exp/api/v1/bbbb2222"},
                    # duplicate of the first doc under a second widget: dedup on sys_id
                    {"attachment_name": "Narrative.pdf",
                     "url": "https://api-prod.fcc.gov/icfs-attachment/exp/api/v1/aaaa1111"},
                ],
            },
        },
        {"actions": {"display_value": "HTML", "url": "/ibfs?id=schedule_s&sys_id=zz"}},
    ]}}]}]}]}}
    docs = {d["sys_id"]: d for d in extract_documents(page)}
    assert set(docs) == {"aaaa1111", "bbbb2222"}
    assert docs["aaaa1111"]["doc_name"] == "Narrative.pdf"
    assert docs["aaaa1111"]["doc_date"] == "2025-06-25"
    assert "?" not in docs["aaaa1111"]["download_url"]
    assert docs["bbbb2222"]["doc_date"] == "2025-06-26"


def test_extract_documents_empty_tree_yields_nothing():
    assert extract_documents({"result": {"containers": []}}) == []


@pytest.mark.db
def test_harvested_inventory_and_documents_endpoint_agree(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT file_number, count(*) FROM fcc_filing_document "
            "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("no harvested documents in this database")
    fn, n = row
    client = _client()
    d = client.get(f"/api/filings/{fn}/documents").json()
    assert len(d["documents"]) == n
    assert all(doc["download_url"].startswith("https://api-prod.fcc.gov/icfs-attachment/")
               for doc in d["documents"])
    # Unharvested filings answer with an empty inventory, never a 404.
    r = client.get("/api/filings/SATLOA1999010100001/documents")
    assert r.status_code == 200 and r.json()["documents"] == []


@pytest.mark.db
def test_pending_rows_carry_document_counts_and_cohort_slug(db_conn):
    client = _client()
    r = client.get("/api/filings/pending?applicant_slug=spx&limit=50").json()
    assert r["total"] > 0
    row = r["rows"][0]
    assert {"documents_n", "applicant_slug", "note_summary"} <= set(row)
    assert all(x["applicant_slug"] == "spx" for x in r["rows"])
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM fcc_filing_document")
        harvested = cur.fetchone()[0]
    if harvested:
        assert any(x["documents_n"] > 0 for x in r["rows"]), \
            "harvest ran but no spx filing shows documents"


@pytest.mark.db
def test_curated_notes_surface_on_their_filings(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT file_number, summary FROM fcc_filing_note LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no curated filing notes loaded in this database")
    fn, summary = row
    client = _client()
    d = client.get(f"/api/filings/{fn}/documents").json()
    assert d["analyst_note"] is not None and d["analyst_note"]["summary"] == summary
