"""source_qa.py - P2b of the source-strict app: QA built only from the verified
source (APP_SOURCE_STRICT_DESIGN.md sections 2, 3, 7; D2 = option (a)).

  plan_topic_requests(ctx, regulation)  -> [TopicRequest]  (what to ask per covered sub-topic)
  build_qa_prompt / topic_response_format                  (one Groq call per sub-topic)
  check_qa_answer(raw, req)             -> QACheck         (sources, limits, new-term check)
  qa_for_topic(..., ask)                -> TopicQA         (one re-ask, like Notes)
  assemble_unit_qa(results, regulation) -> UnitQA          (dedupe, take-turns trim, label)

qa_format.py is NOT changed (John, 2026-09-27): its pattern counts are used here
as a MAXIMUM; the old app keeps its exact-count rule until it is retired.
Pure: no network, no DB, writes nothing. Same honest limit as source_prompts:
the new-term check is lexical.
"""
from __future__ import annotations

import json
from collections import namedtuple
from dataclasses import dataclass, field

import qa_format
import source_context as sc
from source_prompts import (LATEX_RETRY, NEW_TERM_LIMIT, Dropped, _HEADING_RE, _allowed, _latex_marks,
                            _new_terms, _odd_chars, _passage_chars, _passage_text, describe_chars)

QA_MAX_COMPLETION_TOKENS = 3500
PART_A_PER_TOPIC = 2          # 2-mark questions asked per covered sub-topic
PART_B_PER_TOPIC = 1          # 13-mark question per sub-topic ...
PART_B_MIN_WORDS = 600        # ... only if its passages have at least this many words
PART_C_MIN_WORDS = 1500       # 15-mark: one per unit, from the biggest sub-topic, if this big
DUP_JACCARD = 0.75            # same part: key-term overlap at/above this = duplicate question

NOT_FULL_PATTERN_LABEL = ("Not the full exam pattern — questions only from topics covered "
                          "by the verified source.")

_KNOWN_KEYS = ("part_a", "part_b", "part_c")
# question words carry no topic: 'What is X?' == 'Define X.'
_QUESTION_WORDS = {sc.normalize(w) for w in (
    "what why how which when where who define definition explain describe discuss state list "
    "write give mention outline illustrate elaborate briefly detail brief short note notes "
    "suitable neat sketch").split()}

_C_GUIDE = ("a model answer of about 400-500 words that applies the passages' concepts to a "
            "problem or worked example taken FROM the passages, with clear steps")

Limit = namedtuple("Limit", "key title marks max_count answer_guide")


def unit_limits(regulation):
    parts = qa_format.get_pattern(regulation)
    if tuple(p.key for p in parts) != _KNOWN_KEYS:
        raise NotImplementedError(f"source-strict QA knows parts {_KNOWN_KEYS}, pattern has "
                                  f"{tuple(p.key for p in parts)}")
    return {p.key: Limit(p.key, p.title, p.marks, p.count, p.answer_guide) for p in parts}


@dataclass
class TopicRequest:
    plan: object          # source_context.TopicPlan
    want: dict            # part_key -> how many questions to ask for


def _words(plan):
    return sum(p.word_count for p in plan.passages)


def plan_topic_requests(ctx, regulation):
    unit_limits(regulation)                                   # R-2025 etc. refused here
    covered = [t for t in ctx.topics if t.status in ("FOUND", "PARTIAL") and t.passages]
    biggest = max(covered, key=_words) if covered else None
    reqs = []
    for t in covered:
        want = {"part_a": PART_A_PER_TOPIC}
        if _words(t) >= PART_B_MIN_WORDS:
            want["part_b"] = PART_B_PER_TOPIC
        if t is biggest and _words(t) >= PART_C_MIN_WORDS:
            want["part_c"] = 1
        reqs.append(TopicRequest(plan=t, want=want))
    return reqs


