"""topic_store.py - BL-037 step c (BL037_DESIGN.md; John's decisions Q1-Q5, 2026-09-29).

Pure rules for per-topic keep / regenerate. No Streamlit, no DB, no network, writes nothing.
Rows are plain dicts shaped like Neon topic_texts (migration_007): one row per written attempt of one topic.

  passages_sha256(plan)          -> fingerprint of the passages exactly as sent to the writer
  current_rows(rows)             -> {topic: newest row}
  recheck(row, plan)             -> (ok, why): re-runs TODAY's writer checks on the saved raw answer (no model call)
  topic_state(row, ctx, plan)    -> gap | none | stale | failed | rejected | needs_review | draft | approved
  unit_view(ctx, rows)           -> [TopicEntry] in syllabus order (gaps included)
  to_regenerate(view)            -> topics the main Generate remakes (Q4)
  avoid_notes(rows, topic)       -> John's reject notes for that topic, newest first (Q5)
  avoid_block(notes)             -> the 'Do NOT write this' lines added to that topic's prompt
  pdf_ready(view) / pdf_row_ids  -> the PDF is built only when every covered topic is approved (Q3)
  active_marks / old_marks       -> BL-041: John's 'Not really in the source' marks that count / that are out of date
  is_gap(entry)                  -> a gap of the source OR marked by John: listed in the PDF, never written
  content_blocks(markdown)       -> BL-043: the lines John can tick (paragraphs / bullets / steps; no headings)
  remove_blocks(markdown, texts) -> BL-043: the text without those lines (removal ONLY - checked in code)
  apply_removals(markdown, rem)  -> BL-043: re-applies John's saved removals (check_data['removed'])
  block_sentences(text)          -> BL-045: the sentences John can tick inside a multi-sentence line
  remove_parts(md, texts, parts) -> BL-045: remove whole lines AND single sentences (removal ONLY - checked in code)

BL-041 (BL041_DESIGN.md): a covered topic John MARKED 'not really in the source' (topic_gap_marks, migration_008)
is MARKED: treated like a gap everywhere (no Groq call, not needed for the PDF, listed on page 1). A mark counts
only while its source_sha256 is today's source; undoing it (unmarked_at) leaves the topic's rows as they were.

Q2: an APPROVED topic stays approved while its source + passages are unchanged AND its saved text still passes
today's checks with the SAME kept text. A code/rule change alone never throws an approval away.
Notes only for now; QA rows raise NotImplementedError (QA comes later, same table).

BL-043 (BL043_DESIGN.md; John Q1-Q4 + F1-F8, 2026-09-29): John removes bad lines from a draft / approved topic.
The trimmed text is a NEW draft row whose check_data['removed'] lists every removed line (cumulative). Removing
can never add a fact. recheck() re-applies the removals to today's checked text, so a rule change that alters the
kept text still sends the topic to NEEDS REVIEW; a removed line that no longer matches does too.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import source_prompts as sp

GAP, NONE, STALE, FAILED = "gap", "none", "stale", "failed"
REJECTED, NEEDS_REVIEW, DRAFT, APPROVED = "rejected", "needs_review", "draft", "approved"
MARKED = "marked"                                                 # BL-041: John marked it 'not really in the source'
REGENERATE = (NONE, STALE, FAILED, REJECTED, NEEDS_REVIEW)       # a draft waits for John; approved is kept
GAP_STATES = (GAP, MARKED)                                       # listed as gaps in the PDF, never written
AVOID_MAX_NOTES = 5


@dataclass
class TopicEntry:
    topic: str
    index: int              # 1-based position in the unit's syllabus order
    state: str
    row: dict | None
    why: list               # re-check reasons (empty unless the re-check failed)
    mark: dict | None = None      # BL-041: the active 'not really in the source' mark (state MARKED)
    old_mark: dict | None = None  # BL-041: an active mark made for an OLDER source (does not count)


def covered(plan) -> bool:
    return plan.status in ("FOUND", "PARTIAL") and bool(plan.passages)


def passages_sha256(plan) -> str:
    """sha256 of the passages exactly as build_notes_prompt puts them in the prompt."""
    sent = "\n\n".join(f"[{p.pid}] {p.text}" for p in plan.passages)
    return hashlib.sha256(sent.encode("utf-8")).hexdigest()


def _newest_key(row):
    return (row["generated_at"], row["id"])


def current_rows(rows) -> dict:
    cur = {}
    for r in rows:
        if r["topic"] not in cur or _newest_key(r) > _newest_key(cur[r["topic"]]):
            cur[r["topic"]] = r
    return cur


def recheck(row, plan):
    """Today's writer checks on the saved raw answer. ok only if the answer passes AND keeps the same text."""
    if row.get("variant", "Notes") != "Notes":
        raise NotImplementedError("per-topic re-check is built for Notes only (QA comes later)")
    chk = sp.check_notes_answer(row["raw_answer"], plan)
    why = []
    markdown = chk.markdown
    removed = (row.get("check_data") or {}).get("removed") or []
    if removed and chk.kept_blocks:                                  # BL-043 F5: John's removals, re-applied
        try:
            markdown = apply_removals(markdown, removed)
        except RemoveError:
            why.append("a line you removed no longer matches exactly one line of the re-checked text")
    if not chk.kept_blocks:
        why.append("nothing would be kept")
    if len(chk.new_terms) > sp.NEW_TERM_LIMIT:
        why.append("words not in the passages: " + ", ".join(chk.new_terms))
    if chk.odd_chars:
        why.append("odd characters: " + sp.describe_chars(chk.odd_chars))
    if chk.latex:
        why.append("LaTeX: " + ", ".join(chk.latex))
    if not why and not chk.ok:
        why.append("fails today's checks")
    if not why and markdown != row["markdown"]:
        why.append("today's checks would keep a different text than the one saved")
    return (not why), why


