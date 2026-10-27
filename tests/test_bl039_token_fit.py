"""BL-039 (BL039_DESIGN.md, John 30 Sep: "Room only for too-big topics").
A topic whose passages would push one Groq call over the 8,000/min free budget drops its LEAST relevant
passages (tail of the best-first list) until it fits with REASK_RESERVE free; topics that fit are NOT
changed. A re-ask that does not fit is skipped; a first call that does not fit fails ONLY that topic.
Every test here FAILS on the old code unless labelled GUARD (kept behaviour)."""
import re

import pytest

import source_app as sa
import source_context as sc
import source_prompts as sp
import source_runner as sr
import topic_review as tr
import topic_store as ts

SUBJECT = "Natural Language Processing"


def _p(i, words):
    return sc.Passage(pid=f"P{i}", text=" ".join(["word"] * words), word_count=words)


def _tok(ps):
    return sum(sc.estimate_tokens(p.text) for p in ps)


# ---------- the fit rule (pure) ----------

def test_constants_live_in_source_context_and_prompts_reuse_them():
    assert sc.GROQ_TOKENS_PER_MINUTE == sp.GROQ_TOKENS_PER_MINUTE == 8000
    assert sc.NOTES_MAX_COMPLETION_TOKENS == sp.NOTES_MAX_COMPLETION_TOKENS == 3500
    assert sp.estimate_tokens is sc.estimate_tokens
    assert (sc.TEMPLATE_ALLOWANCE_TOKENS, sc.REASK_RESERVE_TOKENS) == (700, 400)


def test_too_big_drops_tail_until_it_fits_with_reserve():
    ps = [_p(i, 340) for i in range(1, 9)]
    assert _tok(ps) > 3800                          # too big: 3,800 = 8,000 - 3,500 - 700
    kept, dropped = sc.fit_passages(ps)
    assert [p.pid for p in kept] == [f"P{i}" for i in range(1, 8)][:len(kept)]
    assert _tok(kept) <= 3800 - 400
    assert dropped == [p.pid for p in ps[len(kept):]]
    assert _tok(ps[:len(kept) + 1]) > 3400          # it drops no more than needed


def test_fitting_topic_is_not_changed():               # decision: topics that fit stay exactly as they are
    ps = [_p(i, 330) for i in range(1, 9)]
    assert 3400 < _tok(ps) <= 3800                  # fits, even though it is inside the re-ask reserve
    kept, dropped = sc.fit_passages(ps)
    assert kept == ps and dropped == []


def test_at_least_one_passage_is_kept():
    ps = [_p(1, 3000), _p(2, 300)]
    kept, dropped = sc.fit_passages(ps)
    assert [p.pid for p in kept] == ["P1"] and dropped == ["P2"]


def test_topic_plan_has_trimmed_default_empty():
    assert sc.TopicPlan(topic="t", status="FOUND", match_ratio=1.0, passages=[]).trimmed == []


# ---------- real data ----------

@pytest.mark.parametrize("unit", ["I", "II", "III", "IV"])
def test_al3501_plans_unchanged(unit):                 # approved AL3501 topics must keep their passages_sha256
    ctx = sc.build_unit_context("AL3501", unit)
    for plan in ctx.topics:
        if plan.status in ("FOUND", "PARTIAL"):
            assert plan.trimmed == []
            assert plan.passages == sc.select_passages(ctx.passages, plan.topic)


def test_cs3591_unit1_every_first_call_now_fits():
    ctx = sc.build_unit_context("CS3591", "I")
    trimmed = [p for p in ctx.topics if p.trimmed]
    assert any(p.topic == "Data Communication" for p in trimmed)
    for plan in ctx.topics:
        if plan.status not in ("FOUND", "PARTIAL") or not plan.passages:
            continue
        prompt = sp.build_notes_prompt(ctx, plan, "COMPUTER NETWORKS L T P C", "INTRODUCTION AND APPLICATION LAYER 10")
        need = sp.estimate_tokens(prompt) + sp.NOTES_MAX_COMPLETION_TOKENS
        limit = 8000 - (400 if plan.trimmed else 0)
        assert need <= limit, (plan.topic, need)
        assert sp.estimate_tokens(prompt) - _tok(plan.passages) <= sc.TEMPLATE_ALLOWANCE_TOKENS


def test_trimmed_plan_is_what_the_writer_and_the_store_see():
    ctx = sc.build_unit_context("CS3591", "I")
    plan = next(p for p in ctx.topics if p.trimmed)
    prompt = sp.build_notes_prompt(ctx, plan, "CN", "Intro")
    for pid in plan.trimmed:
        assert f"[{pid}]" not in prompt
    again = sc.build_unit_context("CS3591", "I")                    # deterministic -> same fingerprint
    same = next(p for p in again.topics if p.topic == plan.topic)
    assert ts.passages_sha256(same) == ts.passages_sha256(plan)


