"""BL-038 step a (BL038_DESIGN.md dd031760d6; John's go 2026-09-29): organised topic layout for students.
The writer is asked for: ### <topic> / #### Definition / #### Explanation / #### Example / #### Algorithm / Steps /
#### Key points - each section ONLY if the passages contain it (John: a missing section is LEFT OUT).
New heading rule in check_notes_answer (closes a hole: heading text was never checked):
  - the title heading (first heading, before any text, not a section name) is always '### <syllabus topic>';
  - section headings: only the 5 names (case, '###'/'####', trailing ':' tolerated) -> written as '#### Name';
  - any other heading -> dropped ('heading not allowed') -> named in the re-ask like a lost line;
  - a section with no kept text under it is removed; order / repeats are LOGGED (not a re-ask - measure first).
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import pytest

import source_context as sc
import source_pdf as spdf
import source_prompts as sp
from test_source_prompts import _topic

MED = "Minimum Edit Distance"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, MED)


def _s(plan, n=4):
    import re
    out = []
    for p in plan.passages:
        for s in re.split(r"(?<=[.?!])\s+", p.text.strip()):
            if len(s.split()) >= 6:
                out.append((s, p.pid))
    assert len(out) >= n
    return out[:n]


def _layout(plan, *sections):
    s = _s(plan, 5)
    parts = [f"### {plan.topic}"]
    for i, name in enumerate(sections):
        text, pid = s[i % len(s)]
        body = f"- {text} [{pid}]" if name == "Key points" else f"{text} [{pid}]"
        parts += [f"#### {name}", body]
    return "\n\n".join(parts) + "\n"


# ---------- prompt ----------

def test_prompt_asks_for_the_five_sections_in_order_and_to_leave_missing_ones_out(ctx, plan):
    p = sp.build_notes_prompt(ctx, plan, "S", "U")
    pos = [p.index(f"#### {name}") for name in sp.SECTIONS]
    assert pos == sorted(pos) and sp.SECTIONS == ("Definition", "Explanation", "Example", "Algorithm / Steps",
                                                   "Key points")
    assert "Leave a section OUT if the passages do not contain it" in p
    assert "never write a definition, example or algorithm that is not in the passages" in p
    assert "Use no other headings" in p
    assert f'Start with "### {plan.topic}"' in p                     # kept
    assert "Only cite ids given below" in p and "Do not add facts" in p   # every old rule still there


# ---------- heading rule ----------

def test_full_layout_is_kept_exactly_and_sections_are_listed(plan):
    ans = _layout(plan, *sp.SECTIONS)
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok and chk.dropped == [] and chk.markdown.strip() == ans.strip()
    assert chk.sections == list(sp.SECTIONS) and chk.layout == []


def test_section_heading_spelling_is_tolerated_and_written_the_one_way(plan):
    s = _s(plan, 2)
    ans = (f"### {plan.topic}\n\n### definition:\n\n{s[0][0]} [{s[0][1]}]\n\n"
           f"####   KEY POINTS  \n\n- {s[1][0]} [{s[1][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert "#### Definition\n" in chk.markdown and "#### Key points\n" in chk.markdown
    assert chk.sections == ["Definition", "Key points"] and chk.dropped == []


def test_other_heading_is_dropped_and_named(plan):
    s = _s(plan, 2)
    ans = (f"### {plan.topic}\n\n#### Explanation\n\n{s[0][0]} [{s[0][1]}]\n\n"
           f"#### Zebrafish algorithm invented here\n\n{s[1][0]} [{s[1][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert "Zebrafish" not in chk.markdown
    assert [d.reason for d in chk.dropped] == ["heading not allowed"]
    assert s[1][0][:30] in chk.markdown                                 # the tagged text itself is kept


def test_title_heading_is_always_the_syllabus_topic(plan):
    s = _s(plan, 1)
    ans = f"## Overview Zebrafish\n\n#### Explanation\n\n{s[0][0]} [{s[0][1]}]\n"
    chk = sp.check_notes_answer(ans, plan)
    assert chk.markdown.startswith(f"### {plan.topic}\n") and "Zebrafish" not in chk.markdown
    assert chk.dropped == [] and "title replaced by the syllabus topic" in chk.layout


def test_empty_section_is_removed(plan):
    s = _s(plan, 1)
    ans = (f"### {plan.topic}\n\n#### Definition\n\nA made-up definition without a tag.\n\n"
           f"#### Explanation\n\n{s[0][0]} [{s[0][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert "#### Definition" not in chk.markdown and "#### Explanation" in chk.markdown
    assert chk.sections == ["Explanation"] and "empty section removed: Definition" in chk.layout
    assert [d.reason for d in chk.dropped] == ["no citation"]           # the lost line is still reported


def test_out_of_order_and_repeated_sections_are_logged_not_dropped(plan):
    ans = _layout(plan, "Example", "Definition", "Example")
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok and chk.dropped == [] and chk.sections == ["Example", "Definition", "Example"]
    assert "sections out of order" in chk.layout and "section repeated: Example" in chk.layout


def test_reask_names_the_heading_that_was_not_allowed(ctx, plan):
    s = _s(plan, 2)
    first = (f"### {plan.topic}\n\n#### Explanation\n\n{s[0][0]} [{s[0][1]}]\n\n#### My Own Heading\n\n"
             f"{s[1][0]} [{s[1][1]}]\n")
    prompts = []

    def ask(p):
        prompts.append(p)
        return first if len(prompts) == 1 else _layout(plan, "Explanation")
    res = sp.notes_for_topic(ctx, plan, "S", "U", ask=ask)
    assert res.reask_reason == "lost lines" and len(prompts) == 2
    assert "headings that are not allowed" in prompts[1] and "My Own Heading" in prompts[1]
    assert "had no valid citation" not in prompts[1]                    # it was not a citation problem


def test_GUARD_old_style_answer_still_passes_unchanged(plan):
    """Approved rows written before BL-038 (only '### topic' + tagged text) must stay valid (Q2 re-check)."""
    s = _s(plan, 2)
    ans = f"### {plan.topic}\n\n{s[0][0]} [{s[0][1]}]\n\n- {s[1][0]} [{s[1][1]}]\n"
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok and chk.dropped == [] and chk.markdown.strip() == ans.strip()


def test_GUARD_heading_is_not_a_citation_problem_for_lead_ins(plan):
    s = _s(plan, 2)
    ans = f"### {plan.topic}\n\nThe steps are:\n- {s[0][0]} [{s[0][1]}]\n- {s[1][0]} [{s[1][1]}]\n"
    chk = sp.check_notes_answer(ans, plan)
    assert chk.kept_leadins == ["The steps are:"] and chk.dropped == []


# ---------- check log + PDF ----------

def test_check_log_carries_sections_and_layout(ctx, plan):
    import source_app
    ans = _layout(plan, "Example", "Explanation")
    res = sp.notes_for_topic(ctx, plan, "S", "U", ask=lambda p: ans)
    log = source_app._writer_log(res)
    assert log["sections"] == ["Example", "Explanation"] and log["layout"] == ["sections out of order"]


def test_pdf_prints_the_section_headings_and_hides_tags(ctx, plan):
    from io import BytesIO
    from pypdf import PdfReader
    import source_runner as sr
    ans = _layout(plan, *sp.SECTIONS)
    res = sp.notes_for_topic(ctx, plan, "S", "U", ask=lambda p: ans)
    run = sr.UnitNotesRun(source=ctx.source, topics=[res], gaps=[], needs_review=[], failed=[],
                          prompt_version="sp:x")
    buf = spdf.build_notes_pdf(run, "Natural Language Processing", "AL3501", "AI&DS", "Introduction", "R-2021")
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(buf.getvalue())).pages)
    for name in ("Definition", "Explanation", "Example", "Algorithm / Steps", "Key points"):
        assert name in text
    body = spdf.notes_body_markdown(run)
    assert body.startswith(f"### {plan.topic}") and "#### Definition" in body and "[P" not in body