def topic_state(row, ctx, plan) -> str:
    return _state(row, ctx, plan)[0]


def _state(row, ctx, plan):
    if not covered(plan):
        return GAP, []
    if row is None:
        return NONE, []
    if row["source_sha256"] != ctx.source.sha256 or row["passages_sha256"] != passages_sha256(plan):
        return STALE, ["source or passages changed"]
    if (row.get("check_data") or {}).get("status") != "ok":
        return FAILED, ["the writer's answer did not pass the checks"]
    if row["review_status"] == REJECTED:
        return REJECTED, []
    ok, why = recheck(row, plan)
    if not ok:
        return (NEEDS_REVIEW if row["review_status"] == APPROVED else STALE), why
    return (APPROVED if row["review_status"] == APPROVED else DRAFT), []


def _newest_marks(marks, keep):
    out = {}
    for m in marks or ():
        if m.get("unmarked_at") is None and keep(m) and (
                m["topic"] not in out or (m["marked_at"], m["id"]) > (out[m["topic"]]["marked_at"], out[m["topic"]]["id"])):
            out[m["topic"]] = m
    return out


def active_marks(marks, source_sha256) -> dict:
    """BL-041: {topic: mark} - marks not undone AND made for THIS source file."""
    return _newest_marks(marks, lambda m: m["source_sha256"] == source_sha256)


def old_marks(marks, source_sha256) -> dict:
    """BL-041: {topic: mark} - marks not undone but made for an OLDER source: they do not count any more."""
    return _newest_marks(marks, lambda m: m["source_sha256"] != source_sha256)


def is_gap(entry) -> bool:
    return entry.state in GAP_STATES


def unit_view(ctx, rows, marks=()) -> list:
    """marks (BL-041) = topic_gap_marks rows of this unit (any state; only active ones for this source count)."""
    cur = current_rows(rows)
    active, old = active_marks(marks, ctx.source.sha256), old_marks(marks, ctx.source.sha256)
    out = []
    for i, plan in enumerate(ctx.topics, 1):
        row = cur.get(plan.topic)
        state, why = _state(row, ctx, plan)
        mark = active.get(plan.topic) if state != GAP else None
        if mark is not None:
            state, why = MARKED, []
        out.append(TopicEntry(plan.topic, i, state, row, why, mark, old.get(plan.topic)))
    return out


def to_regenerate(view) -> list:
    return [e.topic for e in view if e.state in REGENERATE]


def _clean(note) -> str:
    return " ".join(str(note or "").split())


def avoid_notes(rows, topic) -> list:
    seen, out = set(), []
    for r in sorted((r for r in rows if r["topic"] == topic and r["review_status"] == REJECTED),
                    key=_newest_key, reverse=True):
        n = _clean(r.get("review_note"))
        if n and n not in seen:
            seen.add(n)
            out.append(n)
        if len(out) == AVOID_MAX_NOTES:
            break
    return out


def avoid_block(notes) -> str:
    notes = [n for n in (_clean(n) for n in notes) if n]
    if not notes:
        return ""
    lines = ["", "EARLIER VERSIONS OF THIS SUB-TOPIC WERE REJECTED BY THE REVIEWER. Do NOT write this:"]
    lines += [f"- {n}" for n in notes]
    lines.append("All rules above still apply: write only what the passages say.")
    return "\n".join(lines) + "\n"


def pdf_ready(view) -> bool:
    cov = [e for e in view if not is_gap(e)]
    return bool(cov) and all(e.state == APPROVED for e in cov)


def pdf_row_ids(view) -> list:
    if not pdf_ready(view):
        raise ValueError("not every covered topic is approved - the PDF cannot be built yet")
    return [e.row["id"] for e in view if e.state == APPROVED]


