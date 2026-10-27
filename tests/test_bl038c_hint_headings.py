"""BL-038 step c (live 29 Sep 16:08, English Morphology): the model copied the prompt's hint into its headings
('#### Definition - what it is'); the BL-038 heading rule dropped them as 'heading not allowed', so the topic lost
its Definition / Explanation titles. Fix: (1) the prompt puts the section names on their own lines and the hints
in a separate list, so there is nothing to copy onto a heading line; (2) a heading that is a section name followed
by EXACTLY that section's hint (any dash / ':' / brackets) is read as that section and written as '#### Name'.
Any other extra text on a heading is still 'heading not allowed' (headings stay closed).
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import pytest

import source_context as sc
import source_prompts as sp
from test_source_prompts import _topic

MED = "Minimum Edit Distance"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, MED)


def _s(plan, n):
    import re
    out = []
    for p in plan.passages:
        for s in re.split(r"(?<=[.?!])\s+", p.text.strip()):
            if len(s.split()) >= 6:
                out.append((s, p.pid))
    return out[:n]


def test_prompt_heading_lines_are_the_bare_section_names(ctx, plan):
    p = sp.build_notes_prompt(ctx, plan, "S", "U")
    lines = [l.strip() for l in p.splitlines() if l.strip().startswith("####")]
    assert lines == [f"#### {n}" for n in sp.SECTIONS]                 # nothing after the name to copy
    for n, h in zip(sp.SECTIONS, sp.SECTION_HINTS):
        assert f"{n}: {h}" in p                                         # hints still given, on their own lines
    assert "Write each heading exactly as shown, with nothing after it" in p


@pytest.mark.parametrize("line,name", [
    ("#### Definition - what it is", "Definition"),
    ("#### Explanation – how or why it works", "Explanation"),
    ("### Example: only a concrete example that is in the passages", "Example"),
    ("#### Key points (2 to 5 bullets that repeat only what is written above)", "Key points"),
    ("#### Algorithm / Steps — numbered steps, procedure or formula lines, only if the passages give them",
     "Algorithm / Steps")])
def test_heading_with_the_exact_copied_hint_is_that_section(line, name):
    assert sp.section_name(line) == name


@pytest.mark.parametrize("line", ["#### Definition - of a regular language", "#### Explanation: Zebrafish",
                                  "#### Example - what it is"])        # someone else's hint is not allowed either
def test_GUARD_heading_with_other_extra_text_stays_not_allowed(line):
    assert sp.section_name(line) is None


def test_live_morphology_case_keeps_its_section_titles(plan):
    s = _s(plan, 2)
    ans = (f"### {plan.topic}\n\n#### Definition - what it is\n\n{s[0][0]} [{s[0][1]}]\n\n"
           f"#### Explanation - how or why it works\n\n{s[1][0]} [{s[1][1]}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert chk.dropped == [] and chk.sections == ["Definition", "Explanation"]
    assert "#### Definition\n" in chk.markdown and "what it is" not in chk.markdown


def test_GUARD_bare_section_names_unchanged(plan):
    for n in sp.SECTIONS:
        assert sp.section_name(f"#### {n}") == n
