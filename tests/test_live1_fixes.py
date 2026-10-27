"""P5 live run #2 fixes (AL3501 Unit I Notes, 2026-09-27; BACKLOG 'live run #2').

(3) a character that is NOT in the sent passages and not plain typography (the live PDF printed
    'placeholder (e.g., -\\ufffd or -e)') -> answer not ok -> ONE re-ask naming it; still there ->
    needs review (left out of the student PDF). Same for QA.
(4) the hidden [Pn] tags left a space before punctuation ('processing .'): gpt-oss puts a
    narrow no-break space (U+202F) / no-break space before the tag -> must go too.
Offline, fake Groq, real AL3501 Unit I passages. All tests FAIL on source_prompts 564e73c3c1 /
source_qa 4940dd51b7 / source_pdf 9b002b2f82, except the 'plain typography is fine' guards."""
import json

import pytest

import source_context as sc
import source_pdf as spdf
import source_prompts as sp
import source_qa as sq
from test_source_prompts import SUBJECT, UNIT_TITLE, _good_answer, _topic
from test_source_qa import REG, _good_json, _req

BAD = "�"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def _with_line(plan, line):
    return _good_answer(plan) + f"\n{line} [{plan.passages[0].pid}]\n"


# ---------- (3) odd characters: notes ----------

def test_replacement_character_makes_the_answer_not_ok(ctx):
    plan = _topic(ctx, "English Morphology")
    chk = sp.check_notes_answer(_with_line(plan, f"A placeholder such as -{BAD} or -e is attached."), plan)
    assert not chk.ok and BAD in chk.odd_chars


def test_symbol_not_in_the_passages_is_flagged(ctx):
    plan = _topic(ctx, "English Morphology")
    assert "ε" not in "".join(p.text for p in plan.passages)
    chk = sp.check_notes_answer(_with_line(plan, "The empty string is written ε here."), plan)
    assert not chk.ok and chk.odd_chars == ["ε"]


def test_plain_typography_is_fine(ctx):
    """Guard (passes before and after): dashes, curly quotes, arrow, times, odd spaces."""
    plan = _topic(ctx, "Minimum Edit Distance")
    line = "Strings “boy” → “boys” — cost n × m… it’s one step – done."
    chk = sp.check_notes_answer(_with_line(plan, line), plan)
    assert chk.ok and not getattr(chk, "odd_chars", [])


def test_odd_character_reasks_once_naming_it_then_ok(ctx):
    plan = _topic(ctx, "English Morphology")
    answers = iter([_with_line(plan, f"A placeholder -{BAD} is attached."), _good_answer(plan)])
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or next(answers))
    assert res.status == "ok" and len(calls) == 2
    assert "characters" in calls[1] and "U+FFFD" in calls[1]


def test_odd_character_twice_is_needs_review(ctx):
    plan = _topic(ctx, "English Morphology")
    bad = _with_line(plan, f"A placeholder -{BAD} is attached.")
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: bad)
    assert res.status == "needs_review" and res.calls == 3                  # BL-035: 2 re-asks (was 1)


# ---------- (3) odd characters: QA ----------

def test_qa_odd_character_flagged_and_reasked(ctx):
    r = _req(sq.plan_topic_requests(ctx, REG), "Minimum Edit Distance")
    bad = json.loads(_good_json(r))
    bad["part_a"][0]["answer"] += f" The marker -{BAD} is used."
    chk = sq.check_qa_answer(json.dumps(bad), r)
    assert not chk.ok and BAD in chk.odd_chars
    answers = iter([json.dumps(bad), _good_json(r)])
    calls = []
    res = sq.qa_for_topic(ctx, r, SUBJECT, "Introduction", REG,
                          ask=lambda p, rf: calls.append(p) or next(answers))
    assert res.status == "ok" and len(calls) == 2 and "U+FFFD" in calls[1]


def test_qa_plain_typography_is_fine(ctx):
    r = _req(sq.plan_topic_requests(ctx, REG), "Minimum Edit Distance")
    good = json.loads(_good_json(r))
    good["part_a"][0]["answer"] += " It costs n × m — “fast”."
    assert sq.check_qa_answer(json.dumps(good), r).ok


# ---------- (4) no space left before punctuation ----------

@pytest.mark.parametrize("space", [" ", " ", " ", " ", "\t"])
def test_tag_with_any_space_before_leaves_no_gap(space):
    assert spdf.strip_citations(f"during processing{space}[P3].") == "during processing."
    assert spdf.strip_citations(f"a{space}[P1, P2]; b{space}[P4][P5].") == "a; b."


def test_tag_on_its_own_line_keeps_the_newline():
    assert spdf.strip_citations("First line. [P1]\n[P2] Second line.") == "First line.\n Second line."


def test_every_allowed_typography_character_is_drawable_by_the_pdf_fonts():
    """The allowed list must never let through a character the PDF prints as a box."""
    from pathlib import Path
    TTFont = pytest.importorskip("fontTools.ttLib").TTFont
    fonts = Path(spdf.__file__).resolve().parent / "fonts"
    for name in ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSans.ttf"):
        cmap = TTFont(str(fonts / name)).getBestCmap()
        missing = [f"U+{ord(c):04X}" for c in sp.PLAIN_TYPOGRAPHY if ord(c) not in cmap]
        assert not missing, (name, missing)


def test_gpt_oss_hyphens_are_fine(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    chk = sp.check_notes_answer(_with_line(plan, "A finite‑state step and a two‐way edit."), plan)
    assert chk.ok
