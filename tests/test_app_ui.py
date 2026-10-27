"""P3c-2: the real Streamlit screen (academicsgen_app.main) driven by Streamlit's
AppTest, with fake Neon / GitHub / Groq and no real waiting.

Checks D1 (Notes + QA only), R3 (unsourced unit: message + Generate disabled),
design section 5 (coverage table before generating), the serve/regenerate rule
(old rows never served), and that a Groq failure saves nothing.
"""
import json
import os
import textwrap

import pytest
from streamlit.testing.v1 import AppTest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA1 = "f034c46d67adb38e092d690a47e14e8958c537cef1d33be35b9b97a1ef09af91"

DRIVER = '''
import datetime, json, sys
sys.path.insert(0, {app_dir!r}); sys.path.insert(0, {tests_dir!r})
import academicsgen_app as app
import source_runner, test_source_app as fakes
LOG = {log!r}
def rec(kind, **kw):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({{"kind": kind, **kw}}) + "\\n")
source_runner.TokenPacer.wait_for = lambda self, tokens: None        # no real waiting
app.load_departments = lambda: [{{"id": 1, "code": "AI&DS", "name": "Artificial Intelligence and Data Science"}}]
app.load_subjects_for_dept = lambda i: [{{"id": 1, "code": "AL3501", "name": "Natural Language Processing", "regulation": "R-2021"}}]
app.load_units_for_subject = lambda c: [{{"id": n, "unit_number": n, "title": t}} for n, t in
    [(1, "Introduction"), (2, "Word Level Analysis"), (3, "Syntactic Analysis"),
     (4, "Semantics and Pragmatics"), (5, "Discourse Analysis and Lexical Resources")]]
EXISTING = {existing!r}
app.check_existing_pdf = lambda code, n, v: EXISTING
app.check_existing_notes_pdf = lambda code, n, v: EXISTING   # BL-037 f: Notes rows also carry topic_row_ids
app.upsert_topic_pdf = lambda **kw: rec("upsert_topics", **kw)
def upload(buf, key):
    data = buf.read(); rec("upload", key=key, size=len(data), pdf=data[:5].decode("latin-1"))
    return "github", len(data), data
app.upload_to_github = upload
app.download_pdf = lambda bucket, key: (rec("download", key=key) or b"%PDF-served")
app.upsert_generated_pdf = lambda **kw: rec("upsert", **kw)
STORE = {store!r}                                # BL-037 e: fake topic_texts table in a file (survives reruns)
def _load_store():
    try:
        with open(STORE, encoding="utf-8") as f:
            rows = json.load(f)
    except FileNotFoundError:
        rows = []
    for r in rows:
        r["generated_at"] = datetime.datetime.fromisoformat(r["generated_at"])
    return rows
def _save_store(rows):
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump([dict(r, generated_at=r["generated_at"].isoformat()) for r in rows], f)
app.load_topic_rows = lambda code, n, v: [r for r in _load_store()
                                          if (r["subject_code"], r["unit_number"], r["variant"]) == (code, n, v)]
TOPIC_IDS = []                                   # BL-037 d: fake topic_texts INSERT (one id per saved topic)
def save_topic(row):
    rows = _load_store()
    TOPIC_IDS.append(500 + len(rows))
    rows.append(dict(row, id=TOPIC_IDS[-1], generated_at=datetime.datetime.now(datetime.timezone.utc),
                     review_status="draft", reviewed_at=None, review_note=None))
    _save_store(rows)
    rec("topic_row", id=TOPIC_IDS[-1], topic=row["topic"], topic_index=row["topic_index"],
        status=row["check_data"]["status"], has_raw=bool(row["raw_answer"]), variant=row["variant"])
    return TOPIC_IDS[-1]
app.save_topic_row = save_topic
def _topic_review(params):                       # BL-037 e: same guard as topic_review.TOPIC_REVIEW_SQL
    rec("topic_review", params=[str(p) for p in params])
    decision, note, rid, seen_status, seen_at = params
    rows = _load_store()
    key = lambda r: (r["subject_code"], r["unit_number"], r["variant"], r["topic"])
    n = 0
    for r in rows:
        newest = not any(key(o) == key(r) and (o["generated_at"], o["id"]) > (r["generated_at"], r["id"])
                         for o in rows)
        if r["id"] == rid and r["review_status"] == seen_status and r["generated_at"] == seen_at and newest:
            r.update(review_status=decision, review_note=note, reviewed_at="now")
            n += 1
    _save_store(rows)
    return n
app.set_topic_review = _topic_review
MARKS = {marks!r}                                # BL-041: fake topic_gap_marks table in a file (survives reruns)
def _load_marks():
    try:
        with open(MARKS, encoding="utf-8") as f:
            ms = json.load(f)
    except FileNotFoundError:
        ms = []
    for m in ms:
        for k in ("marked_at", "unmarked_at"):
            if m.get(k):
                m[k] = datetime.datetime.fromisoformat(m[k])
    return ms
def _save_marks(ms):
    with open(MARKS, "w", encoding="utf-8") as f:
        json.dump([{{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in m.items()}} for m in ms], f)
app.load_gap_marks = lambda code, n: [m for m in _load_marks() if (m["subject_code"], m["unit_number"]) == (code, n)]
def _mark(params):                               # same guard as topic_review.GAP_MARK_INSERT_SQL
    rec("gap_mark", params=[str(p) for p in params])
    code, n, topic, sha, note = params[:5]
    ms = _load_marks()
    if any((m["subject_code"], m["unit_number"], m["topic"], m["source_sha256"]) == (code, n, topic, sha)
           and m["unmarked_at"] is None for m in ms):
        return 0
    ms.append({{"id": 900 + len(ms), "subject_code": code, "unit_number": n, "topic": topic, "source_sha256": sha,
               "note": note, "marked_at": datetime.datetime.now(datetime.timezone.utc), "unmarked_at": None}})
    _save_marks(ms)
    return 1
app.mark_topic = _mark
def _unmark(params):                             # same guard as topic_review.GAP_MARK_UNDO_SQL
    rec("gap_unmark", params=[str(p) for p in params])
    ms, n = _load_marks(), 0
    for m in ms:
        if m["id"] == params[0] and m["unmarked_at"] is None:
            m["unmarked_at"] = datetime.datetime.now(datetime.timezone.utc)
            n += 1
    _save_marks(ms)
    return n
app.unmark_topic = _unmark
REVIEW_ROWS = {review_rows!r}
def _review(params):
    rec("review", params=[str(p) for p in params])
    return REVIEW_ROWS
app.set_review = _review
def groq(prompt, **kw):
    rec("groq")
    if {groq_fails!r}:
        raise RuntimeError("HTTP 429: rate limit")
    return fakes.fake_groq(prompt, **kw)
fakes.fake_groq.calls = []
app._groq_chat = groq
app.main()
'''


