"""BL-033: gpt-oss sometimes writes its passage tags as 【P22】 (U+3010/U+3011). Data run #8 (2026-09-28,
check_AL3501_U1_Notes_20260928-183033.json): English Morphology, Transducers and Spelling Errors FAILED
because every paragraph ended with 【Pn】 and _CITE_RE read only [Pn]. The checks are NOT loosened: the tag is
rewritten to [Pn] first, then every existing check runs as before.
Checked 2026-09-28: 5 tests FAIL on source_prompts b281cfdf74; 3 pass on both: the 2 GUARDs and
test_pdf_text_has_no_tags_at_all (on the old code it passes only because every paragraph is dropped)."""
import json
import re

import pytest

import source_context as sc
import source_pdf as spdf
import source_prompts as sp
import source_qa as sq
from test_source_prompts import SUBJECT, UNIT_TITLE, _good_answer, _topic
from test_source_qa import REG, _good_json, _req


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def _wide(md):
    return re.sub(r"\[(P\d+(?:, P\d+)*)\]", "【\\1】", md)


def test_normalize_forms_seen_and_close_relatives():
    assert sp.normalize_tags("linear time【P22】.") == "linear time[P22]."
    assert sp.normalize_tags("surface spelling【P25】【P26】.") == "surface spelling[P25][P26]."
    assert sp.normalize_tags("x【P1, P2】") == "x[P1, P2]"
    assert sp.normalize_tags("x【P1，P2】") == "x[P1, P2]"
    assert sp.normalize_tags("x［P3］") == "x[P3]"


def test_normalize_leaves_everything_else_alone():
    """ASCII tags and non-tag brackets are untouched."""
    for t in ["a [P4] b", "【note】", "[P1–P3]", "plain"]:
        assert sp.normalize_tags(t) == t


def test_wide_tags_are_read_and_the_topic_is_ok(ctx):
    plan = _topic(ctx, "English Morphology")
    chk = sp.check_notes_answer(_wide(_good_answer(plan)), plan)
    assert chk.ok and chk.kept_blocks == 2 and not chk.dropped
    assert "【" not in chk.markdown and f"[{plan.passages[0].pid}]" in chk.markdown


def test_wide_tag_to_a_passage_not_sent_is_still_dropped(ctx):
    plan = _topic(ctx, "English Morphology")
    ans = _good_answer(plan) + "\nAn extra claim here.【P999】\n"
    chk = sp.check_notes_answer(ans, plan)
    assert [d.reason for d in chk.dropped] == ["cites passage not sent: P999"]


def test_notes_for_topic_with_wide_tags_needs_one_call(ctx):
    plan = _topic(ctx, "Transducers for lexicon and rules, Tokenization")
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or _wide(_good_answer(plan)))
    assert res.status == "ok" and len(calls) == 1


def test_pdf_text_has_no_tags_at_all(ctx):
    plan = _topic(ctx, "English Morphology")
    md = sp.check_notes_answer(_wide(_good_answer(plan)), plan).markdown
    out = spdf.strip_citations(md)
    assert "【" not in out and "[P" not in out


def test_uncited_text_is_still_dropped(ctx):
    """GUARD (passes before and after): no tag at all -> dropped, as before."""
    plan = _topic(ctx, "English Morphology")
    chk = sp.check_notes_answer(_good_answer(plan) + "\nTwo main scenarios are considered:\n", plan)
    assert [d.reason for d in chk.dropped] == ["no citation"]


def test_qa_uses_a_sources_list_not_tags(ctx):
    """GUARD (passes before and after): QA cites via the JSON 'sources' list, so BL-033 needs no QA change."""
    r = _req(sq.plan_topic_requests(ctx, REG), "Minimum Edit Distance")
    assert sq.check_qa_answer(_good_json(r), r).ok
