"""source_runner.py - P3a of the source-strict app (APP_SOURCE_STRICT_DESIGN.md 3, 4, 5).

Runs ONE unit topic by topic, in syllabus order:
  run_unit_notes(code, unit, subject_name, unit_title, ask, progress=None) -> UnitNotesRun
  run_unit_qa(code, unit, subject_name, unit_title, regulation, ask, progress=None) -> UnitQARun

- Refusals (no source, sha mismatch, R-2025 ...) happen BEFORE any Groq call.
- Every Groq call (re-asks included) goes through TokenPacer: in any 60 s window
  the estimated tokens (prompt estimate + max completion) stay <= 8,000.
- progress(event) gets {"stage": "start"|"done"|"error", "index", "total", "topic", "status"}.
- Any error from ask (HTTP 429 after retries, network ...) aborts the whole unit
  with RunAborted: nothing half-made is returned or stored.
- prompt_version() changes whenever source_prompts.py or source_qa.py changes, so a stored PDF can be
  marked stale when the prompts/checks change. A run WITH the verifier carries prompt_version(verified=True)
  (+sv:<sha10> of source_verify.py), so it never looks like an app-made PDF.
- BL-037 d: run_unit_notes can run ONLY the given topics (topics=[names]; None = all), adds John's reject
  notes to that topic's prompt only (avoid={topic: [notes]} -> topic_store.avoid_block), and hands every
  covered topic to on_topic(ctx, plan, index, result, avoid_notes) RIGHT AFTER its checks (the app saves it as a
  draft topic_texts row). A save error stops the run (RunAborted) before the next Groq call; topics saved
  before a stop stay saved.
- BL-028: with verify_ask (JSON ask) every topic that passed the writer checks gets ONE verifier call
  (source_verify): unsupported sentences / QA items removed; > 30% removed -> needs_review.
  BL-029: the verifier was found unreliable -> source_app does NOT pass verify_ask any more (John approves
  every PDF instead). verify_ask stays for experiments only.
Pure apart from sleeping: no network (ask is injected), no DB, writes nothing.
"""
from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import source_context as sc
import source_prompts as sp
import source_qa as sq
import source_verify as sv
import topic_store as ts

GROQ_TOKENS_PER_MINUTE = sp.GROQ_TOKENS_PER_MINUTE
WINDOW_SECONDS = 60.0
_SAFETY_SECONDS = 1.0


class RunAborted(RuntimeError):
    """A Groq call failed mid-unit; the unit is abandoned (nothing stored)."""


TOO_BIG_HINT = ("One call is too big for Groq's free limit - waiting will not help. "
                "Tell Claude (BL-039: passage trimming).")
RETRY_HINT = "Try again in a few minutes."


def abort_hint(err) -> str:
    """BL-039: honest last sentence for a stopped run - a too-big call never fits by waiting."""
    e = err
    while e is not None:
        if isinstance(e, sp.CallTooBig):
            return TOO_BIG_HINT
        e = e.__cause__
    return RETRY_HINT


class TokenPacer:
    """Sliding 60 s window of estimated tokens; sleeps until a call fits."""

    def __init__(self, limit=GROQ_TOKENS_PER_MINUTE, window=WINDOW_SECONDS,
                 clock=time.monotonic, sleep=time.sleep):
        self.limit, self.window, self.clock, self.sleep = limit, window, clock, sleep
        self._log = deque()          # (time, tokens)

    def _prune(self, now):
        while self._log and self._log[0][0] <= now - self.window:
            self._log.popleft()

    def wait_for(self, tokens):
        if tokens > self.limit:
            raise sp.CallTooBig(f"one call needs ~{tokens} tokens, more than the {self.limit}/min budget")
        now = self.clock()
        self._prune(now)
        while sum(t for _, t in self._log) + tokens > self.limit:
            wait = self._log[0][0] + self.window - now + _SAFETY_SECONDS
            self.sleep(max(wait, _SAFETY_SECONDS))
            now = self.clock()
            self._prune(now)
        self._log.append((now, tokens))


def _sha10(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:10]


def prompt_version(verified=False):
    base = f"sp:{_sha10(sp.__file__)}+sq:{_sha10(sq.__file__)}"
    gs = sc.garbled_spots_path()           # BL-035: changing the skip list makes stored PDFs OUT OF DATE
    if gs.is_file():
        base += f"+gs:{_sha10(gs)}"
    return f"{base}+sv:{_sha10(sv.__file__)}" if verified else base


def _emit(progress, **event):
    if progress:
        progress(event)


@dataclass
class UnitNotesRun:
    source: object
    topics: list                 # [source_prompts.TopicResult] in syllabus order (gaps included)
    gaps: list
    needs_review: list
    failed: list
    prompt_version: str
    calls: int = 0
    verify: dict = field(default_factory=dict)      # topic -> source_verify.VerifyResult


@dataclass
class UnitQARun:
    source: object
    unit: object                 # source_qa.UnitQA
    topics: list                 # [source_qa.TopicQA] for covered topics
    gaps: list
    needs_review: list
    failed: list
    prompt_version: str
    calls: int = 0
    verify: dict = field(default_factory=dict)      # topic -> source_verify.VerifyQAResult


def _paced_verifier(pacer, calls, verify_ask):
    def paced(prompt, rf):
        pacer.wait_for(sp.estimate_tokens(prompt) + sv.VERIFY_MAX_COMPLETION_TOKENS)
        calls["n"] += 1
        return verify_ask(prompt, rf)
    return paced


