"""BL-037 step e (BL037_DESIGN.md f1cf0ca455; John's go 2026-09-29): the Admin per-topic screen for Notes.
Each topic: status, check summary, its text + the source passages, Approve / Reject (reject needs a note),
'Regenerate this topic'. Main Generate writes every topic that is not approved (Q4) and makes NO PDF
(the PDF from approved topics is step f). QA keeps the old whole-unit PDF flow (QA per-topic comes later).
Pure rules live in topic_review.py (no Streamlit, no DB, no network); the real SELECT / UPDATE SQL is tested on a
throwaway Postgres in the scripts folder (tests/test_bl037e_topic_review_pg.py).
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import source_context as sc
import source_prompts as sp
import source_runner as sr
import topic_store as ts
from test_source_app import fake_groq
from test_source_prompts import _good_answer, _topic
from test_app_ui import _all_text, _app, _events, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)      # before any 'now' in a test
MED, REGEX, MORPH = "Minimum Edit Distance", "Regular Expressions", "English Morphology"
NOTE = "Do not say the automaton is converted back into a regular expression."


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(sr.TokenPacer, "wait_for", lambda self, tokens: None)
    fake_groq.calls = []


def tr():
    import topic_review
    return topic_review


def _row(ctx, topic, rid=1, status="draft", note=None, minutes=0, answer=None, **over):
    plan = _topic(ctx, topic)
    answer = answer if answer is not None else _good_answer(plan)
    chk = sp.check_notes_answer(answer, plan)
    r = {"id": rid, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": topic,
         "topic_index": [t.topic for t in ctx.topics].index(topic) + 1, "raw_answer": answer,
         "markdown": chk.markdown, "check_data": {"status": "ok" if chk.ok else "needs_review", "calls": 1,
                                                  "new_terms": [], "odd_chars": [], "latex": [], "dropped": [],
                                                  "kept_leadins": []},
         "source_sha256": ctx.source.sha256, "passages_sha256": ts.passages_sha256(plan),
         "rules_version": "sp:x", "avoid_notes": [], "generated_at": T0 + timedelta(minutes=minutes),
         "review_status": status, "reviewed_at": None if status == "draft" else T0, "review_note": note}
    r.update(over)
    return r


def _entry(ctx, rows, topic):
    return next(e for e in ts.unit_view(ctx, rows) if e.topic == topic)


def _seen(row):
    return {"id": row["id"], "review_status": row["review_status"], "generated_at": row["generated_at"]}


# ---------- SQL (pure checks; real Postgres run in the scripts folder) ----------

MIGRATION_007_COLUMNS = ("id", "subject_code", "unit_number", "variant", "topic", "topic_index", "raw_answer",
                         "markdown", "check_data", "source_sha256", "passages_sha256", "rules_version",
                         "avoid_notes", "generated_at", "review_status", "reviewed_at", "review_note")


def test_rows_sql_reads_every_topic_texts_column_for_one_unit_and_variant():
    sql = " ".join(tr().TOPIC_ROWS_SQL.split())
    assert tr().TOPIC_ROW_COLUMNS == MIGRATION_007_COLUMNS
    assert sql.startswith("SELECT " + ", ".join(MIGRATION_007_COLUMNS) + " FROM topic_texts")
    assert "WHERE subject_code = %s AND unit_number = %s AND variant = %s" in sql
    assert sql.endswith("ORDER BY generated_at, id") and sql.count("%s") == 3


def test_review_sql_changes_only_the_review_of_the_exact_current_row():
    sql = " ".join(tr().TOPIC_REVIEW_SQL.split())
    assert sql.startswith("UPDATE topic_texts AS t SET review_status = %s, reviewed_at = NOW(), review_note = %s WHERE")
    assert "t.id = %s AND t.review_status = %s AND t.generated_at = %s" in sql
    assert "NOT EXISTS" in sql and "(n.generated_at, n.id) > (t.generated_at, t.id)" in sql
    assert sql.count("%s") == 5
    for col in ("markdown", "raw_answer", "check_data", "topic ="):
        assert f"SET {col}" not in sql and f", {col} =" not in sql


# ---------- which buttons for which state ----------

@pytest.mark.parametrize("state,approve,reject,regen", [
    (ts.GAP, False, False, False), (ts.NONE, False, False, True), (ts.STALE, False, False, True),
    (ts.FAILED, False, False, True), (ts.REJECTED, False, False, True), (ts.NEEDS_REVIEW, False, True, True),
    (ts.DRAFT, True, True, True), (ts.APPROVED, False, True, False)])
def test_buttons_per_state(state, approve, reject, regen):
    e = ts.TopicEntry("T", 1, state, {"id": 1} if state not in (ts.GAP, ts.NONE) else None, [])
    assert (tr().can_approve(e), tr().can_reject(e), tr().can_regenerate(e)) == (approve, reject, regen)


def test_every_state_has_a_label_and_a_plain_text():
    for s in (ts.GAP, ts.NONE, ts.STALE, ts.FAILED, ts.REJECTED, ts.NEEDS_REVIEW, ts.DRAFT, ts.APPROVED):
        e = ts.TopicEntry("T", 1, s, {"id": 1, "review_note": "n"}, ["why"])
        assert tr().LABELS[s] and tr().state_text(e)


def test_state_texts_say_what_happens_next(ctx):
    rej = ts.TopicEntry(MED, 5, ts.REJECTED, {"review_note": NOTE}, [])
    assert NOTE in tr().state_text(rej) and "Do NOT write this" in tr().state_text(rej)
    stale = ts.TopicEntry(MED, 5, ts.STALE, {}, ["source or passages changed"])
    assert "OUT OF DATE" in tr().state_text(stale) and "source or passages changed" in tr().state_text(stale)
    assert "never touches" in tr().state_text(ts.TopicEntry(MED, 5, ts.APPROVED, {}, []))
    assert "Approve or Reject" in tr().state_text(ts.TopicEntry(MED, 5, ts.DRAFT, {}, []))


# ---------- review params (Approve / Reject one topic) ----------

def test_approve_a_draft_gives_the_guarded_params(ctx):
    row = _row(ctx, MED, rid=7)
    got = tr().review_params("approved", "  ", _entry(ctx, [row], MED), _seen(row))
    assert got == ("approved", None, 7, "draft", row["generated_at"])


def test_reject_needs_a_note_and_keeps_it(ctx):
    row = _row(ctx, MED, rid=7)
    e = _entry(ctx, [row], MED)
    with pytest.raises(tr().ReviewError, match="note"):
        tr().review_params("rejected", " ", e, _seen(row))
    assert tr().review_params("rejected", f"  {NOTE} ", e, _seen(row))[:2] == ("rejected", NOTE)


def test_note_too_long_and_unknown_decision(ctx):
    row = _row(ctx, MED, rid=7)
    e = _entry(ctx, [row], MED)
    with pytest.raises(tr().ReviewError, match="too long"):
        tr().review_params("rejected", "x" * 1001, e, _seen(row))
    with pytest.raises(tr().ReviewError, match="Unknown decision"):
        tr().review_params("maybe", "", e, _seen(row))


def test_an_approved_topic_can_be_rejected_but_not_approved_again(ctx):
    row = _row(ctx, MED, rid=7, status="approved")
    e = _entry(ctx, [row], MED)
    assert e.state == ts.APPROVED
    assert tr().review_params("rejected", NOTE, e, _seen(row))[:4] == ("rejected", NOTE, 7, "approved")
    with pytest.raises(tr().ReviewError, match="cannot be approved"):
        tr().review_params("approved", "", e, _seen(row))


@pytest.mark.parametrize("status,answer_change", [("rejected", False), ("draft", True)])
def test_no_approve_for_rejected_or_failed_topics(ctx, status, answer_change):
    plan = _topic(ctx, MED)
    ans = _good_answer(plan).replace("[P", "[Q") if answer_change else None
    row = _row(ctx, MED, rid=7, status=status, note="bad" if status == "rejected" else None, answer=ans,
               **({"check_data": {"status": "failed"}} if answer_change else {}))
    e = _entry(ctx, [row], MED)
    assert e.state in (ts.REJECTED, ts.FAILED)
    with pytest.raises(tr().ReviewError):
        tr().review_params("approved", "", e, _seen(row))


def test_topic_changed_since_the_page_loaded_is_not_saved(ctx):
    old, new = _row(ctx, MED, rid=7), _row(ctx, MED, rid=8, minutes=5)
    e = _entry(ctx, [old, new], MED)                                  # a newer attempt is current now
    with pytest.raises(tr().ReviewError, match="changed since the page loaded"):
        tr().review_params("approved", "", e, _seen(old))
    done = _row(ctx, MED, rid=7, status="approved")                   # same id, other review now
    with pytest.raises(tr().ReviewError, match="changed since the page loaded"):
        tr().review_params("rejected", NOTE, _entry(ctx, [done], MED), _seen(old))


def test_no_row_no_review(ctx):
    e = _entry(ctx, [], MED)
    with pytest.raises(tr().ReviewError, match="nothing written"):
        tr().review_params("approved", "", e, {"id": 1, "review_status": "draft", "generated_at": T0})


# ---------- check summary + counts + Generate plan ----------

def test_check_summary_from_check_data(ctx):
    row = _row(ctx, MED, check_data={"status": "ok", "calls": 3, "used_try": 2, "reask_reason": "not ok",
                                     "new_terms": ["DP"], "odd_chars": ["U+00B7"], "latex": ["\\frac"],
                                     "dropped": [{"reason": "no citation", "text": "a", "end": "b"}] * 2,
                                     "kept_leadins": ["x:"]})
    s = tr().check_summary(row)
    assert s == {"status": "ok", "calls": 3, "used_try": 2, "reask_reason": "not ok", "new_terms": ["DP"],
                 "odd_chars": ["U+00B7"], "latex": ["\\frac"], "dropped": 2, "kept_leadins": 1,
                 "sections": [], "layout": [], "list_tagged": 0,          # CHANGED in BL-042: + 3 summary fields
                 "trimmed": [], "note": ""}                                # CHANGED in BL-039: + 2 summary fields
    assert tr().check_summary(_row(ctx, MED, check_data={"status": "ok"}))["dropped"] == 0   # old rows: no keys


def test_counts_line(ctx):
    rows = [_row(ctx, MED, rid=1, status="approved"), _row(ctx, REGEX, rid=2),
            _row(ctx, MORPH, rid=3, status="rejected", note="bad")]
    line = tr().counts_line(ts.unit_view(ctx, rows))
    assert line == "7 covered sub-topics: 1 approved · 1 draft · 1 rejected · 4 not written yet | 1 not in the source"


def test_generation_plan_main_is_every_non_approved_topic_with_its_notes(ctx):
    rows = [_row(ctx, MED, rid=1, status="approved"), _row(ctx, REGEX, rid=2),
            _row(ctx, MORPH, rid=3, status="rejected", note=NOTE)]
    view = ts.unit_view(ctx, rows)
    topics, avoid = tr().generation_plan(view, rows)
    assert topics == ts.to_regenerate(view) and MED not in topics and REGEX not in topics   # draft waits
    assert MORPH in topics and avoid == {MORPH: [NOTE]}
    assert tr().generate_label(view) == f"⚡ Generate {len(topics)} topic(s) — keeps 1 approved"


def test_generation_plan_one_topic(ctx):
    rows = [_row(ctx, MED, rid=1, status="approved"), _row(ctx, REGEX, rid=2),
            _row(ctx, MORPH, rid=3, status="rejected", note=NOTE)]
    view = ts.unit_view(ctx, rows)
    assert tr().generation_plan(view, rows, only=REGEX) == ([REGEX], {})           # a draft may be re-rolled
    assert tr().generation_plan(view, rows, only=MORPH) == ([MORPH], {MORPH: [NOTE]})
    gap = next(e.topic for e in view if e.state == ts.GAP)
    for bad in (MED, gap, "No such topic"):
        with pytest.raises(ValueError):
            tr().generation_plan(view, rows, only=bad)


def test_nothing_to_generate_label(ctx):
    rows = [_row(ctx, p.topic, rid=i, status="approved") for i, p in enumerate(ctx.topics, 1) if ts.covered(p)]
    view = ts.unit_view(ctx, rows)
    assert tr().generation_plan(view, rows) == ([], {})
    assert tr().generate_label(view) == "⚡ Generate — nothing to write (7 approved)"


# ---------- source_app.generate_topics (no PDF) ----------

def _gen_topics(**kw):
    import source_app
    base = dict(subject_code="AL3501", subject_name="Natural Language Processing", unit_number=1,
                unit_title="Introduction", groq_chat=fake_groq)
    base.update(kw)
    return source_app.generate_topics(**base)


def test_generate_topics_writes_only_the_given_topics_and_saves_each(ctx):
    rows, logs = [], []
    out = _gen_topics(topics=[REGEX, MED], avoid={MED: [NOTE]},
                      save_topic=lambda r: rows.append(r) or 700 + len(rows), on_run=logs.append)
    assert [r["topic"] for r in rows] == [REGEX, MED] or [r["topic"] for r in rows] == [MED, REGEX]
    assert [r["topic"] for r in rows] == [t.topic for t in ctx.topics if t.topic in (REGEX, MED)]  # syllabus order
    assert out.topic_row_ids == [701, 702] and len(fake_groq.calls) == 2
    assert next(r for r in rows if r["topic"] == MED)["avoid_notes"] == [NOTE]
    assert next(r for r in rows if r["topic"] == REGEX)["avoid_notes"] == []
    assert not hasattr(out, "pdf_bytes")                                 # no PDF in step e
    assert [t["topic"] for t in logs[0]["topics"]] == [r["topic"] for r in rows]


def test_generate_topics_refuses_before_any_call():
    for kw in ({"topics": [MED], "save_topic": None}, {"topics": [], "save_topic": lambda r: 1},
               {"topics": [MED], "save_topic": lambda r: 1, "variant": "QA"},
               {"topics": ["No such topic"], "save_topic": lambda r: 1}):
        with pytest.raises(ValueError):
            _gen_topics(**kw)
    assert fake_groq.calls == []


# ---------- the screen (AppTest) ----------

def _store_rows(tmp_path):
    p = tmp_path / "topics.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _screen(tmp_path, rows=None, **kw):
    at, log = _app(tmp_path, topic_rows=rows, **kw)
    _unlock(at)
    return at, log


def _btn(at, key):
    return next((b for b in at.button if b.key == key), None)


def test_screen_lists_every_topic_with_its_state(tmp_path, ctx):
    at, log = _screen(tmp_path)
    assert not at.exception
    labels = [x.label for x in at.expander]
    assert len(labels) == 8 and labels[0].startswith("1. Origins and challenges of NLP")
    assert sum("not written yet" in l for l in labels) == 7 and sum("not in the source" in l for l in labels) == 1
    assert _btn(at, "generate").label == "⚡ Generate 7 topic(s) — keeps 0 approved"
    assert "7 covered sub-topics: 7 not written yet | 1 not in the source" in _all_text(at)
    assert _events(log) == []


def test_screen_generate_writes_the_topics_and_makes_no_pdf(tmp_path, ctx):
    at, log = _screen(tmp_path)
    _btn(at, "generate").click().run()
    assert not at.exception, at.exception
    kinds = [e["kind"] for e in _events(log)]
    assert kinds.count("groq") == 7 and kinds.count("topic_row") == 7
    assert "upload" not in kinds and "upsert" not in kinds
    text = _all_text(at)
    assert "7 topic(s) written and saved as DRAFTS" in text and "No PDF is made in this step" in text
    assert sum("DRAFT" in x.label for x in at.expander) == 7
    assert _btn(at, "generate").disabled and "nothing to write" in _btn(at, "generate").label


def test_screen_generate_keeps_approved_and_sends_reject_notes(tmp_path, ctx):
    rows = [_row(ctx, MED, rid=1, status="approved"), _row(ctx, MORPH, rid=2, status="rejected", note=NOTE)]
    at, log = _screen(tmp_path, rows)
    assert _btn(at, "generate").label == "⚡ Generate 6 topic(s) — keeps 1 approved"
    _btn(at, "generate").click().run()
    assert not at.exception, at.exception
    written = [e["topic"] for e in _events(log) if e["kind"] == "topic_row"]
    assert MED not in written and MORPH in written and len(written) == 6
    new_morph = [r for r in _store_rows(tmp_path) if r["topic"] == MORPH and r["id"] != 2]
    assert new_morph and new_morph[0]["avoid_notes"] == [NOTE]
    assert all(r["review_status"] == "approved" for r in _store_rows(tmp_path) if r["topic"] == MED)


def test_screen_shows_the_text_and_the_check_summary(tmp_path, ctx):
    row = _row(ctx, MED, rid=1, check_data={"status": "ok", "calls": 2, "used_try": 2, "reask_reason": "lost lines",
                                            "new_terms": ["DPX"], "odd_chars": [], "latex": [], "dropped": [],
                                            "kept_leadins": []})
    at, _ = _screen(tmp_path, [row])
    text = _all_text(at)
    first_sentence = row["markdown"].split("\n\n")[1][:40]
    assert first_sentence in text                                        # John reads the text itself
    assert "2 call(s)" in text and "lost lines" in text and "DPX" in text


def test_screen_can_show_the_source_passages(tmp_path, ctx):
    at, _ = _screen(tmp_path, [_row(ctx, MED, rid=1)])
    idx = [t.topic for t in ctx.topics].index(MED) + 1
    plan = _topic(ctx, MED)
    shown = lambda: "\n".join(t.value for t in at.text)                   # passages are shown as plain text
    assert plan.passages[1].text[:40] not in shown()
    next(c for c in at.checkbox if c.key == f"tsrc_{idx}").check().run()
    assert f"[{plan.passages[1].pid}] {plan.passages[1].text[:40]}" in shown()


def test_screen_approve_a_draft(tmp_path, ctx):
    at, log = _screen(tmp_path, [_row(ctx, MED, rid=1)])
    idx = [t.topic for t in ctx.topics].index(MED) + 1
    assert _btn(at, f"treject_{idx}") and _btn(at, f"tregen_{idx}")
    _btn(at, f"tapprove_{idx}").click().run()
    assert not at.exception, at.exception
    ev = [e for e in _events(log) if e["kind"] == "topic_review"]
    assert len(ev) == 1 and ev[0]["params"][:4] == ["approved", "None", "1", "draft"]
    assert [r["review_status"] for r in _store_rows(tmp_path)] == ["approved"]
    assert f"Saved: '{MED}' is now APPROVED." in _all_text(at)
    exp = next(x for x in at.expander if MED in x.label)
    assert "APPROVED" in exp.label
    assert _btn(at, f"tapprove_{idx}") is None and _btn(at, f"tregen_{idx}") is None     # never touched again
    assert _btn(at, f"treject_{idx}") is not None                                          # can change mind


def test_screen_reject_needs_a_note_then_saves_it(tmp_path, ctx):
    at, log = _screen(tmp_path, [_row(ctx, MED, rid=1)])
    idx = [t.topic for t in ctx.topics].index(MED) + 1
    _btn(at, f"treject_{idx}").click().run()
    assert "Not saved" in _all_text(at) and not [e for e in _events(log) if e["kind"] == "topic_review"]
    at.text_area(key=f"tnote_{idx}").input(NOTE).run()
    _btn(at, f"treject_{idx}").click().run()
    ev = [e for e in _events(log) if e["kind"] == "topic_review"]
    assert len(ev) == 1 and ev[0]["params"][:2] == ["rejected", NOTE]
    assert [r["review_status"] for r in _store_rows(tmp_path)] == ["rejected"]
    assert "REJECTED" in next(x for x in at.expander if MED in x.label).label
    assert _btn(at, "generate").label == "⚡ Generate 7 topic(s) — keeps 0 approved"


def test_screen_topic_changed_meanwhile_is_not_saved(tmp_path, ctx):
    at, log = _screen(tmp_path, [_row(ctx, MED, rid=1)])
    idx = [t.topic for t in ctx.topics].index(MED) + 1
    store = tmp_path / "topics.json"                                   # another tab wrote a newer attempt
    rows = json.loads(store.read_text(encoding="utf-8"))
    rows.append(dict(rows[0], id=2, generated_at=(T0 + timedelta(minutes=9)).isoformat()))
    store.write_text(json.dumps(rows), encoding="utf-8")
    _btn(at, f"tapprove_{idx}").click().run()
    assert "changed since the page loaded" in _all_text(at)
    assert [r["review_status"] for r in _store_rows(tmp_path)] == ["draft", "draft"]


def test_screen_regenerate_one_topic_only(tmp_path, ctx):
    rows = [_row(ctx, MED, rid=1, status="rejected", note=NOTE), _row(ctx, REGEX, rid=2, status="approved")]
    at, log = _screen(tmp_path, rows)
    idx = [t.topic for t in ctx.topics].index(MED) + 1
    _btn(at, f"tregen_{idx}").click().run()
    assert not at.exception, at.exception
    ev = _events(log)
    assert [e["kind"] for e in ev].count("groq") == 1
    assert [e["topic"] for e in ev if e["kind"] == "topic_row"] == [MED]
    new = [r for r in _store_rows(tmp_path) if r["id"] not in (1, 2)]
    assert len(new) == 1 and new[0]["topic"] == MED and new[0]["avoid_notes"] == [NOTE]
    assert "DRAFT" in next(x for x in at.expander if MED in x.label).label
    assert "upload" not in [e["kind"] for e in ev]


def test_screen_gap_topic_has_no_buttons(tmp_path, ctx):
    at, _ = _screen(tmp_path)
    gap = next(i for i, p in enumerate(ctx.topics, 1) if not ts.covered(p))
    assert any(x.label.startswith(f"{gap}. ") and "not in the source" in x.label for x in at.expander)
    assert not [b for b in at.button if b.key in (f"tapprove_{gap}", f"treject_{gap}", f"tregen_{gap}")]
    assert not [t for t in at.text_area if t.key == f"tnote_{gap}"]


def test_GUARD_screen_qa_keeps_the_pdf_flow(tmp_path):
    at, log = _app(tmp_path)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()
    _unlock(at)
    assert not at.expander
    at.button(key="generate").click().run()
    kinds = [e["kind"] for e in _events(log)]
    assert "upload" in kinds and "upsert" in kinds and "topic_row" not in kinds
