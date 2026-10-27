"""topic_review.py - BL-037 step e (BL037_DESIGN.md f1cf0ca455; John's go 2026-09-29): the decisions behind the
Admin per-topic screen for Notes, kept out of Streamlit so they are tested offline. No Streamlit, no DB connection,
no network, writes nothing. The states themselves come from topic_store (Q2 re-check included).

  TOPIC_ROWS_SQL / TOPIC_ROW_COLUMNS -> every attempt of one unit + variant (history; topic_store picks the current)
  TOPIC_REVIEW_SQL                   -> ONE update of review_status / reviewed_at / review_note, only for the exact
                                        row John looked at (same id + same review + same generated_at) AND only while
                                        it is still the newest attempt of its topic (migration_007 allows only the
                                        review columns to change anyway)
  LABELS / state_text(entry)         -> the plain-words status of a topic
  can_approve / can_reject / can_regenerate(entry)
                                     -> Approve only a DRAFT (it passed today's checks). Reject a draft, an approved
                                        topic (change of mind) or one that needs review. Regenerate anything written
                                        or missing except an APPROVED topic (Q4: approved is never touched) and a gap.
  review_params(decision, note, entry, seen) -> the values for TOPIC_REVIEW_SQL (reject needs a note)
  check_summary(row) / counts_line(view)     -> what the checks said / how far the unit is
  generation_plan(view, rows, only=None)     -> (topics to write, {topic: reject notes}) for Generate (Q4, Q5)
  generate_label(view)                       -> the main Generate button text
The PDF from approved topics is step f; nothing here builds or serves a PDF.

BL-041 (BL041_DESIGN.md; John Q1-Q4, 2026-09-29): 'Not really in the source' marks (Neon topic_gap_marks,
migration_008).
  GAP_MARK_COLUMNS / GAP_MARK_ROWS_SQL -> every mark of one unit (active + undone; topic_store picks what counts)
  GAP_MARK_INSERT_SQL                  -> ONE new active mark, only if this topic has no active mark for this source
                                          (a double click inserts 0 rows)
  GAP_MARK_UNDO_SQL                    -> sets unmarked_at on exactly the mark John saw, only while it is active
  can_mark / can_unmark(entry)         -> mark any COVERED topic (any state); undo only a MARKED topic
  mark_params(note, entry, ...)        -> values for GAP_MARK_INSERT_SQL (a note is REQUIRED, Q2)
  unmark_params(entry, seen_mark_id)   -> values for GAP_MARK_UNDO_SQL

BL-043 (BL043_DESIGN.md; John Q1-Q4 + F1-F8, 2026-09-29): 'Remove lines'.
  can_remove_lines(entry)              -> only a DRAFT or APPROVED topic (approved = change of mind -> new draft)
  removal_row(entry, seen_id, texts, note)
                                       -> the values for a NEW draft topic_texts row (TOPIC_INSERT_SQL): a copy of the
                                          row John saw, markdown without the ticked lines, check_data['removed'] + the
                                          removed lines with John's note (REQUIRED). No Groq call, no DB change.
  removed_text(row)                    -> 'N line(s) removed by you: <notes>' for the topic box
"""
from __future__ import annotations

import copy

import topic_store as ts
from source_review import NOTE_MAX, ReviewError

DECISIONS = ("approved", "rejected")

TOPIC_ROW_COLUMNS = ("id", "subject_code", "unit_number", "variant", "topic", "topic_index", "raw_answer",
                     "markdown", "check_data", "source_sha256", "passages_sha256", "rules_version",
                     "avoid_notes", "generated_at", "review_status", "reviewed_at", "review_note")
TOPIC_ROWS_SQL = (f"SELECT {', '.join(TOPIC_ROW_COLUMNS)} FROM topic_texts "
                  "WHERE subject_code = %s AND unit_number = %s AND variant = %s ORDER BY generated_at, id")

GAP_MARK_COLUMNS = ("id", "subject_code", "unit_number", "topic", "source_sha256", "note", "marked_at",
                    "unmarked_at")
GAP_MARK_ROWS_SQL = (f"SELECT {', '.join(GAP_MARK_COLUMNS)} FROM topic_gap_marks "
                     "WHERE subject_code = %s AND unit_number = %s ORDER BY marked_at, id")
