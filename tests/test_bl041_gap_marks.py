"""BL-041 small (BL041_DESIGN.md; John Q1-Q4, 2026-09-29): 'Not really in the source' marks.
- Q1 a mark is a row in Neon topic_gap_marks (migration_008), written by an Admin button; tied to the source sha.
- Q2 a note is REQUIRED to mark (max 1000 chars).
- Q3 a marked topic joins the SAME page-1 list 'Not in the source'; the note is never printed.
- Q4 Undo only ends the mark; the topic's rows are untouched -> it is back exactly as it was.
A marked topic is treated like a gap: no Groq call, not needed for the PDF, its id is not in the PDF's topic list.
Pure rules in topic_store / topic_review; the real SQL runs on a throwaway Postgres in the scripts folder
(tests/test_bl041_gap_marks_pg.py). Tests marked GUARD protect kept behaviour and pass on the old code too; every
other test FAILS on the old code."""
import json
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from pypdf import PdfReader

import source_app as sa
import source_context as sc
import source_prompts as sp
import topic_store as ts
from test_source_prompts import _good_answer, _topic
from test_app_ui import SHA1, _all_text, _app, _events, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
LM = "Language Modeling: Grammar-based LM, Statistical LM"
REGEX, MED, ORIGINS = "Regular Expressions", "Minimum Edit Distance", "Origins and challenges of NLP"
WHY_LM = "Unit1.txt has 0 hits for 'language model'; the text is really POS tagging."
WHY_RX = "The text is really about finite-state automata, a near-copy of the FSA topic."


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def tr():
    import topic_review
    return topic_review


def _row(ctx, topic, rid, status="approved"):
    plan = _topic(ctx, topic)
    ans = _good_answer(plan)
    chk = sp.check_notes_answer(ans, plan)
    return {"id": rid, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": topic,
            "topic_index": [t.topic for t in ctx.topics].index(topic) + 1, "raw_answer": ans,
            "markdown": chk.markdown, "check_data": {"status": "ok"}, "source_sha256": ctx.source.sha256,
            "passages_sha256": ts.passages_sha256(plan), "rules_version": "sp:x", "avoid_notes": [],
            "generated_at": T0, "review_status": status, "reviewed_at": None if status == "draft" else T0,
            "review_note": None}


def _all_approved(ctx):
    return [_row(ctx, p.topic, i) for i, p in enumerate(ctx.topics, 1) if ts.covered(p)]


def _mark(topic, mid=1, sha=SHA1, note=WHY_LM, minutes=0, undone=False):
    at = T0 + timedelta(hours=1, minutes=minutes)
    return {"id": mid, "subject_code": "AL3501", "unit_number": 1, "topic": topic, "source_sha256": sha,
            "note": note, "marked_at": at, "unmarked_at": at + timedelta(minutes=5) if undone else None}


def _view(ctx, rows, marks=()):
    return {e.topic: e for e in ts.unit_view(ctx, rows, marks)}


# ---------- topic_store: what a mark does ----------

def test_a_marked_covered_topic_is_MARKED_and_counts_as_a_gap(ctx):
    rows = _all_approved(ctx)
    v = _view(ctx, rows, [_mark(LM)])
    assert v[LM].state == ts.MARKED and ts.is_gap(v[LM]) and v[LM].mark["note"] == WHY_LM
    assert v[LM].row is not None                                  # its saved text is kept (Q4), just not used
    assert v[REGEX].state == ts.APPROVED and not ts.is_gap(v[REGEX])
    assert ts.is_gap(v[ORIGINS]) and v[ORIGINS].state == ts.GAP


def test_a_mark_for_an_older_source_does_not_count_but_is_shown(ctx):
    rows = _all_approved(ctx)
    v = _view(ctx, rows, [_mark(LM, sha="0" * 64)])
    assert v[LM].state == ts.APPROVED and v[LM].mark is None
    assert v[LM].old_mark["note"] == WHY_LM


def test_an_undone_mark_does_not_count(ctx):
    v = _view(ctx, _all_approved(ctx), [_mark(LM, undone=True)])
    assert v[LM].state == ts.APPROVED and v[LM].mark is None and v[LM].old_mark is None


def test_a_mark_on_a_source_gap_changes_nothing(ctx):
    v = _view(ctx, [], [_mark(ORIGINS)])
    assert v[ORIGINS].state == ts.GAP and v[ORIGINS].mark is None


def test_newest_active_mark_is_the_one_shown(ctx):
    marks = [_mark(LM, mid=1, note="old"), _mark(LM, mid=2, note="new", minutes=3)]
    assert _view(ctx, [], marks)[LM].mark["id"] == 2
    assert ts.active_marks(marks, SHA1)[LM]["note"] == "new"


