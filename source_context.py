"""source_context.py - P1 of the source-strict app (APP_SOURCE_STRICT_DESIGN.md).

Offline and pure: reads subjects.json + one verified unit file, writes nothing,
no network, no Groq, no DB.

  load_unit_source(code, unit)   -> UnitSource  (R1 live file only, R2 sha check, R3 refuse)
  split_passages(text)           -> [Passage]   (~300 words, P1, P2, ...)
  build_unit_context(code, unit) -> UnitContext (per-sub-topic passages + gap list, R4)

Word matching, sub-topic list and FOUND/PARTIAL/MISSING come from the scripts
folder's coverage_checker.py (John, 2026-09-27, option (a): one shared copy,
so the app can never drift from the checker - the BL-017 lesson). The status is
recomputed on the sha-verified text, never read from a saved report.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_DOCS_DIR = _APP_DIR.parent.parent        # ...\Documents (App lives in Documents\AcademicsGen\App)

SCRIPTS_DIR = Path(os.environ.get(
    "ACADEMICSGEN_SCRIPTS_DIR",
    _DOCS_DIR / "Python scripts for Anna University Course Material Generation")).resolve()
RESOURCES_DIR = Path(os.environ.get(
    "ACADEMICSGEN_RESOURCES_DIR",
    _DOCS_DIR / "Anna-University-PDF-Academic-Resources")).resolve()

# APPEND (never insert first): generate_all_pdfs.py exists in both folders and
# the App's own modules must always win.
# coverage_checker.py itself does sys.path.insert(0, <scripts folder>) on
# import, so the path is saved and restored around the imports.
_saved_path = list(sys.path)
try:
    sys.path.append(str(SCRIPTS_DIR))
    import coverage_checker as _cc       # noqa: E402
    import source_scraper as _scraper    # noqa: E402
finally:
    sys.path[:] = _saved_path + ([str(SCRIPTS_DIR)] if str(SCRIPTS_DIR) not in _saved_path else [])

normalize = _cc.normalize
key_terms = _cc.key_terms
tokenize = _cc.tokenize

PASSAGE_TARGET_WORDS = 300     # close a passage at the first sentence end after this
PASSAGE_MAX_WORDS = 450        # hard cut if no sentence end comes
PASSAGE_MIN_TAIL_WORDS = 100   # a shorter last passage joins the one before (if it fits)
PASSAGE_BUDGET_WORDS = 2500    # per sub-topic, fits the Groq free tier (design section 4)

# BL-039 (BL039_DESIGN.md, John 30 Sep "Room only for too-big topics"): one Groq call = prompt + completion must
# fit the free budget. A topic is TOO BIG when its passage tokens + TEMPLATE_ALLOWANCE + completion > the budget;
# only then its least relevant passages (tail of the best-first list) are dropped until the passages fit with
# REASK_RESERVE free. Topics that fit are never changed. Depends only on the passages (not on names or reject
# notes) -> the same plan everywhere (runner, topic state, re-check, QA).
GROQ_TOKENS_PER_MINUTE = 8000        # Groq free tier, openai/gpt-oss-120b (design section 4)
NOTES_MAX_COMPLETION_TOKENS = 3500   # answer + model reasoning; prompt + this must fit the minute
TEMPLATE_ALLOWANCE_TOKENS = 700      # measured max prompt text around the passages: 617 (30 Sep, all subjects)
REASK_RESERVE_TOKENS = 400           # room for a re-ask's extra lines (~400 measured) on a trimmed topic

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_INT_TO_ROMAN = {v: k for k, v in _ROMAN.items()}
_SENTENCE_END = (".", "?", "!", '."', ".'", '?"', '!"')


class SourceRefused(Exception):
    """The unit may not be generated (no source, sha mismatch, bad path ...)."""


@dataclass
class UnitSource:
    code: str
    unit: str
    path: Path
    text: str
    sha256: str
    source: str
    url: str
    title: str
    license: str
    word_count: int
    meta: dict = field(repr=False)
    skipped_spots: list = field(default_factory=list)     # BL-035: reasons of garbled spots removed


@dataclass
class Passage:
    pid: str
    text: str
    word_count: int


@dataclass
class TopicPlan:
    topic: str
    status: str
    match_ratio: float
    passages: list
    trimmed: list = field(default_factory=list)     # BL-039: passage ids left out to fit the budget


@dataclass
class UnitContext:
    source: UnitSource
    passages: list
    topics: list
    gaps: list


def _unit_key(unit) -> str:
    u = str(unit).strip().upper()
    if u.isdigit():
        u = _INT_TO_ROMAN.get(int(u), u)
    if u not in _ROMAN:
        raise SourceRefused(f"Unknown unit '{unit}'")
    return u


def _subject_group(data, code):
    for c, canonical, copies, _depts in _scraper.iter_subject_groups(data):
        if c == code:
            return canonical, copies
    raise SourceRefused(f"Subject {code} is not in subjects.json")


def _load_subjects(resources_dir: Path):
    return json.loads((resources_dir / "subjects.json").read_text(encoding="utf-8"))


def _safe_unit_file(resources_dir: Path, code: str, rel: str) -> Path:
    """R1: only sources/<CODE>/UnitN.txt - never _pending, never outside sources/."""
    rel = str(rel or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p]
    if (not rel or rel.startswith("/") or ":" in rel or ".." in parts
            or len(parts) != 2 or parts[0] != code or parts[0].startswith("_")):
        raise SourceRefused(f"{code}: source file path '{rel}' is not a live source file")
    sources_dir = (resources_dir / "sources").resolve()
    path = (sources_dir / parts[0] / parts[1]).resolve()
    if path.parent != sources_dir / code:
        raise SourceRefused(f"{code}: source file path '{rel}' is not a live source file")
    return path


def load_unit_source(code, unit, resources_dir=None) -> UnitSource:
    resources_dir = Path(resources_dir or RESOURCES_DIR).resolve()
    code = str(code).strip().upper()
    u = _unit_key(unit)
    _canonical, copies = _subject_group(_load_subjects(resources_dir), code)

    entries = [(c.get("sources") or {}).get(u) for c in copies]
    entries = [e for e in entries if e]
    if not entries:
        raise SourceRefused(f"No verified source for Unit {u} yet")
    keys = {(e.get("file"), e.get("content_sha256")) for e in entries}
    if len(keys) != 1:
        raise SourceRefused(f"{code} Unit {u}: department copies disagree on the source file/sha256")
    meta = entries[0]
    want = str(meta.get("content_sha256") or "").lower()
    if len(want) != 64:
        raise SourceRefused(f"{code} Unit {u}: no content_sha256 recorded")

    path = _safe_unit_file(resources_dir, code, meta.get("file"))
    if not path.is_file():
        raise SourceRefused(f"{code} Unit {u}: source file missing ({meta.get('file')})")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != want:
        raise SourceRefused(f"{code} Unit {u}: sha256 mismatch - file changed since it was verified "
                            f"(want {want[:10]}, got {got[:10]})")
    text = raw.decode("utf-8")
    return UnitSource(code=code, unit=u, path=path, text=text, sha256=got,
                      source=meta.get("source", ""), url=meta.get("url", ""),
                      title=meta.get("title", ""), license=meta.get("license", ""),
                      word_count=len(text.split()), meta=dict(meta))


def split_passages(text: str) -> list:
    """~300-word passages, closed at a sentence end; every word kept, in order."""
    words = text.split()
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if (len(cur) >= PASSAGE_TARGET_WORDS and w.endswith(_SENTENCE_END)) or len(cur) >= PASSAGE_MAX_WORDS:
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len(cur) < PASSAGE_MIN_TAIL_WORDS and len(chunks[-1]) + len(cur) <= PASSAGE_MAX_WORDS:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    return [Passage(pid=f"P{i}", text=" ".join(c), word_count=len(c)) for i, c in enumerate(chunks, 1)]


def estimate_tokens(text: str) -> int:
    """Conservative: transcripts tokenise worse than clean prose."""
    return math.ceil(max(len(text.split()) * 1.4, len(text) / 3.5))


def fit_passages(passages):
    """BL-039: (kept, dropped_pids). Unchanged when the passages fit; else drop from the tail (least relevant)
    until they fit with REASK_RESERVE_TOKENS free. At least one passage is kept."""
    room = GROQ_TOKENS_PER_MINUTE - NOTES_MAX_COMPLETION_TOKENS - TEMPLATE_ALLOWANCE_TOKENS
    kept = list(passages)
    if sum(estimate_tokens(p.text) for p in kept) <= room:
        return kept, []
    while len(kept) > 1 and sum(estimate_tokens(p.text) for p in kept) > room - REASK_RESERVE_TOKENS:
        kept.pop()
    return kept, [p.pid for p in passages[len(kept):]]


def select_passages(passages, topic, budget_words=PASSAGE_BUDGET_WORDS) -> list:
    """Best passages for one sub-topic, best first, within the word budget.
    Score = (distinct topic terms present, total term hits); a passage needs at
    least half the topic's terms."""
    terms = list(dict.fromkeys(key_terms(topic)))
    if not terms:
        return []
    need = max(1, math.ceil(len(terms) / 2))
    scored = []
    for idx, p in enumerate(passages):
        toks = tokenize(p.text)
        hits = [t for t in toks if t in terms]
        distinct = len(set(hits))
        if distinct >= need:
            scored.append((-distinct, -len(hits), idx, p))
    scored.sort(key=lambda s: s[:3])
    chosen, used = [], 0
    for *_k, p in scored:
        if used + p.word_count > budget_words:
            continue
        chosen.append(p)
        used += p.word_count
    return chosen


