"""P3a of the source-strict app: run a whole unit (Notes or QA), topic by topic,
with progress reports and Groq free-tier pacing (APP_SOURCE_STRICT_DESIGN.md
sections 3, 4, 5). Offline: fake Groq + fake clock (no real waiting).
"""
import json
import re

import pytest

import source_context as sc
import source_prompts as sp
import source_qa as sq
import source_runner as sr

SUBJECT = "Natural Language Processing"


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        assert s > 0
        self.slept.append(s)
        self.t += s


def _sentences(text, n):
    return [p for p in re.split(r"(?<=[.?!])\s+", text.strip()) if len(p.split()) >= 6][:n]


def _good_notes(prompt):
    """Answer built from the first passage in the prompt (copied text, cited)."""
    m = re.search(r"^\[(P\d+)\] (.+)$", prompt, re.M)
    pid, text = m.group(1), m.group(2)
    s = _sentences(text, 2)
    return f"{s[0]} [{pid}]\n\n- {s[1]} [{pid}]\n"


_WORDS = "apple bread cloud dance eagle fruit grape house igloo jelly kite lemon mango night".split()


def _good_qa(prompt, rf):
    m = re.search(r"^\[(P\d+)\] (.+)$", prompt, re.M)
    pid, text = m.group(1), m.group(2)
    ans = " ".join(_sentences(text, 2))
    topic = re.search(r'Sub-topic: "(.+?)"', prompt).group(1)
    out, k = {}, 0
    for key in rf["json_schema"]["schema"]["required"]:
        n = int(re.search(rf"- {key}: UP TO (\d+)", prompt).group(1))
        out[key] = []
        for _ in range(n):
            out[key].append({"question": f"Explain {topic} with {_WORDS[k]} {_WORDS[k + 1]}.",
                             "answer": ans, "sources": [pid]})
            k += 2
    return json.dumps(out)


def _run_notes(unit="I", ask=None, clock=None, events=None):
    clock = clock or FakeClock()
    log = []

    def _ask(prompt):
        log.append((clock.now(), prompt))
        return (ask or _good_notes)(prompt)
    res = sr.run_unit_notes("AL3501", unit, SUBJECT, "Introduction", ask=_ask,
                            progress=(events.append if events is not None else None),
                            clock=clock.now, sleep=clock.sleep)
    return res, log, clock


# ---------- refusal ----------

def test_unit_without_source_refused_before_any_call():
    calls, clock = [], FakeClock()
    with pytest.raises(sc.SourceRefused, match="No verified source for Unit V yet"):
        sr.run_unit_notes("AL3501", "V", SUBJECT, "Discourse", ask=lambda p: calls.append(p),
                          clock=clock.now, sleep=clock.sleep)
    assert calls == [] and clock.slept == []


# ---------- notes: whole unit ----------

def test_notes_unit_i_one_call_per_covered_topic_in_syllabus_order():
    events = []
    res, log, _ = _run_notes(events=events)
    assert len(log) == 7
    ctx = sc.build_unit_context("AL3501", "I")
    assert [t.topic for t in res.topics] == [t.topic for t in ctx.topics]
    assert res.gaps == ["Origins and challenges of NLP"]
    assert all(t.status == "ok" for t in res.topics if t.topic not in res.gaps)
    assert res.needs_review == [] and res.failed == []


def test_progress_reported_for_every_topic_including_gaps():
    events = []
    _run_notes(events=events)
    done = [e for e in events if e["stage"] == "done"]
    assert len(done) == 8
    assert [e["index"] for e in done] == list(range(1, 9)) and all(e["total"] == 8 for e in done)
    assert done[0]["topic"] == "Origins and challenges of NLP" and done[0]["status"] == "gap"


def test_result_carries_source_and_prompt_version():
    res, _, _ = _run_notes()
    assert res.source.sha256.startswith("f034c46d67")
    assert res.source.license == "CC BY-SA 4.0"
    assert re.fullmatch(r"sp:[0-9a-f]{10}\+sq:[0-9a-f]{10}(?:\+gs:[0-9a-f]{10})?", res.prompt_version)   # BL-029: no +sv; BL-035: +gs
    assert res.prompt_version == sr.prompt_version()


# ---------- pacing (Groq free tier ~8k tokens/min) ----------

