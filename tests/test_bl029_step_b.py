"""BL-029 step b: review rule (is_servable = approved AND current) + verifier OFF in the app.
Offline: fake Groq, fake clock. All tests FAIL on source_app 5644bd0e56 / source_runner 5310252cf1 /
academicsgen_app 078ff91d4d (checked 2026-09-28)."""
import re

import pytest

import source_app as sa
import source_runner as sr
from test_app_ui import _all_text, _app, _undo_driver_patches, _unlock  # noqa: F401 (autouse: undo UI-test patches)
from test_bl028_wiring import fake_verify
from test_source_app import SHA1, UNIT, Clock, fake_groq
from test_source_runner import SUBJECT, FakeClock, _good_notes

PV2 = r"sp:[0-9a-f]{10}\+sq:[0-9a-f]{10}(?:\+gs:[0-9a-f]{10})?"   # BL-035: +gs when the skip list exists


def _row(**kw):
    row = {"storage_key": "k", "storage_bucket": "github", "source_sha256": SHA1,
           "prompt_version": sr.prompt_version(), "review_status": "approved"}
    row.update(kw)
    return row


# ---------- review rule ----------

def test_review_statuses_match_migration_006():
    assert sa.REVIEW_STATUSES == ("draft", "approved", "rejected")


def test_approved_and_current_is_servable():
    assert sa.is_servable(_row(), SHA1, sr.prompt_version())


@pytest.mark.parametrize("status", ["draft", "rejected", None, "", "Approved"])
def test_not_approved_is_never_servable(status):
    assert not sa.is_servable(_row(review_status=status), SHA1, sr.prompt_version())


def test_row_without_review_column_is_not_servable():
    row = _row()
    del row["review_status"]
    assert not sa.is_servable(row, SHA1, sr.prompt_version())


def test_approved_but_stale_is_not_servable():
    assert not sa.is_servable(_row(source_sha256="0" * 64), SHA1, sr.prompt_version())     # source changed
    assert not sa.is_servable(_row(prompt_version="sp:x+sq:y"), SHA1, sr.prompt_version())  # prompts changed
    assert not sa.is_servable(None, SHA1, sr.prompt_version())


# ---------- prompt_version ----------

def test_prompt_version_without_the_verifier():
    assert re.fullmatch(PV2, sr.prompt_version())


def test_prompt_version_with_the_verifier_is_different():
    assert re.fullmatch(PV2 + r"\+sv:[0-9a-f]{10}", sr.prompt_version(verified=True))


def test_verified_experiment_run_is_marked_with_sv():
    """A run WITH the verifier (experiments only) never carries the app's version."""
    c = FakeClock()
    res = sr.run_unit_notes("AL3501", "I", SUBJECT, "Introduction", ask=_good_notes,
                            verify_ask=fake_verify(), clock=c.now, sleep=c.sleep)
    assert res.prompt_version == sr.prompt_version(verified=True) != sr.prompt_version()


def test_old_verified_row_is_stale_now():
    """Today's Neon row 10 was made with the verifier -> no longer current."""
    old = "sp:a7ab2ea220+sq:7117414ada+sv:d1c53640d2"
    assert not sa.is_current(_row(prompt_version=old), SHA1, sr.prompt_version())


# ---------- verifier OFF in the app ----------

@pytest.mark.parametrize("variant", ["Notes", "QA"])
def test_generate_makes_no_verifier_calls(variant):
    fake_groq.calls = []
    c = Clock()
    out = sa.generate_unit_pdf(variant=variant, unit_number=1, groq_chat=fake_groq, clock=c.now,
                               sleep=c.sleep, **UNIT)
    names = [x["response_format"]["json_schema"]["name"] for x in fake_groq.calls if x["response_format"]]
    assert "sentence_verdicts" not in names and len(fake_groq.calls) == 7
    assert out.run.verify == {} and out.run.calls == 7
    assert re.fullmatch(PV2, out.row_values["prompt_version"])


def test_check_log_has_no_verifier_verdicts():
    fake_groq.calls, got = [], []
    c = Clock()
    sa.generate_unit_pdf(variant="Notes", unit_number=1, groq_chat=fake_groq, clock=c.now, sleep=c.sleep,
                         on_run=got.append, **UNIT)
    assert got and all(t["verify"] is None for t in got[0]["topics"])
    assert any(t["writer"] for t in got[0]["topics"])            # writer checks still logged


# ---------- screen ----------

def test_screen_says_one_call_per_topic(tmp_path):
    at, _ = _app(tmp_path)
    _unlock(at)                                  # BL-029 step c: the estimate is in the Admin tab
    text = _all_text(at)
    assert "one Groq call per covered sub-topic" in text and "two Groq calls" not in text
