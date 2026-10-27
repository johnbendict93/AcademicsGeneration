"""P5 live-run bug (2026-09-27): Neon's free tier drops idle connections (~5 min). A Groq run takes
6-10 min, so the pooled connection was dead when the app saved the row:
'InterfaceError: connection already closed' -> PDF uploaded to GitHub, row NOT saved, and the app
wrongly said 'Nothing was saved'.
Fix under test: a dead connection is thrown away and the DB call is retried ONCE on a fresh one;
other errors are not retried; the error message says what was really saved.
Fake pool/connections (no Neon). 6 tests FAIL on academicsgen_app.py 771ec974ef; 2 guard kept behaviour
(a normal SQL error is not retried; a failure BEFORE the upload still says 'Nothing was saved')."""
import pytest
import psycopg2

import academicsgen_app as app
from test_app_ui import _all_text, _app, _generate, _events, _undo_driver_patches  # noqa: F401 (autouse fixture)
import test_app_ui


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = -1                            # like psycopg2 before execute (BL-029 c: db_execute reads it)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.conn.executed.append(sql)
        if self.conn.closed:
            raise psycopg2.InterfaceError("connection already closed")
        if self.conn.drop_on_execute:                 # server went away mid-call (psycopg2 marks it closed)
            self.conn.closed = 2
            raise psycopg2.OperationalError("SSL connection has been closed unexpectedly")
        if self.conn.fail_with:
            raise self.conn.fail_with
        self.rowcount = 1

    def fetchall(self):
        return [{"id": 1}]

    def fetchone(self):
        return {"id": 1}


class FakeConn:
    def __init__(self, name, closed=0, drop_on_execute=False, fail_with=None):
        self.name, self.closed = name, closed
        self.drop_on_execute, self.fail_with = drop_on_execute, fail_with
        self.executed, self.commits, self.rollbacks = [], 0, 0

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        if self.closed:
            raise psycopg2.InterfaceError("connection already closed")
        self.commits += 1

    def rollback(self):
        if self.closed:
            raise psycopg2.InterfaceError("connection already closed")
        self.rollbacks += 1


class FakePool:
    def __init__(self, *conns):
        self.conns, self.given, self.returned = list(conns), [], []

    def getconn(self):
        c = self.conns.pop(0)
        self.given.append(c.name)
        return c

    def putconn(self, conn, close=False):
        self.returned.append((conn.name, close))


@pytest.fixture
def pool(monkeypatch):
    def use(*conns):
        p = FakePool(*conns)
        monkeypatch.setattr(app, "get_db_pool", lambda: p)
        return p
    return use


def test_query_on_connection_already_closed_in_pool_uses_a_fresh_one(pool):
    p = pool(FakeConn("dead", closed=1), FakeConn("fresh"))
    assert app.db_query("SELECT 1") == [{"id": 1}]
    assert p.given == ["dead", "fresh"] and ("dead", True) in p.returned and ("fresh", False) in p.returned


def test_execute_dropped_mid_call_is_retried_once_and_committed(pool):
    """The live failure: connection looked open, Neon had closed it -> OperationalError on execute."""
    fresh = FakeConn("fresh")
    p = pool(FakeConn("stale", drop_on_execute=True), fresh)
    app.db_execute("INSERT ...", (1,))
    assert fresh.commits == 1 and fresh.executed == ["INSERT ..."]
    assert ("stale", True) in p.returned and ("fresh", False) in p.returned


def test_query_dropped_mid_call_is_retried_once(pool):
    p = pool(FakeConn("stale", drop_on_execute=True), FakeConn("fresh"))
    assert app.db_query("SELECT 1", fetchone=True) == {"id": 1}
    assert p.given == ["stale", "fresh"]


def test_only_one_retry_then_the_real_error(pool):
    p = pool(FakeConn("a", drop_on_execute=True), FakeConn("b", drop_on_execute=True), FakeConn("c"))
    with pytest.raises(psycopg2.OperationalError):
        app.db_execute("INSERT ...")
    assert p.given == ["a", "b"]                                # 'c' never used


def test_normal_sql_error_is_not_retried_and_rolled_back(pool):
    bad = FakeConn("ok", fail_with=psycopg2.IntegrityError("duplicate key"))
    p = pool(bad, FakeConn("unused"))
    with pytest.raises(psycopg2.IntegrityError):
        app.db_execute("INSERT ...")
    assert p.given == ["ok"] and bad.rollbacks == 1 and bad.commits == 0
    assert ("ok", False) in p.returned                          # healthy connection kept


def test_every_connection_goes_back_to_the_pool(pool):
    p = pool(FakeConn("stale", drop_on_execute=True), FakeConn("fresh"))
    app.db_query("SELECT 1")
    assert sorted(n for n, _ in p.returned) == sorted(p.given)


# ── the screen: honest message when the row cannot be saved ──────────────────
def test_upload_ok_row_failed_says_so_and_still_offers_the_pdf(tmp_path, monkeypatch):
    driver = test_app_ui.DRIVER.replace(
        'app.upsert_generated_pdf = lambda **kw: rec("upsert", **kw)',
        'def _up(**kw):\n    rec("upsert_try")\n    raise psycopg2.InterfaceError("connection already closed")\n'
        'import psycopg2\napp.upsert_generated_pdf = _up')
    assert driver != test_app_ui.DRIVER
    monkeypatch.setattr(test_app_ui, "DRIVER", driver)
    at, log = _app(tmp_path)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()   # BL-037 e
    _generate(at)                                  # CHANGED in BL-037 e: QA (Notes makes no PDF now)
    text = _all_text(at)
    kinds = [e["kind"] for e in _events(log)]
    assert "upload" in kinds and "upsert_try" in kinds
    assert "Nothing was saved" not in text
    assert "uploaded to GitHub" in text and "database row was NOT saved" in text
    assert any(b.label.startswith("⬇️ Download") for b in at.get("download_button"))


def test_failure_before_upload_still_says_nothing_saved(tmp_path, monkeypatch):
    driver = test_app_ui.DRIVER.replace(
        "app.upload_to_github = upload", "def _boom(buf, key):\n    raise RuntimeError('GitHub down')\n"
        "app.upload_to_github = _boom")
    assert driver != test_app_ui.DRIVER
    monkeypatch.setattr(test_app_ui, "DRIVER", driver)
    at, log = _app(tmp_path)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()   # BL-037 e
    _generate(at)
    text = _all_text(at)
    # CHANGED in BL-037 e: runs on QA - only QA still uploads a whole-unit PDF. QA saves no topic rows, so
    # 'Nothing was saved' is true again. (What a Notes stop saved is tested in test_bl037d / test_bl037e.)
    assert "topic_row" not in [e["kind"] for e in _events(log)]
    assert "Nothing was saved" in text and "uploaded to GitHub" not in text
