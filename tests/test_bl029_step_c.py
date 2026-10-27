"""BL-029 step c: Students / Admin tabs, ADMIN_PASSWORD gate, review panel (Approve / Reject + note).
Pure logic in source_review.py; the screen via Streamlit AppTest with fake Neon / GitHub / Groq.
Checked 2026-09-28: with the old academicsgen_app 60384ebc3e, all 22 app/screen tests FAIL; the 30 pure
tests are for the new source_review.py (absent before -> fail); test_admin_password_is_not_a_required_secret
GUARDS kept behaviour (passes on old and new)."""
import datetime
import hmac
import json
import re

import pytest

import academicsgen_app as app
import source_review as rv
import source_runner as sr
from test_app_ui import (ADMIN_PW, SHA1, _all_text, _app, _events, _generate, _undo_driver_patches,  # noqa: F401
                         _unlock)

PV = sr.prompt_version()
MADE = datetime.datetime(2026, 9, 28, 10, 0, 0, tzinfo=datetime.timezone.utc)
KEY = "source-strict/AI&DS/AL3501/AL3501_Unit1_Notes.pdf"


def _row(**kw):
    row = {"id": 10, "storage_key": KEY, "storage_bucket": "github", "source_sha256": SHA1,
           "prompt_version": PV, "generated_at": MADE, "review_status": "draft", "reviewed_at": None,
           "review_note": None}
    row.update(kw)
    return row


# ── password ──────────────────────────────────────────────────────────────────
def test_password_right_and_wrong():
    assert rv.password_ok("s3cret-ü", "s3cret-ü")
    assert not rv.password_ok("s3cret", "s3cret-ü")
    assert not rv.password_ok("S3CRET-ü", "s3cret-ü")


@pytest.mark.parametrize("secret", [None, "", 123])
def test_missing_secret_never_unlocks(secret):
    assert not rv.password_ok("", secret) and not rv.password_ok("anything", secret)
    assert not rv.password_ok(None, secret)


@pytest.mark.parametrize("typed", [None, "", 5])
def test_empty_typed_never_unlocks(typed):
    assert not rv.password_ok(typed, "pw")


def test_password_uses_constant_time_compare(monkeypatch):
    seen = []
    real = hmac.compare_digest
    monkeypatch.setattr(rv.hmac, "compare_digest", lambda a, b: seen.append(1) or real(a, b))
    assert rv.password_ok("pw", "pw")
    assert seen == [1]


# ── review state + decision ───────────────────────────────────────────────────
@pytest.mark.parametrize("row, want", [
    (None, "none"), ({}, "none"), ({"storage_key": None}, "none"),
    (_row(), "draft"), (_row(review_status="approved"), "approved"),
    (_row(review_status="rejected"), "rejected"), (_row(review_status=None), "draft"),
    (_row(source_sha256="0" * 64), "stale"), (_row(prompt_version="sp:x+sq:y+sv:z"), "stale"),
    (_row(review_status="approved", prompt_version="old"), "stale"),
])
def test_review_state(row, want):
    assert rv.review_state(row, SHA1, PV) == want


def test_state_texts_are_plain_and_rejected_shows_note():
    assert "cannot" in rv.state_text("draft").lower()
    assert "students can download" in rv.state_text("approved").lower()
    assert "OUT OF DATE" in rv.state_text("stale")
    assert "too short" in rv.state_text("rejected", _row(review_note="too short"))


def test_approve_params_in_sql_order():
    p = rv.review_params("approved", "  ", _row(), SHA1, PV)
    assert p == ("approved", None, 10, KEY, SHA1, PV, MADE)
    assert rv.REVIEW_SQL.count("%s") == len(p)


def test_reject_needs_a_note_and_stores_it():
    with pytest.raises(rv.ReviewError, match="note"):
        rv.review_params("rejected", "   ", _row(), SHA1, PV)
    assert rv.review_params("rejected", " wrong lemma ", _row(), SHA1, PV)[:2] == ("rejected", "wrong lemma")


@pytest.mark.parametrize("row", [None, _row(prompt_version="old"), _row(source_sha256="0" * 64)])
def test_no_decision_on_missing_or_stale_pdf(row):
    with pytest.raises(rv.ReviewError):
        rv.review_params("approved", "", row, SHA1, PV)


