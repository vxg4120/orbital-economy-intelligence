"""Contract + behavior tests for the Review API (api/routers/review.py).

DB-marked: a FastAPI TestClient runs against the live dev DB. Read tests assert shapes on the real
246 gold_case rows without mutating anything. Write tests operate ONLY on a reserved synthetic row
(case_type 'zz_test_review', a reserved-range subject_ref) that is inserted and deleted by a
fixture, and redirect the verdicts.jsonl append to a tmp path -- so neither the real 246 rows'
verdicts nor the committed gold file are ever touched.
"""

import warnings

import pytest
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from api.routers.review import observed_through, since_drawn
from common.db import get_conn

TEST_CASE_TYPE = "zz_test_review"
TEST_SUBJECT = "norad:940000777"
TEST_TOKEN = "unit-test-review-token"


@pytest.fixture
def client(db_conn):  # db_conn: skips the whole module when the dev DB is unreachable
    return TestClient(app)


# --------------------------------------------------------------------------------------------
# Read endpoints (against the real corpus; read-only)
# --------------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.graph
def test_stats_shape(client):
    r = client.get("/api/review/stats")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"strata", "overall", "accuracy_so_far", "drawn"}
    # The queue dates itself: the span of days its cases were drawn on.
    assert set(body["drawn"]) == {"first", "last"}
    assert body["drawn"]["first"] <= body["drawn"]["last"]
    assert isinstance(body["strata"], list) and body["strata"]
    for s in body["strata"]:
        assert set(s) == {
            "case_type", "total", "labeled", "dossiers_ready",
            "correct", "incorrect", "partial", "unresolvable",
        }
        assert s["total"] >= s["labeled"] >= 0
        assert 0 <= s["dossiers_ready"] <= s["total"]
    assert body["overall"]["dossiers_ready"] >= 0
    overall = body["overall"]
    assert overall["total"] >= overall["labeled"]
    # The real build carries the 246-case queue.
    assert overall["total"] >= 200
    assert body["accuracy_so_far"] is None or 0.0 <= body["accuracy_so_far"] <= 1.0


