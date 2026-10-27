"""BL-045 (BL045_DESIGN.md; John Q1-Q2 + fill-ins F1-F7 OK'd 2026-10-01): 'Remove lines' can also remove single
SENTENCES of a multi-sentence line. Why: CS3591 U1 SNMP - the Explanation paragraph is good except its last sentence
('...port 162 for traps [P42].', 162 not in the source); BL-043 could only drop the whole paragraph.
- Q1 a removed sentence that holds the line's only [Pn] tag: the tag MOVES BACK to the end of what is left.
- Q2 every line with 2+ sentences (paragraph, bullet, step) gets one tick per sentence.
- F1 split at . ? ! (+ its tag) before a capital / digit / '('; never inside ( ) [ ], after e.g. / i.e. / etc. / vs.,
  or inside a number; the pieces join back exactly.
- F2 whole line + its sentence ticked -> whole line goes; all sentences ticked -> whole line goes.
- F3 a tag moves only when no tag is left; only ids of that line.
- F4 check_data.removed {'text', 'in_line', 'note', 'from_row'}; whole-line entries unchanged.
- F5 re-check re-applies sentence removals; a changed line -> NEEDS REVIEW. F6 removal-only proof in code.
- F7 Admin: indented '↳' ticks under a multi-sentence line.
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import json
from datetime import timedelta

import pytest

import topic_store as ts
from source_review import ReviewError
from test_bl043_remove_lines import MED, NOTE, T0, _entry, _row, ctx, plan, tr  # noqa: F401 (fixtures)
from test_app_ui import _all_text, _app, _events, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

SNMP = ("SNMP lets a manager read and change values held by agents on devices. The manager sends Get and Set "
        "requests and the agent answers them. SNMP operates primarily over UDP, using port 161 for requests and "
        "responses and port 162 for traps [P42].")
LAST = "SNMP operates primarily over UDP, using port 161 for requests and responses and port 162 for traps [P42]."
DOC = ("### SNMP\n\n"
       "#### Explanation\n\n" + SNMP + "\n\n"
       "#### Algorithm / Steps\n\n1. Manager sends a Get request. [P40]\n"
       "2. Agent reads the value. It sends a Get-response back. [P41]\n"
       "3. Agent acknowledges with a Set-response. [P41]\n\n"
       "#### Key points\n\n- Point A has two parts. [P1] Part two is here. [P2]\n- Point B. [P2]\n")


# ---------- F1: the sentence splitter ----------

def test_split_the_snmp_paragraph():
    assert ts.block_sentences(SNMP) == [
        "SNMP lets a manager read and change values held by agents on devices.",
        "The manager sends Get and Set requests and the agent answers them.", LAST]


def test_a_one_sentence_line_has_no_sentence_ticks():
    assert ts.block_sentences("A definition line. [P1]") == []
    assert ts.block_sentences("- Point B. [P2]") == []
    assert ts.block_sentences("") == []


@pytest.mark.parametrize("text", [
    "Use a protocol, e.g. SNMP for this [P1].",
    "Values like 1.5 and 192.168 are kept [P1].",
    "Versions (v1. V2c. v3) are named here [P1].",
    "Tags [P1. P2] stay whole here [P3].",
    "It does this i.e. Reads values, etc. Then stops [P1].",
    "It is lower case. then more words [P1].",
])
def test_lines_that_are_not_split(text):
    assert ts.block_sentences(text) == []


def test_tag_after_the_full_stop_stays_with_its_sentence():
    assert ts.block_sentences("Part one is here. [P1] Part two is here. [P2]") == \
        ["Part one is here. [P1]", "Part two is here. [P2]"]


def test_list_marker_is_not_part_of_a_sentence():
    assert ts.block_sentences("2. Agent reads the value. It sends a Get-response back. [P41]") == \
        ["Agent reads the value.", "It sends a Get-response back. [P41]"]
    assert ts.block_sentences("- Point A has two parts. [P1] Part two is here. [P2]") == \
        ["Point A has two parts. [P1]", "Part two is here. [P2]"]


def test_two_line_paragraph_splits_at_the_line_break():
    assert ts.block_sentences("First one here.\nSecond one here [P2].") == ["First one here.", "Second one here [P2]."]


# ---------- Q1 / F3: removing a sentence ----------

def _parts(line, *sents):
    return [{"in_line": line, "text": s} for s in sents]


def test_remove_the_last_sentence_moves_the_only_tag_back():
    out = ts.remove_parts(DOC, [], _parts(SNMP, LAST))
    assert ("SNMP lets a manager read and change values held by agents on devices. The manager sends Get and Set "
            "requests and the agent answers them [P42].\n\n#### Algorithm / Steps") in out
    assert "162" not in out


def test_remove_a_middle_sentence_keeps_the_tag_where_it_is():
    mid = "The manager sends Get and Set requests and the agent answers them."
    out = ts.remove_parts(DOC, [], _parts(SNMP, mid))
    assert ("devices. SNMP operates primarily over UDP, using port 161 for requests and responses and port 162 "
            "for traps [P42].\n\n") in out and "Get and Set" not in out


def test_remove_a_sentence_when_another_tag_is_left_drops_its_tag():
    line = "- Point A has two parts. [P1] Part two is here. [P2]"
    out = ts.remove_parts(DOC, [], _parts(line, "Part two is here. [P2]"))
    assert "- Point A has two parts. [P1]\n- Point B. [P2]\n" in out


def test_remove_the_tag_after_full_stop_style():
    line = "- Point A has two parts. [P1] Part two is here. [P2]"
    doc = DOC.replace(line, "- Point A has two parts. Part two is here. [P2]")
    out = ts.remove_parts(doc, [], _parts("- Point A has two parts. Part two is here. [P2]", "Part two is here. [P2]"))
    assert "- Point A has two parts. [P2]\n" in out


def test_remove_the_first_sentence_of_a_step_keeps_its_number():
    line = "2. Agent reads the value. It sends a Get-response back. [P41]"
    out = ts.remove_parts(DOC, [], _parts(line, "Agent reads the value."))
    assert "1. Manager sends a Get request. [P40]\n2. It sends a Get-response back. [P41]\n3. Agent" in out


def test_all_sentences_ticked_removes_the_whole_line_and_renumbers():
    line = "2. Agent reads the value. It sends a Get-response back. [P41]"
    out = ts.remove_parts(DOC, [], _parts(line, *ts.block_sentences(line)))
    assert out == ts.remove_blocks(DOC, [line])


def test_whole_line_wins_over_its_sentences():
    out = ts.remove_parts(DOC, [SNMP], _parts(SNMP, LAST))
    assert out == ts.remove_blocks(DOC, [SNMP])


def test_sentences_and_whole_lines_together():
    out = ts.remove_parts(DOC, ["3. Agent acknowledges with a Set-response. [P41]"], _parts(SNMP, LAST))
    assert "Set-response" not in out and "162" not in out and "answers them [P42]." in out


def test_unknown_sentence_or_line_is_refused():
    with pytest.raises(ts.RemoveError, match="no longer matches"):
        ts.remove_parts(DOC, [], _parts(SNMP, "A sentence that is not there."))
    with pytest.raises(ts.RemoveError, match="no longer matches"):
        ts.remove_parts(DOC, [], _parts("A line that is not there. Two. [P1]", "Two. [P1]"))


def test_sentence_twice_in_a_line_is_refused():
    line = "- Same here now. [P1] Same here now. [P1]"
    doc = DOC.replace("- Point B. [P2]", line)
    with pytest.raises(ts.RemoveError, match="exactly once"):
        ts.remove_parts(doc, [], _parts(line, "Same here now. [P1]"))


def test_GUARD_remove_parts_with_whole_lines_only_equals_remove_blocks():
    line = "3. Agent acknowledges with a Set-response. [P41]"
    assert ts.remove_parts(DOC, [line], []) == ts.remove_blocks(DOC, [line])


def test_removal_only_proof_trips_on_a_forged_line(monkeypatch):
    real = ts._cut_sentences

    def forged(line, sents):
        return real(line, sents).replace("answers them", "answers them quickly")
    monkeypatch.setattr(ts, "_cut_sentences", forged)
    with pytest.raises(RuntimeError, match="removal-only"):
        ts.remove_parts(DOC, [], _parts(SNMP, LAST))


def test_removal_only_proof_trips_on_a_new_tag(monkeypatch):
    real = ts._cut_sentences
    monkeypatch.setattr(ts, "_cut_sentences", lambda line, sents: real(line, sents).replace("[P42]", "[P9]"))
    with pytest.raises(RuntimeError, match="removal-only"):
        ts.remove_parts(DOC, [], _parts(SNMP, LAST))


# ---------- F4 / F5: saved record + re-check ----------

def test_apply_removals_reapplies_sentence_entries():
    rem = [{"text": LAST, "in_line": SNMP, "note": NOTE, "from_row": 3}]
    assert ts.apply_removals(DOC, rem) == ts.remove_parts(DOC, [], _parts(SNMP, LAST))


def test_apply_removals_in_rounds():
    """Round 1 cuts a sentence of SNMP, round 2 removes the trimmed line's step 3: re-applied in the same order."""
    once = ts.remove_parts(DOC, [], _parts(SNMP, LAST))
    rem = [{"text": LAST, "in_line": SNMP, "note": "a", "from_row": 3},
           {"text": "3. Agent acknowledges with a Set-response. [P41]", "note": "b", "from_row": 4}]
    assert ts.apply_removals(DOC, rem) == ts.remove_blocks(once, ["3. Agent acknowledges with a Set-response. [P41]"])