def test_bad_decision_long_note_missing_id():
    with pytest.raises(rv.ReviewError):
        rv.review_params("draft", "", _row(), SHA1, PV)            # 'draft' is never set by hand
    with pytest.raises(rv.ReviewError, match="too long"):
        rv.review_params("approved", "x" * (rv.NOTE_MAX + 1), _row(), SHA1, PV)
    with pytest.raises(rv.ReviewError):
        rv.review_params("approved", "", _row(id=None), SHA1, PV)
    with pytest.raises(rv.ReviewError):
        rv.review_params("approved", "", _row(generated_at=None), SHA1, PV)


def test_review_sql_touches_only_the_review_columns_and_the_exact_version():
    sql = " ".join(rv.REVIEW_SQL.split())
    set_part = re.search(r"SET (.*) WHERE", sql).group(1)
    assert sorted(c.split("=")[0].strip() for c in set_part.split(",")) == \
        ["review_note", "review_status", "reviewed_at"]
    where = sql.split("WHERE", 1)[1]
    for col in ("id", "storage_key", "source_sha256", "prompt_version", "generated_at"):
        assert re.search(rf"\b{col} = %s", where)
    assert sql.startswith("UPDATE generated_pdfs ") and ";" not in sql


def test_local_naive():
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    d = datetime.datetime(2026, 9, 28, 4, 30, tzinfo=datetime.timezone.utc)
    assert rv.local_naive(d) == d.astimezone().replace(tzinfo=None)
    assert rv.local_naive(d.astimezone(ist)) == rv.local_naive(d)
    assert rv.local_naive(datetime.datetime(2026, 1, 1)) == datetime.datetime(2026, 1, 1)
    assert rv.local_naive(None) is None


# ── check log for the review ──────────────────────────────────────────────────
def _log(d, name, sha=SHA1, pv=PV, **extra):
    d.mkdir(exist_ok=True)
    rep = {"subject_code": "AL3501", "unit": 1, "variant": "Notes", "source_sha256": sha,
           "prompt_version": pv, "needs_review": [], "failed": [], "topics": [], **extra}
    (d / name).write_text(json.dumps(rep), encoding="utf-8")
    return d / name


def test_latest_check_log_picks_newest_of_this_version(tmp_path):
    d = tmp_path / "logs"
    _log(d, "check_AL3501_U1_Notes_20260928-090000.json")
    want = _log(d, "check_AL3501_U1_Notes_20260928-093000_1.json")
    _log(d, "check_AL3501_U1_Notes_20260928-093000.json")
    _log(d, "check_AL3501_U1_Notes_20260928-095000.json", pv="sp:old+sq:old")     # other prompts
    _log(d, "check_AL3501_U1_Notes_20260928-095500.json", sha="0" * 64)          # other source
    _log(d, "check_AL3501_U1_QA_20260928-095900.json")                           # other variant
    _log(d, "check_AL3501_U2_Notes_20260928-095900.json")                        # other unit
    _log(d, "check_AL3501_U1_Notes_20260928-110000.json")                        # after the PDF was made
    (d / "check_AL3501_U1_Notes_20260928-095800.json").write_text("{broken", encoding="utf-8")
    _log(d, "check_AL3501_U1_Notes_20260928-099999.json")                        # not a real time
    got = rv.latest_check_log(d, "AL3501", 1, "Notes", _row(),
                              made_at=datetime.datetime(2026, 9, 28, 10, 0, 0))
    assert got and got[0] == want
    assert rv.latest_check_log(d, "AL3501", 1, "Notes", _row())[0].name.endswith("110000.json")


def test_latest_check_log_none(tmp_path):
    assert rv.latest_check_log(tmp_path / "nope", "AL3501", 1, "Notes", _row()) is None
    assert rv.latest_check_log(tmp_path, "AL3501", 1, "Notes", None) is None


def test_check_summary_points_at_risky_spots():
    rep = {"needs_review": ["Minimum Edit Distance"], "failed": ["X", "Minimum Edit Distance"], "topics": [
        {"topic": "Origins", "status": "gap", "writer": None},
        {"topic": "Regex", "status": "ok", "writer": {"new_terms": [], "odd_chars": [], "dropped": []}},
        {"topic": "LM", "status": "ok", "writer": {"new_terms": ["POS"], "odd_chars": [], "dropped": []}},
        {"topic": "Minimum Edit Distance", "status": "needs_review",
         "writer": {"new_terms": ["DP"], "odd_chars": ["U+00B7"], "dropped": [{"reason": "r", "text": "t"}]}}]}
    s = rv.check_summary(rep)
    assert s["in_pdf"] == ["Regex", "LM"] and s["gaps"] == ["Origins"]
    assert s["left_out"] == ["Minimum Edit Distance", "X"]
    assert [f["topic"] for f in s["flagged"]] == ["LM", "Minimum Edit Distance"]
    assert s["flagged"][1] == {"topic": "Minimum Edit Distance", "status": "needs_review",
                               "new_terms": ["DP"], "odd_chars": ["U+00B7"], "latex": [], "dropped": 1}  # BL-031: +latex


