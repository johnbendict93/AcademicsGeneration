"""BL-033 part 2 (design BL033P2_DESIGN.md ff5899a554): untagged lines. Runs #8/#9 (2026-09-28) lost 8 real
content lines (formula bullets, D[i][j] definition, algorithm steps) inside topics that were 'ok' -> no re-ask ->
orphans in the PDF ('these recurrences'). Fix: (1) prompt rule names formula lines / steps / lines ending ':',
(2) an ok topic that lost lines is re-asked once; the second answer is used only if it is ok AND lost fewer lines,
(3) a short fact-free lead-in ending ':' is kept only when the very next block is a KEPT bullet.
GUARD tests pass on the old code too; they protect kept behaviour."""
import pytest

import source_app as sa
import source_context as sc
import source_pdf as spdf
import source_prompts as sp
from test_source_prompts import SUBJECT, UNIT_TITLE, _good_answer, _sentences, _topic


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def plan(ctx):
    return _topic(ctx, "Minimum Edit Distance")


def _tagged(plan, i=2):
    p1 = plan.passages[0]
    return f"- {_sentences(p1.text, i + 1)[i]} [{p1.pid}]"


LEAD = "The main points are these:"
LOST = "- Deletion: D[i-1][j] + 1"


def _run(ctx, plan, answers):
    prompts = []

    def ask(p):
        prompts.append(p)
        return answers[len(prompts) - 1]
    return sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=ask), prompts


# ---------- 1. prompt rule ----------

def test_prompt_rule_names_formula_lines_steps_and_lead_ins(ctx, plan):
    p = sp.build_notes_prompt(ctx, plan, SUBJECT, UNIT_TITLE)
    assert "formula lines, numbered steps and any line ending with ':'" in p


# ---------- 2. re-ask when an ok topic lost lines ----------

def test_ok_answer_that_lost_a_formula_bullet_is_asked_again(ctx, plan):
    first = _good_answer(plan) + LOST + "\n"
    assert sp.check_notes_answer(first, plan).ok                    # ok, but one line lost
    res, prompts = _run(ctx, plan, [first, _good_answer(plan)])
    assert len(prompts) == 2 and res.calls == 2 and res.status == "ok"
    assert "Deletion: D[i-1][j] + 1" in prompts[1]                   # the lost line is named
    assert res.reask_reason == "lost lines" and res.used == 1
    assert not res.checks[res.used].dropped


def test_second_answer_worse_keeps_the_first(ctx, plan):
    first = _good_answer(plan) + LOST + "\n"
    res, prompts = _run(ctx, plan, [first, "### x\n\nNo tags at all here.\n"])
    assert len(prompts) == 2 and res.status == "ok" and res.used == 0
    assert res.markdown == sp.check_notes_answer(first, plan).markdown


def test_second_answer_not_fewer_lost_lines_keeps_the_first(ctx, plan):
    first = _good_answer(plan) + LOST + "\n"
    second = _good_answer(plan) + "- Insertion: D[i][j-1] + 1\n"
    res, _ = _run(ctx, plan, [first, second])
    assert res.used == 0 and "Deletion" not in res.markdown and "Insertion" not in res.markdown


def test_lost_lines_list_is_capped(ctx, plan):
    first = _good_answer(plan) + "".join(f"- Lost line number {'x' * 200}{i}\n" for i in range(12))
    _res, prompts = _run(ctx, plan, [first, _good_answer(plan)])
    listed = [ln for ln in prompts[1].splitlines() if ln.startswith("  - ")]
    assert len(listed) == 8 and all(len(ln) <= 4 + 80 for ln in listed)


def test_no_lost_lines_means_one_call(ctx, plan):
    """GUARD (passes before and after)."""
    res, prompts = _run(ctx, plan, [_good_answer(plan)])
    assert len(prompts) == 1 and res.status == "ok"


def test_first_not_ok_still_uses_the_second(ctx, plan):
    """GUARD (passes before and after): the old re-ask path is unchanged."""
    res, prompts = _run(ctx, plan, ["### x\n\nNo tags at all here.\n", _good_answer(plan)])
    assert len(prompts) == 2 and res.status == "ok"
    assert res.markdown == sp.check_notes_answer(_good_answer(plan), plan).markdown


# ---------- 3. narrow lead-in keep ----------

def test_lead_in_before_a_kept_bullet_is_kept(plan):
    ans = _good_answer(plan) + "\n" + LEAD + "\n" + _tagged(plan) + "\n"
    chk = sp.check_notes_answer(ans, plan)
    assert LEAD in chk.markdown and chk.kept_leadins == [LEAD] and not chk.dropped and chk.ok
    assert LEAD in spdf.strip_citations(chk.markdown)


def test_kept_lead_in_alone_does_not_trigger_a_reask(ctx, plan):
    ans = _good_answer(plan) + "\n" + LEAD + "\n" + _tagged(plan) + "\n"
    _res, prompts = _run(ctx, plan, [ans])
    assert len(prompts) == 1


@pytest.mark.parametrize("lead", [
    "The 3 main points are these:",                                    # digit
    "These are the main points that the lecture makes about this whole idea here:",   # > 12 words
    "The Zorblax points are these:",                                   # new term
    "The main points are these.",                                      # no ':'  (GUARD: dropped before too)
])
def test_lead_in_failing_a_condition_is_dropped(plan, lead):
    ans = _good_answer(plan) + "\n" + lead + "\n" + _tagged(plan) + "\n"
    chk = sp.check_notes_answer(ans, plan)
    assert lead not in chk.markdown and [d.text for d in chk.dropped] == [lead]


def test_lead_in_whose_bullet_was_dropped_is_dropped(plan):
    chk = sp.check_notes_answer(_good_answer(plan) + "\n" + LEAD + "\n" + LOST + "\n", plan)
    assert LEAD not in chk.markdown and [d.text for d in chk.dropped] == [LEAD, LOST]


def test_lead_in_at_the_end_is_dropped(plan):
    chk = sp.check_notes_answer(_good_answer(plan) + "\n" + LEAD + "\n", plan)
    assert LEAD not in chk.markdown and chk.kept_leadins == []


def test_closing_filler_is_still_dropped(plan):
    """GUARD (passes before and after): run #9 'These characteristics make ...' style filler."""
    filler = "These characteristics make it a fundamental tool for many tasks."
    chk = sp.check_notes_answer(_good_answer(plan) + "\n" + filler + "\n", plan)
    assert [d.text for d in chk.dropped] == [filler]


# ---------- 4. check log ----------

def test_check_log_uses_the_answer_that_was_used(ctx, plan):
    first = _good_answer(plan) + "\n" + LEAD + "\n" + _tagged(plan) + "\n" + LOST + "\n"
    res, _ = _run(ctx, plan, [first, "### x\n\nNo tags at all here.\n"])
    log = sa._writer_log(res)
    assert log["used_try"] == 1 and log["reask_reason"] == "lost lines"
    assert log["kept_leadins"] == [LEAD]
    assert [d["end"] for d in log["dropped"]] == [LOST]              # from the USED answer (first)
    assert "first_try" in log
