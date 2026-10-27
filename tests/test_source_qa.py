"""P2b of the source-strict app: QA built only from the verified source
(APP_SOURCE_STRICT_DESIGN.md sections 2, 3, 7; decision D2 = option (a)).

Tests for App/source_qa.py. qa_format.py is NOT changed: its pattern counts are
used as a MAXIMUM here. One Groq call per covered sub-topic (free-tier budget).
Offline: Groq is a fake. Real AL3501 passages from source_context (P1).
"""
import dataclasses
import json
import re

import pytest

import qa_format
import source_context as sc
import source_prompts as sp
import source_qa as sq

SUBJECT = "Natural Language Processing"
REG = "R-2021"


@pytest.fixture(scope="module")
def ctx1():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(scope="module")
def ctx2():
    return sc.build_unit_context("AL3501", "II")


def _req(reqs, name):
    return next(r for r in reqs if r.plan.topic == name)


def _sent(text, n):
    parts = [p for p in re.split(r"(?<=[.?!])\s+", text.strip()) if len(p.split()) >= 6]
    return parts[:n]


def _item(plan, i=0, q=None):
    p = plan.passages[i % len(plan.passages)]
    s = _sent(p.text, 2)
    return {"question": q or f"Explain {plan.topic} with {' '.join(_FILLER[3 * i:3 * i + 3])}.",
            "answer": " ".join(s), "sources": [p.pid]}


# distinct lower-case words, so fake questions of one topic are not duplicates
_FILLER = ("apple bread cloud dance eagle fruit grape house igloo jelly kite lemon mango "
           "night ocean piano quilt river stone tiger umbra velvet water yacht zebra").split()


def _good_json(req, qs=None):
    """Answer with exactly what was asked for; answers copied from the source."""
    out, k = {}, 0
    for key, n in req.want.items():
        out[key] = []
        for _ in range(n):
            out[key].append(_item(req.plan, k, (qs or {}).get(k)))
            k += 1
    return json.dumps(out)


# ---------- limits + plan ----------

def test_limits_come_from_qa_format_as_maximum():
    lim = sq.unit_limits(REG)
    pat = {p.key: p.count for p in qa_format.get_pattern(REG)}
    assert {k: v.max_count for k, v in lim.items()} == pat == {"part_a": 10, "part_b": 5, "part_c": 1}


def test_r2025_still_refused(ctx1):
    with pytest.raises(qa_format.UnsupportedRegulationError):
        sq.plan_topic_requests(ctx1, "R-2025")


def test_gap_topics_get_no_request(ctx1):
    names = [r.plan.topic for r in sq.plan_topic_requests(ctx1, REG)]
    assert "Origins and challenges of NLP" not in names
    assert len(names) == 7


def test_thin_topic_gets_two_mark_questions_only(ctx2):
    r = _req(sq.plan_topic_requests(ctx2, REG), "Issues in PoS tagging")
    assert sum(p.word_count for p in r.plan.passages) < sq.PART_B_MIN_WORDS
    assert r.want == {"part_a": sq.PART_A_PER_TOPIC}


def test_part_c_only_from_the_single_biggest_topic(ctx1):
    reqs = sq.plan_topic_requests(ctx1, REG)
    with_c = [r for r in reqs if "part_c" in r.want]
    assert len(with_c) == 1
    biggest = max(reqs, key=lambda r: sum(p.word_count for p in r.plan.passages))
    assert with_c[0].plan.topic == biggest.plan.topic == "Minimum Edit Distance"
    assert sum(p.word_count for p in biggest.plan.passages) >= sq.PART_C_MIN_WORDS


def test_no_part_c_when_no_topic_is_big_enough(ctx1):
    small = [dataclasses.replace(t, passages=t.passages[:2]) for t in ctx1.topics]
    reqs = sq.plan_topic_requests(dataclasses.replace(ctx1, topics=small), REG)
    assert all("part_c" not in r.want for r in reqs)


# ---------- prompt + schema ----------

def test_prompt_has_only_this_topics_passages_and_fits_budget(ctx1):
    for r in sq.plan_topic_requests(ctx1, REG):
        prompt = sq.build_qa_prompt(ctx1, r, SUBJECT, "Introduction", REG)
        assert set(re.findall(r"\[(P\d+)\]", prompt)) == {p.pid for p in r.plan.passages}
        assert sp.estimate_tokens(prompt) + sq.QA_MAX_COMPLETION_TOKENS <= sp.GROQ_TOKENS_PER_MINUTE
        low = prompt.lower()
        assert "only" in low and "sources" in low


def test_schema_is_groq_strict_and_asks_only_wanted_parts(ctx2):
    r = _req(sq.plan_topic_requests(ctx2, REG), "Issues in PoS tagging")
    rf = sq.topic_response_format(r)
    schema = rf["json_schema"]["schema"]
    assert rf["json_schema"]["strict"] is True
    assert set(schema["properties"]) == {"part_a"} and schema["required"] == ["part_a"]
    item = schema["properties"]["part_a"]["items"]
    assert set(item["required"]) == {"question", "answer", "sources"}
    assert item["additionalProperties"] is False


# ---------- answer checks ----------