def unit_topics(code, unit, text, resources_dir=None):
    """Sub-topics + status exactly as coverage_checker computes them."""
    resources_dir = Path(resources_dir or RESOURCES_DIR).resolve()
    canonical, _copies = _subject_group(_load_subjects(resources_dir), str(code).strip().upper())
    raw = (canonical.get("topics") or {}).get(_unit_key(unit), [])
    topics = _cc.scoring_subtopics_from_lines(_scraper.clean_unit_topics(raw))
    return _cc.check_unit(text, topics)["topics"]


GARBLED_SPOTS_FILE = "_garbled_spots.json"    # BL-035: in <resources>/sources/, reviewed by John


def garbled_spots_path(resources_dir=None) -> Path:
    return Path(resources_dir or RESOURCES_DIR).resolve() / "sources" / GARBLED_SPOTS_FILE


def unit_garbled_spots(src, resources_dir=None) -> list:
    """BL-035: this unit's entries of the skip list (none -> []). Loud, never silent: an entry checked
    against another version of the file, or a text not found exactly once -> SourceRefused."""
    path = garbled_spots_path(resources_dir)
    if not path.is_file():
        return []
    spots = json.loads(path.read_text(encoding="utf-8")).get("spots") or []
    mine = [e for e in spots if str(e.get("code", "")).strip().upper() == src.code
            and _unit_key(e.get("unit", "")) == src.unit]
    norm = " ".join(src.text.split())
    for e in mine:
        where = f"{src.code} Unit {src.unit}: garbled-spot list ({GARBLED_SPOTS_FILE})"
        if str(e.get("source_sha256", "")).lower() != src.sha256:
            raise SourceRefused(f"{where} is out of date - the source file changed; re-check the entry")
        n = norm.count(" ".join(str(e.get("text", "")).split()))
        if not str(e.get("text", "")).strip() or n != 1:
            raise SourceRefused(f"{where}: text found {n} times (must be exactly once) - re-check the entry")
    return mine


