"""BL-028 step 1: App/source_verify.py — a second Groq call checks every sentence against the passages.
Design: 'Python scripts ...\\BL028_DESIGN.md' (John: option C, 30% rule, 2026-09-27).
Offline: fake verifier answers, real AL3501 Unit I passages. All tests FAIL before the module exists."""
import json

import pytest

import source_context as sc
import source_prompts as sp
import source_verify as sv
from test_source_prompts import _topic

THRESHOLD = ("If the smallest distance exceeds a predefined threshold, the word may be left unchanged, "
             "as it is considered too far from any known word to be a reliable correction.")


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def _notes(plan):
    """Notes in the writer's format (live run #2 style), 6 sentences, one invented."""
    p = plan.passages[0].pid
    return (f"### {plan.topic}\n\n"
            f"Spelling correction is needed when the text is noisy. It contains misspelled words, e.g., "
            f"behaf instead of behalf. [{p}]\n\n"
            f"- Compute the edit distance to each word. [{p}]\n"
            f"- Select the word with the smallest distance. [{p}]\n\n"
            f"The distance counts insertions, deletions and substitutions. {THRESHOLD} [{p}]\n")


def _answer(verdicts):
    return json.dumps({"verdicts": [{"id": i, "supported": ok, "reason": "" if ok else "not in passages"}
                                    for i, ok in verdicts]})


def _all(sents, bad=()):
    return _answer([(s.sid, s.sid not in bad) for s in sents])


# ---------- sentences ----------

def test_split_sentences_keeps_every_word_and_skips_headings(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    sents = sv.split_sentences(_notes(plan))
    assert [s.sid for s in sents] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert sents[1].text.startswith("It contains misspelled words, e.g., behaf")     # 'e.g.,' not a break
    assert all("[P" not in s.text and "###" not in s.text for s in sents)            # tags/headings not sent
    assert sents[5].text == THRESHOLD


def test_abbreviations_do_not_split():
    sents = sv.split_sentences("FSAs recognise regular languages, i.e., those of regular expressions. "
                               "Determinism vs. nondeterminism matters. [P1]\n")
    assert len(sents) == 2


# ---------- prompt + schema ----------

def test_prompt_has_the_passages_and_numbered_sentences_and_the_rule(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    sents = sv.split_sentences(_notes(plan))
    prompt = sv.build_verify_prompt(ctx, plan, sents).lower()
    for p in plan.passages:
        assert p.text.lower() in prompt
    assert "s6: " + THRESHOLD.lower() in prompt
    for must in ("supported", "rephras", "not supported", "if unsure"):
        assert must in prompt


def test_schema_is_strict_json():
    rf = sv.verify_response_format()
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    item = rf["json_schema"]["schema"]["properties"]["verdicts"]["items"]
    assert set(item["required"]) == {"id", "supported", "reason"}


def test_every_covered_topic_fits_the_groq_minute(ctx):
    for plan in ctx.topics:
        if plan.status not in ("FOUND", "PARTIAL") or not plan.passages:
            continue
        long_notes = " ".join(f"This is sentence number {i} of long study notes about the topic." for i in range(60))
        prompt = sv.build_verify_prompt(ctx, plan, sv.split_sentences(long_notes + " [P1]"))
        assert sp.estimate_tokens(prompt) + sv.VERIFY_MAX_COMPLETION_TOKENS <= sp.GROQ_TOKENS_PER_MINUTE


# ---------- notes ----------

def test_all_supported_keeps_the_notes_unchanged_one_call(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    calls = []
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: calls.append(rf) or _all(sents))
    assert res.status == "ok" and res.markdown == md and res.removed == [] and len(calls) == 1
    assert calls[0] == sv.verify_response_format()


def test_invented_sentence_is_removed_rest_kept(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: _all(sents, bad={"S6"}))
    assert res.status == "ok" and res.removed == [THRESHOLD] and res.total == 6
    assert "threshold" not in res.markdown
    assert "The distance counts insertions, deletions and substitutions." in res.markdown
    assert f"[{plan.passages[0].pid}]" in res.markdown.split("substitutions.")[1][:12]   # citation kept
    assert res.markdown.startswith(f"### {plan.topic}")


def test_missing_verdict_counts_as_unsupported(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    ans = _answer([(s.sid, True) for s in sents if s.sid != "S3"] + [("S99", True)])
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: ans)
    assert len(res.removed) == 1 and "Compute the edit distance" in res.removed[0]
    assert "Compute the edit distance" not in res.markdown and "- Select the word" in res.markdown


def test_more_than_30_percent_removed_is_needs_review(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: _all(sents, bad={"S1", "S3", "S6"}))  # 3/6 = 50%
    assert res.status == "needs_review"


def test_exactly_30_percent_is_still_ok():
    md = "".join(f"Sentence number {i} is about edit distance. " for i in range(10)) + "[P1]\n"
    sents = sv.split_sentences(md)
    assert len(sents) == 10
    kept, removed = sv.apply_verdicts(md, sents, {s.sid: s.sid not in {"S1", "S2", "S3"} for s in sents})
    assert len(removed) == 3 and sv.decide(len(removed), len(sents)) == "ok"
    assert sv.decide(4, 10) == "needs_review" and sv.decide(1, 1) == "needs_review"


def test_bad_json_reasks_once_then_ok(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    answers = iter(["{not json", _all(sents)])
    calls = []
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: calls.append(p) or next(answers))
    assert res.status == "ok" and len(calls) == 2 and "not valid json" in calls[1].lower()


def test_bad_json_twice_is_needs_review(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    res = sv.verify_notes(ctx, plan, _notes(plan), ask=lambda p, rf: '{"verdicts": "x"}')
    assert res.status == "needs_review" and res.calls == 2


def test_empty_notes_make_no_call(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    calls = []
    res = sv.verify_notes(ctx, plan, f"### {plan.topic}\n", ask=lambda p, rf: calls.append(1))
    assert res.status == "ok" and calls == [] and res.total == 0


# ---------- QA ----------

def _items(plan):
    p = plan.passages[0].pid
    return {"part_a": [{"question": "What is edit distance?", "answer": "The minimum number of edits.",
                        "sources": [p], "topic": plan.topic},
                       {"question": "When is a word left unchanged?", "answer": THRESHOLD,
                        "sources": [p], "topic": plan.topic}],
            "part_b": [{"question": "Explain spelling correction.", "answer": "Compute distances. Pick the smallest.",
                        "sources": [p], "topic": plan.topic}]}


def test_qa_unsupported_item_dropped_others_kept(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    seen = []

    def ask(prompt, rf):
        seen.append(prompt)
        return _answer([("S1", True), ("S2", False), ("S3", True)])
    res = sv.verify_qa(ctx, plan, _items(plan), ask=ask)
    assert [i["question"] for i in res.items["part_a"]] == ["What is edit distance?"]
    assert len(res.items["part_b"]) == 1 and res.removed == ["When is a word left unchanged?"]
    assert "question: when is a word left unchanged?" in seen[0].lower() and THRESHOLD.lower() in seen[0].lower()
    assert res.status == "needs_review"                        # 1 of 3 = 33% > 30%


def test_qa_all_supported_ok(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    res = sv.verify_qa(ctx, plan, _items(plan), ask=lambda p, rf: _answer([("S1", True), ("S2", True), ("S3", True)]))
    assert res.status == "ok" and res.removed == [] and sum(len(v) for v in res.items.values()) == 3
