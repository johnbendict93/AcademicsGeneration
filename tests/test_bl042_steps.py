"""BL-042 (BL042_DESIGN.md ba4a364f41; John 29 Sep: A + B + re-check button). Broken algorithm steps.
Measured: 14 of 16 dropped content lines were numbered steps / formula bullets the model left untagged while tagging
a LATER item of the same list -> PDFs showed a lone '5.' or only the substitution rule.
A: prompt shows a tagged step list; re-ask says 'tag at the END of EACH step'; kept numbered items renumbered;
   'steps incomplete' layout note when a list still lost items.
B (John's narrow R6 exception, like the BL-033 lead-in): an untagged list item is KEPT only if a LATER item of the
   SAME list has a valid tag AND the item has no new terms, no odd characters, no LaTeX and no number that is not in
   the passages. Logged as list_tagged.
Button: a NEEDS REVIEW / OUT OF DATE topic whose SAVED raw answer passes today's checks (same source + passages) can
   be saved again as a new draft with the re-checked text - no Groq call.
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import re
from datetime import datetime, timedelta, timezone

import pytest

import source_app as sa
import source_context as sc
import source_prompts as sp
import topic_store as ts
from test_source_prompts import _topic
from test_app_ui import _all_text, _app, _events, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

MED = "Minimum Edit Distance"
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, MED)


def _s(plan, n):
    out = []
    for p in plan.passages:
        for s in re.split(r"(?<=[.?!])\s+", p.text.strip()):
            if 6 <= len(s.split()) <= 40 and not re.search(r"\d", s):
                out.append((s, p.pid))
    assert len(out) >= n
    return out[:n]


def _steps(plan, tags, head="#### Algorithm / Steps"):
    """Numbered list; tags[i] True -> item i carries its passage tag."""
    s = _s(plan, len(tags) + 1)
    lines = [f"### {plan.topic}", "", "#### Explanation", "", f"{s[-1][0]} [{s[-1][1]}]", "", head, ""]
    for i, t in enumerate(tags):
        lines.append(f"{i + 1}. {s[i][0]}" + (f" [{s[i][1]}]" if t else ""))
    return "\n".join(lines) + "\n", s


# ---------- A1: prompt ----------

def test_prompt_shows_a_tagged_step_list(ctx, plan):
    p = sp.build_notes_prompt(ctx, plan, "S", "U")
    assert '"1. <first step> [P3]"' in p and '"2. <next step> [P3]"' in p
    assert "EVERY step and EVERY formula line ends with its own tag" in p


# ---------- B: list tag ----------

def test_untagged_steps_before_a_tagged_step_are_kept(plan):
    ans, s = _steps(plan, [False, False, False, True])
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok and chk.dropped == []
    for i in range(4):
        assert s[i][0][:30] in chk.markdown
    assert len(chk.list_tagged) == 3


def test_GUARD_untagged_step_after_the_last_tagged_one_is_dropped(plan):
    ans, s = _steps(plan, [True, False])
    chk = sp.check_notes_answer(ans, plan)
    assert s[1][0][:30] not in chk.markdown and [d.reason for d in chk.dropped] == ["no citation"]


@pytest.mark.parametrize("extra", [" Then apply the Zorblax rule.", " Repeat it 7777 times.", " Use ∑ here.",
                                   " Write \\frac{a}{b} here."])
def test_list_tag_needs_every_other_check_to_pass(plan, extra):
    ans, s = _steps(plan, [False, True])
    ans = ans.replace(f"1. {s[0][0]}", f"1. {s[0][0]}{extra}")
    chk = sp.check_notes_answer(ans, plan)
    assert s[0][0][:30] not in chk.markdown and chk.list_tagged == []


def test_GUARD_a_paragraph_ends_the_list(plan):
    s = _s(plan, 3)
    ans = (f"### {plan.topic}\n\n1. {s[0][0]}\n\n{s[1][0]} [{s[1][1]}]\n\n2. {s[2][0]} [{s[2][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert s[0][0][:30] not in chk.markdown                           # no LATER tagged item in ITS list


def test_sub_bullets_belong_to_the_same_list(plan):
    s = _s(plan, 3)
    ans = (f"### {plan.topic}\n\n1. {s[0][0]}\n   - {s[1][0]}\n2. {s[2][0]} [{s[2][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert s[0][0][:30] in chk.markdown and s[1][0][:30] in chk.markdown and len(chk.list_tagged) == 2


# ---------- A3: renumber ----------

def test_kept_numbered_steps_are_renumbered(plan):
    ans, s = _steps(plan, [True, True, True])
    ans = ans.replace(f"1. {s[0][0]} [{s[0][1]}]", f"2. {s[0][0]} [{s[0][1]}]") \
             .replace(f"2. {s[1][0]} [{s[1][1]}]", f"5. {s[1][0]} [{s[1][1]}]") \
             .replace(f"3. {s[2][0]} [{s[2][1]}]", f"9. {s[2][0]} [{s[2][1]}]")
    chk = sp.check_notes_answer(ans, plan)
    nums = re.findall(r"^(\d+)\. ", chk.markdown, re.M)
    assert nums == ["1", "2", "3"] and f"1. {s[0][0]}" in chk.markdown


def test_separate_lists_are_numbered_separately(plan):
    s = _s(plan, 4)
    ans = (f"### {plan.topic}\n\n3. {s[0][0]} [{s[0][1]}]\n4. {s[1][0]} [{s[1][1]}]\n\n{s[2][0]} [{s[2][1]}]\n\n"
           f"7. {s[3][0]} [{s[3][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert re.findall(r"^(\d+)\. ", chk.markdown, re.M) == ["1", "2", "1"]


def test_GUARD_correct_numbering_is_unchanged(plan):
    ans, _s2 = _steps(plan, [True, True])
    assert sp.check_notes_answer(ans, plan).markdown.strip() == ans.strip()


# ---------- A4: steps incomplete ----------

def test_list_that_lost_items_is_flagged(plan):
    ans, s = _steps(plan, [True, True, False, False])                  # last two: no later tag -> dropped
    chk = sp.check_notes_answer(ans, plan)
    assert "steps incomplete: Algorithm / Steps lost 2 of 4 items" in chk.layout


def test_GUARD_no_flag_when_nothing_lost(plan):
    ans, _ = _steps(plan, [True, True])
    assert not [n for n in sp.check_notes_answer(ans, plan).layout if n.startswith("steps incomplete")]


# ---------- A2: re-ask + log ----------

def test_reask_asks_for_a_tag_at_the_end_of_each_step(ctx, plan):
    ans, s = _steps(plan, [True, False])
    prompts = []

    def ask(p):
        prompts.append(p)
        return ans
    sp.notes_for_topic(ctx, plan, "S", "U", ask=ask)
    assert "put the passage tag at the END of EACH step" in prompts[1]


def test_check_log_lists_the_items_kept_by_list_tag(ctx, plan):
    ans, _ = _steps(plan, [False, True])
    res = sp.notes_for_topic(ctx, plan, "S", "U", ask=lambda p: ans)
    assert len(sa._writer_log(res)["list_tagged"]) == 1


# ---------- re-check button (no Groq) ----------

def _old_row(ctx, plan, rid=5, status="approved"):
    """A saved attempt made by the OLD rules: its markdown lost the untagged steps."""
    ans, s = _steps(plan, [False, False, True])
    old_md = "\n\n".join(b for b in sp.check_notes_answer(ans, plan).markdown.split("\n\n")) \
        .replace(f"1. {s[0][0]}\n", "").replace(f"2. {s[1][0]}\n", "").replace("3. ", "1. ", 1)
    return {"id": rid, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": plan.topic,
            "topic_index": 8, "raw_answer": ans, "markdown": old_md,
            "check_data": {"status": "ok"}, "source_sha256": ctx.source.sha256,
            "passages_sha256": ts.passages_sha256(plan), "rules_version": "sp:old", "avoid_notes": ["n1"],
            "generated_at": T0, "review_status": status, "reviewed_at": T0 if status != "draft" else None,
            "review_note": None}, s


def test_rechecked_row_recovers_the_steps_without_a_model_call(ctx, plan):
    row, s = _old_row(ctx, plan)
    entry = next(e for e in ts.unit_view(ctx, [row]) if e.topic == MED)
    assert entry.state == ts.NEEDS_REVIEW
    new = sa.rechecked_topic_row(entry, plan, subject_code="AL3501", unit_number=1, rules_version="sp:new")
    assert new["raw_answer"] == row["raw_answer"] and s[0][0][:30] in new["markdown"]
    assert new["check_data"]["rechecked_from"] == 5 and new["check_data"]["calls"] == 0
    assert new["check_data"]["status"] == "ok" and new["avoid_notes"] == ["n1"] and new["rules_version"] == "sp:new"
    assert new["topic_index"] == 8 and set(new) == set(sa.TOPIC_COLUMNS)


def test_no_recheck_offer_when_it_would_not_help(ctx, plan):
    row, _ = _old_row(ctx, plan)
    moved = dict(row, passages_sha256="0" * 64)                      # passages changed -> needs Groq
    e = next(e for e in ts.unit_view(ctx, [moved]) if e.topic == MED)
    assert sa.rechecked_topic_row(e, plan, subject_code="AL3501", unit_number=1, rules_version="v") is None
    same = dict(row, markdown=sp.check_notes_answer(row["raw_answer"], plan).markdown)   # nothing to recover
    e = next(e for e in ts.unit_view(ctx, [same]) if e.topic == MED)
    assert e.state == ts.APPROVED
    assert sa.rechecked_topic_row(e, plan, subject_code="AL3501", unit_number=1, rules_version="v") is None


# ---------- the screen ----------

def test_screen_use_rechecked_text_saves_a_new_draft_without_groq(tmp_path, ctx, plan):
    row, s = _old_row(ctx, plan)
    at, log = _app(tmp_path, topic_rows=[row])
    _unlock(at)
    idx = [t.topic for t in ctx.topics].index(MED) + 1
    b = next(b for b in at.button if b.key == f"trecheck_{idx}")
    assert b.label == "✅ Use re-checked text (no Groq call)"
    b.click().run()
    assert not at.exception, at.exception
    ev = _events(log)
    assert "groq" not in [e["kind"] for e in ev] and [e["topic"] for e in ev if e["kind"] == "topic_row"] == [MED]
    assert f"Saved the re-checked text of '{MED}' as a DRAFT" in _all_text(at)
    assert "DRAFT" in next(x for x in at.expander if MED in x.label).label


def test_screen_shows_sections_and_steps_incomplete(tmp_path, ctx, plan):
    ans, _ = _steps(plan, [True, True, False, False])
    chk = sp.check_notes_answer(ans, plan)
    row = {"id": 7, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": MED, "topic_index": 8,
           "raw_answer": ans, "markdown": chk.markdown,
           "check_data": {"status": "ok", "calls": 1, "sections": chk.sections, "layout": chk.layout},
           "source_sha256": ctx.source.sha256, "passages_sha256": ts.passages_sha256(plan), "rules_version": "v",
           "avoid_notes": [], "generated_at": T0, "review_status": "draft", "reviewed_at": None, "review_note": None}
    at, _ = _app(tmp_path, topic_rows=[row])
    _unlock(at)
    text = _all_text(at)
    assert "Sections: Explanation, Algorithm / Steps" in text
    assert "Steps incomplete: Algorithm / Steps lost 2 of 4 items" in text
