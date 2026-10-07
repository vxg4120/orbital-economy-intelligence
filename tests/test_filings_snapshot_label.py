"""The pending list says how old it is.

The IBFS bulk download stopped gaining filings in mid-2025, when the FCC moved satellite
applications to ICFS, so /api/filings/pending is a snapshot. The response dates it with the newest
filing in the whole list (not the search result) and says so in its note. No database: a stub
connection answers every query.
"""

import warnings


class _Cursor:
    def __init__(self):
        self.last = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.last = query

    def fetchall(self):
        return []

    def fetchone(self):
        if "max(date_filed)" in self.last:
            return {"newest_filed": "2025-06-18"}
        return {"total": 667}


class _Conn:
    def cursor(self):
        return _Cursor()


def _pending_response(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.deps import get_db
    from api.main import app

    app.dependency_overrides[get_db] = _Conn
    try:
        return TestClient(app).get(path)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_pending_list_is_dated_and_says_it_is_a_snapshot():
    body = _pending_response("/api/filings/pending").json()
    assert body["newest_filed"] == "2025-06-18"
    assert "snapshot" in body["note"]
    assert "ICFS" in body["note"]


def test_the_date_covers_the_whole_list_not_the_search():
    body = _pending_response("/api/filings/pending?q=zzz-no-such-applicant").json()
    assert body["newest_filed"] == "2025-06-18"