def test_GUARD_apply_removals_refuses_when_the_line_changed():
    rem = [{"text": LAST, "in_line": SNMP, "note": NOTE, "from_row": 3}]
    with pytest.raises(ts.RemoveError):
        ts.apply_removals(DOC.replace("agents on devices", "agents"), rem)


def test_GUARD_apply_removals_whole_lines_as_before():
    rem = [{"text": "- Point B. [P2]", "note": NOTE, "from_row": 3}]
    assert ts.apply_removals(DOC, rem) == ts.remove_blocks(DOC, ["- Point B. [P2]"])


def _med_two_sentence_row(ctx, plan, **kw):
    """A real MED row whose Explanation has TWO tagged sentences (both from real passages)."""
    from test_bl043_remove_lines import _sents
    s = _sents(plan, 7)
    row = _row(ctx, plan, **kw)
    ans = row["raw_answer"]
    first = f"{s[0][0]} [{s[0][1]}]"
    assert first in ans
    ans = ans.replace(first, f"{s[0][0]} {s[5][0]} [{s[5][1]}]", 1)
    import source_prompts as sp
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok
    row.update(raw_answer=ans, markdown=chk.markdown)
    line = next(b for b in ts.content_blocks(row["markdown"]) if ts.block_sentences(b))
    return row, line


