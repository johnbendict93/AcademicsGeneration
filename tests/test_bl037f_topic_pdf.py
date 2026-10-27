"""BL-037 step f (BL037_DESIGN.md f1cf0ca455; John's go 2026-09-29): build the Notes PDF from the APPROVED topic texts.
- Only when every covered topic is approved (topic_store.pdf_ready); no Groq call; syllabus order; gaps listed.
- The PDF row records topic_row_ids (migration 007) and is saved as a DRAFT (John's final OK, R9).
- A PDF built from topics is CURRENT while its topic ids are exactly the current approved topics (same source);
  a prompt change alone no longer makes it out of date. Old whole-unit rows keep the old rule (prompt_version).
Tests marked GUARD protect kept behaviour and pass on the old code too; every other test FAILS on the old code."""
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from pypdf import PdfReader

import source_app as sa
import source_context as sc
import source_prompts as sp
import source_review as rv
import source_runner as sr
import topic_store as ts
from test_source_prompts import _good_answer, _topic
from test_app_ui import SHA1, _all_text, _app, _events, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
KEY = "source-strict/AI&DS/AL3501/AL3501_Unit1_Notes.pdf"
MED = "Minimum Edit Distance"


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context("AL3501", "I")


def _row(ctx, topic, rid, status="approved", minutes=0, note=None):
    plan = _topic(ctx, topic)
    ans = _good_answer(plan)
    chk = sp.check_notes_answer(ans, plan)
    return {"id": rid, "subject_code": "AL3501", "unit_number": 1, "variant": "Notes", "topic": topic,
            "topic_index": [t.topic for t in ctx.topics].index(topic) + 1, "raw_answer": ans,
            "markdown": chk.markdown, "check_data": {"status": "ok"}, "source_sha256": ctx.source.sha256,
            "passages_sha256": ts.passages_sha256(plan), "rules_version": "sp:x", "avoid_notes": [],
            "generated_at": T0 + timedelta(minutes=minutes), "review_status": status,
            "reviewed_at": None if status == "draft" else T0, "review_note": note}


def _all_approved(ctx):
    return [_row(ctx, p.topic, i) for i, p in enumerate(ctx.topics, 1) if ts.covered(p)]


def _build(ctx, rows):
    return sa.build_pdf_from_topics(ctx=ctx, view=ts.unit_view(ctx, rows), subject_name="Natural Language Processing",
                                    subject_code="AL3501", dept_code="AI&DS",
                                    dept_name="Artificial Intelligence and Data Science", unit_number=1,
                                    unit_title="Introduction", regulation="R-2021")


def _text(pdf_bytes):
    return "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf_bytes)).pages)


# ---------- building (no model) ----------

def test_pdf_is_built_from_the_approved_texts_only(ctx):
    rows = _all_approved(ctx)
    out = _build(ctx, rows)
    assert out.pdf_bytes.startswith(b"%PDF") and out.storage_key == KEY
    assert out.topic_row_ids == [r["id"] for r in rows]
    assert out.row_values["topics_covered"] == 7 and out.row_values["topics_total"] == 8
    assert out.row_values["source_sha256"] == ctx.source.sha256
    text = " ".join(_text(out.pdf_bytes).split())
    for r in rows:
        first = re.sub(r"\s*\[P\d+\]", "", r["markdown"].split("\n\n")[1])[:30]
        assert " ".join(first.split()) in text, r["topic"]
    assert "Origins and challenges of NLP" in text                       # the gap is listed
    assert not re.search(r"\[P\d+\]", text.split("Source and licence")[0])   # tags hidden


def test_pdf_topics_are_in_syllabus_order(ctx):
    rows = list(reversed(_all_approved(ctx)))
    out = _build(ctx, rows)
    got = [t.topic for t in out.run.topics if t.status == "ok"]
    assert got == [p.topic for p in ctx.topics if ts.covered(p)]


@pytest.mark.parametrize("status", ["draft", "rejected"])
def test_no_pdf_unless_every_covered_topic_is_approved(ctx, status):
    rows = _all_approved(ctx)
    rows[-1] = dict(rows[-1], review_status=status, reviewed_at=None if status == "draft" else T0,
                    review_note="bad" if status == "rejected" else None)
    with pytest.raises(ValueError):
        _build(ctx, rows)


def test_no_model_call_when_building(ctx, monkeypatch):
    monkeypatch.setattr(sr, "run_unit_notes", lambda *a, **k: pytest.fail("no Groq run for a topic PDF"))
    assert _build(ctx, _all_approved(ctx)).pdf_bytes


# ---------- which PDF is current ----------

def _pdf_row(ids, **over):
    r = {"id": 10, "storage_key": KEY, "storage_bucket": "github", "source_sha256": SHA1,
         "prompt_version": "sp:old+sq:old", "review_status": "approved", "topic_row_ids": ids}
    r.update(over)
    return r


def test_topic_pdf_is_current_while_its_topics_are_the_current_approved_ones():
    assert sa.is_current(_pdf_row([1, 2]), SHA1, "sp:NEW", topic_ids=[1, 2])      # prompt change: still current
    assert sa.is_servable(_pdf_row([1, 2]), SHA1, "sp:NEW", topic_ids=[1, 2])
    assert not sa.is_current(_pdf_row([1, 2]), SHA1, "sp:NEW", topic_ids=[1, 3])  # a topic was remade
    assert not sa.is_current(_pdf_row([1, 2]), SHA1, "sp:NEW", topic_ids=None)    # not every topic approved now
    assert not sa.is_current(_pdf_row([1, 2]), "0" * 64, "sp:NEW", topic_ids=[1, 2])   # source changed
    assert not sa.is_servable(_pdf_row([1, 2], review_status="draft"), SHA1, "sp:NEW", topic_ids=[1, 2])