def remove_garbled_spots(text, spots) -> str:
    """Whitespace-normalised text with every listed spot cut out (only removes, never adds)."""
    norm = " ".join(str(text).split())
    for e in spots:
        norm = norm.replace(" ".join(str(e["text"]).split()), " ")
    return " ".join(norm.split())


def build_unit_context(code, unit, resources_dir=None) -> UnitContext:
    src = load_unit_source(code, unit, resources_dir)
    spots = unit_garbled_spots(src, resources_dir)
    src.skipped_spots = [str(e.get("reason", "")) for e in spots]
    passages = split_passages(remove_garbled_spots(src.text, spots) if spots else src.text)
    plans = []
    for r in unit_topics(src.code, src.unit, src.text, resources_dir):
        status = r.get("status", "MISSING")
        chosen = select_passages(passages, r["topic"]) if status in ("FOUND", "PARTIAL") else []
        chosen, trimmed = fit_passages(chosen)
        plans.append(TopicPlan(topic=r["topic"], status=status,
                               match_ratio=r.get("match_ratio", 0.0), passages=chosen, trimmed=trimmed))
    gaps = [p.topic for p in plans if p.status not in ("FOUND", "PARTIAL")]
    return UnitContext(source=src, passages=passages, topics=plans, gaps=gaps)
