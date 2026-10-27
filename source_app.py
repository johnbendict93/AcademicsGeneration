"""source_app.py - P3c-1 of the source-strict app: the decisions academicsgen_app.py
needs, kept out of Streamlit so they are tested offline (design sections 5, 8 P3).

  VARIANTS                        -> ["Notes", "QA"]  (D1: VerifiedNotes merged into Notes)
  unit_overview(code, unit_no)    -> can this unit be generated? + per-topic coverage table
  is_current(row, sha, version)   -> was a stored PDF built from THIS source with THESE prompts?
  is_servable(row, sha, version)  -> BL-029: may STUDENTS get it? only 'approved' AND current
  storage_key(...)                -> 'source-strict/<dept>/<code>/<file>' (old files never overwritten, D3)
  generate_unit_pdf(...)          -> runs the unit (source_runner) + builds the PDF (source_pdf)
                                     + the values for the new Neon columns (migration_005)
groq_chat is injected: academicsgen_app._groq_chat in the app, a fake in tests.
BL-029: the yes/no verifier (source_verify) is OFF - John approves every PDF before students see it.
BL-037 d: generate_unit_pdf(save_topic=...) saves every covered Notes topic as a draft topic_texts row
(topic_row + TOPIC_INSERT_SQL) right after its checks; QA topics are not saved yet (QA per-topic comes later).
BL-037 e: generate_topics(...) writes ONLY the given Notes topics (John's reject notes per topic), saves each as a
draft topic_texts row and makes NO PDF - the Admin Notes Generate / 'Regenerate this topic' use it. The PDF from
approved topics is step f.
BL-041: build_pdf_from_topics lists topics John marked 'not really in the source' with the gaps (page 1).
BL-043: rechecked_topic_row keeps John's removed lines removed (check_data['removed']) while they still match.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import source_context as sc
import source_pdf as spdf
import source_prompts as sp
import source_qa as sq
import source_runner as sr
import topic_store as ts

VARIANTS = ["Notes", "QA"]
REVIEW_STATUSES = ("draft", "approved", "rejected")    # migration_006 generated_pdfs.review_status
STORAGE_PREFIX = "source-strict"
REASONING_EFFORT = "low"      # gpt-oss reasoning tokens count against max_completion_tokens
SourceRefused = sc.SourceRefused

_LABELS = {"FOUND": "covered", "PARTIAL": "partly covered", "MISSING": "not in source"}


def status_label(status):
    return _LABELS.get(status, "not in source")


@dataclass
class UnitOverview:
    available: bool
    message: str
    source: object          # source_context.UnitSource or None
    topics: list            # [(topic, FOUND|PARTIAL|MISSING)]
    covered: int
    total: int


def unit_overview(subject_code, unit_number, resources_dir=None) -> UnitOverview:
    try:
        src = sc.load_unit_source(subject_code, unit_number, resources_dir)
    except SourceRefused as e:
        return UnitOverview(False, str(e), None, [], 0, 0)
    topics = [(r["topic"], r["status"])
              for r in sc.unit_topics(subject_code, unit_number, src.text, resources_dir)]
    covered = sum(1 for _t, s in topics if s in ("FOUND", "PARTIAL"))
    return UnitOverview(True, "", src, topics, covered, len(topics))


def is_current(row, source_sha256, prompt_version, topic_ids=None) -> bool:
    """Serve a stored PDF only if it was built from THIS source file with THESE prompts.
    Rows made the old way have source_sha256 NULL -> never served.
    BL-037 f: a PDF built from topics (topic_row_ids set) is current while its topic ids are EXACTLY the current
    approved topics (topic_ids = topic_store.pdf_row_ids, None when not every covered topic is approved) and the
    source is the same - a prompt change alone does not make it out of date (each topic was re-checked, Q2)."""
    if row and row.get("topic_row_ids") is not None:
        return bool(row.get("storage_key") and row.get("source_sha256") == source_sha256
                    and topic_ids is not None and list(row["topic_row_ids"]) == list(topic_ids))
    return bool(row and row.get("storage_key") and row.get("source_sha256")
                and row.get("source_sha256") == source_sha256
                and row.get("prompt_version") == prompt_version)


def is_servable(row, source_sha256, prompt_version, topic_ids=None) -> bool:
    """BL-029 (R9): students get a PDF ONLY if John approved it AND it is still current.
    Draft, rejected, unknown or missing status -> never."""
    return bool(row and row.get("review_status") == "approved"
                and is_current(row, source_sha256, prompt_version, topic_ids))


def storage_key(dept_code, subject_code, unit_number, variant) -> str:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant '{variant}' (only {', '.join(VARIANTS)})")
    return f"{STORAGE_PREFIX}/{dept_code}/{subject_code}/{subject_code}_Unit{unit_number}_{variant}.pdf"


def row_values(run) -> dict:
    """Values for the migration_005 columns."""
    covered = sum(1 for t in run.topics if t.status == "ok")
    total = len(run.topics) + (0 if any(t.status == "gap" for t in run.topics) else len(run.gaps))
    return {
        "source_sha256": run.source.sha256,
        "prompt_version": run.prompt_version,
        "topics_covered": covered,
        "topics_total": total,
        "gap_topics": json.dumps(run.gaps),
        "needs_review_topics": json.dumps(run.needs_review),
        "failed_topics": json.dumps(run.failed),
    }


DROPPED_END_CHARS = 120    # BL-032: the [Pn] tag sits at the END of a paragraph -> keep the end too


def _tail(text):
    """Last characters of a dropped text (whole text if short) - shows HOW the model wrote its tag."""
    text = str(text)
    return text[-DROPPED_END_CHARS:] if len(text) > DROPPED_END_CHARS else text


def _writer_log(t):
    checks = list(getattr(t, "checks", []) or [])
    used = getattr(t, "used", -1)          # BL-033 part 2: the answer actually used (may be the first)
    chk = checks[used] if checks else None
    out = {"calls": t.calls,
           "new_terms": list(getattr(chk, "new_terms", []) or []),
           "odd_chars": [f"U+{ord(c):04X}" for c in (getattr(chk, "odd_chars", []) or [])],
           "latex": list(getattr(chk, "latex", []) or []),
           "dropped": [{"reason": d.reason, "text": str(d.text)[:160], "end": _tail(d.text)}
                       for d in (getattr(chk, "dropped", []) or [])],
           "kept_leadins": list(getattr(chk, "kept_leadins", []) or []),
           "sections": list(getattr(chk, "sections", []) or []),          # BL-038
           "layout": list(getattr(chk, "layout", []) or []),
           "list_tagged": list(getattr(chk, "list_tagged", []) or []),         # BL-042
           "trimmed": list(getattr(t, "trimmed", []) or []),                  # BL-039
           "note": getattr(t, "note", "") or ""}
    if len(checks) > 1:
        out["used_try"] = (used % len(checks)) + 1
        out["reask_reason"] = getattr(t, "reask_reason", "") or "not ok"        # BL-032: what the FIRST try got wrong, too (it decided the re-ask)
        first = checks[0]
        out["first_try"] = {"new_terms": list(getattr(first, "new_terms", []) or []),
                            "odd_chars": [f"U+{ord(c):04X}" for c in (getattr(first, "odd_chars", []) or [])],
                            "latex": list(getattr(first, "latex", []) or []),
                            "dropped": [{"reason": d.reason, "end": _tail(d.text)}
                                        for d in (getattr(first, "dropped", []) or [])]}
    if hasattr(t, "items"):
        out["items"] = sum(len(v) for v in t.items.values())
    return out


def _verify_log(v):
    if v is None:
        return None
    return {"status": v.status, "total": v.total, "removed": list(v.removed), "error": v.error,
            "verdicts": list(v.verdicts)}


def run_report(run, variant, subject_code, unit_number) -> dict:
    """JSON-safe check log: per topic the writer checks and every verifier verdict (why kept /
    removed / left out). No secrets. Made BEFORE the PDF is built."""
    topics = []
    for t in run.topics:
        gap = t.status == "gap"
        topics.append({"topic": t.topic, "status": t.status,
                       "writer": None if gap else _writer_log(t),
                       "verify": _verify_log(run.verify.get(t.topic))})
    listed = {t["topic"] for t in topics}
    topics += [{"topic": g, "status": "gap", "writer": None, "verify": None} for g in run.gaps if g not in listed]
    return {"subject_code": subject_code, "unit": unit_number, "variant": variant,
            "source_sha256": run.source.sha256, "prompt_version": run.prompt_version,
            "calls": run.calls, "needs_review": list(run.needs_review), "failed": list(run.failed),
            "skipped_spots": list(getattr(run.source, "skipped_spots", []) or []),     # BL-035
            "topics": topics}


# BL-037 d: ONE new topic_texts row per written topic (migration_007). review_status / reviewed_at /
# review_note / generated_at are NOT sent: the table defaults + guard trigger make every new row a draft.
TOPIC_COLUMNS = ("subject_code", "unit_number", "variant", "topic", "topic_index", "raw_answer", "markdown",
                 "check_data", "source_sha256", "passages_sha256", "rules_version", "avoid_notes")
_JSONB = ("check_data", "avoid_notes")
TOPIC_INSERT_SQL = (
    f"INSERT INTO topic_texts ({', '.join(TOPIC_COLUMNS)}) VALUES ("
    + ", ".join("%s::jsonb" if c in _JSONB else "%s" for c in TOPIC_COLUMNS)
    + ") RETURNING id")


def topic_row(*, subject_code, unit_number, variant, plan, index, result, source_sha256, rules_version,
              avoid_notes=()) -> dict:
    """The topic_texts values for one written topic. check_data = the check-log entry + the status."""
    if result.status == "gap":
        raise ValueError(f"'{result.topic}' is a gap - nothing was written, nothing to save")
    check = dict(_writer_log(result))
    check["status"] = result.status
    return {"subject_code": subject_code, "unit_number": int(unit_number), "variant": variant,
            "topic": plan.topic, "topic_index": int(index), "raw_answer": result.raw_answer,
            "markdown": result.markdown, "check_data": check, "source_sha256": source_sha256,
            "passages_sha256": ts.passages_sha256(plan), "rules_version": rules_version,
            "avoid_notes": list(avoid_notes)}


def topic_insert_params(row) -> tuple:
    return tuple(json.dumps(row[c], ensure_ascii=False) if c in _JSONB else row[c] for c in TOPIC_COLUMNS)


@dataclass
class GeneratedUnit:
    pdf_bytes: bytes
    run: object
    storage_key: str
    row_values: dict
    topic_row_ids: list = None       # BL-037 d: topic_texts ids saved during this run (Notes)


def _report(on_run, run, variant, subject_code, unit_number):
    """Hand the check log to the caller; a failing logger never breaks generation."""
    if on_run:
        try:
            on_run(run_report(run, variant, subject_code, unit_number))
        except Exception:                       # noqa: BLE001 - logging is best effort
            pass


def generate_unit_pdf(*, variant, subject_code, subject_name, dept_code, dept_name, unit_number,
                      unit_title, regulation, groq_chat, progress=None, on_run=None,
                      clock=time.monotonic, sleep=time.sleep, resources_dir=None,
                      save_topic=None) -> GeneratedUnit:
    """save_topic(row) -> id (BL-037 d): called for every covered Notes topic right after its checks.
    If it raises, the run stops (RunAborted) - topics saved before that stay saved."""
    key = storage_key(dept_code, subject_code, unit_number, variant)       # bad variant -> no calls
    saved_ids = []

    if variant == "Notes":
        def ask(prompt):
            return groq_chat(prompt, max_completion_tokens=sp.NOTES_MAX_COMPLETION_TOKENS,
                             reasoning_effort=REASONING_EFFORT)
        on_topic = None
        if save_topic:
            rules = sr.prompt_version()

            def on_topic(ctx, plan, index, res, notes):
                saved_ids.append(save_topic(topic_row(
                    subject_code=subject_code, unit_number=unit_number, variant=variant, plan=plan,
                    index=index, result=res, source_sha256=ctx.source.sha256, rules_version=rules,
                    avoid_notes=notes)))
        run = sr.run_unit_notes(subject_code, unit_number, subject_name, unit_title, ask=ask,
                                progress=progress, clock=clock, sleep=sleep, resources_dir=resources_dir,
                                on_topic=on_topic)
        _report(on_run, run, variant, subject_code, unit_number)
        buf = spdf.build_notes_pdf(run, subject_name, subject_code, dept_name, unit_title, regulation)
    else:
        def ask(prompt, rf):
            return groq_chat(prompt, max_completion_tokens=sq.QA_MAX_COMPLETION_TOKENS,
                             reasoning_effort=REASONING_EFFORT, response_format=rf)
        run = sr.run_unit_qa(subject_code, unit_number, subject_name, unit_title, regulation, ask=ask,
                             progress=progress, clock=clock, sleep=sleep, resources_dir=resources_dir)
        _report(on_run, run, variant, subject_code, unit_number)
        buf = spdf.build_qa_pdf(run, subject_name, subject_code, dept_name, unit_title, regulation)
    return GeneratedUnit(pdf_bytes=buf.getvalue(), run=run, storage_key=key, row_values=row_values(run),
                         topic_row_ids=list(saved_ids))


@dataclass
class GeneratedTopics:
    run: object                      # source_runner.UnitNotesRun of ONLY the topics written
    topic_row_ids: list              # topic_texts ids saved during this run


def generate_topics(*, subject_code, subject_name, unit_number, unit_title, groq_chat, topics, save_topic,
                    avoid=None, variant="Notes", progress=None, on_run=None, clock=time.monotonic,
                    sleep=time.sleep, resources_dir=None) -> GeneratedTopics:
    """BL-037 e: write only `topics` (syllabus order), each with its reject notes (avoid={topic: [notes]}), save each
    covered topic with save_topic(row) -> id right after its checks. No PDF. Refuses before any Groq call when
    the variant is not Notes, the list is empty, a topic is unknown, or there is no save step."""
    if variant != "Notes":
        raise ValueError("per-topic writing is built for Notes only (QA per-topic comes later)")
    if not topics:
        raise ValueError("no topics to write")
    if save_topic is None:
        raise ValueError("per-topic writing needs a save step (the topics are the only output)")
    saved_ids = []
    rules = sr.prompt_version()

    def ask(prompt):
        return groq_chat(prompt, max_completion_tokens=sp.NOTES_MAX_COMPLETION_TOKENS,
                         reasoning_effort=REASONING_EFFORT)

    def on_topic(ctx, plan, index, res, notes):
        saved_ids.append(save_topic(topic_row(
            subject_code=subject_code, unit_number=unit_number, variant=variant, plan=plan, index=index,
            result=res, source_sha256=ctx.source.sha256, rules_version=rules, avoid_notes=notes)))
    run = sr.run_unit_notes(subject_code, unit_number, subject_name, unit_title, ask=ask, progress=progress,
                            clock=clock, sleep=sleep, resources_dir=resources_dir, topics=list(topics),
                            avoid=avoid or {}, on_topic=on_topic)
    _report(on_run, run, variant, subject_code, unit_number)
    return GeneratedTopics(run=run, topic_row_ids=list(saved_ids))


def build_pdf_from_topics(*, ctx, view, subject_name, subject_code, dept_code, dept_name, unit_number, unit_title,
                          regulation, variant="Notes") -> GeneratedUnit:
    """BL-037 f: the Notes PDF from John's APPROVED topic texts (no model call). Syllabus order, gaps listed.
    Refuses (ValueError) unless every covered topic is approved (topic_store.pdf_row_ids)."""
    if variant != "Notes":
        raise ValueError("a PDF from approved topics is built for Notes only (QA per-topic comes later)")
    ids = ts.pdf_row_ids(view)                                     # not every topic approved -> ValueError
    key = storage_key(dept_code, subject_code, unit_number, variant)
    topics = [sp.TopicResult(e.topic, "gap", "") if ts.is_gap(e)       # BL-041: marked topics are gaps too
              else sp.TopicResult(e.topic, "ok", e.row["markdown"]) for e in view]
    run = sr.UnitNotesRun(source=ctx.source, topics=topics, gaps=[t.topic for t in topics if t.status == "gap"],
                          needs_review=[], failed=[], prompt_version=sr.prompt_version())
    buf = spdf.build_notes_pdf(run, subject_name, subject_code, dept_name, unit_title, regulation)
    return GeneratedUnit(pdf_bytes=buf.getvalue(), run=run, storage_key=key, row_values=row_values(run),
                         topic_row_ids=list(ids))


def rechecked_topic_row(entry, plan, *, subject_code, unit_number, rules_version, variant="Notes"):
    """BL-042 button: a NEEDS REVIEW / OUT OF DATE topic whose SAVED raw answer passes today's checks (same source and
    passages) -> the values for a NEW draft topic_texts row with today's kept text. No model call. None when that
    would not help (nothing saved, source/passages changed, still failing, or the same text)."""
    row = entry.row
    if row is None or entry.state not in (ts.NEEDS_REVIEW, ts.STALE):
        return None
    if "source or passages changed" in (entry.why or []) or row["passages_sha256"] != ts.passages_sha256(plan):
        return None
    chk = sp.check_notes_answer(row["raw_answer"], plan)
    if not chk.ok:
        return None
    markdown, removed = chk.markdown, list((row.get("check_data") or {}).get("removed") or [])
    if removed:                                  # BL-043: John's removals are re-applied when they still match;
        try:                                     # if not, the full text comes back as a draft for him to read
            markdown = ts.apply_removals(markdown, removed)
        except ts.RemoveError:
            removed = []
    if markdown == row["markdown"]:
        return None
    res = sp.TopicResult(plan.topic, "ok", markdown, checks=[chk], calls=0, used=0)
    new = topic_row(subject_code=subject_code, unit_number=unit_number, variant=variant, plan=plan,
                    index=row["topic_index"], result=res, source_sha256=row["source_sha256"],
                    rules_version=rules_version, avoid_notes=list(row.get("avoid_notes") or []))
    new["check_data"]["rechecked_from"] = row["id"]
    if removed:
        new["check_data"]["removed"] = removed
    return new
