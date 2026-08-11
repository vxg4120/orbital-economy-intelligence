"""Meta-test: every test that needs a database declares it.

CI runs two jobs. The fast one is `pytest -m "not db"` with no database anywhere, and it exists
so that a broken import or a logic error is caught in seconds rather than after a container
spins up. That job is only honest if the db marker is actually applied, and the failure mode
when it is not is nasty in a specific way: the test passes on every developer machine (where a
database happens to be running) and fails only in CI, which is precisely the signal developers
learn to distrust.

That happened on 2026-08-11. tests/test_pending_link.py::test_filings_q_searches_applicant_name
shipped without the marker, so the network-free job tried to open a connection and CI went red
while every local run stayed green. This test is the tripwire for the next one.

The rule enforced here: a test function that consumes the db_conn fixture, or that builds an API
TestClient (every router in this app reads the database), must carry @pytest.mark.db. It is a
static check over the test sources themselves, so it costs nothing and needs no database.
"""

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# Calling any of these means the test reaches the database through the API layer. `_client` is
# the local helper name several API test modules use to build a TestClient, but it is only that
# in modules that actually import the app: tests/test_ingest_spacetrack.py has its own unrelated
# `_client(conn)` that builds a Space-Track HTTP client, so the name alone is not evidence.
_DB_TOUCHING_CALLS = {"_client", "TestClient"}
_APP_IMPORT_MARKERS = ("api.main", "TestClient")
_DB_FIXTURES = {"db_conn", "seeded"}


def _has_db_marker(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        # @pytest.mark.db  ->  Attribute(attr='db')
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "db":
            return True
    return False


def _calls_db_touching(node: ast.FunctionDef) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in _DB_TOUCHING_CALLS:
                return True
    return False


def _module_is_marked(tree: ast.Module) -> bool:
    """A file can mark every test at once with `pytestmark = pytest.mark.db` (five files here
    do). Missing this is how a well-meaning hygiene check becomes a wall of false positives."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "pytestmark" for t in node.targets):
            continue
        values = node.value.elts if isinstance(node.value, ast.List | ast.Tuple) else [node.value]
        for v in values:
            target = v.func if isinstance(v, ast.Call) else v
            if isinstance(target, ast.Attribute) and target.attr == "db":
                return True
    return False


def _offenders() -> list[str]:
    found = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if _module_is_marked(tree):
            continue
        builds_app_client = any(m in source for m in _APP_IMPORT_MARKERS)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if _has_db_marker(node):
                continue
            params = {a.arg for a in node.args.args}
            uses_fixture = bool(params & _DB_FIXTURES)
            hits_api = builds_app_client and _calls_db_touching(node)
            if uses_fixture or hits_api:
                reason = "uses a db fixture" if uses_fixture else "builds an API client"
                found.append(f"{path.name}::{node.name} ({reason})")
    return found


def test_db_touching_tests_carry_the_db_marker():
    offenders = _offenders()
    assert not offenders, (
        "these tests reach the database but are not marked @pytest.mark.db, so they will run "
        "in CI's network-free job and fail there while passing locally:\n  "
        + "\n  ".join(offenders)
    )


def test_the_hygiene_check_can_actually_detect_an_offender(tmp_path):
    """Anti-vacuity: a static check that silently matches nothing is worse than no check, so
    prove the detector fires on a known-bad shape."""
    bad = tmp_path / "test_sample.py"
    bad.write_text(
        "import pytest\n"
        "def test_unmarked(db_conn):\n"
        "    assert db_conn\n"
        "@pytest.mark.db\n"
        "def test_marked(db_conn):\n"
        "    assert db_conn\n",
        encoding="utf-8",
    )
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not _has_db_marker(fns["test_unmarked"])
    assert _has_db_marker(fns["test_marked"])


@pytest.mark.db
def test_marker_itself_is_registered():
    """The marker must exist in pyproject's marker list, or -W error turns every use of it into
    a PytestUnknownMarkWarning and the whole suite fails on a typo."""
    pyproject = (TESTS_DIR.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"db:' in pyproject or "'db:" in pyproject