@pytest.fixture(autouse=True)
def _undo_driver_patches():
    """AppTest runs the driver in THIS process: its patches (fake DB/Groq, no waiting)
    must not leak into other test files (e.g. the pacing tests)."""
    import academicsgen_app
    import source_runner
    saved_app = dict(vars(academicsgen_app))
    saved_wait = source_runner.TokenPacer.wait_for
    yield
    for k in [k for k in vars(academicsgen_app) if k not in saved_app]:
        delattr(academicsgen_app, k)
    for k, v in saved_app.items():
        setattr(academicsgen_app, k, v)
    source_runner.TokenPacer.wait_for = saved_wait


ADMIN_PW = "test-Admin-pw-7"                 # a fake secret: must never show up on screen


def _app(tmp_path, existing=None, groq_fails=False, admin_pw=ADMIN_PW, review_rows=1, topic_rows=None,
         gap_marks=None):
    """topic_rows (BL-037 e): topic_texts rows the fake table starts with (generated_at = datetime).
    gap_marks (BL-041): topic_gap_marks rows the fake table starts with (marked_at / unmarked_at = datetime|None)."""
    log = tmp_path / "log.jsonl"
    script = tmp_path / "driver.py"
    store = tmp_path / "topics.json"
    marks = tmp_path / "marks.json"
    if gap_marks:
        marks.write_text(json.dumps([{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in m.items()}
                                     for m in gap_marks]), encoding="utf-8")
    if topic_rows:
        store.write_text(json.dumps([dict(r, generated_at=r["generated_at"].isoformat(),
                                          reviewed_at=None if r.get("reviewed_at") is None else "then")
                                     for r in topic_rows]), encoding="utf-8")
    script.write_text(textwrap.dedent(DRIVER.format(
        app_dir=APP_DIR, tests_dir=os.path.join(APP_DIR, "tests"), log=str(log),
        existing=existing, groq_fails=groq_fails, review_rows=review_rows, store=str(store),
        marks=str(marks))), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=120)
    at.secrets.update({k: "x" for k in ("NEON_HOST", "NEON_DATABASE", "NEON_USER", "NEON_PASSWORD",
                                        "GITHUB_PDF_TOKEN", "GITHUB_PDF_REPO", "SUBJECTS_JSON_PATH",
                                        "GROQ_API_KEY")})
    if admin_pw is not None:
        at.secrets["ADMIN_PASSWORD"] = admin_pw
    at.run()
    return at, log


