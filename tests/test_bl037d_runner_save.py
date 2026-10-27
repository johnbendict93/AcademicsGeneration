"""BL-037 step d (BL037_DESIGN.md f1cf0ca455, John's go 2026-09-29 "save from now"):
the runner runs only the given topics, adds John's reject notes to THAT topic's prompt only, keeps the raw
answer that was used, and hands every covered Notes topic to a save step RIGHT AFTER its checks; the app
saves it as a draft topic_texts row. Offline: fake Groq, no waiting, no DB (the real INSERT is tested on a
throwaway Postgres in the scripts folder: tests/test_bl037d_topic_insert_pg.py).
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import json
import re

import pytest

import source_app
import source_context as sc
import source_prompts as sp
import source_runner as sr
import topic_store as ts
from test_source_app import fake_groq
from test_source_prompts import _good_answer, _topic
from test_app_ui import _all_text, _app, _events, _generate, _undo_driver_patches  # noqa: F401 (autouse fixture)
import test_app_ui

SUBJECT, UNIT_TITLE = "Natural Language Processing", "Introduction"
MED, REGEX = "Minimum Edit Distance", "Regular Expressions"
NOTE = "Do not say the automaton is converted back into a regular expression."


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(sr.TokenPacer, "wait_for", lambda self, tokens: None)
    fake_groq.calls = []


def _good(prompt):
    """Copied, cited text from the first passage of the prompt (passes every check)."""
    return fake_groq(prompt, max_completion_tokens=1)


def _covered(ctx):
    return [p.topic for p in ctx.topics if ts.covered(p)]


# ---------- raw answer kept (needed by topic_store.recheck, Q2) ----------

def test_check_keeps_the_raw_answer_exactly_as_received(ctx):
    plan = _topic(ctx, MED)
    wide = _good_answer(plan).replace(f"[{plan.passages[0].pid}]", f"【{plan.passages[0].pid}】")
    chk = sp.check_notes_answer(wide, plan)
    assert chk.raw == wide and "【" not in chk.markdown          # raw untouched, markdown normalised


def test_raw_answer_is_the_answer_that_was_used_first_try(ctx):
    plan = _topic(ctx, MED)
    good = _good_answer(plan)
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: good)
    assert res.status == "ok" and res.calls == 1 and res.raw_answer == good


def test_raw_answer_is_the_reask_answer_when_the_reask_was_used(ctx):
    plan = _topic(ctx, MED)
    good = _good_answer(plan)
    bad = good.replace("[P", "[Q")                                     # no valid tag -> not ok -> re-ask
    answers = iter([bad, good])
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: next(answers))
    assert res.status == "ok" and res.used == 1 and res.raw_answer == good


def test_raw_answer_stays_the_first_when_a_lost_lines_reask_was_not_better(ctx):
    plan = _topic(ctx, MED)
    pid = plan.passages[0].pid
    first = _good_answer(plan) + "\nAn untagged closing line without any tag.\n"
    second = first + "\nAnother untagged line.\n"                      # ok but loses MORE lines -> not used
    answers = iter([first, second])
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: next(answers))
    assert res.reask_reason == "lost lines" and res.used == 0 and res.raw_answer == first
    assert f"[{pid}]" in res.raw_answer


def test_saved_raw_answer_passes_topic_store_recheck(ctx):
    """Full circle: what the runner hands over re-checks as the SAME kept text (Q2 works on real rows)."""
    plan = _topic(ctx, MED)
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=_good)
    row = source_app.topic_row(subject_code="AL3501", unit_number=1, variant="Notes", plan=plan, index=5,
                               result=res, source_sha256=ctx.source.sha256, rules_version="sp:x")
    assert ts.recheck(row, plan) == (True, [])


# ---------- avoid notes (Q5) ----------

def test_avoid_block_is_in_the_first_prompt_and_in_every_reask(ctx):
    plan = _topic(ctx, MED)
    bad = _good_answer(plan).replace("[P", "[Q")
    prompts = []

    def ask(p):
        prompts.append(p)
        return bad
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=ask, avoid=ts.avoid_block([NOTE]))
    assert res.status == "failed" and len(prompts) == 3                # checks unchanged: still re-asked
    assert all(f"- {NOTE}" in p for p in prompts)
    assert "YOUR PREVIOUS ANSWER WAS REJECTED" in prompts[1]


def test_GUARD_no_avoid_gives_exactly_the_old_prompt(ctx):
    plan = _topic(ctx, MED)
    prompts = []
    sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: prompts.append(p) or _good(p))
    assert prompts == [sp.build_notes_prompt(ctx, plan, SUBJECT, UNIT_TITLE)]


def test_avoid_note_cannot_add_a_fact(ctx):
    """The note is in the prompt, but a word from the NOTE alone is still a new term for the check."""
    plan = _topic(ctx, MED)
    pid = plan.passages[0].pid
    names = ["Zorblax", "Quintor", "Vexa", "Morrow", "Plinth"]            # 5 invented names (limit is 3)
    text = _good_answer(plan) + f"\nIt relates to {', '.join(names)} here. [{pid}]\n"
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: text,
                             avoid=ts.avoid_block([" ".join(names)]))
    assert res.status != "ok" and set(names) <= set(res.checks[-1].new_terms)


# ---------- runner: only given topics, avoid per topic, save after checks ----------

def _run(ctx, **kw):
    prompts = []

    def ask(p):
        prompts.append(p)
        return _good(p)
    run = sr.run_unit_notes("AL3501", "I", SUBJECT, UNIT_TITLE, ask=ask, **kw)
    return run, prompts


def test_runner_runs_only_the_given_topics(ctx):
    events = []
    run, prompts = _run(ctx, topics=[REGEX, MED], progress=events.append)
    assert [t.topic for t in run.topics] == [p.topic for p in ctx.topics if p.topic in (REGEX, MED)]
    assert len(prompts) == 2 and run.calls == 2
    assert [e["topic"] for e in events if e["stage"] == "done"] == [t.topic for t in run.topics]


def test_runner_refuses_an_unknown_topic_before_any_call(ctx):
    calls = []
    with pytest.raises(ValueError, match="not a sub-topic"):
        sr.run_unit_notes("AL3501", "I", SUBJECT, UNIT_TITLE, ask=lambda p: calls.append(p),
                          topics=[MED, "Quantum Parsing"])
    assert calls == []


def test_GUARD_runner_default_still_runs_every_topic(ctx):
    run, prompts = _run(ctx)
    assert [t.topic for t in run.topics] == [p.topic for p in ctx.topics] and len(prompts) == 7


def test_runner_sends_the_avoid_note_to_that_topic_only(ctx):
    run, prompts = _run(ctx, avoid={MED: [NOTE]})
    with_note = [re.search(r'Sub-topic: "(.+?)"', p).group(1) for p in prompts if NOTE in p]
    assert with_note == [MED]


def test_runner_hands_each_covered_topic_to_on_topic_after_its_checks(ctx):
    seen = []

    def on_topic(c, plan, index, res, notes):
        seen.append((plan.topic, index, res.status, bool(res.raw_answer), notes, c is not None))
    run, _ = _run(ctx, on_topic=on_topic, avoid={MED: [NOTE]})
    order = [p.topic for p in ctx.topics]
    assert [s[0] for s in seen] == _covered(ctx)                       # gap not saved, syllabus order
    assert all(s[1] == order.index(s[0]) + 1 and s[2] == "ok" and s[3] and s[5] for s in seen)
    assert dict((s[0], s[4]) for s in seen)[MED] == [NOTE]


def test_runner_index_is_the_syllabus_position_even_for_a_subset(ctx):
    seen = []
    _run(ctx, topics=[MED], on_topic=lambda c, plan, index, res, notes: seen.append(index))
    assert seen == [[p.topic for p in ctx.topics].index(MED) + 1]


def test_a_save_error_stops_the_run_before_the_next_call(ctx):
    prompts = []

    def ask(p):
        prompts.append(p)
        return _good(p)

    def boom(*a):
        raise RuntimeError("Neon down")
    with pytest.raises(sr.RunAborted, match="could not save .*Neon down"):
        sr.run_unit_notes("AL3501", "I", SUBJECT, UNIT_TITLE, ask=ask, on_topic=boom)
    assert len(prompts) == 1


def test_topics_saved_before_a_groq_failure_stay_saved(ctx):
    saved, n = [], {"calls": 0}

    def ask(p):
        n["calls"] += 1
        if n["calls"] == 3:
            raise RuntimeError("HTTP 429: rate limit")
        return _good(p)
    with pytest.raises(sr.RunAborted, match="rate limit"):
        sr.run_unit_notes("AL3501", "I", SUBJECT, UNIT_TITLE, ask=ask,
                          on_topic=lambda c, plan, i, res, notes: saved.append(plan.topic))
    assert saved == _covered(ctx)[:2]


# ---------- source_app: the row + the INSERT ----------

def test_topic_row_values(ctx):
    plan = _topic(ctx, MED)
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=_good)
    row = source_app.topic_row(subject_code="AL3501", unit_number=1, variant="Notes", plan=plan, index=5,
                               result=res, source_sha256=ctx.source.sha256, rules_version="sp:v",
                               avoid_notes=[NOTE])
    assert row["topic"] == MED and row["topic_index"] == 5 and row["unit_number"] == 1
    assert row["raw_answer"] == res.raw_answer and row["markdown"] == res.markdown
    assert row["passages_sha256"] == ts.passages_sha256(plan) and row["source_sha256"] == ctx.source.sha256
    assert row["rules_version"] == "sp:v" and row["avoid_notes"] == [NOTE]
    assert row["check_data"]["status"] == "ok" and row["check_data"]["calls"] == 1
    assert set(row) == set(source_app.TOPIC_COLUMNS)


def test_a_failed_topic_is_saved_with_its_raw_answer_and_status_failed(ctx):
    plan = _topic(ctx, MED)
    bad = _good_answer(plan).replace("[P", "[Q")
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=lambda p: bad)
    row = source_app.topic_row(subject_code="AL3501", unit_number=1, variant="Notes", plan=plan, index=5,
                               result=res, source_sha256=ctx.source.sha256, rules_version="sp:v")
    assert row["check_data"]["status"] == "failed" and row["markdown"] == "" and row["raw_answer"] == bad
    assert ts.topic_state(dict(row, id=1, generated_at=0, review_status="draft"), ctx, plan) == ts.FAILED


def test_topic_row_refuses_a_gap(ctx):
    gap = next(p for p in ctx.topics if not ts.covered(p))
    res = sp.notes_for_topic(ctx, gap, SUBJECT, UNIT_TITLE, ask=lambda p: pytest.fail("no call for a gap"))
    with pytest.raises(ValueError, match="gap"):
        source_app.topic_row(subject_code="AL3501", unit_number=1, variant="Notes", plan=gap, index=1,
                             result=res, source_sha256="x", rules_version="v")


def test_insert_sql_is_one_insert_without_review_columns():
    sql = " ".join(source_app.TOPIC_INSERT_SQL.split())
    assert sql.startswith("INSERT INTO topic_texts (") and sql.endswith("RETURNING id") and ";" not in sql
    for col in ("review_status", "reviewed_at", "review_note", "generated_at", " id,"):
        assert col not in sql.split("VALUES")[0]
    assert sql.count("%s") == len(source_app.TOPIC_COLUMNS) and sql.count("::jsonb") == 2


def test_insert_params_send_json_for_jsonb(ctx):
    plan = _topic(ctx, MED)
    res = sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=_good)
    row = source_app.topic_row(subject_code="AL3501", unit_number=1, variant="Notes", plan=plan, index=5,
                               result=res, source_sha256="s", rules_version="v", avoid_notes=[NOTE])
    params = dict(zip(source_app.TOPIC_COLUMNS, source_app.topic_insert_params(row)))
    assert json.loads(params["check_data"]) == row["check_data"] and json.loads(params["avoid_notes"]) == [NOTE]
    assert params["raw_answer"] == row["raw_answer"]


def _gen(variant, **kw):
    return source_app.generate_unit_pdf(
        variant=variant, subject_code="AL3501", subject_name=SUBJECT, dept_code="AI&DS",
        dept_name="Artificial Intelligence and Data Science", unit_number=1, unit_title=UNIT_TITLE,
        regulation="R-2021", groq_chat=fake_groq, **kw)


def test_generate_notes_saves_every_covered_topic_and_returns_the_ids(ctx):
    rows = []

    def save(row):
        rows.append(row)
        return 900 + len(rows)
    out = _gen("Notes", save_topic=save)
    assert [r["topic"] for r in rows] == _covered(ctx)
    assert out.topic_row_ids == [901 + i for i in range(len(rows))]
    assert all(r["rules_version"] == sr.prompt_version() == out.run.prompt_version for r in rows)
    assert all(r["source_sha256"] == ctx.source.sha256 and r["avoid_notes"] == [] for r in rows)


def test_generate_qa_does_not_save_topic_rows_yet():
    out = _gen("QA", save_topic=lambda row: pytest.fail("QA per-topic comes later"))
    assert out.topic_row_ids == []


def test_GUARD_generate_without_save_topic_still_makes_the_pdf():
    out = _gen("Notes")
    assert out.pdf_bytes.startswith(b"%PDF") and out.row_values["topics_covered"] == 7


# ---------- the screen (AppTest) ----------

def test_screen_generate_notes_saves_each_topic_before_the_upload(tmp_path, ctx):
    """CHANGED in BL-037 e (John's go): a Notes Generate saves every topic and makes NO PDF (no upload);
    the PDF from approved topics is step f."""
    at, log = _app(tmp_path)
    _generate(at)
    ev = _events(log)
    kinds = [e["kind"] for e in ev]
    rows = [e for e in ev if e["kind"] == "topic_row"]
    assert [r["topic"] for r in rows] == _covered(ctx)
    assert all(r["status"] == "ok" and r["has_raw"] and r["variant"] == "Notes" for r in rows)
    assert "upload" not in kinds and "upsert" not in kinds
    assert "7 topic(s) written and saved as DRAFTS" in _all_text(at)


def test_screen_groq_stop_mid_run_says_what_was_saved(tmp_path, monkeypatch):
    driver = test_app_ui.DRIVER.replace(
        'def groq(prompt, **kw):\n    rec("groq")',
        'N = {{"c": 0}}\ndef groq(prompt, **kw):\n    rec("groq")\n    N["c"] += 1\n'
        '    if N["c"] == 3:\n        raise RuntimeError("HTTP 429: rate limit")')
    assert driver != test_app_ui.DRIVER
    monkeypatch.setattr(test_app_ui, "DRIVER", driver)
    at, log = _app(tmp_path)
    _generate(at)
    text = _all_text(at)
    kinds = [e["kind"] for e in _events(log)]
    assert kinds.count("topic_row") == 2 and "upload" not in kinds and "upsert" not in kinds
    assert "Generation stopped" in text and "Nothing was saved" not in text
    assert "2 written topic(s) were saved as drafts for per-topic review; no PDF was saved." in text


def test_GUARD_screen_groq_fails_at_once_still_says_nothing_saved(tmp_path):
    at, log = _app(tmp_path, groq_fails=True)
    _generate(at)
    assert "topic_row" not in [e["kind"] for e in _events(log)]
    assert "Nothing was saved" in _all_text(at)


def test_screen_save_error_stops_the_run(tmp_path, monkeypatch):
    driver = test_app_ui.DRIVER.replace(
        "app.save_topic_row = save_topic",
        "def _boom(row):\n    rec('topic_try')\n    raise RuntimeError('Neon insert failed')\napp.save_topic_row = _boom")
    assert driver != test_app_ui.DRIVER
    monkeypatch.setattr(test_app_ui, "DRIVER", driver)
    at, log = _app(tmp_path)
    _generate(at)
    kinds = [e["kind"] for e in _events(log)]
    assert kinds.count("groq") == 1 and kinds.count("topic_try") == 1 and "upload" not in kinds
    text = _all_text(at)
    assert "could not save" in text and "Nothing was saved" in text


def test_GUARD_screen_qa_generate_saves_no_topic_rows(tmp_path):
    at, log = _app(tmp_path)
    next(s for s in at.sidebar.selectbox if s.label == "Variant").select("QA").run()
    _generate(at)
    kinds = [e["kind"] for e in _events(log)]
    assert "topic_row" not in kinds and "upload" in kinds
