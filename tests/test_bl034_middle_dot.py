"""BL-034 (John's decision 2026-09-28, live run #10): U+00B7 '·' (middle dot, used as a multiply sign like
N·M) is plain typography - it adds no fact. Run #10 left Minimum Edit Distance out because the re-ask answer
used '·'. The odd-character check (R5) must still reject every other character that is not in the passages.
GUARD tests pass on the old code too; they protect kept behaviour."""
import pytest

import source_context as sc
import source_prompts as sp
import source_qa as sq
from test_source_prompts import _good_answer, _topic


@pytest.fixture(scope="module")
def plan():
    return _topic(sc.build_unit_context("AL3501", "I"), "Minimum Edit Distance")


def _with(plan, extra):
    p1 = plan.passages[0]
    return _good_answer(plan) + f"- The table has N {extra} M cells [{p1.pid}]\n"


def test_middle_dot_is_plain_typography():
    assert "·" in sp.PLAIN_TYPOGRAPHY


def test_middle_dot_is_not_an_odd_char(plan):
    assert "·" not in sp._passage_chars(plan)            # the passages do NOT contain it
    assert sp._odd_chars("N · M", sp._passage_chars(plan)) == []


def test_notes_answer_with_middle_dot_is_ok(plan):
    chk = sp.check_notes_answer(_with(plan, "·"), plan)
    assert chk.ok and chk.odd_chars == [] and "·" in chk.markdown


def test_qa_uses_the_same_rule():
    assert sq._odd_chars is sp._odd_chars and sq._odd_chars("a · b", set()) == []


@pytest.mark.parametrize("ch", ["§", "�", "∑", "₀"])
def test_other_odd_chars_still_rejected(plan, ch):
    """GUARD (passes before and after): section sign, U+FFFD, n-ary sum, subscript zero stay odd."""
    chk = sp.check_notes_answer(_with(plan, ch), plan)
    assert not chk.ok and chk.odd_chars == [ch]