GAP_MARK_INSERT_SQL = """
INSERT INTO topic_gap_marks (subject_code, unit_number, topic, source_sha256, note)
SELECT %s, %s, %s, %s, %s
 WHERE NOT EXISTS (SELECT 1 FROM topic_gap_marks
                    WHERE subject_code = %s AND unit_number = %s AND topic = %s AND source_sha256 = %s
                      AND unmarked_at IS NULL)
ON CONFLICT DO NOTHING
"""
GAP_MARK_UNDO_SQL = "UPDATE topic_gap_marks SET unmarked_at = NOW() WHERE id = %s AND unmarked_at IS NULL"

TOPIC_REVIEW_SQL = """
UPDATE topic_texts AS t
   SET review_status = %s, reviewed_at = NOW(), review_note = %s
 WHERE t.id = %s AND t.review_status = %s AND t.generated_at = %s
   AND NOT EXISTS (SELECT 1 FROM topic_texts AS n
                    WHERE n.subject_code = t.subject_code AND n.unit_number = t.unit_number
                      AND n.variant = t.variant AND n.topic = t.topic
                      AND (n.generated_at, n.id) > (t.generated_at, t.id))
"""

LABELS = {
    ts.GAP: "not in the source",
    ts.NONE: "not written yet",
    ts.STALE: "OUT OF DATE",
    ts.FAILED: "FAILED the checks",
    ts.REJECTED: "REJECTED",
    ts.NEEDS_REVIEW: "NEEDS REVIEW",
    ts.DRAFT: "DRAFT — waiting for your review",
    ts.APPROVED: "APPROVED",
    ts.MARKED: "MARKED not really in the source",
}

_COUNT_WORDS = ((ts.APPROVED, "approved"), (ts.DRAFT, "draft"), (ts.NEEDS_REVIEW, "need review"),
                (ts.REJECTED, "rejected"), (ts.FAILED, "failed"), (ts.STALE, "out of date"),
                (ts.NONE, "not written yet"))


def _mark_text(mark) -> str:
    when = mark.get("marked_at")
    when = when.strftime("%d %b %Y %H:%M") if hasattr(when, "strftime") else str(when or "")
    return f"on {when}: {(mark.get('note') or '').strip()}"


def old_mark_text(entry) -> str:
    """BL-041: an active mark made for an OLDER source file - shown, but it does not count."""
    m = entry.old_mark
    return ("" if not m else "Old mark (the source file changed since, so it does not count any more) "
            + _mark_text(m) + " - read the new source and mark it again if it still applies.")


def state_text(entry) -> str:
    if entry.state == ts.MARKED:
        return ("MARKED by you as NOT REALLY IN THE SOURCE " + _mark_text(entry.mark or {})
                + ". Listed as a gap in the PDF; Generate skips it. 'Undo this mark' brings it back as it was.")
    row = entry.row or {}
    why = "; ".join(entry.why or [])
    note = (row.get("review_note") or "").strip()
    return {
        ts.GAP: "Not in the verified source — listed as a gap in the PDF. Nothing to write.",
        ts.NONE: "Not written yet. Generate writes it.",
        ts.STALE: "OUT OF DATE" + (f" ({why})" if why else "") + ". Generate writes it again.",
        ts.FAILED: "The last answer FAILED the checks, so it is not used. Generate writes it again.",
        ts.REJECTED: "REJECTED." + (f" Your note: {note}." if note else "")
                     + " Generate writes it again and tells the writer: Do NOT write this.",
        ts.NEEDS_REVIEW: "Was APPROVED, but it no longer passes today's checks" + (f" ({why})" if why else "")
                         + ". Reject it with a note, or Generate writes it again.",
        ts.DRAFT: "DRAFT — read it against the source passages, then Approve or Reject.",
        ts.APPROVED: "APPROVED — kept as it is; Generate never touches it. (Reject it if you change your mind.)",
    }[entry.state]


def can_approve(entry) -> bool:
    return entry.state == ts.DRAFT and entry.row is not None


def can_reject(entry) -> bool:
    return entry.state in (ts.DRAFT, ts.APPROVED, ts.NEEDS_REVIEW) and entry.row is not None


def can_regenerate(entry) -> bool:
    return entry.state in ts.REGENERATE or entry.state == ts.DRAFT


def can_mark(entry) -> bool:
    """BL-041: any COVERED topic (whatever its state) can be marked; a source gap or a marked topic cannot."""
    return not ts.is_gap(entry)