def test_removal_row_records_sentences_and_rechecks(ctx, plan):
    row, line = _med_two_sentence_row(ctx, plan, status="approved")
    sents = ts.block_sentences(line)
    assert len(sents) == 2
    new = tr().removal_row(_entry(ctx, [row]), 7, [], NOTE, parts=[{"in_line": line, "text": sents[1]}])
    assert new["check_data"]["removed"] == [{"text": sents[1], "in_line": line, "note": NOTE, "from_row": 7}]
    assert new["markdown"] == ts.remove_parts(row["markdown"], [], [{"in_line": line, "text": sents[1]}])
    assert sents[1] not in ts.content_blocks(new["markdown"])[0]          # (key point 1 has it too)
    assert ts.content_blocks(new["markdown"])[0].startswith(sents[0][:-1])
    new.update(id=8, generated_at=T0 + timedelta(minutes=5), review_status="draft", reviewed_at=None,
               review_note=None)
    assert ts.recheck(new, plan) == (True, [])
    assert _entry(ctx, [row, new]).state == ts.DRAFT


def test_recheck_needs_review_when_the_sentence_line_changed(ctx, plan):
    row, line = _med_two_sentence_row(ctx, plan)
    sents = ts.block_sentences(line)
    new = tr().removal_row(_entry(ctx, [row]), 7, [], NOTE, parts=[{"in_line": line, "text": sents[1]}])
    new["check_data"]["removed"][0]["in_line"] = line.replace(sents[0], "Something else entirely here.")
    new.update(id=8, generated_at=T0 + timedelta(minutes=5), review_status="approved", reviewed_at=T0,
               review_note=None)
    ok, why = ts.recheck(new, plan)
    assert not ok and any("no longer matches" in w for w in why)
    assert _entry(ctx, [row, new]).state == ts.NEEDS_REVIEW


