"""P2 of the source-strict app (APP_SOURCE_STRICT_DESIGN.md sections 2 R5/R6, 3 steps 4-5, 4).

Tests for App/source_prompts.py: prompt building + code checks on the model's
answer. Offline: Groq is replaced by fake answers (good / invented / uncited /
wrong citation). Real AL3501 Unit I passages from source_context (P1).
"""
import re

import pytest

import source_context as sc
import source_prompts as sp

SUBJECT = "Natural Language Processing"
UNIT_TITLE = "Introduction"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def _topic(ctx, name):
    return next(t for t in ctx.topics if t.topic == name)


def _sentences(text, n):
    """First n real sentences of a passage (copied text = no new terms)."""
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    out = [p for p in parts if len(p.split()) >= 6][:n]
    assert len(out) == n
    return out


def _good_answer(plan):
    p1 = plan.passages[0]
    s = _sentences(p1.text, 2)
    return (f"### {plan.topic}\n\n{s[0]} [{p1.pid}]\n\n- {s[1]} [{p1.pid}]\n")


# ---------- prompt (R5, R6, budget) ----------

def test_prompt_holds_exactly_the_chosen_passages(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    prompt = sp.build_notes_prompt(ctx, plan, SUBJECT, UNIT_TITLE)
    sent = {p.pid for p in plan.passages}
    assert set(re.findall(r"\[(P\d+)\]", prompt)) == sent
    for p in plan.passages:
        assert p.text in prompt
    assert plan.topic in prompt


def test_prompt_states_the_rules(ctx):
    prompt = sp.build_notes_prompt(ctx, _topic(ctx, "Regular Expressions"), SUBJECT, UNIT_TITLE).lower()
    for must in ("only", "do not add", "cite", "[p"):
        assert must in prompt


def test_every_covered_topic_fits_the_groq_free_tier(ctx):
    for plan in ctx.topics:
        if plan.status in ("FOUND", "PARTIAL"):
            prompt = sp.build_notes_prompt(ctx, plan, SUBJECT, UNIT_TITLE)
            assert sp.estimate_tokens(prompt) + sp.NOTES_MAX_COMPLETION_TOKENS <= sp.GROQ_TOKENS_PER_MINUTE, plan.topic


def test_missing_topic_gets_no_prompt(ctx):
    with pytest.raises(ValueError):
        sp.build_notes_prompt(ctx, _topic(ctx, "Origins and challenges of NLP"), SUBJECT, UNIT_TITLE)


# ---------- answer checks ----------

def test_good_answer_passes_untouched(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    ans = _good_answer(plan)
    chk = sp.check_notes_answer(ans, plan)
    assert chk.ok
    assert chk.dropped == [] and chk.new_terms == []
    assert chk.markdown.strip() == ans.strip()


def test_uncited_paragraph_is_dropped(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    ans = _good_answer(plan) + "\nThis sentence has no citation at all.\n"
    chk = sp.check_notes_answer(ans, plan)
    assert "no citation at all" not in chk.markdown
    assert any("no citation" in d.reason for d in chk.dropped)


def test_citation_to_a_passage_not_sent_is_dropped(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    sent = {p.pid for p in plan.passages}
    other = next(p.pid for p in ctx.passages if p.pid not in sent)   # real passage, but not sent
    ans = _good_answer(plan) + f"\nAlpha claim here. [{other}]\n\nBeta claim here. [P999]\n"
    chk = sp.check_notes_answer(ans, plan)
    assert "Alpha claim" not in chk.markdown and "Beta claim" not in chk.markdown
    assert len([d for d in chk.dropped if "not sent" in d.reason]) == 2


def test_citation_formats_accepted(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    a, b = plan.passages[0].pid, plan.passages[1].pid
    s = _sentences(plan.passages[0].text, 1)[0]
    for tag in (f"[{a}]", f"[{a}, {b}]", f"[{a}][{b}]", f"[{a},{b}]"):
        chk = sp.check_notes_answer(f"{s} {tag}\n", plan)
        assert chk.dropped == [], tag


def test_invented_names_numbers_acronyms_are_flagged(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    invented = ["Wagner", "Fischer", "1974", "BERT"]
    low = ctx.source.text.lower()
    for w in invented:                         # keep the test honest
        assert w.lower() not in low
    ans = _good_answer(plan) + (f"\nThe algorithm was published by Wagner and Fischer in 1974, "
                                f"long before BERT. [{plan.passages[0].pid}]\n")
    chk = sp.check_notes_answer(ans, plan)
    assert set(invented) <= set(chk.new_terms)
    assert not chk.ok                          # 4 new terms > NEW_TERM_LIMIT


def test_sentence_start_words_and_headings_are_not_flagged(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    pid = plan.passages[0].pid
    ans = f"## Overview Zebrafish\n\nConsider the distance between words. [{pid}]\n"
    chk = sp.check_notes_answer(ans, plan)
    assert chk.new_terms == []                 # 'Consider' starts a sentence; heading not checked


def test_everything_dropped_is_not_ok(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    chk = sp.check_notes_answer("Nothing cited here.\n\n- Nor here.\n", plan)
    assert not chk.ok and chk.markdown.strip() == ""


# ---------- one topic end to end, fake Groq ----------

def test_good_first_answer_one_call(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or _good_answer(plan))
    assert res.status == "ok" and len(calls) == 1


def test_invented_then_good_reasks_once_with_feedback(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    bad = _good_answer(plan) + f"\nIt was shown by Wagner and Fischer, 1974, BERT, GPT. [{plan.passages[0].pid}]\n"
    answers = iter([bad, _good_answer(plan)])
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or next(answers))
    assert res.status == "ok" and len(calls) == 2
    assert "Wagner" in calls[1] and "1974" in calls[1]       # the re-ask names the flagged terms


def test_invented_three_times_needs_review_no_fourth_call(ctx):   # BL-035: was 'twice ... no third call' 
    plan = _topic(ctx, "Minimum Edit Distance")
    bad = _good_answer(plan) + f"\nIt was shown by Wagner and Fischer, 1974, BERT, GPT. [{plan.passages[0].pid}]\n"
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or bad)
    assert res.status == "needs_review" and len(calls) == 3                  # BL-035: 2 re-asks (was 1)


def test_all_uncited_twice_fails_with_no_content(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: "No citations anywhere.\n")
    assert res.status == "failed" and res.markdown.strip() == ""


def test_missing_topic_makes_no_call(ctx):
    plan = _topic(ctx, "Origins and challenges of NLP")
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or "x")
    assert res.status == "gap" and calls == []