def can_unmark(entry) -> bool:
    return entry.state == ts.MARKED and entry.mark is not None


def mark_params(note, entry, *, subject_code, unit_number, source_sha256) -> tuple:
    """BL-041 Q2: a note is REQUIRED (max NOTE_MAX). Values for GAP_MARK_INSERT_SQL."""
    if entry is None:
        raise ReviewError("This topic is not a sub-topic of this unit.")
    if not can_mark(entry):
        raise ReviewError(f"A topic that is {LABELS[entry.state]} cannot be marked.")
    note = (note or "").strip()
    if not note:
        raise ReviewError("Please write a short note saying why this topic is not really in the source.")
    if len(note) > NOTE_MAX:
        raise ReviewError(f"The note is too long ({len(note)} characters, max {NOTE_MAX}).")
    key = (subject_code, int(unit_number), entry.topic, source_sha256)
    return key + (note,) + key


def unmark_params(entry, seen_mark_id) -> tuple:
    if entry is None or not can_unmark(entry):
        raise ReviewError("This topic is not marked (any more). Look at it again below.")
    if entry.mark.get("id") != seen_mark_id:
        raise ReviewError("this mark changed since the page loaded. Look at it again below.")
    return (entry.mark["id"],)


def review_params(decision, note, entry, seen) -> tuple:
    """seen = {'id', 'review_status', 'generated_at'} of the row shown on the screen when John clicked."""
    if decision not in DECISIONS:
        raise ReviewError(f"Unknown decision '{decision}'")
    row = entry.row if entry is not None else None
    if row is None:
        raise ReviewError("This topic has nothing written yet - generate it first.")
    if (row.get("id"), row.get("review_status"), row.get("generated_at")) != \
            (seen.get("id"), seen.get("review_status"), seen.get("generated_at")):
        raise ReviewError("this topic changed since the page loaded (maybe it was written again or reviewed "
                          "in another tab). Look at it again below.")
    allowed = can_approve(entry) if decision == "approved" else can_reject(entry)
    if not allowed:
        verb = "approved" if decision == "approved" else "rejected"
        raise ReviewError(f"A topic that is {LABELS[entry.state]} cannot be {verb}.")
    note = (note or "").strip()
    if decision == "rejected" and not note:
        raise ReviewError("Please write a short note saying why you reject this topic "
                          "(it is also sent to the writer as 'Do NOT write this').")
    if len(note) > NOTE_MAX:
        raise ReviewError(f"The note is too long ({len(note)} characters, max {NOTE_MAX}).")
    return (decision, note or None, row["id"], row["review_status"], row["generated_at"])


def check_summary(row) -> dict:
    d = (row or {}).get("check_data") or {}
    return {"status": d.get("status"), "calls": d.get("calls"), "used_try": d.get("used_try"),
            "reask_reason": d.get("reask_reason"), "new_terms": list(d.get("new_terms") or []),
            "odd_chars": list(d.get("odd_chars") or []), "latex": list(d.get("latex") or []),
            "dropped": len(d.get("dropped") or []), "kept_leadins": len(d.get("kept_leadins") or []),
            "sections": list(d.get("sections") or []), "layout": list(d.get("layout") or []),       # BL-038 b
            "list_tagged": len(d.get("list_tagged") or []),                                        # BL-042
            "trimmed": list(d.get("trimmed") or []), "note": d.get("note") or ""}                  # BL-039


def counts_line(view) -> str:
    cov = [e for e in view if not ts.is_gap(e)]
    parts = [f"{n} {word}" for state, word in _COUNT_WORDS
             for n in [sum(1 for e in cov if e.state == state)] if n]
    gaps = sum(1 for e in view if e.state == ts.GAP)
    marked = sum(1 for e in view if e.state == ts.MARKED)
    return (f"{len(cov)} covered sub-topics: " + " · ".join(parts)
            + (f" | {gaps} not in the source" if gaps else "")
            + (f" | {marked} marked not really in the source" if marked else ""))