# ── app wiring ────────────────────────────────────────────────────────────────
def test_existing_row_select_reads_the_review_columns(monkeypatch):
    seen = {}
    monkeypatch.setattr(app, "db_query", lambda sql, params=None, fetchone=False: seen.update(sql=sql))
    app.check_existing_pdf("AL3501", 1, "Notes")
    for col in ("id", "generated_at", "review_status", "reviewed_at", "review_note"):
        assert re.search(rf"\b{col}\b", seen["sql"])


def test_set_review_runs_the_review_sql_and_returns_rows_changed(monkeypatch):
    seen = {}
    monkeypatch.setattr(app, "db_execute", lambda sql, params=None: seen.update(sql=sql, params=params) or 1)
    p = rv.review_params("approved", "", _row(), SHA1, PV)
    assert app.set_review(p) == 1 and seen == {"sql": rv.REVIEW_SQL, "params": p}


def test_db_execute_returns_rowcount(monkeypatch):
    class Cur:
        rowcount = -1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params): self.rowcount = 0
    class Conn:
        closed = 0
        def cursor(self): return Cur()
        def commit(self): pass
    monkeypatch.setattr(app, "_run_db", lambda work: work(Conn()))
    assert app.db_execute("UPDATE ...", (1,)) == 0


def test_admin_password_is_not_a_required_secret():
    """GUARDS kept behaviour: a missing ADMIN_PASSWORD must not stop the Students tab."""
    assert "ADMIN_PASSWORD" not in app.REQUIRED_SECRETS


# ── screen: Students tab ──────────────────────────────────────────────────────
def test_two_tabs(tmp_path):
    at, _ = _app(tmp_path)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["🎓 Students", "🔒 Admin"]


def test_no_generate_button_without_the_password(tmp_path):
    at, log = _app(tmp_path)
    assert not any(b.key == "generate" for b in at.button)
    assert not at.sidebar.button
    assert _events(log) == []


@pytest.mark.parametrize("row", [None, _row(), _row(review_status="rejected"),
                                 _row(review_status="approved", prompt_version="sp:a+sq:b+sv:c")])
def test_students_get_nothing_unless_approved_and_current(tmp_path, row):
    at, log = _app(tmp_path, existing=row)
    assert not at.exception
    assert "Not available yet" in _all_text(at)
    assert not any(b.key == "student_get" for b in at.button)
    assert _events(log) == []                                    # nothing fetched


def test_students_download_an_approved_current_pdf(tmp_path):
    at, log = _app(tmp_path, existing=_row(review_status="approved"))
    assert "Ready — checked and approved" in _all_text(at) and "CC BY-SA 4.0" in _all_text(at)
    assert _events(log) == []                                    # fetched only on click
    at.button(key="student_get").click().run()
    assert not at.exception
    assert [e["kind"] for e in _events(log)] == ["download"]
    assert any(d.label.startswith("⬇️ Download AL3501_Unit1_Notes.pdf") for d in at.get("download_button"))


def test_students_unit_without_source(tmp_path):
    at, _ = _app(tmp_path)
    unit = next(s for s in at.sidebar.selectbox if s.label == "Unit")
    unit.select(next(o for o in unit.options if o.startswith("Unit 5"))).run()
    assert "Not available yet — No verified source for Unit V yet" in _all_text(at)


# ── screen: Admin tab ─────────────────────────────────────────────────────────
def test_admin_locked_when_secret_missing(tmp_path):
    at, log = _app(tmp_path, admin_pw=None)
    assert not at.exception
    assert "Admin is locked: ADMIN_PASSWORD is not set" in _all_text(at)
    assert not any(t.key == "admin_pw" for t in at.text_input)
    assert not any(b.key == "generate" for b in at.button)