def test_marked_topics_are_not_written_and_not_needed_for_the_pdf(ctx):
    rows = [r for r in _all_approved(ctx) if r["topic"] not in (LM, REGEX)]
    rows.append(_row(ctx, REGEX, 99, status="draft"))             # LM never written, Regex only a draft
    marks = [_mark(LM, 1), _mark(REGEX, 2, note=WHY_RX)]
    view = ts.unit_view(ctx, rows, marks)
    assert ts.to_regenerate(view) == []                           # no Groq call for marked topics
    assert ts.pdf_ready(view)
    assert ts.pdf_row_ids(view) == [r["id"] for r in rows if r["topic"] != REGEX]
    assert not ts.pdf_ready(ts.unit_view(ctx, rows))              # without the marks: not ready (GUARD of old rule)


def test_undo_brings_the_topic_back_exactly_as_it_was(ctx):
    rows = _all_approved(ctx)
    before = ts.pdf_row_ids(ts.unit_view(ctx, rows))
    marked = ts.pdf_row_ids(ts.unit_view(ctx, rows, [_mark(LM)]))
    assert len(marked) == len(before) - 1
    after = ts.unit_view(ctx, rows, [_mark(LM, undone=True)])
    assert {e.topic: e.state for e in after}[LM] == ts.APPROVED and ts.pdf_row_ids(after) == before


def test_GUARD_unit_view_without_marks_is_unchanged(ctx):
    rows = _all_approved(ctx)
    assert [(e.topic, e.state) for e in ts.unit_view(ctx, rows)] == \
           [(p.topic, ts.GAP if not ts.covered(p) else ts.APPROVED) for p in ctx.topics]


# ---------- topic_review: buttons, params, texts ----------

MARKED = getattr(ts, "MARKED", "marked")        # the old code has no such state: the test must FAIL there, not crash


@pytest.mark.parametrize("state,mark,unmark", [
    (ts.GAP, False, False), (ts.NONE, True, False), (ts.STALE, True, False), (ts.FAILED, True, False),
    (ts.REJECTED, True, False), (ts.NEEDS_REVIEW, True, False), (ts.DRAFT, True, False),
    (ts.APPROVED, True, False), (MARKED, False, True)])
def test_mark_and_undo_buttons_per_state(state, mark, unmark):
    e = ts.TopicEntry("T", 2, state, None, [], {"id": 5} if state == MARKED else None)
    assert (tr().can_mark(e), tr().can_unmark(e)) == (mark, unmark)


def test_mark_needs_a_note_and_gives_the_guarded_params(ctx):
    e = _view(ctx, _all_approved(ctx))[LM]
    kw = dict(subject_code="AL3501", unit_number="1", source_sha256=SHA1)
    with pytest.raises(tr().ReviewError, match="note"):
        tr().mark_params("   ", e, **kw)
    with pytest.raises(tr().ReviewError, match="too long"):
        tr().mark_params("x" * 1001, e, **kw)
    key = ("AL3501", 1, LM, SHA1)
    assert tr().mark_params(f"  {WHY_LM} ", e, **kw) == key + (WHY_LM,) + key


def test_a_gap_or_marked_topic_cannot_be_marked(ctx):
    v = _view(ctx, _all_approved(ctx), [_mark(LM)])
    for t in (ORIGINS, LM):
        with pytest.raises(tr().ReviewError, match="cannot be marked"):
            tr().mark_params(WHY_LM, v[t], subject_code="AL3501", unit_number=1, source_sha256=SHA1)


def test_undo_params_only_for_the_mark_that_was_shown(ctx):
    v = _view(ctx, _all_approved(ctx), [_mark(LM, mid=7)])
    assert tr().unmark_params(v[LM], 7) == (7,)
    with pytest.raises(tr().ReviewError, match="changed since the page loaded"):
        tr().unmark_params(v[LM], 6)
    with pytest.raises(tr().ReviewError, match="not marked"):
        tr().unmark_params(v[REGEX], 7)


def test_marked_label_and_text_show_the_note_and_the_way_back(ctx):
    e = _view(ctx, _all_approved(ctx), [_mark(LM)])[LM]
    assert tr().LABELS[ts.MARKED] == "MARKED not really in the source"
    text = tr().state_text(e)
    assert WHY_LM in text and "Listed as a gap in the PDF" in text and "Undo this mark" in text
    old = _view(ctx, _all_approved(ctx), [_mark(LM, sha="0" * 64)])[LM]
    assert "source file changed" in tr().old_mark_text(old) and WHY_LM in tr().old_mark_text(old)
    assert tr().old_mark_text(_view(ctx, _all_approved(ctx))[LM]) == ""


def test_counts_line_and_generate_skip_marked_topics(ctx):
    rows = [r for r in _all_approved(ctx) if r["topic"] != LM]
    view = ts.unit_view(ctx, rows, [_mark(LM)])
    assert tr().counts_line(view) == \
        "6 covered sub-topics: 6 approved | 1 not in the source | 1 marked not really in the source"
    with pytest.raises(ValueError, match="MARKED"):
        tr().generation_plan(view, rows, only=LM)