@pytest.mark.db
@pytest.mark.graph
def test_cases_list_and_filters(client):
    # Unlabeled default, filtered to one stratum: rows carry only the list-shape keys.
    r = client.get("/api/review/cases", params={"type": "status_conflict", "only": "unlabeled"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"rows", "total"}
    assert body["total"] >= 1
    for row in body["rows"]:
        assert set(row) == {
            "case_id", "case_type", "subject_ref", "question", "verdict", "labeled_at",
            "has_dossier",
        }
        assert row["case_type"] == "status_conflict"
        assert row["verdict"] is None  # only=unlabeled
        assert isinstance(row["has_dossier"], bool)

    # Pagination + stable ordering (case_type, case_id).
    page = client.get("/api/review/cases", params={"only": "all", "limit": 5, "offset": 0}).json()
    assert len(page["rows"]) == 5
    ids = [(x["case_type"], x["case_id"]) for x in page["rows"]]
    assert ids == sorted(ids)

    # A bad `only` is rejected by the query validator.
    assert client.get("/api/review/cases", params={"only": "bogus"}).status_code == 422


@pytest.mark.db
@pytest.mark.graph
def test_case_detail_has_evidence(client):
    first = client.get("/api/review/cases", params={"only": "all", "limit": 1}).json()["rows"][0]
    r = client.get(f"/api/review/cases/{first['case_id']}")
    assert r.status_code == 200
    body = r.json()
    for key in ("case_id", "case_type", "satellite_id", "subject_ref", "question",
                "system_answer", "evidence", "verdict", "corrected_answer", "labeled_at",
                "dossier", "drawn_at", "observed_through", "since_drawn"):
        assert key in body
    assert body["drawn_at"] is not None
    assert isinstance(body["since_drawn"], list)
    assert isinstance(body["evidence"], dict)  # JSONB decoded to an object
    # dossier is null until a research agent lands one (LEFT JOIN); when present it's an object.
    assert body["dossier"] is None or isinstance(body["dossier"], dict)

    assert client.get("/api/review/cases/99999999").status_code == 404


@pytest.mark.db
def test_case_detail_includes_dossier(client, planted_case):
    """A dossier LEFT-JOINs onto the case detail: null until one lands, an object once inserted.

    Operates only on the reserved temp case (never the real 246); deletes the dossier before the
    planted_case fixture tears the case down, respecting the FK gold_dossier.case_id -> gold_case.
    Research agents are concurrently INSERTing dossiers for the REAL cases — using our own temp row
    keeps this test isolated from that traffic.
    """
    import json

    # No dossier yet -> null on the detail.
    assert client.get(f"/api/review/cases/{planted_case}").json()["dossier"] is None

    evidence = [
        {"claim": "SATCAT lists a day-level decay.", "source_name": "CelesTrak",
         "url": "https://celestrak.org/"},
        {"claim": "GCAT lists only a month.", "source_name": "GCAT",
         "url": "https://planet4589.org/"},
        {"claim": "Space-Track agrees.", "source_name": "Space-Track",
         "url": "https://www.space-track.org/"},
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gold_dossier (case_id, recommended_verdict, recommended_answer, "
                "confidence, summary, evidence, caveats, agent_model) "
                "VALUES (%s, 'correct', 'the day-level date', 'high', 'why this call', "
                "%s::jsonb, 'a caveat', 'unit-test-agent')",
                (planted_case, json.dumps(evidence)),
            )
        conn.commit()

        d = client.get(f"/api/review/cases/{planted_case}").json()["dossier"]
        assert d is not None
        assert d["recommended_verdict"] == "correct"
        assert d["recommended_answer"] == "the day-level date"
        assert d["confidence"] == "high"
        assert d["summary"] == "why this call"
        assert d["caveats"] == "a caveat"
        assert d["agent_model"] == "unit-test-agent"
        assert d["researched_at"] is not None
        assert isinstance(d["evidence"], list) and len(d["evidence"]) == 3
        assert set(d["evidence"][0]) == {"claim", "source_name", "url"}
        assert d["evidence"][0]["source_name"] == "CelesTrak"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gold_dossier WHERE case_id = %s", (planted_case,))
        conn.commit()
        conn.close()


@pytest.mark.db
def test_next_unlabeled_wraps(client, db_conn):
    """next after the LAST unlabeled case (in stable order) wraps to the FIRST unlabeled case."""
    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT case_id FROM gold_case WHERE verdict IS NULL "
            "ORDER BY case_type, case_id LIMIT 1"
        )
        first = cur.fetchone()
        cur.execute(
            "SELECT case_id FROM gold_case WHERE verdict IS NULL "
            "ORDER BY case_type DESC, case_id DESC LIMIT 1"
        )
        last = cur.fetchone()
    if first is None or last is None:
        pytest.skip("no unlabeled cases to exercise wrap")

    # No cursor -> first unlabeled.
    assert client.get("/api/review/next").json()["next_case_id"] == first["case_id"]
    # After the last -> wraps back to the first.
    wrapped = client.get("/api/review/next", params={"after_case_id": last["case_id"]}).json()
    assert wrapped["next_case_id"] == first["case_id"]
    # After the first -> a different (later) case, not the first again.
    after_first = client.get(
        "/api/review/next", params={"after_case_id": first["case_id"]}
    ).json()["next_case_id"]
    assert after_first != first["case_id"]


# --------------------------------------------------------------------------------------------
# A case is a dated snapshot: what it says is what the graph said the day it was drawn
# --------------------------------------------------------------------------------------------


def _packet(**over):
    """A single-satellite evidence packet as build_gold_queue.satellite_evidence writes it."""
    ev = {
        "satellite_id": 1871452, "norad_id": None, "canonical_name": "SENTINEL-1",
        "assertions": [
            {"attribute": "name", "value": "SENTINEL-1", "source": "gcat",
             "observed_at": "2026-07-08T04:24:24+00:00"},
            {"attribute": "status", "value": "R", "source": "gcat",
             "observed_at": "2026-07-07T19:10:00+00:00"},
        ],
        "resolved": {"status": "DECAYED", "owner": "Ethereal Space"},
    }
    ev.update(over)
    return ev


def test_since_drawn_names_what_the_graph_now_says_differently():
    """Case 49 on production: drawn as a NORAD-less SENTINEL-1, the object has since been given
    NORAD 69869 and the name NEXTSAT-4, and it is no longer decayed."""
    now = {"norad_id": 69869, "canonical_name": "NEXTSAT-4", "status": "ACTIVE"}
    assert since_drawn(_packet(), now) == [
        {"field": "norad_id", "then": None, "now": 69869},
        {"field": "canonical_name", "then": "SENTINEL-1", "now": "NEXTSAT-4"},
        {"field": "status", "then": "DECAYED", "now": "ACTIVE"},
    ]
    # Nothing moved: nothing to say.
    same = {"norad_id": None, "canonical_name": "SENTINEL-1", "status": "DECAYED"}
    assert since_drawn(_packet(), same) == []
    # The graph no longer holds the record at all.
    assert since_drawn(_packet(), None) == [{"field": "record", "then": 1871452, "now": None}]
    # A COSPAR cluster has no single subject to compare.
    assert since_drawn({"cospar": "1966-116B", "satellites": [_packet()]}, same) == []


def test_observed_through_walks_every_packet_in_the_case():
    assert observed_through(_packet()) == "2026-07-08T04:24:24+00:00"
    cluster = {"cospar": "x", "satellites": [
        _packet(), _packet(assertions=[{"observed_at": "2026-07-09T00:00:00+00:00"}])]}
    assert observed_through(cluster) == "2026-07-09T00:00:00+00:00"
    joined = _packet(missed_join={"candidate": _packet(
        assertions=[{"observed_at": "2026-07-10T00:00:00+00:00"}])})
    assert observed_through(joined) == "2026-07-10T00:00:00+00:00"
    assert observed_through({"v": 1}) is None


@pytest.mark.db
def test_case_detail_dates_itself_and_reports_what_changed(db_conn):
    """End to end on planted rows in an uncommitted transaction: the stored question is served
    exactly as drawn, and the detail adds when it was drawn and what the graph says now."""
    import json

    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO satellite (norad_id, canonical_name, object_type) "
            "VALUES (940000901, 'NEXTSAT-4', 'PAYLOAD') RETURNING satellite_id"
        )
        sid = cur.fetchone()["satellite_id"]
        cur.execute(
            "INSERT INTO satellite_status_history (satellite_id, canonical_status, observed_at, "
            "source) VALUES (%s, 'ACTIVE', now(), 'gcat')",
            (sid,),
        )
        evidence = _packet(satellite_id=sid, drawn_at="2026-07-11T02:16:54+00:00")
        cur.execute(
            "INSERT INTO gold_case (case_type, satellite_id, subject_ref, question, "
            "system_answer, evidence) VALUES (%s, %s, %s, %s, 'a.', %s::jsonb) "
            "RETURNING case_id",
            (TEST_CASE_TYPE, sid, TEST_SUBJECT,
             "SENTINEL-1 has no NORAD catalog entry yet.", json.dumps(evidence)),
        )
        case_id = cur.fetchone()["case_id"]

    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        client = TestClient(app)
        body = client.get(f"/api/review/cases/{case_id}").json()
        drawn = client.get("/api/review/stats").json()["drawn"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_conn.rollback()

    # The queue's span includes the planted draw day, read through the same expression.
    assert drawn["first"][:10] <= "2026-07-11" <= drawn["last"][:10]
    assert body["question"] == "SENTINEL-1 has no NORAD catalog entry yet."  # untouched
    assert body["drawn_at"].startswith("2026-07-11")  # the builder's stamp wins over created_at
    assert body["observed_through"] == "2026-07-08T04:24:24+00:00"
    assert body["since_drawn"] == [
        {"field": "norad_id", "then": None, "now": 940000901},
        {"field": "canonical_name", "then": "SENTINEL-1", "now": "NEXTSAT-4"},
        {"field": "status", "then": "DECAYED", "now": "ACTIVE"},
    ]


# --------------------------------------------------------------------------------------------
# Write endpoint (token-guarded) — operates only on a reserved synthetic row
# --------------------------------------------------------------------------------------------


@pytest.fixture
def planted_case():
    """Insert (committed) a reserved gold_case row so the API's separate write connection can see
    it; delete it on teardown. Never touches the real 246 rows."""
    conn = get_conn()
    conn.row_factory = dict_row
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM gold_case WHERE case_type = %s AND subject_ref = %s",
                (TEST_CASE_TYPE, TEST_SUBJECT),
            )
            cur.execute(
                "INSERT INTO gold_case (case_type, satellite_id, subject_ref, question, "
                "system_answer, evidence) VALUES (%s, NULL, %s, 'q?', 'a.', '{\"v\": 1}'::jsonb) "
                "RETURNING case_id",
                (TEST_CASE_TYPE, TEST_SUBJECT),
            )
            case_id = cur.fetchone()["case_id"]
        conn.commit()
        yield case_id
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM gold_case WHERE case_type = %s AND subject_ref = %s",
                (TEST_CASE_TYPE, TEST_SUBJECT),
            )
        conn.commit()
        conn.close()


