"""BL-031: raw LaTeX must not reach the student PDF (live run #6, AL3501 I Notes, 2026-09-28, REJECTED:
Minimum Edit Distance printed '(D(i,j))', '\\text{cost}', '\\times', '(i^{th})').
Every LaTeX character is plain ASCII, so the odd-character check could not see it.
Measured first: none of the marks (\\word, \\( \\) \\[ \\], ^{ _{) occurs in any of the 58 source files;
'$' is NOT a marker (sources are full of money).
All tests FAIL on source_prompts a7ab2ea220 / source_qa 7117414ada / source_app 6dff9adc8b /
source_review 9074f03729 / academicsgen_app 6a8f8877c2, except the ones labelled GUARD."""
import json
import types

import pytest

import source_app as sa
import source_context as sc
import source_prompts as sp
import source_qa as sq
import source_review as rv
from test_app_ui import _all_text, _app, _undo_driver_patches, _unlock  # noqa: F401
from test_bl029_step_c import _log, _row
from test_source_prompts import SUBJECT, UNIT_TITLE, _good_answer, _topic
from test_source_qa import REG, _good_json, _req

# What gpt-oss wrote in live run #6 (the \( \) became plain brackets in the PDF, the rest stayed).
LIVE6 = r"- Substitution (or match): \(D(i-1,j-1) + \text{cost}\), where the cost is 0 if the characters match."
LIVE6_B = r"The algorithm fills an \((n+1) \times (m+1)\) matrix; delete the \(i^{th}\) character of \(X\)."


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def _with_line(plan, line):
    return _good_answer(plan) + f"\n{line} [{plan.passages[0].pid}]\n"


# ---------- the pure check ----------

def test_live_run_6_text_is_flagged_and_not_ok(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    chk = sp.check_notes_answer(_with_line(plan, LIVE6) + f"\n{LIVE6_B} [{plan.passages[0].pid}]\n", plan)
    assert not chk.ok
    assert chk.latex == ["\\(", "\\text", "\\)", "\\times", "^{"]


@pytest.mark.parametrize("line,want", [
    (r"cost \frac{1}{2}", ["\\frac"]), (r"see \[ D \]", ["\\[", "\\]"]), ("x_{1} is first", ["_{"]),
    ("the i^{th} one", ["^{"]), (r"use \min here", ["\\min"])])
def test_each_mark(line, want, ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    assert sp.check_notes_answer(_with_line(plan, line), plan).latex == want


def test_plain_formulas_and_money_are_fine(ctx):
    """GUARD (passes before and after): plain maths, Unicode times, money, markdown bold."""
    plan = _topic(ctx, "Minimum Edit Distance")
    line = "D(i,j) = min(D(i-1,j) + 1, D(i,j-1) + 1); n × m cells; x^2; a_b; $700; **cost**; i-th."
    chk = sp.check_notes_answer(_with_line(plan, line), plan)
    assert chk.ok and not getattr(chk, "latex", [])


def test_a_mark_that_is_in_the_passages_is_allowed():
    """R5 is about ADDING: a mark the sent passages already contain is not new (none do today)."""
    assert sp._latex_marks(r"n \times m", r"the lecture writes n \times m") == []
    assert sp._latex_marks(r"n \times m", "no marks here") == ["\\times"]


def test_citation_tags_are_not_marks():
    assert sp._latex_marks("text [P1, P2] and [P3]") == []


# ---------- re-ask once, then left out ----------

def test_latex_reasks_once_naming_the_marks_then_ok(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    answers = iter([_with_line(plan, LIVE6), _good_answer(plan)])
    calls = []
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p) or next(answers))
    assert res.status == "ok" and len(calls) == 2
    assert "LaTeX" in calls[1] and "\\text" in calls[1] and "plain text" in calls[1]
    assert "\\text" not in res.markdown


def test_latex_twice_is_needs_review_left_out(ctx):
    plan = _topic(ctx, "Minimum Edit Distance")
    bad = _with_line(plan, LIVE6)
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: bad)
    assert res.status == "needs_review" and res.calls == 3                  # BL-035: 2 re-asks (was 1)


# ---------- QA ----------

def test_qa_latex_flagged_and_reasked(ctx):
    r = _req(sq.plan_topic_requests(ctx, REG), "Minimum Edit Distance")
    bad = json.loads(_good_json(r))
    bad["part_a"][0]["answer"] += r" It takes \(O(n \times m)\) time."
    chk = sq.check_qa_answer(json.dumps(bad), r)
    assert not chk.ok and chk.latex == ["\\(", "\\times", "\\)"]
    answers = iter([json.dumps(bad), _good_json(r)])
    calls = []
    res = sq.qa_for_topic(ctx, r, SUBJECT, "Introduction", REG,
                          ask=lambda p, rf: calls.append(p) or next(answers))
    assert res.status == "ok" and len(calls) == 2 and "LaTeX" in calls[1] and "\\times" in calls[1]


def test_qa_question_with_latex_flagged(ctx):
    r = _req(sq.plan_topic_requests(ctx, REG), "Minimum Edit Distance")
    bad = json.loads(_good_json(r))
    bad["part_a"][0]["question"] += r" What is \(D(n,m)\)?"
    assert sq.check_qa_answer(json.dumps(bad), r).latex == ["\\(", "\\)"]


def test_qa_plain_is_fine(ctx):
    """GUARD (passes before and after)."""
    r = _req(sq.plan_topic_requests(ctx, REG), "Minimum Edit Distance")
    good = json.loads(_good_json(r))
    good["part_a"][0]["answer"] += " It takes O(n × m) time; costs $5."
    chk = sq.check_qa_answer(json.dumps(good), r)
    assert chk.ok and not getattr(chk, "latex", [])


# ---------- check log + review panel ----------

def test_check_log_records_latex():
    chk = types.SimpleNamespace(new_terms=[], odd_chars=[], dropped=[], latex=["\\text", "^{"])
    t = types.SimpleNamespace(calls=2, checks=[chk])
    assert sa._writer_log(t)["latex"] == ["\\text", "^{"]


def test_summary_flags_latex_and_old_logs_still_work():
    rep = {"needs_review": ["Minimum Edit Distance"], "failed": [], "topics": [
        {"topic": "Regex", "status": "ok", "writer": {"new_terms": [], "odd_chars": [], "dropped": []}},
        {"topic": "Minimum Edit Distance", "status": "needs_review",
         "writer": {"new_terms": [], "odd_chars": [], "dropped": [], "latex": ["\\text", "\\times"]}}]}
    s = rv.check_summary(rep)
    assert [f["topic"] for f in s["flagged"]] == ["Minimum Edit Distance"]
    assert s["flagged"][0]["latex"] == ["\\text", "\\times"]


def test_review_panel_shows_formula_code_column(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setenv("ACADEMICSGEN_RUN_LOG_DIR", str(logs))
    _log(logs, "check_AL3501_U1_Notes_20200101-000000.json", needs_review=["Minimum Edit Distance"], topics=[
        {"topic": "Minimum Edit Distance", "status": "needs_review",
         "writer": {"new_terms": [], "odd_chars": [], "dropped": [], "latex": ["\\text", "\\times"]}}])
    at, _ = _app(tmp_path, existing=_row())
    _unlock(at)
    assert not at.exception
    text = _all_text(at)
    assert "Formula code" in text and "\\text, \\times" in text