def test_mark_sql_is_guarded_and_touches_only_topic_gap_marks():
    ins = " ".join(tr().GAP_MARK_INSERT_SQL.split())
    assert ins.startswith("INSERT INTO topic_gap_marks (subject_code, unit_number, topic, source_sha256, note) "
                          "SELECT %s, %s, %s, %s, %s WHERE NOT EXISTS")
    assert "unmarked_at IS NULL" in ins and ins.endswith("ON CONFLICT DO NOTHING") and ins.count("%s") == 9
    undo = " ".join(tr().GAP_MARK_UNDO_SQL.split())
    assert undo == "UPDATE topic_gap_marks SET unmarked_at = NOW() WHERE id = %s AND unmarked_at IS NULL"
    rows = " ".join(tr().GAP_MARK_ROWS_SQL.split())
    assert rows == ("SELECT id, subject_code, unit_number, topic, source_sha256, note, marked_at, unmarked_at "
                    "FROM topic_gap_marks WHERE subject_code = %s AND unit_number = %s ORDER BY marked_at, id")


# ---------- the PDF (Q3) ----------

def _build(ctx, rows, marks):
    return sa.build_pdf_from_topics(ctx=ctx, view=ts.unit_view(ctx, rows, marks),
                                    subject_name="Natural Language Processing", subject_code="AL3501",
                                    dept_code="AI&DS", dept_name="Artificial Intelligence and Data Science",
                                    unit_number=1, unit_title="Introduction", regulation="R-2021")


def test_pdf_lists_marked_topics_with_the_gaps_and_never_prints_their_text_or_note(ctx):
    rows = _all_approved(ctx)
    out = _build(ctx, rows, [_mark(LM, 1), _mark(REGEX, 2, note=WHY_RX)])
    assert out.topic_row_ids == [r["id"] for r in rows if r["topic"] not in (LM, REGEX)]
    assert out.row_values["topics_covered"] == 5 and out.row_values["topics_total"] == 8
    assert json.loads(out.row_values["gap_topics"]) == [ORIGINS, LM, REGEX]          # syllabus order
    text = " ".join("\n".join(p.extract_text() for p in PdfReader(BytesIO(out.pdf_bytes)).pages).split())
    page1 = text.split("Not in the source (not covered in this PDF):")[1][:400]
    for t in (ORIGINS, "Language Modeling", REGEX):
        assert t in page1
    lm_first = re.sub(r"\s*\[P\d+\]", "", next(r for r in rows if r["topic"] == LM)["markdown"].split("\n\n")[1])
    assert " ".join(lm_first.split())[:30] not in text
    assert "POS tagging" not in text and "near-copy" not in text                      # notes stay in Admin


def test_old_pdf_goes_out_of_date_when_marked_and_back_when_undone(ctx):
    rows = _all_approved(ctx)
    pdf = {"storage_key": "k", "source_sha256": SHA1, "prompt_version": "sp:x", "review_status": "draft",
           "topic_row_ids": [r["id"] for r in rows]}
    ids = lambda marks: ts.pdf_row_ids(ts.unit_view(ctx, rows, marks))
    assert sa.is_current(pdf, SHA1, "sp:y", topic_ids=ids([]))
    assert not sa.is_current(pdf, SHA1, "sp:y", topic_ids=ids([_mark(LM)]))
    assert sa.is_current(pdf, SHA1, "sp:y", topic_ids=ids([_mark(LM, undone=True)]))


# ---------- the screen ----------

def _screen(tmp_path, rows=None, marks=None, **kw):
    at, log = _app(tmp_path, topic_rows=rows, gap_marks=marks, **kw)
    _unlock(at)
    return at, log


def _btn(at, key):
    return next((b for b in at.button if b.key == key), None)


def _idx(ctx, topic):
    return [t.topic for t in ctx.topics].index(topic) + 1


def _marks_file(tmp_path):
    p = tmp_path / "marks.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def test_screen_mark_needs_a_note_then_saves_it(tmp_path, ctx):
    at, log = _screen(tmp_path, _all_approved(ctx))
    i = _idx(ctx, LM)
    _btn(at, f"tmark_{i}").click().run()
    assert not at.exception, at.exception
    assert "Not saved" in _all_text(at) and "note" in _all_text(at)
    assert not [e for e in _events(log) if e["kind"] == "gap_mark"]
    at.text_area(key=f"tmarknote_{i}").input(WHY_LM).run()
    _btn(at, f"tmark_{i}").click().run()
    assert not at.exception, at.exception
    ev = [e for e in _events(log) if e["kind"] == "gap_mark"]
    assert len(ev) == 1 and ev[0]["params"][:5] == ["AL3501", "1", LM, SHA1, WHY_LM]
    assert [(m["topic"], m["note"], m["unmarked_at"]) for m in _marks_file(tmp_path)] == [(LM, WHY_LM, None)]
    assert "MARKED not really in the source" in next(x for x in at.expander if LM in x.label).label
    assert _btn(at, f"tmark_{i}") is None and _btn(at, f"tunmark_{i}") is not None
    for k in ("tapprove", "treject", "tregen", "trecheck"):
        assert _btn(at, f"{k}_{i}") is None                                      # nothing else to do with it
    assert WHY_LM in _all_text(at)


