"""BL-043 (BL043_DESIGN.md 0b08af3728; John Q1-Q4 + fill-ins F1-F8 OK'd 2026-09-29): 'Remove lines' in Admin.
Why: AL3501 U2 'Stochastic and Transformation-based tagging' - 5 reject + regenerate rounds, each adding a new
invented line in words that ARE in the source (R5 cannot catch it). John ticks bad lines and removes them himself.
Removing can never add a fact, so R1-R6 stay intact.
- Q1 tick any lines (paragraph / bullet / step); a section whose lines all go disappears with its heading.
- Q2 a note is REQUIRED (max 1000 chars); kept in check_data.removed, never printed.
- Q3 the trimmed text is a NEW DRAFT row (copy of the shown row, markdown trimmed); the old row stays.
- Q4 remaining numbered steps renumbered 1, 2, 3 ...
- F1 only DRAFT / APPROVED · F2 title + headings not tickable, >= 1 line must stay, nothing ticked refused ·
  F3 seen-row guard · F4 no migration (check_data.removed, cumulative) · F5 re-check re-applies the removals ·
  F6 remove_blocks enforces removal-only in code · F7 Admin controls + caption · F8 no Groq call, no check log.
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import source_app as sa
import source_context as sc
import source_prompts as sp
import topic_store as ts
from source_review import ReviewError
from test_source_prompts import _good_answer, _topic
from test_app_ui import _all_text, _app, _events, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

MED = "Minimum Edit Distance"
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
NOTE = "Step 3 is not in Unit1.txt - the source never says this."

DOC = ("### Topic\n\n"
       "#### Definition\n\nA definition line. [P1]\n\n"
       "#### Explanation\n\nFirst explanation. [P1]\n\nSecond explanation\ngoes on here. [P2]\n\n"
       "#### Algorithm / Steps\n\n1. Step one. [P1]\n2. Step two. [P2]\n3. Step three. [P2]\n\n"
       "#### Key points\n\n- Point A. [P1]\n- Point B. [P2]\n")


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, MED)


def _sents(plan, n):
    out = []
    for p in plan.passages:
        for s in re.split(r"(?<=[.?!])\s+", p.text.strip()):
            if 6 <= len(s.split()) <= 40 and not re.search(r"\d", s):
                out.append((s, p.pid))
    assert len(out) >= n
    return out[:n]


def _answer(plan):
    """A real-looking answer that passes today's checks: Explanation, 4 tagged steps, 2 key points."""
    s = _sents(plan, 7)
    lines = [f"### {plan.topic}", "", "#### Explanation", "", f"{s[0][0]} [{s[0][1]}]", "",
             "#### Algorithm / Steps", ""]
    lines += [f"{i}. {s[i][0]} [{s[i][1]}]" for i in range(1, 5)]
    lines += ["", "#### Key points", "", f"- {s[5][0]} [{s[5][1]}]", f"- {s[6][0]} [{s[6][1]}]"]
    return "\n".join(lines) + "\n"


def _row(ctx, plan, rid=7, status="draft", minutes=0, **kw):
    ans = _answer(plan)
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok
    r = {"id": rid, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": plan.topic,
         "topic_index": [t.topic for t in ctx.topics].index(plan.topic) + 1, "raw_answer": ans,
         "markdown": chk.markdown, "check_data": {"status": "ok", "calls": 1, "dropped": []},
         "source_sha256": ctx.source.sha256, "passages_sha256": ts.passages_sha256(plan),
         "rules_version": "sp:x", "avoid_notes": ["older note"],
         "generated_at": T0 + timedelta(minutes=minutes), "review_status": status,
         "reviewed_at": None if status == "draft" else T0, "review_note": None}
    r.update(kw)
    return r


def _entry(ctx, rows, topic=MED):
    return {e.topic: e for e in ts.unit_view(ctx, rows)}[topic]


def tr():
    import topic_review
    return topic_review


# ---------- F6: remove_blocks (pure, removal only) ----------

def test_content_blocks_lists_every_line_but_not_the_headings():
    blocks = ts.content_blocks(DOC)
    assert blocks[0] == "A definition line. [P1]"
    assert "Second explanation\ngoes on here. [P2]" in blocks          # a two-line paragraph is ONE block
    assert "2. Step two. [P2]" in blocks and "- Point B. [P2]" in blocks
    assert not any(b.startswith("#") for b in blocks) and len(blocks) == 8


def test_remove_one_bullet():
    out = ts.remove_blocks(DOC, ["- Point A. [P1]"])
    assert out == DOC.replace("- Point A. [P1]\n", "")


def test_remove_a_step_renumbers_the_rest():
    out = ts.remove_blocks(DOC, ["2. Step two. [P2]"])
    assert "1. Step one. [P1]\n2. Step three. [P2]\n\n#### Key points" in out
    assert "Step two" not in out


