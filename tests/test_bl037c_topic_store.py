"""BL-037 step c (BL037_DESIGN.md f1cf0ca455; John's Q1-Q5, 2026-09-29): pure rules for per-topic keep / regenerate.
topic_store has no Streamlit, no DB, no network. Rows are plain dicts shaped like Neon topic_texts (migration_007).
All tests FAIL before topic_store.py exists."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

import source_context as sc
import source_prompts as sp
import topic_store as ts
from test_source_prompts import _good_answer, _topic

T0 = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
MED = "Minimum Edit Distance"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, MED)


def _row(ctx, plan, answer=None, rid=1, status="draft", note=None, minutes=0, **over):
    answer = answer if answer is not None else _good_answer(plan)
    chk = sp.check_notes_answer(answer, plan)
    r = {"id": rid, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": plan.topic,
         "topic_index": [t.topic for t in ctx.topics].index(plan.topic) + 1, "raw_answer": answer,
         "markdown": chk.markdown, "check_data": {"status": "ok" if chk.ok else "needs_review"},
         "source_sha256": ctx.source.sha256, "passages_sha256": ts.passages_sha256(plan),
         "rules_version": "sp:x", "avoid_notes": [], "generated_at": T0 + timedelta(minutes=minutes),
         "review_status": status, "reviewed_at": None if status == "draft" else T0, "review_note": note}
    r.update(over)
    return r


# ---------- fingerprint of what was sent ----------

def test_passages_sha_is_the_passages_as_sent_in_the_prompt(ctx, plan):
    sent = "\n\n".join(f"[{p.pid}] {p.text}" for p in plan.passages)
    assert ts.passages_sha256(plan) == hashlib.sha256(sent.encode("utf-8")).hexdigest()
    assert f"PASSAGES\n{sent}\n" in sp.build_notes_prompt(ctx, plan, "S", "U")      # same text the model sees


# ---------- current row ----------

def test_current_row_is_newest_per_topic_ties_by_id(ctx, plan):
    a, b, c = _row(ctx, plan, rid=1), _row(ctx, plan, rid=2, minutes=5), _row(ctx, plan, rid=3, minutes=5)
    other = _row(ctx, _topic(ctx, "Regular Expressions"), rid=4, minutes=99)
    cur = ts.current_rows([a, c, other, b])
    assert cur[MED]["id"] == 3 and cur["Regular Expressions"]["id"] == 4


# ---------- topic_state (Q2) ----------

def test_state_none_when_never_written(ctx, plan):
    assert ts.topic_state(None, ctx, plan) == ts.NONE


def test_state_gap_for_a_topic_not_in_the_source(ctx):
    gap = next(t for t in ctx.topics if t.status not in ("FOUND", "PARTIAL") or not t.passages)
    assert ts.topic_state(None, ctx, gap) == ts.GAP


@pytest.mark.parametrize("status,expected", [("draft", "draft"), ("approved", "approved"),
                                             ("rejected", "rejected")])
def test_state_follows_review_when_current_and_passing(ctx, plan, status, expected):
    note = "why" if status == "rejected" else None
    assert ts.topic_state(_row(ctx, plan, status=status, note=note), ctx, plan) == expected


def test_state_stale_when_source_or_passages_changed(ctx, plan):
    assert ts.topic_state(_row(ctx, plan, status="approved", source_sha256="0" * 64), ctx, plan) == ts.STALE
    assert ts.topic_state(_row(ctx, plan, status="approved", passages_sha256="0" * 64), ctx, plan) == ts.STALE


def test_state_failed_when_the_writer_left_it_out(ctx, plan):
    r = _row(ctx, plan, check_data={"status": "needs_review"})
    assert ts.topic_state(r, ctx, plan) == ts.FAILED


def test_approved_topic_that_fails_todays_checks_needs_review(ctx, plan):
    bad = _good_answer(plan) + "- Uses \\(x\\) here [" + plan.passages[0].pid + "]\n"      # LaTeX -> not ok today
    r = _row(ctx, plan, status="approved")
    r["raw_answer"] = bad                                     # as if saved under older, looser rules
    assert ts.topic_state(r, ctx, plan) == ts.NEEDS_REVIEW


def test_approved_topic_whose_text_would_change_today_needs_review(ctx, plan):
    r = _row(ctx, plan, status="approved")
    r["markdown"] = r["markdown"] + "\n- one more line the checks today would not keep"
    assert ts.topic_state(r, ctx, plan) == ts.NEEDS_REVIEW


def test_draft_that_fails_todays_checks_is_stale(ctx, plan):
    r = _row(ctx, plan)
    r["markdown"] = "changed"
    assert ts.topic_state(r, ctx, plan) == ts.STALE


def test_rules_version_change_alone_keeps_approved(ctx, plan):
    """GUARD for Q2: a code/rule change whose checks the saved text still passes keeps the approval."""
    assert ts.topic_state(_row(ctx, plan, status="approved", rules_version="sp:OLD+sq:OLD"), ctx, plan) == \
        ts.APPROVED


def test_recheck_reports_why(ctx, plan):
    ok, why = ts.recheck(_row(ctx, plan), plan)
    assert ok and why == []
    r = _row(ctx, plan)
    r["raw_answer"] = _good_answer(plan) + "- odd § sign [" + plan.passages[0].pid + "]\n"
    ok, why = ts.recheck(r, plan)
    assert not ok and any("character" in w for w in why)


def test_recheck_refuses_qa_rows_for_now(ctx, plan):
    with pytest.raises(NotImplementedError):
        ts.recheck(_row(ctx, plan, variant="QA"), plan)


# ---------- unit view / Generate (Q4) ----------

def _view(ctx, rows):
    return ts.unit_view(ctx, rows)


def test_unit_view_is_in_syllabus_order_with_gaps(ctx):
    v = _view(ctx, [])
    assert [e.topic for e in v] == [t.topic for t in ctx.topics]
    assert [e.index for e in v] == list(range(1, len(ctx.topics) + 1))
    assert any(e.state == ts.GAP for e in v) and all(e.state in (ts.GAP, ts.NONE) for e in v)


def test_to_regenerate_lists_every_not_approved_covered_topic(ctx, plan):
    covered = [t.topic for t in ctx.topics if t.status in ("FOUND", "PARTIAL") and t.passages]
    rows = [_row(ctx, plan, status="approved")]
    todo = ts.to_regenerate(_view(ctx, rows))
    assert MED not in todo and set(todo) == set(covered) - {MED}


def test_to_regenerate_includes_rejected_stale_failed_needs_review_not_draft(ctx):
    names = [t.topic for t in ctx.topics if t.status in ("FOUND", "PARTIAL") and t.passages]
    p = {n: _topic(ctx, n) for n in names}
    rows = [_row(ctx, p[names[0]], rid=1, status="rejected", note="x"),
            _row(ctx, p[names[1]], rid=2, status="approved", passages_sha256="0" * 64),
            _row(ctx, p[names[2]], rid=3, check_data={"status": "needs_review"}),
            _row(ctx, p[names[3]], rid=4, status="draft"),
            _row(ctx, p[names[4]], rid=5, status="approved")]
    rows[-1]["markdown"] += "\n- x"                                   # approved, fails re-check
    todo = ts.to_regenerate(_view(ctx, rows))
    assert names[0] in todo and names[1] in todo and names[2] in todo and names[4] in todo
    assert names[3] not in todo                                        # a draft waits for John


def test_never_regenerates_an_approved_current_topic(ctx):
    rows = [_row(ctx, _topic(ctx, t.topic), rid=i, status="approved")
            for i, t in enumerate(ctx.topics, 1) if t.status in ("FOUND", "PARTIAL") and t.passages]
    assert ts.to_regenerate(_view(ctx, rows)) == []


# ---------- avoid notes (Q5) ----------

def test_avoid_notes_are_this_topics_reject_notes_newest_first_deduped(ctx, plan):
    rx = _topic(ctx, "Regular Expressions")
    rows = [_row(ctx, plan, rid=1, status="rejected", note="recurrence  dropped", minutes=1),
            _row(ctx, plan, rid=2, status="rejected", note="converted back", minutes=2),
            _row(ctx, plan, rid=3, status="rejected", note="recurrence dropped", minutes=3),
            _row(ctx, plan, rid=4, status="approved", note="fine", minutes=4),
            _row(ctx, rx, rid=5, status="rejected", note="other topic", minutes=5)]
    assert ts.avoid_notes(rows, MED) == ["recurrence dropped", "converted back"]


def test_avoid_notes_capped(ctx, plan):
    rows = [_row(ctx, plan, rid=i, status="rejected", note=f"note {i}", minutes=i) for i in range(1, 10)]
    assert ts.avoid_notes(rows, MED) == [f"note {i}" for i in range(9, 9 - ts.AVOID_MAX_NOTES, -1)]


def test_avoid_block_text(ctx):
    assert ts.avoid_block([]) == ""
    b = ts.avoid_block(["converted back to a\nregular expression", "  "])
    assert "Do NOT write this" in b and "- converted back to a regular expression" in b
    assert b.count("\n- ") == 1 and "still apply" in b                   # the rules above stay in force


# ---------- PDF (Q3) ----------

def test_pdf_ready_only_when_every_covered_topic_is_approved(ctx):
    covered = [t for t in ctx.topics if t.status in ("FOUND", "PARTIAL") and t.passages]
    rows = [_row(ctx, _topic(ctx, t.topic), rid=i, status="approved") for i, t in enumerate(covered, 1)]
    assert ts.pdf_ready(_view(ctx, rows))
    assert ts.pdf_row_ids(_view(ctx, rows)) == [r["id"] for r in rows]          # syllabus order
    rows[0] = dict(rows[0], review_status="draft", reviewed_at=None)
    assert not ts.pdf_ready(_view(ctx, rows))
    with pytest.raises(ValueError):
        ts.pdf_row_ids(_view(ctx, rows))
    assert not ts.pdf_ready(_view(ctx, []))


def test_pure_module_imports_no_streamlit_db_or_network():
    src = open(ts.__file__, encoding="utf-8").read()
    for bad in ("streamlit", "psycopg2", "requests", "urllib", "socket"):
        assert f"import {bad}" not in src and f"from {bad}" not in src


def test_pdf_not_ready_when_every_topic_is_a_gap():
    view = [ts.TopicEntry("A", 1, ts.GAP, None, []), ts.TopicEntry("B", 2, ts.GAP, None, [])]
    assert not ts.pdf_ready(view)
