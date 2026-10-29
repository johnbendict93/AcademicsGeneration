"""source_review.py - BL-029 step c: the review (approval) decisions academicsgen_app.py needs,
kept out of Streamlit so they are tested offline. No Streamlit, no network, no DB connection.

  password_ok(typed, secret)       -> hmac.compare_digest; a missing/empty secret NEVER unlocks
  review_state(row, sha, version)  -> 'none' | 'stale' | 'draft' | 'approved' | 'rejected'
  state_text(state, row)           -> the plain-words line shown in the Admin tab
  can_decide(row, sha, version)    -> Approve / Reject only for a PDF that is current
  review_params(...)               -> the values for REVIEW_SQL (checks decision + note)
  REVIEW_SQL                       -> ONE update of review_status / reviewed_at / review_note only
                                      (the migration_006 trigger leaves such an update alone), guarded
                                      so it only hits the exact PDF version John was looking at
  local_naive(dt)                  -> Neon generated_at as this PC's local time (check-log names)
  latest_check_log(...)            -> the newest check log (App/run_logs) made for THIS PDF version
  check_summary(report)            -> left-out topics, gaps, flagged new words / odd characters
R9: students get a PDF only when source_app.is_servable (approved AND current).
"""
from __future__ import annotations

import hmac
import json
import re
from datetime import datetime
from pathlib import Path

import source_app

DECISIONS = ("approved", "rejected")
NOTE_MAX = 1000

REVIEW_SQL = """
UPDATE generated_pdfs
   SET review_status = %s, reviewed_at = NOW(), review_note = %s
 WHERE id = %s AND storage_key = %s AND source_sha256 = %s AND prompt_version = %s
   AND generated_at = %s
"""


class ReviewError(ValueError):
    """A decision that must not be saved (wrong PDF version, missing note, ...)."""


def password_ok(typed, secret) -> bool:
    """Constant-time compare. No secret set (None / empty) -> locked for everyone."""
    if not isinstance(secret, str) or not secret:
        return False
    if not isinstance(typed, str) or not typed:
        return False
    return hmac.compare_digest(typed.encode("utf-8"), secret.encode("utf-8"))


def review_state(row, source_sha256, prompt_version, topic_ids=None) -> str:
    if not row or not row.get("storage_key"):
        return "none"
    if not source_app.is_current(row, source_sha256, prompt_version, topic_ids):
        return "stale"
    status = row.get("review_status")
    return status if status in ("draft", "approved", "rejected") else "draft"


def state_text(state, row=None) -> str:
    note = (row or {}).get("review_note") or ""
    return {
        "none": "No PDF yet for this unit and variant.",
        "stale": "The stored PDF is OUT OF DATE (the source file or the prompts changed). "
                 "Students cannot get it. Generate again.",
        "draft": "DRAFT - waiting for your review. Students cannot get it yet.",
        "approved": "APPROVED - students can download it.",
        "rejected": "REJECTED - students cannot get it." + (f" Your note: {note}" if note else ""),
    }[state]


def can_decide(row, source_sha256, prompt_version, topic_ids=None) -> bool:
    return review_state(row, source_sha256, prompt_version, topic_ids) in ("draft", "approved", "rejected")


def review_params(decision, note, row, source_sha256, prompt_version, topic_ids=None) -> tuple:
    """topic_ids (BL-037 f): the current approved topic ids, for a PDF built from topics."""
    if decision not in DECISIONS:
        raise ReviewError(f"Unknown decision '{decision}'")
    if not can_decide(row, source_sha256, prompt_version, topic_ids):
        raise ReviewError("This PDF is missing or out of date - generate it again before reviewing.")
    if row.get("id") is None or row.get("generated_at") is None:
        raise ReviewError("The PDF row has no id / generated_at - reload the page.")
    note = (note or "").strip()
    if decision == "rejected" and not note:
        raise ReviewError("Please write a short note saying why you reject it.")
    if len(note) > NOTE_MAX:
        raise ReviewError(f"The note is too long ({len(note)} characters, max {NOTE_MAX}).")
    return (decision, note or None, row["id"], row["storage_key"], row["source_sha256"],
            row["prompt_version"], row["generated_at"])


def local_naive(dt):
    """generated_at (timestamptz from Neon) -> this PC's local time without tz, like the check-log
    file names. None stays None; a naive value is returned as it is."""
    if dt is None or not isinstance(dt, datetime):
        return None
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


_STAMP = re.compile(r"_(\d{8}-\d{6})(?:_(\d+))?\.json$")


def latest_check_log(log_dir, subject_code, unit_number, variant, row, made_at=None):
    """Newest check log for THIS PDF version: same source sha + prompt_version, written no later
    than made_at (the row's generated_at as local time; logs are written before the upload).
    Returns (path, report) or None. Unreadable files are skipped."""
    if not row:
        return None
    best = None
    for p in Path(log_dir).glob(f"check_{subject_code}_U{unit_number}_{variant}_*.json"):
        m = _STAMP.search(p.name)
        if not m:
            continue
        try:
            stamp = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
        except ValueError:                    # not a real time -> not one of our logs
            continue
        if made_at is not None and stamp > made_at:
            continue
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict):
            continue
        if (report.get("source_sha256") != row.get("source_sha256")
                or report.get("prompt_version") != row.get("prompt_version")):
            continue
        key = (stamp, int(m.group(2) or 0))
        if best is None or key > best[0]:
            best = (key, p, report)
    return (best[1], best[2]) if best else None


def check_summary(report) -> dict:
    """What John should look at first when reading the PDF."""
    topics = report.get("topics") or []
    left_out = list(dict.fromkeys(list(report.get("needs_review") or []) + list(report.get("failed") or [])))
    flagged = []
    for t in topics:
        w = t.get("writer") or {}
        new_terms, odd, dropped = list(w.get("new_terms") or []), list(w.get("odd_chars") or []), \
            len(w.get("dropped") or [])
        latex = list(w.get("latex") or [])          # BL-031; older logs have no key
        if new_terms or odd or dropped or latex:
            flagged.append({"topic": t.get("topic"), "status": t.get("status"),
                            "new_terms": new_terms, "odd_chars": odd, "latex": latex, "dropped": dropped})
    return {"in_pdf": [t.get("topic") for t in topics if t.get("status") == "ok"],
            "gaps": [t.get("topic") for t in topics if t.get("status") == "gap"],
            "left_out": left_out, "flagged": flagged}
