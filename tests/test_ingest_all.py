"""scripts/ingest_all.py orchestration tests: execution order, per-source isolation from
failures, CLI arg handling. Pure unit tests — loaders are stubbed, no network or DB touched
(except the dedicated real-transaction regression test below, marked `db`)."""

import pytest

from scripts import ingest_all


class _FakeConn:
    def __init__(self):
        self.rollback_calls = 0

    def close(self):
        pass

    def rollback(self):
        self.rollback_calls += 1


def test_order_is_politeness_safe_and_covers_every_source():
    assert ingest_all._ORDER == ["satcat", "gcat", "gp", "supgp", "ucs"]
    assert set(ingest_all._RUNNERS) == set(ingest_all._ORDER)


def test_main_runs_all_sources_in_order_and_survives_one_failure(monkeypatch, capsys):
    calls = []

    def _make_runner(name, *, fail):
        def _runner(conn):
            calls.append(name)
            if fail:
                raise RuntimeError(f"{name} boom")

        return _runner

    fake_runners = {name: _make_runner(name, fail=(name == "gp")) for name in ingest_all._ORDER}
    monkeypatch.setattr(ingest_all, "_RUNNERS", fake_runners)
    monkeypatch.setattr(ingest_all, "_print_run_table", lambda conn: None)
    conn = _FakeConn()

    rc = ingest_all.main(["--source", "all"], conn=conn)

    assert rc == 1  # a partially-failed ingest exits non-zero so cron/CI can detect it
    assert calls == ["satcat", "gcat", "gp", "supgp", "ucs"]  # gp failing didn't stop the rest
    err = capsys.readouterr().err
    assert "gp failed" in err
    assert "FAILED sources (1): gp" in err  # aggregate failure summary line
    # A DB-level failure aborts the transaction; without a rollback every later loader's first
    # query would cascade-fail with InFailedSqlTransaction. Exactly one failure -> one rollback.
    assert conn.rollback_calls == 1


def test_main_runs_a_single_source_only(monkeypatch):
    calls = []
    fake_runners = {
        name: (lambda conn, name=name: calls.append(name)) for name in ingest_all._ORDER
    }
    monkeypatch.setattr(ingest_all, "_RUNNERS", fake_runners)
    monkeypatch.setattr(ingest_all, "_print_run_table", lambda conn: None)

    ingest_all.main(["--source", "ucs"], conn=_FakeConn())

    assert calls == ["ucs"]


def test_invalid_source_choice_exits_nonzero():
    with pytest.raises(SystemExit):
        ingest_all.main(["--source", "bogus"], conn=_FakeConn())


@pytest.mark.db
def test_a_db_level_failure_does_not_cascade_into_later_loaders(db_conn):
    """Regression test: a loader that aborts the transaction (e.g. a constraint violation, not
    an HTTP error — polite_get's HTTP-error path already commits before raising) must not take
    every later loader down with it via InFailedSqlTransaction."""

    def _broken_runner(conn):
        with conn.cursor() as cur:
            # NOT NULL violation: aborts the current transaction without committing/rolling back.
            cur.execute(
                "INSERT INTO gp_elements (norad_id, epoch, mean_motion, eccentricity, source) "
                "VALUES (900000098, now(), NULL, 0.1, 'test')"
            )

    calls = []

    def _ok_runner(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        calls.append("ok")

    ingest_all_runners = {"satcat": _broken_runner, "gcat": _ok_runner, "gp": _ok_runner,
                           "supgp": _ok_runner, "ucs": _ok_runner}
    orig_runners = ingest_all._RUNNERS
    orig_print = ingest_all._print_run_table
    ingest_all._RUNNERS = ingest_all_runners
    ingest_all._print_run_table = lambda conn: None
    try:
        rc = ingest_all.main(["--source", "all"], conn=db_conn)
    finally:
        ingest_all._RUNNERS = orig_runners
        ingest_all._print_run_table = orig_print
        db_conn.rollback()

    assert rc == 1  # satcat aborted -> non-zero exit, but the rest still ran
    assert calls == ["ok", "ok", "ok", "ok"]  # all 4 loaders after the broken one still ran
    with db_conn.cursor() as cur:
        cur.execute("SELECT 1")  # connection is healthy again after main() returns