def test_good_answer_kept_with_topic_and_sources(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    chk = sq.check_qa_answer(_good_json(r), r)
    assert chk.ok and chk.dropped == [] and chk.new_terms == []
    assert {k: len(v) for k, v in chk.items.items()} == r.want
    for items in chk.items.values():
        for it in items:
            assert it["topic"] == "Minimum Edit Distance" and it["sources"]


def test_uncited_and_wrongly_cited_items_dropped(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    data = json.loads(_good_json(r))
    data["part_a"][0]["sources"] = []
    data["part_a"][1]["sources"] = ["P999"]
    chk = sq.check_qa_answer(json.dumps(data), r)
    assert len(chk.items["part_a"]) == 0
    reasons = " | ".join(d.reason for d in chk.dropped)
    assert "no sources" in reasons and "not sent" in reasons


def test_extra_questions_beyond_request_dropped(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    data = json.loads(_good_json(r))
    data["part_a"].append(_item(r.plan, 5, "An extra question?"))
    chk = sq.check_qa_answer(json.dumps(data), r)
    assert len(chk.items["part_a"]) == r.want["part_a"]
    assert any("over the limit" in d.reason for d in chk.dropped)


def test_broken_json_keeps_nothing(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    chk = sq.check_qa_answer("{not json", r)
    assert not chk.ok and all(v == [] for v in chk.items.values())


def test_invented_terms_flagged(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    data = json.loads(_good_json(r))
    data["part_b"][0]["answer"] += " It was shown by Wagner and Fischer in 1974, long before BERT and GPT."
    chk = sq.check_qa_answer(json.dumps(data), r)
    assert {"Wagner", "Fischer", "1974", "BERT"} <= set(chk.new_terms)
    assert not chk.ok


# ---------- one topic end to end (fake Groq) ----------

def test_invented_then_good_reasks_once(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    bad = json.loads(_good_json(r))
    bad["part_b"][0]["answer"] += " It was shown by Wagner and Fischer in 1974, long before BERT and GPT."
    answers = iter([json.dumps(bad), _good_json(r)])
    calls = []
    res = sq.qa_for_topic(ctx1, r, SUBJECT, "Introduction", REG,
                          ask=lambda p, rf: calls.append((p, rf)) or next(answers))
    assert res.status == "ok" and len(calls) == 2 and "Wagner" in calls[1][0]
    assert calls[0][1] == sq.topic_response_format(r)


def test_invented_twice_needs_review(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    bad = json.loads(_good_json(r))
    bad["part_b"][0]["answer"] += " It was shown by Wagner and Fischer in 1974, long before BERT and GPT."
    res = sq.qa_for_topic(ctx1, r, SUBJECT, "Introduction", REG, ask=lambda p, rf: json.dumps(bad))
    assert res.status == "needs_review" and res.calls == 2


def test_nothing_usable_twice_fails(ctx1):
    r = _req(sq.plan_topic_requests(ctx1, REG), "Minimum Edit Distance")
    res = sq.qa_for_topic(ctx1, r, SUBJECT, "Introduction", REG, ask=lambda p, rf: "{bad")
    assert res.status == "failed" and res.calls == 2 and all(v == [] for v in res.items.values())


# ---------- whole unit ----------

def _run_unit(ctx, qs_by_topic=None):
    results = []
    for r in sq.plan_topic_requests(ctx, REG):
        qs = (qs_by_topic or {}).get(r.plan.topic)
        results.append(sq.qa_for_topic(ctx, r, SUBJECT, "Introduction", REG,
                                       ask=lambda p, rf, r=r, qs=qs: _good_json(r, qs)))
    return sq.assemble_unit_qa(results, REG)


def test_unit_i_trims_to_maximum_taking_turns(ctx1):
    unit = _run_unit(ctx1)
    counts = {k: len(v) for k, v in unit.parts.items()}
    assert counts == {"part_a": 10, "part_b": 5, "part_c": 1}
    topics_a = [it["topic"] for it in unit.parts["part_a"]]
    assert len(set(topics_a)) == 7                      # every covered topic keeps >= 1 two-marker
    assert unit.full_pattern and unit.label is None


def test_duplicates_across_topics_removed(ctx1):
    qs = {"Regular Expressions": {0: "What is Minimum Edit Distance?"},
          "Minimum Edit Distance": {0: "Define minimum edit distance."}}
    unit = _run_unit(ctx1, qs)
    norm = [q["question"] for q in unit.parts["part_a"]]
    assert sum("edit distance" in q.lower() and len(q.split()) <= 5 for q in norm) == 1
    assert unit.duplicates_removed >= 1


def test_thin_unit_is_labelled_not_full_pattern(ctx2):
    thin = [t for t in ctx2.topics if t.topic == "Issues in PoS tagging" or not t.passages]
    unit_ctx = dataclasses.replace(ctx2, topics=thin)
    unit = _run_unit(unit_ctx)
    assert {k: len(v) for k, v in unit.parts.items()} == {"part_a": 2, "part_b": 0, "part_c": 0}
    assert not unit.full_pattern
    assert unit.label == sq.NOT_FULL_PATTERN_LABEL
    assert "Not the full exam pattern" in sq.NOT_FULL_PATTERN_LABEL


def test_qa_format_untouched_rules_still_exact():
    # the old app's exact-count check must still reject 9 two-markers
    parts = {p.key: [{"question": f"q{p.key}{i}", "answer": "a"} for i in range(p.count)]
             for p in qa_format.get_pattern(REG)}
    parts["part_a"] = parts["part_a"][:9]
    with pytest.raises(qa_format.QAValidationError):
        qa_format.parse_and_validate_qa(json.dumps(parts), REG)