def _window_ok(log, max_tokens_for):
    """Every 60 s window: estimated tokens of the calls started in it <= limit."""
    for t0, _ in log:
        total = sum(max_tokens_for(p) for t, p in log if t0 <= t < t0 + 60)
        assert total <= sr.GROQ_TOKENS_PER_MINUTE, (t0, total)


def test_notes_calls_are_paced_within_the_minute_budget():
    res, log, clock = _run_notes()
    _window_ok(log, lambda p: sp.estimate_tokens(p) + sp.NOTES_MAX_COMPLETION_TOKENS)
    assert clock.slept, "7 big calls cannot fit in one minute - the runner must wait"
    assert log[0][0] == 1000.0            # first call goes at once


def test_retries_are_paced_too():
    first = {"n": 0}

    def ask(p):
        first["n"] += 1
        if first["n"] == 1:
            return _good_notes(p) + "\nIt was shown by Wagner and Fischer, 1974, BERT, GPT. [" + \
                re.search(r"^\[(P\d+)\]", p, re.M).group(1) + "]\n"
        return _good_notes(p)
    res, log, _ = _run_notes(ask=ask)
    assert len(log) == 8                   # 7 topics + 1 re-ask
    _window_ok(log, lambda p: sp.estimate_tokens(p) + sp.NOTES_MAX_COMPLETION_TOKENS)


def test_pacer_refuses_a_call_bigger_than_the_whole_budget():
    clock = FakeClock()
    pacer = sr.TokenPacer(clock=clock.now, sleep=clock.sleep)
    with pytest.raises(ValueError):
        pacer.wait_for(sr.GROQ_TOKENS_PER_MINUTE + 1)


def test_pacer_no_wait_when_budget_allows():
    clock = FakeClock()
    pacer = sr.TokenPacer(clock=clock.now, sleep=clock.sleep)
    pacer.wait_for(3000)
    pacer.wait_for(3000)
    assert clock.slept == []
    pacer.wait_for(3000)                   # 9000 > 8000 -> must wait for the first to age out
    assert clock.slept and clock.now() >= 1000.0 + 60


# ---------- errors ----------

def test_groq_error_aborts_the_unit_nothing_returned():
    events = []

    def ask(p):
        raise RuntimeError("HTTP 429: rate limit")
    with pytest.raises(sr.RunAborted, match="rate limit") as ei:
        _run_notes(ask=ask, events=events)
    assert "Language Modeling" in str(ei.value)          # first covered topic
    assert events[-1]["stage"] == "error"


def test_needs_review_and_failed_topics_are_listed():
    def ask(p):
        if 'Sub-topic: "Regular Expressions"' in p:
            return "No citation here.\n"
        if 'Sub-topic: "Minimum Edit Distance"' in p:
            pid = re.search(r"^\[(P\d+)\]", p, re.M).group(1)
            return _good_notes(p).split("\n")[0].split(" [")[0] + \
                f" It was shown by Wagner and Fischer, 1974, BERT, GPT. [{pid}]\n"
        return _good_notes(p)
    res, _, _ = _run_notes(ask=ask)
    assert res.failed == ["Regular Expressions"]
    assert res.needs_review == ["Minimum Edit Distance"]


# ---------- QA: whole unit ----------

def test_qa_unit_i_full_pattern_paced():
    clock, log = FakeClock(), []

    def ask(p, rf):
        log.append((clock.now(), p))
        return _good_qa(p, rf)
    res = sr.run_unit_qa("AL3501", "I", SUBJECT, "Introduction", "R-2021", ask=ask,
                         clock=clock.now, sleep=clock.sleep)
    assert len(log) == 7
    assert {k: len(v) for k, v in res.unit.parts.items()} == {"part_a": 10, "part_b": 5, "part_c": 1}
    assert res.unit.full_pattern
    assert res.gaps == ["Origins and challenges of NLP"]
    assert res.source.sha256.startswith("f034c46d67") and res.prompt_version == sr.prompt_version()
    _window_ok(log, lambda p: sp.estimate_tokens(p) + sq.QA_MAX_COMPLETION_TOKENS)


def test_qa_r2025_refused_before_any_call():
    calls = []
    with pytest.raises(Exception, match="R-2025"):
        sr.run_unit_qa("AL3501", "I", SUBJECT, "Introduction", "R-2025", ask=lambda p, rf: calls.append(p))
    assert calls == []