def run_unit_notes(code, unit, subject_name, unit_title, ask, progress=None,
                   clock=time.monotonic, sleep=time.sleep, resources_dir=None, verify_ask=None,
                   topics=None, avoid=None, on_topic=None):
    ctx = sc.build_unit_context(code, unit, resources_dir)      # SourceRefused -> no calls
    plans = list(ctx.topics)
    if topics is not None:                                      # BL-037 d: only these topics (checked first)
        wanted = list(topics)
        unknown = [t for t in wanted if t not in {p.topic for p in plans}]
        if unknown:
            raise ValueError(f"not a sub-topic of {code} Unit {unit}: {', '.join(unknown)}")
        plans = [p for p in plans if p.topic in set(wanted)]
    index = {p.topic: i for i, p in enumerate(ctx.topics, 1)}     # position in the syllabus (topic_index)
    avoid = avoid or {}
    pacer = TokenPacer(clock=clock, sleep=sleep)
    calls = {"n": 0}

    def paced(prompt):
        pacer.wait_for(sp.estimate_tokens(prompt) + sp.NOTES_MAX_COMPLETION_TOKENS)
        calls["n"] += 1
        return ask(prompt)

    vpaced = _paced_verifier(pacer, calls, verify_ask) if verify_ask else None
    total, results, verified = len(plans), [], {}
    for i, plan in enumerate(plans, 1):
        _emit(progress, stage="start", index=i, total=total, topic=plan.topic, status=plan.status)
        notes = list(avoid.get(plan.topic) or [])
        try:
            res = sp.notes_for_topic(ctx, plan, subject_name, unit_title, ask=paced,
                                     avoid=ts.avoid_block(notes))
            if vpaced and res.status == "ok":
                _emit(progress, stage="checking", index=i, total=total, topic=plan.topic,
                      status="checking against the source")
                v = sv.verify_notes(ctx, plan, res.markdown, ask=vpaced)
                verified[plan.topic] = v
                res.markdown, res.status = v.markdown, v.status
        except Exception as e:                                 # noqa: BLE001 - reported, unit aborted
            _emit(progress, stage="error", index=i, total=total, topic=plan.topic, status=str(e))
            raise RunAborted(f"stopped at '{plan.topic}': {e}") from e
        if on_topic and res.status != "gap":                   # BL-037 d: save right after the checks
            try:
                on_topic(ctx, plan, index[plan.topic], res, notes)
            except Exception as e:                             # noqa: BLE001 - no more Groq calls
                _emit(progress, stage="error", index=i, total=total, topic=plan.topic, status=str(e))
                raise RunAborted(f"could not save '{plan.topic}': {e}") from e
        results.append(res)
        _emit(progress, stage="done", index=i, total=total, topic=plan.topic, status=res.status)
    return UnitNotesRun(source=ctx.source, topics=results,
                        gaps=[r.topic for r in results if r.status == "gap"],
                        needs_review=[r.topic for r in results if r.status == "needs_review"],
                        failed=[r.topic for r in results if r.status == "failed"],
                        prompt_version=prompt_version(bool(verify_ask)), calls=calls["n"], verify=verified)


def run_unit_qa(code, unit, subject_name, unit_title, regulation, ask, progress=None,
                clock=time.monotonic, sleep=time.sleep, resources_dir=None, verify_ask=None):
    ctx = sc.build_unit_context(code, unit, resources_dir)      # SourceRefused -> no calls
    reqs = {r.plan.topic: r for r in sq.plan_topic_requests(ctx, regulation)}   # R-2025 -> no calls
    pacer = TokenPacer(clock=clock, sleep=sleep)
    calls = {"n": 0}

    def paced(prompt, rf):
        pacer.wait_for(sp.estimate_tokens(prompt) + sq.QA_MAX_COMPLETION_TOKENS)
        calls["n"] += 1
        return ask(prompt, rf)

    vpaced = _paced_verifier(pacer, calls, verify_ask) if verify_ask else None
    total, results, gaps, verified = len(ctx.topics), [], [], {}
    for i, plan in enumerate(ctx.topics, 1):
        _emit(progress, stage="start", index=i, total=total, topic=plan.topic, status=plan.status)
        req = reqs.get(plan.topic)
        if req is None:
            gaps.append(plan.topic)
            _emit(progress, stage="done", index=i, total=total, topic=plan.topic, status="gap")
            continue
        try:
            res = sq.qa_for_topic(ctx, req, subject_name, unit_title, regulation, ask=paced)
            if vpaced and res.status == "ok":
                _emit(progress, stage="checking", index=i, total=total, topic=plan.topic,
                      status="checking against the source")
                v = sv.verify_qa(ctx, plan, res.items, ask=vpaced)
                verified[plan.topic] = v
                res.items, res.status = v.items, v.status
        except Exception as e:                                 # noqa: BLE001
            _emit(progress, stage="error", index=i, total=total, topic=plan.topic, status=str(e))
            raise RunAborted(f"stopped at '{plan.topic}': {e}") from e
        results.append(res)
        _emit(progress, stage="done", index=i, total=total, topic=plan.topic, status=res.status)
    return UnitQARun(source=ctx.source, unit=sq.assemble_unit_qa(results, regulation), topics=results,
                     gaps=gaps,
                     needs_review=[r.topic for r in results if r.status == "needs_review"],
                     failed=[r.topic for r in results if r.status == "failed"],
                     prompt_version=prompt_version(bool(verify_ask)), calls=calls["n"], verify=verified)
