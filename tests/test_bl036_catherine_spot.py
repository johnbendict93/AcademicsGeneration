"""BL-036 (John's decision 2026-09-28, live run #12): 2nd skip-list entry for AL3501 Unit I. The transcript says
'Catherine cook k notation' (a garbled name); run #12 printed 'Catherine-Cook notation' as a fact. Only that one
sentence is cut; the rule example right after it stays. Data-only change (sources/_garbled_spots.json)."""
import json

import pytest

import source_context as sc
from test_source_prompts import _topic

SPOT = "Catherine cook"
KEPT = "converted to b if h visited by c and followed by d"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def test_list_has_the_catherine_entry():
    spots = json.loads(sc.garbled_spots_path().read_text(encoding="utf-8"))["spots"]
    assert [e["text"] for e in spots if SPOT in e["text"]] == [
        "This is also called Catherine cook k notation for condensed context as sensitive rules."]


def test_catherine_cook_is_in_no_passage_but_the_rule_example_stays(ctx):
    joined = " ".join(p.text for p in ctx.passages)
    assert SPOT not in joined and KEPT in joined
    assert SPOT in ctx.source.text                                   # the source file itself is untouched (R2)


def test_transducers_topic_does_not_get_it(ctx):
    plan = _topic(ctx, "Transducers for lexicon and rules, Tokenization")
    assert all(SPOT not in p.text for p in plan.passages)


def test_both_spots_are_logged(ctx):
    assert len(ctx.source.skipped_spots) == 2 and any("Catherine" in r for r in ctx.source.skipped_spots)