def test_screen_undo_brings_the_topic_back(tmp_path, ctx):
    at, log = _screen(tmp_path, _all_approved(ctx), [_mark(LM, mid=4)])
    i = _idx(ctx, LM)
    _btn(at, f"tunmark_{i}").click().run()
    assert not at.exception, at.exception
    assert [e["params"] for e in _events(log) if e["kind"] == "gap_unmark"] == [["4"]]
    assert "APPROVED" in next(x for x in at.expander if LM in x.label).label
    assert _btn(at, f"tmark_{i}") is not None and "Mark undone" in _all_text(at)


def test_screen_double_mark_saves_nothing_the_second_time(tmp_path, ctx):
    at, log = _screen(tmp_path, _all_approved(ctx))
    i = _idx(ctx, LM)
    at.text_area(key=f"tmarknote_{i}").input(WHY_LM).run()
    marks = tmp_path / "marks.json"                                  # another tab marked it meanwhile
    marks.write_text(json.dumps([{**_mark(LM, mid=3), "marked_at": T0.isoformat(), "unmarked_at": None}]),
                     encoding="utf-8")
    _btn(at, f"tmark_{i}").click().run()
    assert not at.exception, at.exception
    text = _all_text(at)                                             # the click is checked against the marks NOW
    assert "Not saved" in text and "cannot be marked" in text and len(_marks_file(tmp_path)) == 1
    assert not [e for e in _events(log) if e["kind"] == "gap_mark"]


def test_screen_build_pdf_after_marking_uses_only_the_other_topics(tmp_path, ctx):
    rows = [r for r in _all_approved(ctx) if r["topic"] != LM] + [_row(ctx, LM, 42, status="draft")]
    at, log = _screen(tmp_path, rows, [_mark(LM, mid=1), _mark(REGEX, mid=2, note=WHY_RX)])
    b = _btn(at, "build_pdf")
    assert b is not None and b.label == "📄 Build PDF from the 5 approved topics"
    assert "5 covered sub-topics: 5 approved | 1 not in the source | 2 marked not really in the source" \
        in _all_text(at)
    b.click().run()
    assert not at.exception, at.exception
    ev = _events(log)
    assert "groq" not in [e["kind"] for e in ev]
    up = next(e for e in ev if e["kind"] == "upsert_topics")
    assert up["topic_row_ids"] == [r["id"] for r in rows if r["topic"] not in (LM, REGEX)]
    assert json.loads(up["row_values"]["gap_topics"]) == [ORIGINS, LM, REGEX]


def test_screen_old_mark_is_shown_and_does_not_count(tmp_path, ctx):
    at, _ = _screen(tmp_path, _all_approved(ctx), [_mark(LM, sha="0" * 64)])
    assert "APPROVED" in next(x for x in at.expander if LM in x.label).label
    assert "source file changed" in _all_text(at) and _btn(at, f"tmark_{_idx(ctx, LM)}") is not None


def test_GUARD_screen_gap_topic_has_no_mark_button(tmp_path, ctx):
    at, _ = _screen(tmp_path)
    gap = _idx(ctx, ORIGINS)
    assert not [b for b in at.button if b.key in (f"tapprove_{gap}", f"treject_{gap}", f"tregen_{gap}")]
    assert _btn(at, f"tmark_{gap}") is None


def test_screen_students_get_the_pdf_built_after_marking(tmp_path, ctx):
    rows = _all_approved(ctx)
    ids = [r["id"] for r in rows if r["topic"] != LM]
    existing = {"id": 10, "storage_key": "source-strict/AI&DS/AL3501/AL3501_Unit1_Notes.pdf",
                "storage_bucket": "github", "source_sha256": SHA1, "prompt_version": "sp:old", "generated_at": T0,
                "review_status": "approved", "reviewed_at": T0, "review_note": None, "topic_row_ids": ids}
    at, log = _app(tmp_path, existing=existing, topic_rows=rows, gap_marks=[_mark(LM)])
    assert "Ready — checked and approved" in _all_text(at)
    (tmp_path / "b").mkdir()
    at2, _ = _app(tmp_path / "b", existing=existing, topic_rows=rows)      # without the mark: not current
    assert "Not available yet" in _all_text(at2)