def test_GUARD_whole_unit_rows_keep_the_prompt_version_rule():
    old = _pdf_row(None, prompt_version="sp:a")
    assert sa.is_current(old, SHA1, "sp:a") and not sa.is_current(old, SHA1, "sp:b")


def test_review_state_uses_the_topic_rule():
    assert rv.review_state(_pdf_row([1, 2], review_status="draft"), SHA1, "sp:NEW", topic_ids=[1, 2]) == "draft"
    assert rv.review_state(_pdf_row([1, 2], review_status="draft"), SHA1, "sp:NEW", topic_ids=[9]) == "stale"
    p = rv.review_params("approved", "", dict(_pdf_row([1, 2], review_status="draft"), generated_at=T0),
                         SHA1, "sp:NEW", topic_ids=[1, 2])
    assert p[0] == "approved" and p[5] == "sp:old+sq:old"                # guard uses the row's own version


# ---------- SQL (real Postgres run in the scripts folder) ----------

def test_notes_pdf_select_and_upsert_carry_topic_row_ids():
    import inspect
    import academicsgen_app as app
    sel = " ".join(inspect.getsource(app.check_existing_notes_pdf).split())
    assert "topic_row_ids" in sel and "review_status" in sel and "FROM generated_pdfs" in sel
    up = " ".join(app.TOPIC_PDF_UPSERT_SQL.split())
    assert "topic_row_ids" in up and "%s::jsonb" in up and "topic_row_ids = EXCLUDED.topic_row_ids" in up
    assert "review_status" not in up                                    # the 006/007 trigger makes it a draft


# ---------- the screen ----------

def _existing(ids, **over):
    r = {"id": 10, "storage_key": KEY, "storage_bucket": "github", "source_sha256": SHA1,
         "prompt_version": "sp:old", "generated_at": T0, "review_status": "draft", "reviewed_at": None,
         "review_note": None, "topic_row_ids": ids}
    r.update(over)
    return r


def test_screen_build_button_only_when_every_topic_is_approved(tmp_path, ctx):
    rows = _all_approved(ctx)
    rows[0] = dict(rows[0], review_status="draft", reviewed_at=None)
    at, _ = _app(tmp_path, topic_rows=rows)
    _unlock(at)
    assert not any(b.key == "build_pdf" for b in at.button)
    assert "The PDF can be built when every covered topic is approved (6 of 7 approved)." in _all_text(at)


def test_screen_build_pdf_uploads_and_saves_a_draft_without_groq(tmp_path, ctx):
    rows = _all_approved(ctx)
    at, log = _app(tmp_path, topic_rows=rows)
    _unlock(at)
    b = next(b for b in at.button if b.key == "build_pdf")
    assert b.label == "📄 Build PDF from the 7 approved topics"
    b.click().run()
    assert not at.exception, at.exception
    ev = _events(log)
    kinds = [e["kind"] for e in ev]
    assert "groq" not in kinds and kinds.index("upload") < kinds.index("upsert_topics")
    up = next(e for e in ev if e["kind"] == "upload")
    assert up["key"] == KEY and up["pdf"] == "%PDF-"
    row = next(e for e in ev if e["kind"] == "upsert_topics")
    assert row["topic_row_ids"] == [r["id"] for r in rows] and row["variant"] == "Notes"
    assert row["row_values"]["topics_covered"] == 7
    assert "PDF built from the 7 approved topics — saved as a DRAFT" in _all_text(at)


def test_screen_pdf_already_built_from_these_topics(tmp_path, ctx):
    rows = _all_approved(ctx)
    at, log = _app(tmp_path, existing=_existing([r["id"] for r in rows]), topic_rows=rows)
    _unlock(at)
    assert not any(b.key == "build_pdf" for b in at.button)
    text = _all_text(at)
    assert "The PDF is built from these approved topics (DRAFT)" in text
    assert "DRAFT - waiting for your review" in text and "OUT OF DATE" not in text     # old prompt_version
    at.button(key="approve").click().run()
    assert [e for e in _events(log) if e["kind"] == "review"]


def test_screen_rebuild_offered_when_topics_changed(tmp_path, ctx):
    rows = _all_approved(ctx)
    at, _ = _app(tmp_path, existing=_existing([99] + [r["id"] for r in rows][1:]), topic_rows=rows)
    _unlock(at)
    assert any(b.key == "build_pdf" for b in at.button)
    assert "OUT OF DATE" in _all_text(at)


def test_screen_students_get_an_approved_topic_pdf(tmp_path, ctx):
    rows = _all_approved(ctx)
    at, log = _app(tmp_path, existing=_existing([r["id"] for r in rows], review_status="approved"), topic_rows=rows)
    assert "Ready — checked and approved" in _all_text(at)
    at.button(key="student_get").click().run()
    assert [e["kind"] for e in _events(log)] == ["download"]


def test_GUARD_screen_students_lose_it_when_a_topic_is_rejected_later(tmp_path, ctx):
    rows = _all_approved(ctx)
    ids = [r["id"] for r in rows]
    rows[2] = dict(rows[2], review_status="rejected", review_note="changed my mind")
    at, _ = _app(tmp_path, existing=_existing(ids, review_status="approved"), topic_rows=rows)
    assert "Not available yet" in _all_text(at) and not any(b.key == "student_get" for b in at.button)
