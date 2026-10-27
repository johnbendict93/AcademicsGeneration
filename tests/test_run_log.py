"""Live run #3 follow-up: save WHY topics were kept / removed / left out (BACKLOG 'live run #3').
Every generation writes a JSON check log (writer checks + every verifier verdict with its sentence and
reason) BEFORE the PDF is built, so a unit that ends with nothing to publish is still logged.
Offline: fake Groq, fake clock. All tests FAIL on source_verify 6d57221d1e / source_app 412f7ac1da /
academicsgen_app eb3d05ca47."""
import json
import re

import pytest

import source_app as sa
import source_verify as sv
from test_app_ui import _all_text, _app, _generate, _undo_driver_patches  # noqa: F401 (autouse: undo UI-test patches)
from test_source_app import UNIT, Clock, fake_groq
from test_source_prompts import _topic
from test_source_verify import THRESHOLD, _all, _notes


@pytest.fixture(scope="module")
def ctx():
    import source_context as sc
    return sc.build_unit_context("AL3501", "I")


# ---------- source_verify keeps every verdict ----------

def test_verify_notes_keeps_every_verdict_with_sentence_and_reason(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: _all(sents, bad={"S6"}))
    assert [v["id"] for v in res.verdicts] == [s.sid for s in sents]
    bad = [v for v in res.verdicts if not v["supported"]]
    assert bad == [{"id": "S6", "sentence": THRESHOLD, "supported": False, "reason": "not in passages"}]


def test_missing_verdict_is_logged_as_no_answer(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    md = _notes(plan)
    sents = sv.split_sentences(md)
    ans = json.dumps({"verdicts": [{"id": s.sid, "supported": True, "reason": ""} for s in sents if s.sid != "S2"]})
    res = sv.verify_notes(ctx, plan, md, ask=lambda p, rf: ans)
    s2 = next(v for v in res.verdicts if v["id"] == "S2")
    assert s2["supported"] is False and s2["reason"] == "no verdict returned"


def test_verify_qa_keeps_every_verdict(ctx):
    plan = _topic(ctx, "Detecting and Correcting Spelling Errors")
    p = plan.passages[0].pid
    items = {"part_a": [{"question": "What is edit distance?", "answer": "The number of edits.", "sources": [p],
                         "topic": plan.topic}]}
    res = sv.verify_qa(ctx, plan, items, ask=lambda pr, rf: json.dumps(
        {"verdicts": [{"id": "S1", "supported": False, "reason": "not stated"}]}))
    assert res.verdicts == [{"id": "S1", "sentence": "Question: What is edit distance? Answer: The number of edits.",
                             "supported": False, "reason": "not stated"}]


# ---------- source_app: report + callback ----------

def _gen(variant, on_run, groq=fake_groq):
    fake_groq.calls = []
    c = Clock()
    return sa.generate_unit_pdf(variant=variant, unit_number=1, groq_chat=groq, clock=c.now, sleep=c.sleep,
                                on_run=on_run, **UNIT)


def test_notes_report_has_every_topic_writer_checks_and_no_verdicts():
    got = []
    _gen("Notes", got.append)
    r = got[0]
    assert r["subject_code"] == "AL3501" and r["unit"] == 1 and r["variant"] == "Notes"
    assert r["prompt_version"] and len(r["source_sha256"]) == 64
    assert len(r["topics"]) == 8
    gap = next(t for t in r["topics"] if t["topic"] == "Origins and challenges of NLP")
    assert gap["status"] == "gap" and gap["verify"] is None
    ok = [t for t in r["topics"] if t["status"] == "ok"]
    assert len(ok) == 7
    for t in ok:
        assert t["writer"]["calls"] == 1 and t["writer"]["new_terms"] == [] and t["writer"]["odd_chars"] == []
        assert t["verify"] is None                                   # BL-029: verifier OFF
    json.dumps(r)                                                    # JSON-safe


def test_qa_report_too():
    got = []
    _gen("QA", got.append)
    assert got[0]["variant"] == "QA" and all(t["verify"] is None for t in got[0]["topics"])   # BL-029
    assert any(t["writer"] for t in got[0]["topics"])


def test_report_is_made_even_when_nothing_can_be_published():
    """Every topic fails the WRITER checks -> all left out -> NothingToPublish, but the log exists.
    (BL-029: was 'every sentence rejected by the verifier'; the verifier is off now.)"""
    def groq(prompt, **kw):
        fake_groq.calls.append(kw)
        return "This sentence cites no passage at all and names Zorblax, an invented term.\n"

    got = []
    with pytest.raises(sa.spdf.NothingToPublish):
        _gen("Notes", got.append, groq=groq)
    assert got and all(t["status"] in ("needs_review", "failed", "gap") for t in got[0]["topics"])


def test_a_broken_logger_never_breaks_generation():
    def boom(report):
        raise OSError("disk full")
    out = _gen("Notes", boom)
    assert out.pdf_bytes.startswith(b"%PDF")


def test_report_holds_only_check_data_no_credentials():
    got = []
    _gen("Notes", got.append)
    r = got[0]
    assert set(r) == {"subject_code", "unit", "variant", "source_sha256", "prompt_version", "calls",
                      "needs_review", "failed", "skipped_spots", "topics"}      # BL-035: + skipped_spots
    text = json.dumps(r).lower()
    for k in ("api_key", "password", "neon_", "bearer", "github_pdf", "authorization"):
        assert k not in text


# ---------- the screen writes the log file ----------

def test_screen_writes_one_log_file_per_generation(tmp_path, monkeypatch):
    import test_app_ui
    logs = tmp_path / "logs"
    monkeypatch.setenv("ACADEMICSGEN_RUN_LOG_DIR", str(logs))
    at, _ = _app(tmp_path)
    _generate(at)
    files = list(logs.glob("check_AL3501_U1_Notes_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    # CHANGED in BL-037 e: a Notes Generate writes only the topics that need writing (7 covered; the gap is not run)
    assert data["variant"] == "Notes" and len(data["topics"]) == 7
    assert "check log saved" in _all_text(at).lower()
