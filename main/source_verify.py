"""
source_verify.py — BL-028: a second Groq call checks every sentence against the passages it came from.

Design: 'Python scripts for Anna University Course Material Generation/BL028_DESIGN.md'
(John, 2026-09-27: option C, 30% rule). Pure module: no Streamlit, no network, no DB — the Groq call is
the injected ask(prompt, response_format).

Why: live run #2 printed an invented sentence built from ordinary words ('predefined threshold ...');
the lexical checks in source_prompts / source_qa cannot see such additions.

Notes: the writer's checked Markdown -> numbered sentences S1..Sn (headings and [Pn] tags not sent) ->
verdicts -> unsupported / unanswered sentences REMOVED (their [Pn] tags move to the last kept sentence of
the block; an empty block disappears). More than REMOVE_LIMIT removed -> 'needs_review' (left out of the
student PDF). Verifier answer not valid -> one re-ask -> still bad -> 'needs_review'.
QA: one 'sentence' per question+answer item; unsupported item dropped; same 30% rule.
Honest limit: the verifier is the same model; it can miss an addition or remove a true sentence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from source_prompts import _HEADING_RE, _LIST_RE, _blocks

VERIFY_MAX_COMPLETION_TOKENS = 2000     # verdicts + gpt-oss reasoning; prompt + this must fit the minute
REMOVE_LIMIT = 0.30                     # more than this share removed -> the topic needs review (John)

_TAG = r"\[\s*P\d+(?:\s*,\s*P\d+)*\s*\]"
_CITE_RE = re.compile(r"[^\S\n]*" + _TAG)
_TAG_RE = re.compile(_TAG)
# a sentence ends at . ? ! (+ any [Pn] tags) when the next thing starts like a sentence, or the text ends
_END_RE = re.compile(r"[.?!](?:[^\S\n]*" + _TAG + r")*(?=\s+[A-Z0-9“\"(]|\s*$)")


@dataclass
class Sentence:
    sid: str
    text: str            # clean text sent to the verifier (no [Pn] tags, single spaces)
    raw: str = ""        # as written, with its tags
    block: int = -1


@dataclass
class VerifyResult:
    markdown: str
    status: str                          # ok | needs_review
    removed: list = field(default_factory=list)
    total: int = 0
    calls: int = 0
    reasons: dict = field(default_factory=dict)
    verdicts: list = field(default_factory=list)     # [{id, sentence, supported, reason}] for the check log
    error: str = ""


@dataclass
class VerifyQAResult:
    items: dict
    status: str
    removed: list = field(default_factory=list)
    total: int = 0
    calls: int = 0
    verdicts: list = field(default_factory=list)
    error: str = ""


def _clean(text):
    return re.sub(r"\s+", " ", _CITE_RE.sub("", text)).strip()


def _segments(text):
    """Split one block's text into sentence pieces (raw, with their tags)."""
    out, start = [], 0
    for m in _END_RE.finditer(text):
        piece = text[start:m.end()].strip()
        if piece:
            out.append(piece)
        start = m.end()
    rest = text[start:].strip()
    if rest:
        if out and not _clean(rest):          # only tags left -> belong to the previous sentence
            out[-1] = out[-1] + " " + rest
        else:
            out.append(rest)
    return out


def _parse(markdown):
    """[(kind, prefix, [raw pieces], blank_before, heading_text)] + numbered Sentences."""
    blocks, sents = [], []
    for text, kind, blank in _blocks(markdown or ""):
        if kind == "heading":
            blocks.append((kind, "", [text], blank))
            continue
        prefix = ""
        m = _LIST_RE.match(text)
        if kind == "item" and m:
            prefix, text = text[:m.end()], text[m.end():]
        pieces = _segments(text)
        blocks.append((kind, prefix, pieces, blank))
        for raw in pieces:
            if _clean(raw):
                sents.append(Sentence(f"S{len(sents) + 1}", _clean(raw), raw, len(blocks) - 1))
    return blocks, sents


def split_sentences(markdown):
    return _parse(markdown)[1]


def verify_response_format():
    item = {"type": "object",
            "properties": {"id": {"type": "string"}, "supported": {"type": "boolean"},
                           "reason": {"type": "string"}},
            "required": ["id", "supported", "reason"], "additionalProperties": False}
    schema = {"type": "object", "properties": {"verdicts": {"type": "array", "items": item}},
              "required": ["verdicts"], "additionalProperties": False}
    return {"type": "json_schema", "json_schema": {"name": "sentence_verdicts", "strict": True, "schema": schema}}


def build_verify_prompt(ctx, plan, sentences, what="study notes"):
    passages = "\n\n".join(f"[{p.pid}] {p.text}" for p in plan.passages)
    listed = "\n".join(f"{s.sid}: {s.text}" for s in sentences)
    return (
        f"You are checking {what} against their source, sentence by sentence.\n"
        f"Sub-topic: \"{plan.topic}\". Source: {ctx.source.title}.\n\n"
        "For EACH numbered sentence decide whether it is supported by the PASSAGES.\n"
        "- supported = true: everything the sentence states (facts, steps, rules, conditions, examples, "
        "numbers, names, conclusions) is stated in the passages. Rephrasing, reordering, summarising and "
        "grammar fixes are fine.\n"
        "- not supported = false: the sentence adds anything the passages do not state, e.g. an extra "
        "condition, rule, piece of advice, conclusion, example or detail.\n"
        "- If unsure, answer false.\n"
        "Return exactly one verdict per sentence id (S1, S2, ...). reason: at most 8 words when false, "
        "empty when true.\n\n"
        "PASSAGES\n"
        f"{passages}\n\n"
        "SENTENCES\n"
        f"{listed}\n"
    )