def test_remove_a_whole_section_drops_its_heading():
    out = ts.remove_blocks(DOC, ["- Point A. [P1]", "- Point B. [P2]"])
    assert "Key points" not in out and out.endswith("3. Step three. [P2]\n")
    out = ts.remove_blocks(DOC, ["A definition line. [P1]"])
    assert "#### Definition" not in out and out.startswith("### Topic\n\n#### Explanation\n\n")


def test_remove_a_two_line_paragraph():
    out = ts.remove_blocks(DOC, ["Second explanation\ngoes on here. [P2]"])
    assert "goes on here" not in out and "First explanation. [P1]\n\n#### Algorithm / Steps" in out


def test_headings_and_the_title_cannot_be_removed():
    for h in ("### Topic", "#### Key points"):
        with pytest.raises(ts.RemoveError):
            ts.remove_blocks(DOC, [h])


def test_removing_every_line_is_refused():
    with pytest.raises(ts.RemoveError, match="every line"):
        ts.remove_blocks(DOC, ts.content_blocks(DOC))


def test_nothing_ticked_or_unknown_text_is_refused():
    with pytest.raises(ts.RemoveError):
        ts.remove_blocks(DOC, [])
    with pytest.raises(ts.RemoveError, match="no longer matches"):
        ts.remove_blocks(DOC, ["- A line that is not there. [P1]"])


def test_a_line_that_is_there_twice_is_refused():
    doc = DOC.replace("- Point B. [P2]", "- Point A. [P1]")
    with pytest.raises(ts.RemoveError, match="exactly one"):
        ts.remove_blocks(doc, ["- Point A. [P1]"])


def test_a_step_matches_whatever_its_number_is():
    """Removals are re-applied to the re-checked (not yet renumbered) text: '2. Step three' == '3. Step three'."""
    once = ts.remove_blocks(DOC, ["2. Step two. [P2]"])
    assert "2. Step three. [P2]" in once
    assert ts.remove_blocks(DOC, ["2. Step two. [P2]", "2. Step three. [P2]"]) == \
        ts.remove_blocks(once, ["2. Step three. [P2]"])


def test_result_is_the_old_lines_minus_the_removed_ones():
    ticked = ["First explanation. [P1]", "1. Step one. [P1]", "- Point B. [P2]"]
    out = ts.remove_blocks(DOC, ticked)
    key = lambda t: re.sub(r"^\d+\.", "#.", t)                       # noqa: E731
    assert [key(b) for b in ts.content_blocks(out)] == \
        [key(b) for b in ts.content_blocks(DOC) if b not in ticked]


def test_text_that_does_not_read_back_the_same_is_refused():
    with pytest.raises(ts.RemoveError, match="cannot be split"):
        ts.remove_blocks("### Topic\n\n\n\nA line. [P1]\n\nB line. [P2]\n", ["A line. [P1]"])


# ---------- F5: re-check with removals ----------

def test_recheck_passes_on_a_trimmed_row(ctx, plan):
    row = _row(ctx, plan)
    step2 = [b for b in ts.content_blocks(row["markdown"]) if b.startswith("2. ")][0]
    new = tr().removal_row(_entry(ctx, [row]), 7, [step2], NOTE)
    new.update(id=8, generated_at=T0 + timedelta(minutes=5), review_status="draft", reviewed_at=None,
               review_note=None)
    assert ts.recheck(new, plan) == (True, [])
    assert _entry(ctx, [row, new]).state == ts.DRAFT
    new["review_status"] = "approved"
    assert _entry(ctx, [row, new]).state == ts.APPROVED


def test_recheck_needs_review_when_a_removed_line_no_longer_matches(ctx, plan):
    row = _row(ctx, plan, review_status="approved")
    first = ts.content_blocks(row["markdown"])[0]
    row["markdown"] = ts.remove_blocks(row["markdown"], [first])
    row["check_data"] = dict(row["check_data"], removed=[{"text": "- A line not in the answer. [P1]",
                                                          "note": NOTE, "from_row": 3}])
    ok, why = ts.recheck(row, plan)
    assert not ok and any("no longer matches" in w for w in why)
    assert _entry(ctx, [row]).state == ts.NEEDS_REVIEW


def test_GUARD_a_row_without_removals_rechecks_as_before(ctx, plan):
    row = _row(ctx, plan)
    assert ts.recheck(row, plan) == (True, [])
    first = row["markdown"].split("\n\n")[2]                     # title, heading, FIRST line (no new code used)
    row["markdown"] = row["markdown"].replace(first + "\n\n", "", 1)
    ok, why = ts.recheck(row, plan)                              # trimmed text but NO removal record -> not ok
    assert not ok and why == ["today's checks would keep a different text than the one saved"]