# ---------- BL-043: John removes lines (removal only) ----------

class RemoveError(ValueError):
    """A removal that is refused - nothing is saved."""


_NUMBER_RE = re.compile(r"^(\d+)([.)])(\s)")


def _key(text) -> str:
    """A line's identity without its step number: '3. Step' == '2. Step' (numbers change on renumbering)."""
    return _NUMBER_RE.sub(lambda m: "#" + m.group(2) + m.group(3), str(text).strip(), count=1)


def _render(blocks) -> str:
    """The exact joining rule of source_prompts.check_notes_answer (tight bullets, blank line elsewhere)."""
    parts = []
    for i, (text, kind, blank_before) in enumerate(blocks):
        if i:
            tight = kind == "item" and blocks[i - 1][1] == "item" and not blank_before
            parts.append("\n" if tight else "\n\n")
        parts.append(text)
    return "".join(parts) + ("\n" if parts else "")


def _parse(markdown):
    blocks = sp._blocks(markdown)
    if _render(blocks) != markdown:
        raise RemoveError("this text cannot be split into lines safely - use Reject or Regenerate instead")
    return blocks


def content_blocks(markdown) -> list:
    """The lines John can tick, in order: every paragraph / bullet / step. Title + section headings are not listed."""
    return [t for t, kind, _b in sp._blocks(markdown or "") if kind != "heading"]


def _subsequence(small, big) -> bool:
    it = iter(big)
    return all(x in it for x in small)


def remove_blocks(markdown, texts) -> str:
    """The text without the given lines. Each text must match exactly ONE line (step numbers ignored); headings
    cannot be removed; at least one line must stay. A section left empty loses its heading; steps are renumbered.
    Removal ONLY: the result's lines must be the old lines minus the removed ones (checked, not trusted)."""
    texts = [t for t in (texts or []) if str(t).strip()]
    if not texts:
        raise RemoveError("tick at least one line to remove")
    blocks = _parse(markdown)
    drop = set()
    for t in texts:
        hits = [i for i, (bt, kind, _b) in enumerate(blocks) if kind != "heading" and _key(bt) == _key(t)]
        if len(hits) != 1 or hits[0] in drop:
            raise RemoveError("a removed line no longer matches exactly one line of the text: "
                              + " ".join(str(t).split())[:80])
        drop.add(hits[0])
    kept = [b for i, b in enumerate(blocks) if i not in drop]
    if not any(kind != "heading" for _t, kind, _b in kept):
        raise RemoveError("every line would be removed - use Reject or 'Not really in the source' instead")
    out = _render(sp._renumber(sp._drop_empty_sections(kept, [])))
    new = sp._blocks(out)                                           # F6: prove it is a removal and nothing else
    want_body = [_key(t) for i, (t, kind, _b) in enumerate(blocks) if i not in drop and kind != "heading"]
    got_body = [_key(t) for t, kind, _b in new if kind != "heading"]
    old_heads = [t for t, kind, _b in blocks if kind == "heading"]
    new_heads = [t for t, kind, _b in new if kind == "heading"]
    if got_body != want_body or new_heads[:1] != old_heads[:1] or not _subsequence(new_heads, old_heads):
        raise RuntimeError("BL-043 removal-only check failed - nothing saved")
    return out


def apply_removals(markdown, removed) -> str:
    """Re-apply John's saved removals (check_data['removed'] entries {'text', 'note', 'from_row'}; BL-045 sentence
    entries also carry 'in_line'). Without sentence entries: exactly as BL-043 (one call). With them: each round
    (consecutive entries of the same from_row) is re-applied in the order John made it."""
    if not any(r.get("in_line") for r in removed):
        return remove_blocks(markdown, [r["text"] for r in removed])
    rounds = []
    for r in removed:
        if not rounds or rounds[-1][0] != r.get("from_row"):
            rounds.append((r.get("from_row"), []))
        rounds[-1][1].append(r)
    for _rid, rs in rounds:
        markdown = remove_parts(markdown, [r["text"] for r in rs if not r.get("in_line")],
                                [{"in_line": r["in_line"], "text": r["text"]} for r in rs if r.get("in_line")])
    return markdown


# ---------- BL-045: John removes single SENTENCES of a line (removal only) ----------

_CITE_RE = sp._CITE_RE
_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")                # the list marker of a bullet / step
_SENT_END_RE = re.compile(r"[.?!](?:\s*\[\s*P\d+(?:\s*,\s*P\d+)*\s*\])*\s+(?=[A-Z0-9(])")
_ABBREV = {"e.g", "i.e", "etc", "vs", "cf", "eg", "ie", "approx", "fig", "eq", "no", "nos", "dr", "mr", "mrs",
           "ms", "st", "al"}


