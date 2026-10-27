"""BL-028 step 2: the verifier (source_verify) wired into source_runner, source_app and the screen.
Offline: fake writer + fake verifier, fake clock. All tests FAIL on source_runner d5218d6eea /
source_app f3cb65279c / academicsgen_app 5ecbea48d5."""
import io
import json
import re

import pytest
from pypdf import PdfReader

import source_app as sa
import source_runner as sr
import source_verify as sv
from test_source_runner import SUBJECT, FakeClock, _good_notes, _good_qa, _window_ok
import source_prompts as sp
from test_app_ui import _all_text, _app, _undo_driver_patches  # noqa: F401 (autouse: undo UI-test patches)


def fake_verify(reject=()):
    """Verifier stand-in: every 'S<n>: text' supported unless it contains a reject marker."""
    calls = []

    def ask(prompt, rf):
        calls.append(prompt)
        assert rf == sv.verify_response_format()
        sents = re.findall(r"^(S\d+): (.+)$", prompt.split("SENTENCES", 1)[1], re.M)
        return json.dumps({"verdicts": [{"id": i, "supported": not any(r in t for r in reject),
                                         "reason": ""} for i, t in sents]})
    ask.calls = calls
    return ask


def _notes_with_extra(prompt):
    """Writer adds one invented sentence to the first paragraph of every topic."""
    good = _good_notes(prompt)
    first, rest = good.split("\n\n", 1)
    return first.replace(" [", " INVENTED claim about thresholds here. [", 1) + "\n\n" + rest


def _run(ask=_good_notes, verify=None, events=None, unit="I"):
    clock, log = FakeClock(), []

    def w(p):
        log.append((clock.now(), p, "w"))
        return ask(p)

    def v(p, rf):
        log.append((clock.now(), p, "v"))
        return verify(p, rf)
    res = sr.run_unit_notes("AL3501", unit, SUBJECT, "Introduction", ask=w,
                            verify_ask=(v if verify else None), clock=clock.now, sleep=clock.sleep,
                            progress=(events.append if events is not None else None))
    return res, log


# ---------- runner: notes ----------

def test_every_ok_topic_is_verified_one_call_each():
    verify = fake_verify()
    res, log = _run(verify=verify)
    assert len(verify.calls) == 7 and res.calls == 14
    assert set(res.verify) == {t.topic for t in res.topics if t.status == "ok"}


def test_invented_sentence_removed_from_the_topic():
    """1 of 3 sentences removed = 33% > 30% -> needs review; so use a verifier that rejects only the extra."""
    res, _ = _run(ask=_notes_with_extra, verify=fake_verify(reject=("INVENTED",)))
    for t in res.topics:
        if t.status in ("ok", "needs_review") and t.markdown:
            assert "INVENTED" not in t.markdown
    assert all(len(v.removed) == 1 for v in res.verify.values())


def test_too_much_removed_makes_the_topic_needs_review():
    res, _ = _run(verify=fake_verify(reject=(" ",)))                # every sentence rejected
    assert res.needs_review == [t.topic for t in res.topics if t.status != "gap"]
    assert all(t.status != "ok" for t in res.topics)


def test_gaps_are_not_verified():
    verify = fake_verify()
    res, _ = _run(verify=verify)
    assert "Origins and challenges of NLP" in res.gaps
    assert not any('"Origins and challenges of NLP"' in p for p in verify.calls)


def test_verifier_calls_are_paced_with_their_own_budget():
    res, log = _run(verify=fake_verify())
    _window_ok([(t, p) for t, p, _k in log],
               lambda p: sp.estimate_tokens(p) + (sv.VERIFY_MAX_COMPLETION_TOKENS if "SENTENCES\n" in p
                                                  else sp.NOTES_MAX_COMPLETION_TOKENS))


def test_verifier_error_aborts_the_unit():
    def boom(p, rf):
        raise RuntimeError("HTTP 429")
    with pytest.raises(sr.RunAborted):
        _run(verify=boom)


def test_progress_reports_checking():
    events = []
    _run(verify=fake_verify(), events=events)
    assert "checking" in {e["stage"] for e in events}


def test_prompt_version_includes_the_verifier():
    # BL-029: only a VERIFIED run carries +sv (the app no longer verifies; see test_bl029_step_b.py)
    assert re.fullmatch(r"sp:[0-9a-f]{10}\+sq:[0-9a-f]{10}(?:\+gs:[0-9a-f]{10})?\+sv:[0-9a-f]{10}",   # BL-035: +gs
                        sr.prompt_version(verified=True))


# ---------- runner: QA ----------

def test_qa_unsupported_items_dropped_and_verified():
    verify = fake_verify(reject=("Explain Minimum Edit Distance with apple",))
    clock = FakeClock()
    res = sr.run_unit_qa("AL3501", "I", SUBJECT, "Introduction", "R-2021", ask=_good_qa,
                         verify_ask=verify, clock=clock.now, sleep=clock.sleep)
    assert len(verify.calls) == 7
    med = next(t for t in res.topics if t.topic == "Minimum Edit Distance")
    qs = [i["question"] for k in med.items for i in med.items[k]]
    assert not any(q.startswith("Explain Minimum Edit Distance with apple") for q in qs)
    assert "Minimum Edit Distance" in res.verify


# ---------- source_app + screen ----------
# BL-029 step b (2026-09-28): the app no longer verifies. The old 'always verifies' and 'two calls per
# topic' tests were replaced by test_bl029_step_b.py (no verifier calls, one call per topic).