# ---------- F1-F4: topic_review.removal_row ----------

def test_removal_row_copies_the_row_and_records_the_removal(ctx, plan):
    row = _row(ctx, plan, status="approved")
    ticked = [b for b in ts.content_blocks(row["markdown"]) if b.startswith(("2. ", "3. "))]
    new = tr().removal_row(_entry(ctx, [row]), 7, ticked, "  " + NOTE + " ")
    assert set(new) == set(sa.TOPIC_COLUMNS)                     # exactly what TOPIC_INSERT_SQL sends
    for c in ("subject_code", "unit_number", "variant", "topic", "topic_index", "raw_answer", "source_sha256",
              "passages_sha256", "rules_version", "avoid_notes"):
        assert new[c] == row[c]
    assert new["markdown"] == ts.remove_blocks(row["markdown"], ticked)
    assert new["check_data"]["status"] == "ok" and new["check_data"]["calls"] == 1
    assert new["check_data"]["removed"] == [{"text": t, "note": NOTE, "from_row": 7} for t in ticked]
    assert "removed" not in row["check_data"]                     # the shown row is not changed
    sa.topic_insert_params(new)                                    # serialisable for the INSERT


def test_removals_add_up_over_several_rounds(ctx, plan):
    row = _row(ctx, plan)
    b = ts.content_blocks(row["markdown"])
    new = tr().removal_row(_entry(ctx, [row]), 7, [b[1]], "first")
    new.update(id=9, generated_at=T0 + timedelta(minutes=5), review_status="draft", reviewed_at=None,
               review_note=None)
    third = tr().removal_row(_entry(ctx, [row, new]), 9, [ts.content_blocks(new["markdown"])[-1]], "second")
    assert [(r["note"], r["from_row"]) for r in third["check_data"]["removed"]] == [("first", 7), ("second", 9)]
    third.update(id=10, generated_at=T0 + timedelta(minutes=9), review_status="draft", reviewed_at=None,
                 review_note=None)
    assert ts.recheck(third, plan) == (True, [])


@pytest.mark.parametrize("state_kw", [
    {"review_status": "rejected", "review_note": "bad"},
    {"check_data": {"status": "needs_review"}},
    {"source_sha256": "0" * 64},
])
def test_only_draft_or_approved_topics(ctx, plan, state_kw):
    row = _row(ctx, plan, **state_kw)
    e = _entry(ctx, [row])
    assert e.state not in (ts.DRAFT, ts.APPROVED)
    assert not tr().can_remove_lines(e)
    with pytest.raises(ReviewError, match="DRAFT or APPROVED"):
        tr().removal_row(e, 7, [ts.content_blocks(row["markdown"])[0]], NOTE)


def test_draft_and_approved_can_remove(ctx, plan):
    assert tr().can_remove_lines(_entry(ctx, [_row(ctx, plan)]))
    assert tr().can_remove_lines(_entry(ctx, [_row(ctx, plan, status="approved")]))


def test_note_is_required_and_capped(ctx, plan):
    row = _row(ctx, plan)
    line = ts.content_blocks(row["markdown"])[0]
    with pytest.raises(ReviewError, match="note"):
        tr().removal_row(_entry(ctx, [row]), 7, [line], "   ")
    with pytest.raises(ReviewError, match="too long"):
        tr().removal_row(_entry(ctx, [row]), 7, [line], "x" * 1001)


def test_seen_row_guard(ctx, plan):
    row = _row(ctx, plan)
    with pytest.raises(ReviewError, match="changed since the page loaded"):
        tr().removal_row(_entry(ctx, [row]), 6, [ts.content_blocks(row["markdown"])[0]], NOTE)


def test_nothing_ticked_and_everything_ticked_are_refused(ctx, plan):
    row = _row(ctx, plan)
    with pytest.raises(ReviewError, match="tick"):
        tr().removal_row(_entry(ctx, [row]), 7, [], NOTE)
    with pytest.raises(ReviewError, match="every line"):
        tr().removal_row(_entry(ctx, [row]), 7, ts.content_blocks(row["markdown"]), NOTE)


def test_removed_text_for_the_caption(ctx, plan):
    row = _row(ctx, plan)
    assert tr().removed_text(row) == ""                            # GUARD-like: old rows show nothing
    row["check_data"]["removed"] = [{"text": "a", "note": "n1", "from_row": 1},
                                    {"text": "b", "note": "n1", "from_row": 1},
                                    {"text": "c", "note": "n2", "from_row": 2}]
    assert tr().removed_text(row) == "3 line(s) removed by you: n1 | n2"


# ---------- BL-042 button keeps John's removals ----------