# ---------- pacer / writer / runner ----------

def test_pacer_raises_call_too_big_which_is_a_value_error():
    pacer = sr.TokenPacer(clock=lambda: 0.0, sleep=lambda s: None)
    with pytest.raises(sp.CallTooBig) as ei:
        pacer.wait_for(8001)
    assert isinstance(ei.value, ValueError)


def _plan_and_ctx():
    ctx = sc.build_unit_context("AL3501", "I")
    plan = next(p for p in ctx.topics if p.status in ("FOUND", "PARTIAL") and p.passages)
    return ctx, plan


def _uncited(prompt):
    return "This line has no passage tag at all and it will be dropped by the checks.\n"


def test_reask_that_does_not_fit_is_skipped_best_answer_kept():
    ctx, plan = _plan_and_ctx()
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return _uncited(prompt)
        raise sp.CallTooBig("one call needs ~8200 tokens, more than the 8000/min budget")
    res = sp.notes_for_topic(ctx, plan, SUBJECT, "Intro", ask=ask)
    assert len(calls) == 2 and res.calls == 1
    assert res.status in ("failed", "needs_review")
    assert "re-ask skipped" in res.note


def test_first_call_too_big_fails_only_that_topic():
    ctx, plan = _plan_and_ctx()

    def ask(prompt):
        raise sp.CallTooBig("one call needs ~9000 tokens, more than the 8000/min budget")
    res = sp.notes_for_topic(ctx, plan, SUBJECT, "Intro", ask=ask)
    assert res.status == "failed" and res.calls == 0 and res.markdown == ""
    assert "too big for Groq's free limit" in res.note


def _good_notes(prompt):
    m = re.search(r"^\[(P\d+)\] (.+)$", prompt, re.M)
    pid, text = m.group(1), m.group(2)
    s = [x for x in re.split(r"(?<=[.?!])\s+", text.strip()) if len(x.split()) >= 6][:2]
    return f"{s[0]} [{pid}]\n\n- {s[1]} [{pid}]\n"


def test_run_continues_after_a_too_big_topic():
    ctx = sc.build_unit_context("AL3501", "I")
    covered = [p.topic for p in ctx.topics if p.status in ("FOUND", "PARTIAL") and p.passages]
    first = covered[0]
    t = [1000.0]
    res = sr.run_unit_notes("AL3501", "I", SUBJECT, "Intro", ask=_good_notes, clock=lambda: t[0],
                            sleep=lambda s: t.__setitem__(0, t[0] + s),
                            topics=covered[:2], avoid={first: ["x " * 12000]})
    by = {r.topic: r for r in res.topics}
    assert by[first].status == "failed" and "too big" in by[first].note
    assert by[covered[1]].status == "ok"


# ---------- logs / admin / message ----------

def test_writer_log_and_summary_carry_trimmed_and_note():
    r = sp.TopicResult("T", "failed", "", trimmed=["P7", "P8"], note="too big for Groq's free limit")
    log = sa._writer_log(r)
    assert log["trimmed"] == ["P7", "P8"] and "too big" in log["note"]
    s = tr.check_summary({"check_data": log})
    assert s["trimmed"] == ["P7", "P8"] and "too big" in s["note"]


def test_topic_result_copies_the_plan_trimmed_list():
    ctx = sc.build_unit_context("CS3591", "I")
    plan = next(p for p in ctx.topics if p.trimmed)
    res = sp.notes_for_topic(ctx, plan, "CN", "Intro", ask=_good_notes)
    assert res.trimmed == plan.trimmed


def test_abort_hint_is_honest():
    too_big = sr.RunAborted("stopped at 'X': one call needs ~8051 tokens")
    too_big.__cause__ = sp.CallTooBig("one call needs ~8051 tokens")
    assert "few minutes" not in sr.abort_hint(too_big) and "too big" in sr.abort_hint(too_big)
    other = sr.RunAborted("stopped at 'X': HTTP 503")
    other.__cause__ = RuntimeError("HTTP 503")
    assert sr.abort_hint(other) == "Try again in a few minutes."        # GUARD: network trouble keeps the old hint


def test_app_uses_the_hint_not_a_fixed_text():
    src = open(sa.__file__.replace("source_app.py", "academicsgen_app.py"), encoding="utf-8").read()
    assert "Try again in a few minutes.\")" not in src
    assert src.count("abort_hint(") >= 2
    assert "left out to fit Groq's free limit" in src