def _split(body):
    """F1: the sentences of a line body (marker removed) as exact pieces: ''.join(pieces) == body."""
    pieces, start = [], 0
    for m in _SENT_END_RE.finditer(body):
        i = m.start()
        before = body[start:i]
        if before.count("(") != before.count(")") or before.count("[") != before.count("]"):
            continue                                             # inside ( ) or [ ]
        word = re.search(r"([A-Za-z.]+)$", body[:i])
        if word and (word.group(1).lower().strip(".") in _ABBREV or len(word.group(1).strip(".")) == 1):
            continue                                             # e.g. / i.e. / etc. / vs. / initials
        pieces.append(body[start:m.end()])
        start = m.end()
    pieces.append(body[start:])
    return [p for p in pieces if p]


def _marker(line):
    m = _MARK_RE.match(line)
    return (m.group(0), line[m.end():]) if m else ("", line)


def block_sentences(text) -> list:
    """BL-045 Q2: the sentences John can tick inside one line; [] when the line has only one sentence."""
    _mark, body = _marker(str(text or ""))
    pieces = [p.strip() for p in _split(body)]
    return pieces if len(pieces) >= 2 and all(pieces) else []


def _ids(text):
    return [pid.strip() for tag in _CITE_RE.findall(text) for pid in tag.split(",")]


def _plain(text):
    return re.sub(r"\s+", "", _CITE_RE.sub("", text))


def _cut_sentences(line, sents):
    """The line without the given sentences (each must occur exactly once). Q1/F3: if no tag is left, the removed
    sentences' tag ids (only ids of this line) go to the end of what is left. None = every sentence removed."""
    mark, body = _marker(line)
    pieces = _split(body)
    keys = [p.strip() for p in pieces]
    drop = set()
    for s in sents:
        hits = [i for i, k in enumerate(keys) if k == str(s).strip()]
        if len(hits) != 1:
            raise RemoveError("a removed sentence no longer matches exactly once in its line: "
                              + " ".join(str(s).split())[:80])
        drop.add(hits[0])
    kept = [p for i, p in enumerate(pieces) if i not in drop]
    if not kept:
        return None
    text = "".join(kept).rstrip()
    if not _ids(text):
        moved = list(dict.fromkeys(pid for i, p in enumerate(pieces) if i in drop for pid in _ids(p)))
        if moved:
            tag = "[" + ", ".join(moved) + "]"
            last_dropped = pieces[max(drop)].rstrip()
            if text[-1:] in ".?!" and not last_dropped.endswith("]"):
                text = text[:-1] + " " + tag + text[-1]           # '...responses [P42].' (writer's style)
            else:
                text = text + " " + tag                           # '...responses. [P42]'
    return mark + text


def remove_parts(markdown, texts, parts) -> str:
    """BL-045: remove whole lines (texts, as remove_blocks) AND single sentences (parts: {'in_line', 'text'}).
    F2: a whole line ticked wins over its sentences; all sentences of a line ticked = the whole line.
    F6: every trimmed line must be its old line minus the removed sentences (tags aside), with no new tag id."""
    texts = [t for t in (texts or []) if str(t).strip()]
    parts = [p for p in (parts or []) if str(p.get("text") or "").strip()]
    if not parts:
        return remove_blocks(markdown, texts)
    blocks = _parse(markdown)
    whole = {_key(t) for t in texts}
    by_line = {}
    for p in parts:
        if _key(p["in_line"]) in whole:
            continue                                             # F2: whole line wins
        by_line.setdefault(_key(p["in_line"]), (p["in_line"], []))[1].append(p["text"])
    blocks = [list(b) for b in blocks]
    for k, (line, sents) in by_line.items():
        hits = [i for i, (bt, kind, _b) in enumerate(blocks) if kind != "heading" and _key(bt) == k]
        if len(hits) != 1:
            raise RemoveError("a removed line no longer matches exactly one line of the text: "
                              + " ".join(str(line).split())[:80])
        old = blocks[hits[0]][0]
        mark, body = _marker(old)
        new = _cut_sentences(old, sents)
        if new is None:
            texts.append(old)                                    # F2: all its sentences = the whole line
            continue
        pieces = _split(body)
        gone = {str(s).strip() for s in sents}
        want = _plain(mark + "".join(p for p in pieces if p.strip() not in gone))
        if _plain(new) != want or not set(_ids(new)) <= set(_ids(old)) or not _ids(new) \
                or new.count("\n") > old.count("\n"):
            raise RuntimeError("BL-045 removal-only check failed - nothing saved")
        blocks[hits[0]][0] = new
    out = _render([tuple(b) for b in blocks])
    return remove_blocks(out, texts) if texts else out