def topic_response_format(req):
    item = {"type": "object",
            "properties": {"question": {"type": "string"}, "answer": {"type": "string"},
                           "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["question", "answer", "sources"],
            "additionalProperties": False}
    keys = list(req.want)
    schema = {"type": "object",
              "properties": {k: {"type": "array", "items": item} for k in keys},
              "required": keys, "additionalProperties": False}
    return {"type": "json_schema",
            "json_schema": {"name": "topic_question_bank", "strict": True, "schema": schema}}


def build_qa_prompt(ctx, req, subject_name, unit_title, regulation):
    lim = unit_limits(regulation)
    plan, src = req.plan, ctx.source
    cover = "covered" if plan.status == "FOUND" else "only partly covered"
    asks = []
    for k, n in req.want.items():
        guide = _C_GUIDE if k == "part_c" else lim[k].answer_guide
        asks.append(f"- {k}: UP TO {n} question{'s' if n != 1 else ''} ({lim[k].marks} marks each). "
                    f"Each answer: {guide}. Fewer is fine if the passages do not support more.")
    passages = "\n\n".join(f"[{p.pid}] {p.text}" for p in plan.passages)
    return (
        "You are writing an exam question bank for Anna University students.\n"
        f"Subject: {subject_name} ({src.code}). Unit {src.unit}: {unit_title}. Regulation {qa_format.normalize_regulation(regulation)}.\n"
        f"Sub-topic: \"{plan.topic}\" ({cover} in the source).\n"
        f"Source: {src.title} ({src.license}).\n\n"
        "RULES\n"
        "1. Questions and answers use ONLY the numbered passages below. Do not add facts, numbers, "
        "examples, formulas, names or dates that are not in them.\n"
        "2. Every item's \"sources\" lists the passage ids its answer comes from, e.g. [\"P<n>\"]. "
        "Only ids given below. Items with no valid sources are deleted.\n"
        "3. The passages are lecture transcripts: rephrase and fix grammar freely.\n"
        "4. No two questions may ask the same thing. Do not number questions or write 'Q:'/'A:'.\n"
        "5. Formulas in plain text/Unicode. No LaTeX, HTML or emoji. Do not mention the passages, "
        "the lecturer or \"this course\".\n\n"
        "ASK FOR\n" + "\n".join(asks) + "\n\n"
        "PASSAGES\n"
        f"{passages}\n"
    )


@dataclass
class QACheck:
    items: dict
    dropped: list = field(default_factory=list)
    new_terms: list = field(default_factory=list)
    odd_chars: list = field(default_factory=list)
    latex: list = field(default_factory=list)

    @property
    def ok(self):
        return (any(self.items.values()) and len(self.new_terms) <= NEW_TERM_LIMIT and not self.odd_chars
                and not self.latex)


def _terms_in(text, toks, nums):
    found = []
    for line in str(text).splitlines():
        if not line.strip() or _HEADING_RE.match(line):
            continue
        found.extend(_new_terms(line, toks, nums))
    return found


def check_qa_answer(raw, req):
    items = {k: [] for k in req.want}
    chk = QACheck(items=items)
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except json.JSONDecodeError:
        chk.dropped.append(Dropped(str(raw)[:80], "not valid JSON"))
        return chk
    if not isinstance(data, dict):
        chk.dropped.append(Dropped(str(raw)[:80], "not a JSON object"))
        return chk
    sent = {p.pid for p in req.plan.passages}
    toks, nums = _allowed(req.plan)
    pchars = _passage_chars(req.plan)
    ptext = _passage_text(req.plan)
    for key, n in req.want.items():
        got = data.get(key)
        if not isinstance(got, list):
            chk.dropped.append(Dropped(key, f"{key} missing or not a list"))
            continue
        for it in got:
            if not isinstance(it, dict):
                chk.dropped.append(Dropped(str(it)[:80], "not an object"))
                continue
            q = str(it.get("question", "")).strip()
            a = str(it.get("answer", "")).strip()
            srcs = it.get("sources")
            srcs = [str(s).strip() for s in srcs] if isinstance(srcs, list) else []
            if not q or not a:
                chk.dropped.append(Dropped(q[:80], "empty question or answer"))
            elif not srcs:
                chk.dropped.append(Dropped(q[:80], "no sources"))
            elif [s for s in srcs if s not in sent]:
                bad = ", ".join(s for s in srcs if s not in sent)
                chk.dropped.append(Dropped(q[:80], f"cites passage not sent: {bad}"))
            elif len(items[key]) >= n:
                chk.dropped.append(Dropped(q[:80], f"{key} over the limit of {n}"))
            else:
                items[key].append({"question": q, "answer": a, "sources": srcs,
                                   "topic": req.plan.topic})
                for t in _terms_in(q, toks, nums) + _terms_in(a, toks, nums):
                    if t not in chk.new_terms:
                        chk.new_terms.append(t)
                for c in _odd_chars(q + "\n" + a, pchars):
                    if c not in chk.odd_chars:
                        chk.odd_chars.append(c)
                for mk in _latex_marks(q + "\n" + a, ptext):
                    if mk not in chk.latex:
                        chk.latex.append(mk)
    return chk


@dataclass
class TopicQA:
    topic: str
    status: str          # ok | needs_review | failed
    items: dict
    checks: list = field(default_factory=list)
    calls: int = 0


def _retry_prompt(prompt, chk):
    lines = ["", "YOUR PREVIOUS ANSWER WAS REJECTED."]
    if chk.new_terms:
        lines.append("It used words/numbers that are NOT in the passages: " + ", ".join(chk.new_terms)
                     + ". Leave them out.")
    if chk.odd_chars:
        lines.append("It used characters that are NOT in the passages: " + describe_chars(chk.odd_chars)
                     + ". Write plain text; use only characters that appear in the passages.")
    if getattr(chk, "latex", None):
        lines.append(LATEX_RETRY.format(marks=", ".join(chk.latex)))
    if chk.dropped:
        lines.append(f"{len(chk.dropped)} item(s) were deleted: "
                     + "; ".join(sorted({d.reason for d in chk.dropped})) + ".")
    lines.append("Answer again, following every rule.")
    return prompt + "\n".join(lines) + "\n"


def qa_for_topic(ctx, req, subject_name, unit_title, regulation, ask):
    prompt = build_qa_prompt(ctx, req, subject_name, unit_title, regulation)
    rf = topic_response_format(req)
    chk = check_qa_answer(ask(prompt, rf), req)
    checks = [chk]
    if not chk.ok:
        chk = check_qa_answer(ask(_retry_prompt(prompt, chk), rf), req)
        checks.append(chk)
    if chk.ok:
        status = "ok"
    elif any(chk.items.values()):
        status = "needs_review"
    else:
        status = "failed"
    items = chk.items if status != "failed" else {k: [] for k in req.want}
    if status == "needs_review":
        for lst in items.values():
            for it in lst:
                it["needs_review"] = True
    return TopicQA(req.plan.topic, status, items, checks=checks, calls=len(checks))


def _qkey(question):
    return {t for t in sc.key_terms(question) if t not in _QUESTION_WORDS}


def _is_dup(a, b):
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= DUP_JACCARD


@dataclass
class UnitQA:
    parts: dict
    full_pattern: bool
    label: str | None
    duplicates_removed: int = 0
    topic_status: dict = field(default_factory=dict)


def assemble_unit_qa(results, regulation):
    lim = unit_limits(regulation)
    parts, dups = {}, 0
    for key in lim:
        queues, seen = [], []
        for r in results:
            if r.status == "failed":
                continue
            q = []
            for it in r.items.get(key, []):
                k = _qkey(it["question"])
                if any(_is_dup(k, s) for s in seen):
                    dups += 1
                    continue
                seen.append(k)
                q.append(it)
            if q:
                queues.append(q)
        chosen, rnd = [], 0
        while len(chosen) < lim[key].max_count and any(rnd < len(q) for q in queues):
            for q in queues:
                if rnd < len(q) and len(chosen) < lim[key].max_count:
                    chosen.append(q[rnd])
            rnd += 1
        parts[key] = chosen
    full = all(len(parts[k]) == lim[k].max_count for k in lim)
    return UnitQA(parts=parts, full_pattern=full, label=None if full else NOT_FULL_PATTERN_LABEL,
                  duplicates_removed=dups, topic_status={r.topic: r.status for r in results})
