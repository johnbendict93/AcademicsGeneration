"""BL-037 step a (handoff 7.12 KNOWN GAP, run #13 2026-09-28): after a 'not ok' re-ask, an answer that is ok but LOST
lines got no 'lost lines' re-ask -> orphans in the PDF (Min Edit Distance recurrence bullets, English Morphology
'Linguistic approach'). Fix: if a call is left (max 3 per topic, BL-035), one 'lost lines' re-ask naming them; its
answer is used only if it is ok AND lost fewer lines (never worse). GUARD tests pass on the old code too."""
import pytest

import source_app as sa
import source_context as sc
import source_prompts as sp
from test_source_prompts import SUBJECT, UNIT_TITLE, _good_answer, _topic

BAD = "### x\n\nNo tags at all here.\n"
LOST = "- Deletion: D[i-1][j] + 1\n"
REASON = "not ok, then lost lines"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, "Minimum Edit Distance")


def _run(ctx, plan, answers):
    prompts = []

    def ask(p):
        prompts.append(p)
        return answers[len(prompts) - 1]
    return sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=ask), prompts


def test_ok_with_lost_lines_after_not_ok_is_asked_again(ctx, plan):
    lossy = _good_answer(plan) + LOST
    assert sp.check_notes_answer(lossy, plan).ok and sp.check_notes_answer(lossy, plan).dropped
    res, prompts = _run(ctx, plan, [BAD, lossy, _good_answer(plan)])
    assert len(prompts) == 3 and res.calls == 3 and res.status == "ok"
    assert "Deletion: D[i-1][j] + 1" in prompts[2]              # the lost line is named
    assert res.used == 2 and res.reask_reason == REASON
    assert not res.checks[res.used].dropped


def test_worse_third_answer_keeps_the_second(ctx, plan):
    lossy = _good_answer(plan) + LOST
    res, prompts = _run(ctx, plan, [BAD, lossy, BAD])
    assert len(prompts) == 3 and res.status == "ok" and res.used == 1
    assert res.markdown == sp.check_notes_answer(lossy, plan).markdown


def test_third_not_fewer_lost_lines_keeps_the_second(ctx, plan):
    lossy = _good_answer(plan) + LOST
    other = _good_answer(plan) + "- Insertion: D[i][j-1] + 1\n"
    res, prompts = _run(ctx, plan, [BAD, lossy, other])
    assert len(prompts) == 3 and res.used == 1 and "Insertion" not in res.markdown


def test_check_log_shows_third_try_and_reason(ctx, plan):
    res, _ = _run(ctx, plan, [BAD, _good_answer(plan) + LOST, _good_answer(plan)])
    log = sa._writer_log(res)
    assert log["used_try"] == 3 and log["reask_reason"] == REASON and log["calls"] == 3


def test_no_call_left_means_no_fourth_call(ctx, plan):
    """GUARD (passes before and after): never more than 3 calls per topic."""
    res, prompts = _run(ctx, plan, [BAD, BAD, _good_answer(plan) + LOST])
    assert len(prompts) == 3 and res.status == "ok" and res.used == 2 and res.reask_reason == "not ok"


def test_not_ok_then_clean_answer_stops_at_two(ctx, plan):
    """GUARD (passes before and after): no lost lines -> no extra call."""
    res, prompts = _run(ctx, plan, [BAD, _good_answer(plan)])
    assert len(prompts) == 2 and res.status == "ok" and res.reask_reason == "not ok"