def generation_plan(view, rows, only=None):
    """(topics to write, {topic: reject notes}). only = one topic for 'Regenerate this topic'."""
    if only is None:
        topics = ts.to_regenerate(view)
    else:
        entry = next((e for e in view if e.topic == only), None)
        if entry is None:
            raise ValueError(f"'{only}' is not a sub-topic of this unit")
        if not can_regenerate(entry):
            raise ValueError(f"'{only}' is {LABELS[entry.state]} - it is not written again")
        topics = [only]
    avoid = {}
    for t in topics:
        notes = ts.avoid_notes(rows, t)
        if notes:
            avoid[t] = notes
    return topics, avoid


def generate_label(view) -> str:
    n = len(ts.to_regenerate(view))
    m = sum(1 for e in view if e.state == ts.APPROVED)
    d = sum(1 for e in view if e.state == ts.DRAFT)
    if not n:
        return (f"⚡ Generate — nothing to write ({m} approved"
                + (f", {d} waiting for your review" if d else "") + ")")
    return f"⚡ Generate {n} topic(s) — keeps {m} approved"


# ---------- BL-043: 'Remove lines' ----------

# exactly the columns source_app.TOPIC_INSERT_SQL sends (a test keeps the two equal)
REMOVAL_COPY = ("subject_code", "unit_number", "variant", "topic", "topic_index", "raw_answer", "source_sha256",
                "passages_sha256", "rules_version", "avoid_notes")


def can_remove_lines(entry) -> bool:
    """F1: a DRAFT or an APPROVED topic. Not NEEDS REVIEW / OUT OF DATE (use re-checked text or Regenerate first),
    FAILED, REJECTED, a gap or a marked topic."""
    return entry is not None and entry.state in (ts.DRAFT, ts.APPROVED) and entry.row is not None


def removal_row(entry, seen_id, texts, note, parts=()) -> dict:
    """F2-F4: the new draft row with the ticked lines removed. Raises ReviewError (nothing saved) when the topic
    cannot be trimmed, changed since the page loaded, nothing / everything is ticked, or the note is missing.
    BL-045: parts = ticked SENTENCES [{'in_line', 'text'}]; a whole line ticked wins over its sentences (F2)."""
    if entry is None:
        raise ReviewError("This topic is not a sub-topic of this unit.")
    if not can_remove_lines(entry):
        raise ReviewError(f"Lines can be removed only from a DRAFT or APPROVED topic (this one is "
                          f"{LABELS[entry.state]}).")
    row = entry.row
    if row.get("id") != seen_id:
        raise ReviewError("this topic changed since the page loaded (maybe it was written again or trimmed in "
                          "another tab). Look at it again below.")
    texts = [t for t in (texts or []) if str(t).strip()]
    whole = {ts._key(t) for t in texts}
    parts = [{"in_line": p["in_line"], "text": p["text"]} for p in (parts or [])
             if str(p.get("text") or "").strip() and ts._key(p["in_line"]) not in whole]
    if not texts and not parts:
        raise ReviewError("Please tick at least one line to remove.")
    note = (note or "").strip()
    if not note:
        raise ReviewError("Please write a short note saying why these lines are removed.")
    if len(note) > NOTE_MAX:
        raise ReviewError(f"The note is too long ({len(note)} characters, max {NOTE_MAX}).")
    try:
        markdown = ts.remove_parts(row["markdown"], texts, parts)
    except ts.RemoveError as e:
        raise ReviewError(str(e)) from e
    data = copy.deepcopy(row.get("check_data") or {})
    data["removed"] = list(data.get("removed") or []) + [{"text": t, "note": note, "from_row": row["id"]}
                                                         for t in texts] \
        + [{"text": p["text"], "in_line": p["in_line"], "note": note, "from_row": row["id"]} for p in parts]
    new = {c: copy.deepcopy(row[c]) for c in REMOVAL_COPY}
    new["avoid_notes"] = list(new.get("avoid_notes") or [])
    new.update(markdown=markdown, check_data=data)
    return new


def removed_text(row) -> str:
    removed = ((row or {}).get("check_data") or {}).get("removed") or []
    if not removed:
        return ""
    notes = list(dict.fromkeys(" ".join(str(r.get("note") or "").split()) for r in removed))
    n_sent = sum(1 for r in removed if r.get("in_line"))          # BL-045
    n_line = len(removed) - n_sent
    what = " and ".join(x for x in (f"{n_line} line(s)" if n_line else "",
                                    f"{n_sent} sentence(s)" if n_sent else "") if x)
    return f"{what} removed by you: " + " | ".join(n for n in notes if n)