def parse_verdicts(raw):
    """{id: supported} from the verifier's JSON; ValueError if it is not the agreed shape."""
    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    if not isinstance(data, dict) or not isinstance(data.get("verdicts"), list):
        raise ValueError("no 'verdicts' list")
    out = {}
    for v in data["verdicts"]:
        if isinstance(v, dict) and isinstance(v.get("id"), str) and isinstance(v.get("supported"), bool):
            out[v["id"].strip()] = (v["supported"], str(v.get("reason", "")))
    return out


def _verdict_log(sentences, verdicts):
    out = []
    for s in sentences:
        ok, reason = verdicts.get(s.sid, (False, "no verdict returned"))
        out.append({"id": s.sid, "sentence": s.text, "supported": ok, "reason": reason})
    return out


def decide(removed, total):
    if total == 0:
        return "ok"
    return "needs_review" if removed / total > REMOVE_LIMIT else "ok"


def apply_verdicts(markdown, sentences, supported):
    """Remove unsupported / unanswered sentences. Returns (markdown, [removed clean texts])."""
    bad = {s.sid for s in sentences if not supported.get(s.sid, False)}
    if not bad:
        return markdown, []
    blocks, sents = _parse(markdown)
    by_raw_block = {}
    for s in sents:
        by_raw_block.setdefault(s.block, []).append(s)
    removed, parts = [], []
    kept_blocks = []
    for bi, (kind, prefix, pieces, blank) in enumerate(blocks):
        if kind == "heading":
            kept_blocks.append((pieces[0], kind, blank))
            continue
        keep, lost_tags = [], []
        for s in by_raw_block.get(bi, []):
            if s.sid in bad:
                removed.append(s.text)
                lost_tags += _TAG_RE.findall(s.raw)
            else:
                keep.append(s.raw)
        if not keep:
            continue
        missing = [t for t in dict.fromkeys(lost_tags) if t not in keep[-1]]
        if missing:
            keep[-1] = keep[-1] + " " + " ".join(missing)
        kept_blocks.append((prefix + " ".join(keep), kind, blank))
    for i, (text, kind, blank) in enumerate(kept_blocks):
        if i:
            tight = kind == "item" and kept_blocks[i - 1][1] == "item" and not blank
            parts.append("\n" if tight else "\n\n")
        parts.append(text)
    return ("".join(parts) + ("\n" if parts else "")), removed


def _ask_verdicts(ctx, plan, sentences, ask, what):
    """(verdicts or None, calls). One re-ask when the answer is not the agreed JSON."""
    prompt = build_verify_prompt(ctx, plan, sentences, what)
    rf = verify_response_format()
    for call in (1, 2):
        try:
            return parse_verdicts(ask(prompt, rf)), call
        except (ValueError, TypeError):
            if call == 1:
                prompt = (prompt + "\nYOUR PREVIOUS ANSWER WAS NOT VALID JSON in the required shape. "
                          "Answer again: one verdict per sentence id.\n")
    return None, 2


def verify_notes(ctx, plan, markdown, ask) -> VerifyResult:
    sentences = split_sentences(markdown)
    if not sentences:
        return VerifyResult(markdown, "ok")
    verdicts, calls = _ask_verdicts(ctx, plan, sentences, ask, "study notes")
    if verdicts is None:
        return VerifyResult(markdown, "needs_review", total=len(sentences), calls=calls,
                            error="verifier answer not valid JSON (twice)")
    supported = {k: ok for k, (ok, _r) in verdicts.items()}
    new_md, removed = apply_verdicts(markdown, sentences, supported)
    reasons = {k: r for k, (ok, r) in verdicts.items() if not ok}
    return VerifyResult(new_md if removed else markdown, decide(len(removed), len(sentences)),
                        removed, len(sentences), calls, reasons, _verdict_log(sentences, verdicts))


def verify_qa(ctx, plan, items, ask) -> VerifyQAResult:
    flat = [(key, it) for key in items for it in items[key]]
    if not flat:
        return VerifyQAResult(items, "ok")
    sentences = [Sentence(f"S{i + 1}", f"Question: {_clean(it['question'])} Answer: {_clean(it['answer'])}")
                 for i, (_k, it) in enumerate(flat)]
    verdicts, calls = _ask_verdicts(ctx, plan, sentences, ask, "exam questions and answers")
    if verdicts is None:
        return VerifyQAResult(items, "needs_review", total=len(flat), calls=calls,
                              error="verifier answer not valid JSON (twice)")
    kept = {k: [] for k in items}
    removed = []
    for s, (key, it) in zip(sentences, flat):
        if verdicts.get(s.sid, (False, ""))[0]:
            kept[key].append(it)
        else:
            removed.append(it["question"])
    return VerifyQAResult(kept, decide(len(removed), len(flat)), removed, len(flat), calls,
                          _verdict_log(sentences, verdicts))