def test_rechecked_row_keeps_the_removals(ctx, plan):
    """A trimmed row that goes OUT OF DATE for another reason and is saved again with '✅ Use re-checked text' keeps
    John's removals (they are re-applied), so removed lines never come back silently."""
    row = _row(ctx, plan)
    ticked = [ts.content_blocks(row["markdown"])[1]]
    new = tr().removal_row(_entry(ctx, [row]), 7, ticked, NOTE)
    old_md = new["markdown"]
    new.update(id=8, generated_at=T0 + timedelta(minutes=5), review_status="draft", reviewed_at=None,
               review_note=None, markdown=old_md + "\nextra line from an older rule. [P1]\n")
    e = _entry(ctx, [row, new])
    assert e.state == ts.STALE
    again = sa.rechecked_topic_row(e, plan, subject_code="AL3501", unit_number=1, rules_version="sp:y")
    assert again["markdown"] == old_md
    assert again["check_data"]["removed"] == new["check_data"]["removed"]


# ---------- F7 / F8: the Admin screen ----------

def test_screen_remove_lines_makes_a_new_draft(tmp_path, ctx, plan):
    row = _row(ctx, plan, rid=41, status="approved")
    at, log = _app(tmp_path, topic_rows=[row])
    _unlock(at)
    idx = row["topic_index"]
    assert not [c for c in at.checkbox if c.key.startswith("trm_41_")]     # hidden until opened
    at.checkbox(key=f"trmopen_{idx}").check().run()
    boxes = [c for c in at.checkbox if c.key.startswith("trm_41_")]
    assert [c.label for c in boxes] == ts.content_blocks(row["markdown"])
    boxes[2].check().run()
    at.button(key=f"tremove_{idx}").click().run()                          # no note -> not saved
    assert "Not saved" in _all_text(at) and not [e for e in _events(log) if e["kind"] == "topic_row"]
    at.text_area(key=f"tremnote_{idx}").input(NOTE).run()
    at.button(key=f"tremove_{idx}").click().run()
    assert not at.exception
    saved = [e for e in _events(log) if e["kind"] == "topic_row"]
    assert len(saved) == 1 and saved[0]["topic"] == MED
    assert not [e for e in _events(log) if e["kind"] == "groq"]            # F8: no Groq call
    assert not list((tmp_path).glob("check_*.json"))
    store = json.loads((tmp_path / "topics.json").read_text(encoding="utf-8"))
    new = store[-1]
    assert new["review_status"] == "draft" and new["check_data"]["removed"][0]["note"] == NOTE
    assert new["markdown"] == ts.remove_blocks(row["markdown"], [ts.content_blocks(row["markdown"])[2]])
    text = _all_text(at)
    assert "1 line(s) removed by you: " + NOTE in text and "DRAFT" in text


def test_screen_no_remove_lines_on_a_rejected_topic(tmp_path, ctx, plan):
    row = _row(ctx, plan, rid=42, review_status="rejected", review_note="bad")
    other = next(p for p in ctx.topics if ts.covered(p) and p.topic != MED)
    ans = _good_answer(other)
    ok_row = _row(ctx, other, rid=43, status="approved", raw_answer=ans,
                  markdown=sp.check_notes_answer(ans, other).markdown)
    at, _log = _app(tmp_path, topic_rows=[row, ok_row])
    _unlock(at)
    keys = [c.key for c in at.checkbox]
    assert f"trmopen_{row['topic_index']}" not in keys and f"trmopen_{ok_row['topic_index']}" in keys


def test_screen_build_pdf_uses_the_trimmed_row(tmp_path, ctx, plan):
    """Every covered Unit 1 topic approved; MED trimmed (new row 300) and approved -> Build PDF uses row 300."""
    rows = []
    for i, p in enumerate(ctx.topics, 1):
        if ts.covered(p) and p.topic != MED:
            ans = _good_answer(p)
            rows.append(_row(ctx, p, rid=100 + i, status="approved", raw_answer=ans,
                             markdown=sp.check_notes_answer(ans, p).markdown))
    med = _row(ctx, plan, rid=150, status="approved")
    new = tr().removal_row(_entry(ctx, rows + [med]), 150, [ts.content_blocks(med["markdown"])[1]], NOTE)
    new.update(id=300, generated_at=T0 + timedelta(minutes=5), review_status="approved", reviewed_at=T0,
               review_note=None)
    at, log = _app(tmp_path, topic_rows=rows + [med, new])
    _unlock(at)
    b = next((x for x in at.button if x.key == "build_pdf"), None)
    assert b is not None
    b.click().run()
    assert not at.exception, at.exception
    up = next(e for e in _events(log) if e["kind"] == "upsert_topics")
    assert 300 in up["topic_row_ids"] and 150 not in up["topic_row_ids"]