def _read_row(case_id):
    conn = get_conn()
    conn.row_factory = dict_row
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT verdict, corrected_answer, verdict_notes, labeled_at "
                "FROM gold_case WHERE case_id = %s",
                (case_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


@pytest.mark.db
def test_verdict_requires_token(client, planted_case):
    # No token header at all.
    r = client.post(f"/api/review/cases/{planted_case}/verdict", json={"verdict": "correct"})
    assert r.status_code == 401
    # Wrong token.
    r = client.post(
        f"/api/review/cases/{planted_case}/verdict",
        json={"verdict": "correct"},
        headers={"X-Review-Token": "not-the-token"},
    )
    assert r.status_code == 401
    # Nothing was written.
    assert _read_row(planted_case)["verdict"] is None


@pytest.mark.db
def test_verdict_persists_and_appends_jsonl(client, planted_case, tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_TOKEN", TEST_TOKEN)
    verdicts_file = tmp_path / "verdicts.jsonl"
    # Redirect the committed-gold append to a temp path so the real file is never touched.
    monkeypatch.setattr("api.routers.review.VERDICTS_PATH", verdicts_file)

    r = client.post(
        f"/api/review/cases/{planted_case}/verdict",
        json={"verdict": "incorrect", "corrected_answer": "the truth", "notes": "why"},
        headers={"X-Review-Token": TEST_TOKEN},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["ok"] is True
    assert payload["verdict"]["verdict"] == "incorrect"

    # Persisted to the DB (verified on a fresh connection -> reads committed state).
    row = _read_row(planted_case)
    assert row["verdict"] == "incorrect"
    assert row["corrected_answer"] == "the truth"
    assert row["verdict_notes"] == "why"
    assert row["labeled_at"] is not None

    # Appended one line to the (redirected) verdicts.jsonl.
    import json

    lines = verdicts_file.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["subject_ref"] == TEST_SUBJECT
    assert rec["verdict"] == "incorrect"


@pytest.mark.db
def test_relabel_conflicts_without_overwrite(client, planted_case, tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_TOKEN", TEST_TOKEN)
    monkeypatch.setattr("api.routers.review.VERDICTS_PATH", tmp_path / "verdicts.jsonl")
    hdr = {"X-Review-Token": TEST_TOKEN}

    first = client.post(
        f"/api/review/cases/{planted_case}/verdict", json={"verdict": "correct"}, headers=hdr
    )
    assert first.status_code == 200

    # A second verdict without overwrite is a 409...
    clash = client.post(
        f"/api/review/cases/{planted_case}/verdict", json={"verdict": "partial"}, headers=hdr
    )
    assert clash.status_code == 409
    assert _read_row(planted_case)["verdict"] == "correct"  # unchanged

    # ...but overwrite=true relabels.
    ok = client.post(
        f"/api/review/cases/{planted_case}/verdict",
        json={"verdict": "partial", "corrected_answer": "revised", "overwrite": True},
        headers=hdr,
    )
    assert ok.status_code == 200
    assert _read_row(planted_case)["verdict"] == "partial"


@pytest.mark.db
def test_invalid_verdict_with_token_is_422(client, planted_case, monkeypatch):
    monkeypatch.setenv("REVIEW_TOKEN", TEST_TOKEN)
    r = client.post(
        f"/api/review/cases/{planted_case}/verdict",
        json={"verdict": "not-a-verdict"},
        headers={"X-Review-Token": TEST_TOKEN},
    )
    assert r.status_code == 422