def test_removal_row_whole_line_wins_in_the_record(ctx, plan):
    row, line = _med_two_sentence_row(ctx, plan)
    sents = ts.block_sentences(line)
    new = tr().removal_row(_entry(ctx, [row]), 7, [line], NOTE, parts=[{"in_line": line, "text": sents[1]}])
    assert new["check_data"]["removed"] == [{"text": line, "note": NOTE, "from_row": 7}]


def test_removal_row_sentence_only_still_needs_a_note(ctx, plan):
    row, line = _med_two_sentence_row(ctx, plan)
    sents = ts.block_sentences(line)
    with pytest.raises(ReviewError, match="note"):
        tr().removal_row(_entry(ctx, [row]), 7, [], " ", parts=[{"in_line": line, "text": sents[1]}])
    with pytest.raises(ReviewError, match="tick"):
        tr().removal_row(_entry(ctx, [row]), 7, [], NOTE, parts=[])


def test_removed_text_counts_sentences(ctx, plan):
    row = _row(ctx, plan)
    row["check_data"]["removed"] = [{"text": "a", "note": "n1", "from_row": 1},
                                    {"text": "b", "in_line": "x b", "note": "n2", "from_row": 2}]
    assert tr().removed_text(row) == "1 line(s) and 1 sentence(s) removed by you: n1 | n2"


def test_GUARD_removed_text_lines_only(ctx, plan):
    row = _row(ctx, plan)
    row["check_data"]["removed"] = [{"text": "a", "note": "n1", "from_row": 1}]
    assert tr().removed_text(row) == "1 line(s) removed by you: n1"


# ---------- F7: the Admin screen ----------

def test_screen_sentence_ticks_make_a_new_draft(tmp_path, ctx, plan):
    row, line = _med_two_sentence_row(ctx, plan, rid=51, status="approved")
    sents = ts.block_sentences(line)
    at, log = _app(tmp_path, topic_rows=[row])
    _unlock(at)
    idx = row["topic_index"]
    at.checkbox(key=f"trmopen_{idx}").check().run()
    assert [c.label for c in at.checkbox if c.key.startswith("trm_51_")] == ts.content_blocks(row["markdown"])
    subs = [c for c in at.checkbox if c.key.startswith("trs_51_")]
    assert [c.label for c in subs] == ["↳ " + s for s in sents]          # only the multi-sentence line
    subs[1].check().run()
    at.text_area(key=f"tremnote_{idx}").input(NOTE).run()
    at.button(key=f"tremove_{idx}").click().run()
    assert not at.exception, at.exception
    assert not [e for e in _events(log) if e["kind"] == "groq"]
    store = json.loads((tmp_path / "topics.json").read_text(encoding="utf-8"))
    new = store[-1]
    assert new["review_status"] == "draft"
    assert new["check_data"]["removed"] == [{"text": sents[1], "in_line": line, "note": NOTE, "from_row": 51}]
    assert new["markdown"] == ts.remove_parts(row["markdown"], [], [{"in_line": line, "text": sents[1]}])
    assert "1 sentence(s) removed by you: " + NOTE in _all_text(at)