def test_admin_wrong_password(tmp_path):
    at, log = _app(tmp_path)
    _unlock(at, "not-the-password")
    assert "Wrong password" in _all_text(at)
    assert not any(b.key in ("generate", "approve", "reject") for b in at.button)


def test_password_never_shown(tmp_path):
    at, log = _app(tmp_path, existing=_row())
    _unlock(at)
    at.button(key="approve").click().run()
    shown = _all_text(at) + json.dumps(_events(log)) + "".join(str(b.label) for b in at.button)
    assert ADMIN_PW not in shown
    box = next(t for t in at.text_input if t.key == "admin_pw")
    assert box.proto.type == box.proto.PASSWORD                  # typed as dots


def test_admin_sees_draft_and_approves(tmp_path):
    at, log = _app(tmp_path, existing=_row())
    _unlock(at)
    assert "DRAFT - waiting for your review" in _all_text(at)
    at.button(key="approve").click().run()
    assert not at.exception
    ev = [e for e in _events(log) if e["kind"] == "review"]
    assert len(ev) == 1 and ev[0]["params"][:4] == ["approved", "None", "10", KEY]
    assert "Saved." in _all_text(at)


def test_reject_without_note_saves_nothing_with_note_saves(tmp_path):
    at, log = _app(tmp_path, existing=_row())
    _unlock(at)
    at.button(key="reject").click().run()
    assert "Not saved" in _all_text(at) and not [e for e in _events(log) if e["kind"] == "review"]
    at.text_area(key="review_note").input("'men' lemma is wrong").run()
    at.button(key="reject").click().run()
    ev = [e for e in _events(log) if e["kind"] == "review"]
    assert len(ev) == 1 and ev[0]["params"][:2] == ["rejected", "'men' lemma is wrong"]


def test_pdf_changed_meanwhile_says_not_saved(tmp_path):
    at, log = _app(tmp_path, existing=_row(), review_rows=0)
    _unlock(at)
    at.button(key="approve").click().run()
    assert "this PDF changed since the page loaded" in _all_text(at)


def test_stale_row_has_no_approve(tmp_path):
    at, _ = _app(tmp_path, existing=_row(prompt_version="sp:a7ab2ea220+sq:7117414ada+sv:d1c53640d2"))
    _unlock(at)
    assert "OUT OF DATE" in _all_text(at)
    assert not any(b.key in ("approve", "reject", "review_get") for b in at.button)


def test_admin_can_read_the_draft(tmp_path):
    at, log = _app(tmp_path, existing=_row())
    _unlock(at)
    assert _events(log) == []
    at.button(key="review_get").click().run()
    assert [e["kind"] for e in _events(log)] == ["download"]
    assert any(d.label.startswith("⬇️ Download") for d in at.get("download_button"))


def test_review_panel_shows_the_check_log_summary(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setenv("ACADEMICSGEN_RUN_LOG_DIR", str(logs))
    _log(logs, "check_AL3501_U1_Notes_20200101-000000.json", needs_review=["Minimum Edit Distance"], topics=[
        {"topic": "Minimum Edit Distance", "status": "needs_review",
         "writer": {"new_terms": ["DP"], "odd_chars": ["U+00B7"], "dropped": []}}])
    at, _ = _app(tmp_path, existing=_row())
    _unlock(at)
    text = _all_text(at)
    assert "check_AL3501_U1_Notes_20200101-000000.json" in text
    assert "Left out (could not be fully checked): Minimum Edit Distance" in text
    assert "DP" in text and "U+00B7" in text


def test_rejected_current_row_can_be_generated_again(tmp_path):
    """CHANGED in BL-037 e: runs on QA (a Notes Generate writes topics only; the Notes PDF is step f)."""
    at, log = _app(tmp_path, existing=_row(review_status="rejected", review_note="bad"))
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()   # BL-037 e
    _generate(at)
    assert not at.exception
    kinds = [e["kind"] for e in _events(log)]
    assert kinds.count("groq") == 7 and "upload" in kinds and "upsert" in kinds


def test_after_generate_it_is_a_draft_not_for_students(tmp_path):
    """CHANGED in BL-037 e: runs on QA (a Notes Generate writes topics only; the Notes PDF is step f)."""
    at, log = _app(tmp_path)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()   # BL-037 e
    _generate(at)
    text = _all_text(at)
    assert "saved as a DRAFT" in text and "until you approve it" in text
    assert not any(b.key == "student_get" for b in at.button)