def _unlock(at, pw=ADMIN_PW):
    """BL-029 step c: Generate + review live in the Admin tab behind ADMIN_PASSWORD."""
    at.text_input(key="admin_pw").input(pw).run()
    return at


def _gen_button(at):
    return at.button(key="generate")


def _generate(at):
    _unlock(at)
    _gen_button(at).click().run()
    return at


def _events(log):
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]


def _box(at, label):
    return next(s for s in at.sidebar.selectbox if s.label == label)


def _all_text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + \
            [w.value for w in at.warning] + [e.value for e in at.error] + [s.value for s in at.success] + \
            [i.value for i in at.info]
    for df in at.dataframe:
        parts.append(df.value.to_string())
    return "\n".join(str(p) for p in parts)


def test_only_notes_and_qa(tmp_path):
    at, _ = _app(tmp_path)
    assert not at.exception
    assert _box(at, "Variant").options == ["Notes", "QA"]


def test_unit_without_source_is_marked_and_cannot_generate(tmp_path):
    at, log = _app(tmp_path)
    unit = _box(at, "Unit")
    v = next(o for o in unit.options if o.startswith("Unit 5"))
    assert "no verified source yet" in v.lower()
    unit.select(v).run()
    assert not at.exception
    _unlock(at)
    assert _gen_button(at).disabled
    assert "No verified source for Unit V yet" in _all_text(at)
    assert _events(log) == []


def test_coverage_table_before_generating(tmp_path):
    """CHANGED in BL-029 step c: the coverage table is shown in the Admin tab (after the password)."""
    at, log = _app(tmp_path)
    _unlock(at)
    text = _all_text(at)
    assert "Origins and challenges of NLP" in text and "not in source" in text
    assert "Covers 7 of 8" in text and "CC BY-SA 4.0" in text
    assert not _gen_button(at).disabled
    assert _events(log) == []                                  # nothing called before the click


def test_old_row_is_not_served_new_pdf_generated_and_saved(tmp_path):
    """CHANGED in BL-037 e: runs on QA - a Notes Generate now writes topics only (no PDF, step f builds it)."""
    old = {"storage_key": "AI&DS/AL3501/AL3501_Unit1_QA.pdf", "storage_bucket": "github",
           "source_sha256": None, "prompt_version": None}
    at, log = _app(tmp_path, existing=old)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()   # BL-037 e
    _generate(at)
    assert not at.exception, at.exception
    ev = _events(log)
    kinds = [e["kind"] for e in ev]
    assert "download" not in kinds
    assert kinds.count("groq") == 7                           # 7 writer, verifier OFF (BL-029)
    up = next(e for e in ev if e["kind"] == "upload")
    assert up["key"] == "source-strict/AI&DS/AL3501/AL3501_Unit1_QA.pdf" and up["pdf"] == "%PDF-"
    row = next(e for e in ev if e["kind"] == "upsert")
    assert row["variant"] == "QA" and row["storage_key"] == up["key"]
    assert row["row_values"]["source_sha256"] == SHA1
    assert row["row_values"]["topics_covered"] == 7 and row["row_values"]["topics_total"] == 8
    assert kinds.index("upload") < kinds.index("upsert")       # DB row only after upload


def test_current_row_is_not_generated_again(tmp_path):
    """CHANGED in BL-029 step c (was: served on Generate). A current draft is not remade and not
    handed out by Generate - it is reviewed in the panel; students get it only once approved.
    CHANGED in BL-037 e: runs on QA (Notes Generate no longer makes a whole-unit PDF)."""
    import source_runner
    cur = {"id": 10, "storage_key": "source-strict/AI&DS/AL3501/AL3501_Unit1_QA.pdf",
           "storage_bucket": "github", "source_sha256": SHA1, "prompt_version": source_runner.prompt_version(),
           "review_status": "draft"}
    at, log = _app(tmp_path, existing=cur)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()   # BL-037 e
    _generate(at)
    assert not at.exception
    assert [e["kind"] for e in _events(log)] == []
    assert "already exists (DRAFT)" in _all_text(at)


def test_groq_failure_saves_nothing(tmp_path):
    at, log = _app(tmp_path, groq_fails=True)
    _generate(at)
    assert not at.exception
    kinds = [e["kind"] for e in _events(log)]
    assert "upload" not in kinds and "upsert" not in kinds
    assert "nothing was saved" in _all_text(at).lower()
