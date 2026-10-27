"""
source_scraper.py — AcademicsGen source-gathering pipeline (v2 rewrite)

Cascade per subject/unit: NPTEL -> MIT OCW -> OpenStax, first real hit wins.
Nothing hardcoded: subjects, units and topics all come from subjects.json.
The topic-completeness audit (coverage_checker.py) runs automatically at the
end of every batch — it is part of the same invocation, not a manual step.

This is a clean rewrite of the v1 script. The MATCHING ENGINE (tokenizer,
singularizer, British->American map, IDF-weighted anchor-gated scorer, topic
splitting, boilerplate stripping, OpenStax per-unit volume selection) is
carried over with IDENTICAL behaviour, because it was validated live against
MA3151. Every bug class fixed in v1 has a regression test in
tests/test_source_scraper.py — see the docstring there for the mapping.

Guarantees
  - Idempotent: already-sourced units are skipped unless --force. A sourced
    unit whose UnitN.txt is missing, or whose entry shares one URL with a
    sibling unit (the old MIT "one page for every unit" bug signature), is
    treated as unsourced and healed automatically.
  - Crash-safe: every file (subjects.json, UnitN.txt, registry, checkpoint)
    is written atomically (temp file + fsync + rename). subjects.json is
    persisted after EVERY subject, and backed up once per run.
  - Resumable: checkpoint per scope (all / department); subjects that failed
    are retried first on the next resumed run.
  - Isolated: one subject's unexpected error is logged with a traceback and
    skipped; the batch continues.
  - Honest about outages: a network failure is never mistaken for "no match".
    A unit whose search hit a transient failure is left untouched (no write,
    no stale-file deletion) and its subject is queued for retry.
  - Catalog caches (NPTEL course list, MIT course index) retry with backoff
    and never cache an empty/failed result.
  - robots.txt checked per domain; per-source rate limits; rotating log file.

Usage (run from anywhere; paths default to next to subjects.json):
    python source_scraper.py --subjects-json subjects.json --dry-run
    python source_scraper.py --subjects-json subjects.json --department "H&S"
    python source_scraper.py --subjects-json subjects.json --subject-code MA3151 --force
    python source_scraper.py --subjects-json subjects.json --batch-size 50
"""

from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import html as html_module
import json
import logging
import logging.handlers
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

__version__ = "2.7.0"

log = logging.getLogger("academicsgen.scraper")

# ===========================================================================
# 1. CONFIG — only source endpoints and rules; nothing subject-specific
# ===========================================================================

SOURCES = {
    "nptel": {
        # nptel.ac.in is a SvelteKit app with no public search API; the full
        # course catalog is server-rendered into this one page.
        "search_url": "https://nptel.ac.in/courses",
        "base": "https://nptel.ac.in",
        "license": "CC BY-SA 4.0",
        "monetizable": True,
        "rate_limit_seconds": 3.0,
    },
    "mit_ocw": {
        # The sitemap index enumerates every course's own sitemap -> slugs.
        "search_url": "https://ocw.mit.edu/sitemap.xml",
        "base": "https://ocw.mit.edu",
        "license": "CC BY-NC-SA 4.0",
        "monetizable": False,
        "rate_limit_seconds": 3.0,
    },
    "openstax": {
        # Wagtail CMS API (note the /apps/cms prefix).
        "search_url": "https://openstax.org/apps/cms/api/v2/pages/",
        "base": "https://openstax.org",
        # Fallback only — the real per-book license is detected from the page.
        "license": "CC BY 4.0",
        "monetizable": True,
        "rate_limit_seconds": 2.0,
    },
    # Transcript fetches for NPTEL lectures; separate bucket so YouTube is
    # never hammered at NPTEL's pace (or vice versa).
    "youtube": {"rate_limit_seconds": 1.0},
}
SOURCE_ORDER = ["nptel", "mit_ocw", "openstax"]
# v2.3: NPTEL official course books (full-course transcripts, PDF), downloaded
# ONCE by a person into <output_dir>/_nptel_books/<course_id>.pdf and read
# offline from there. Parsed lectures are cached next to the PDF.
NPTEL_BOOKS_DIR = "_nptel_books"
# v2.4 (BL-012): once a course week matches a unit, the unit's lectures are
# chosen INDIVIDUALLY across all weeks of the book by what their text covers
# (same coverage_checker as the write gate), greedy: most NEW sub-topics first.
# A lecture must FOUND at least this many of the unit's sub-topics on its own
# (a single FOUND is usually a passing mention — calibrated offline on books
# 106106145 / 106106199 / 110107150, 2026-09-25).
NPTEL_MIN_FOUND_PER_LECTURE = 2
NPTEL_MAX_WORDS_PER_UNIT = 40000       # scope cap for the selected lectures
# BL-020 (v2.5.0): content gate, used ONLY when the week-title gate fails
# (see content_gate / lecture_specificity). Measured 2026-09-26 on 30 units /
# 6 books (BL020_DESIGN.md): real units 0.39-0.51, junk <= 0.26.
NPTEL_CONTENT_GATE_MIN_SPECIFICITY = 0.35
NPTEL_BOOK_PARSER_VERSION = 3   # v2.4.2: multi-page INDEX with "Lecture NN" titles; title prefix stripped

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
BACKOFF_SCHEDULE = [2, 4, 8]            # seconds, per request attempt
CATALOG_RETRY_BACKOFF = [5, 15, 45]     # seconds, per whole-catalog attempt
CATALOG_COOLDOWN_SECONDS = 300          # after catalog failure, don't hammer
YOUTUBE_BLOCK_TRIP = 3                  # consecutive IP-block replies that trip the breaker
YOUTUBE_BLOCK_COOLDOWN_SECONDS = 1800   # then stop calling YouTube for this long
MIN_WORDS_PER_UNIT = 500
# Pre-write topic gate (v2.1): a hit is written ONLY if coverage_checker finds
# more than this % of the unit's syllabus sub-topics FOUND in its text.
# Default 0.0 => at least one sub-topic must be genuinely FOUND. A rejected
# hit is treated exactly like "no match" from that source: the cascade moves
# on, and if nothing passes, nothing is written (and under --force any stale
# file is removed). Calibration from the live H&S run: wrong-topic PH3151
# Unit V scored 0%; the weakest REAL partial matches scored 10-15%
# (CY3151 III phase diagrams, GE3151 IV NPTEL Python lists), so any
# threshold above ~5% would start discarding genuine partial content.
MIN_WRITE_COVERAGE_PCT = 0.0
# BL-018 (v2.6.0): lower sources are reviewed by a person before going live.
# Measured 2026-09-27 (BL018_DESIGN.md): no lexical gate separates off-topic
# OpenStax pages from real partial content, so OpenStax / MIT OCW hits are
# written to sources/_pending/ and only go live via review_pending.py approve.
# Pages a person rejected (sources/_reviewed_rejects.json) are never used
# again for that (code, unit). NPTEL stays automatic (stronger gates).
LOWER_SOURCES = ("mit_ocw", "openstax")
PENDING_DIR = "_pending"
PENDING_REGISTRY = "pending.json"
REVIEWED_REJECTS_FILE = "_reviewed_rejects.json"
_REJECT_FIELDS = ("code", "unit", "page_url", "reason", "date", "by")
# BL-024 (v2.7.0): John picks the NPTEL course for a subject/unit where name
# matching finds none (CB3491) or picks a dead-book course (AD3501).
COURSE_CHOICE_FILE = "_course_choice.json"
_CHOICE_FIELDS = ("code", "unit", "course_id", "reason", "date", "by")
USER_AGENT = f"AcademicsGen-SourceScraper/{__version__} (educational, non-abusive rate)"
HEADERS = {"User-Agent": USER_AGENT}

ROMAN_TO_INT = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
                "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


class SourceUnavailable(Exception):
    """A source could not be reached (after retries). NOT the same as 'no
    match' — the caller must not record a result or delete anything."""


class NptelBookNeeded(SourceUnavailable):
    """v2.2: NPTEL matched this unit (course + week) and NPTEL publishes an
    official English course book (a transcript PDF on Google Drive), but the
    book has to be downloaded by a person first: robots.txt forbids automated
    fetching from drive.google.com (/uc) and drive.usercontent.google.com,
    and YouTube's robots.txt forbids the transcript endpoints. The unit is
    left untouched (NOT filled from a lower source — NPTEL is the true
    cascade winner) and the book goes on nptel_books_needed.json."""

    def __init__(self, book: dict):
        self.book = book
        super().__init__(f"NPTEL book needed for course {book.get('course_id')} "
                         f"({book.get('course_title')}), week {book.get('week_title')!r}")


# ===========================================================================
# 2. MATCHING ENGINE — carried over from v1 with identical behaviour.
#    Every constant/list here was calibrated against real catalogs.
# ===========================================================================

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he",
    "in", "is", "it", "its", "of", "on", "that", "the", "to", "was", "were",
    "will", "with", "or", "this", "these", "those", "their", "using",
}
_TOKEN_RE = re.compile(r"[a-zA-Z]{3,}")

# Words ending in "s" that are NOT plurals. Anything ending in us/is/ss is
# excluded generically (calculus, radius, analysis, axis, ...).
_SINGULARIZE_EXCEPTIONS = {
    "series", "species", "gas", "bus", "atlas", "canvas", "chaos", "cosmos",
    "kudos", "bias", "asbestos", "physics", "mathematics", "statistics",
    "thermodynamics", "dynamics", "mechanics", "economics", "electronics",
    "graphics", "ethics", "logistics", "optics", "genetics",
}
_IRREGULAR_SINGULAR = {
    "matrices": "matrix", "vertices": "vertex", "indices": "index",
    "axes": "axis", "bases": "basis",
}
# Derivational-form map (bug class 6 companion): AU "Partial differentiation"
# vs OpenStax "Partial Derivatives". Evidence-driven; add pairs only after
# confirming a real page title uses the other form.
_SYNONYM_CANONICAL = {
    "differentiation": "derivative", "differentiate": "derivative",
    "differentiating": "derivative",
}


def _singularize(token: str) -> str:
    if token in _SYNONYM_CANONICAL:
        return _SYNONYM_CANONICAL[token]
    if token in _IRREGULAR_SINGULAR:
        return _IRREGULAR_SINGULAR[token]
    if token in _SINGULARIZE_EXCEPTIONS or token.endswith(("us", "is", "ss")):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "shes", "ches", "xes", "zes")):
        return token[:-2]
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


# Bug class 7: AU syllabi are British English, OpenStax/MIT/NPTEL American.
# Explicit lists + guarded suffix rules (blanket rules corrupt hour/filling).
_BRITISH_RE_TO_ER = {
    "centre": "center", "theatre": "theater", "fibre": "fiber",
    "litre": "liter", "metre": "meter", "calibre": "caliber",
    "sombre": "somber", "spectre": "specter", "lustre": "luster",
    "sabre": "saber", "manoeuvre": "maneuver",
}
_BRITISH_DOUBLE_CONSONANT = {
    "modelling": "modeling", "modelled": "modeled", "modeller": "modeler",
    "travelling": "traveling", "travelled": "traveled", "traveller": "traveler",
    "labelling": "labeling", "labelled": "labeled",
    "signalling": "signaling", "signalled": "signaled",
    "cancelling": "canceling", "cancelled": "canceled",
    "levelling": "leveling", "levelled": "leveled",
    "fuelling": "fueling", "fuelled": "fueled",
    "totalling": "totaling", "totalled": "totaled",
    "channelling": "channeling", "channelled": "channeled",
    "counselling": "counseling", "counselled": "counseled",
    "equalling": "equaling", "equalled": "equaled",
    "marvelling": "marveling", "marvelled": "marveled",
}
_BRITISH_YSE_TO_YZE = {
    "analyse": "analyze", "analysed": "analyzed", "analyses": "analyzes",
    "analysing": "analyzing", "catalyse": "catalyze", "catalysed": "catalyzed",
    "catalyses": "catalyzes", "catalysing": "catalyzing",
    "paralyse": "paralyze", "paralysed": "paralyzed", "paralyses": "paralyzes",
    "paralysing": "paralyzing", "dialyse": "dialyze", "dialysed": "dialyzed",
    "electrolyse": "electrolyze", "electrolysed": "electrolyzed",
    "hydrolyse": "hydrolyze", "hydrolysed": "hydrolyzed",
}
_ISE_EXCEPTIONS = {
    "advise", "advised", "advises", "advising", "advertise", "advertised",
    "advertises", "advertising", "arise", "arises", "arising", "arisen",
    "chastise", "chastised", "chastises", "chastising", "circumcise",
    "comprise", "comprised", "comprises", "comprising", "compromise",
    "compromised", "compromises", "compromising", "demise", "despise",
    "despised", "despises", "despising", "devise", "devised", "devises",
    "devising", "disguise", "disguised", "disguises", "disguising",
    "exercise", "exercised", "exercises", "exercising", "franchise",
    "franchised", "franchises", "improvise", "improvised", "improvises",
    "improvising", "incise", "incised", "incises", "incising", "premise",
    "premises", "revise", "revised", "revises", "revising", "supervise",
    "supervised", "supervises", "supervising", "surmise", "surmised",
    "surmises", "surmising", "surprise", "surprised", "surprises",
    "surprising", "televise", "televised", "televises", "televising",
    "wise", "wiser", "wisest", "otherwise", "likewise", "clockwise",
    "noise", "noises", "noisy", "poise", "poised", "raise", "raised",
    "raises", "raising", "praise", "praised", "praises", "praising",
    "cruise", "cruised", "cruises", "cruising", "bruise", "bruised",
    "bruises", "bruising", "precise", "precisely", "concise", "concisely",
}
_OUR_EXCEPTIONS = {
    "hour", "hours", "our", "ours", "sour", "flour", "tour", "tours",
    "toured", "touring", "pour", "poured", "pours", "pouring", "your",
    "yours", "contour", "contours", "detour", "detours", "devour",
    "devoured", "devours", "devouring", "scour", "scoured", "scours",
    "scouring", "amour", "velour", "sojourn",
}


def _british_to_american(token: str) -> str:
    if token in _BRITISH_RE_TO_ER:
        return _BRITISH_RE_TO_ER[token]
    if token in _BRITISH_DOUBLE_CONSONANT:
        return _BRITISH_DOUBLE_CONSONANT[token]
    if token in _BRITISH_YSE_TO_YZE:
        return _BRITISH_YSE_TO_YZE[token]
    if token not in _ISE_EXCEPTIONS:
        if token.endswith("isation") and len(token) > 8:
            return token[:-7] + "ization"
        if token.endswith("iser") and len(token) > 6:
            return token[:-4] + "izer"
        if token.endswith("ised") and len(token) > 6:
            return token[:-4] + "ized"
        if token.endswith("ising") and len(token) > 7:
            return token[:-5] + "izing"
        if token.endswith("ise") and len(token) > 5:
            return token[:-3] + "ize"
    if token not in _OUR_EXCEPTIONS:
        if token.endswith("ouring") and len(token) > 8:
            return token[:-6] + "oring"
        if token.endswith("oured") and len(token) > 7:
            return token[:-5] + "ored"
        if token.endswith("our") and len(token) > 5:
            return token[:-3] + "or"
    return token


def tokenize(text: str) -> set:
    return {w.lower() for w in _TOKEN_RE.findall(text or "") if w.lower() not in _STOPWORDS}


def _singularize_tokens(tokens: Iterable[str]) -> set:
    """Plural + derivation + British->American normalization. Opt-in ONLY at
    validated call sites (page selection within an already-chosen book /
    course). Two confirmed regressions when applied more widely:
      - course/book-name discovery: "Matrices"->"matrix" made MA3151 match
        MIT "18.S096 Matrix Calculus for Machine Learning";
      - book-level ranking: let Calculus Vol 3 "Limits and Continuity" tie
        Vol 1 "The Limit of a Function" and pick the wrong VOLUME."""
    return {_british_to_american(_singularize(t)) for t in tokens}


# Bug class 1: when the query names a language/tool/platform, a candidate
# missing it is disqualified outright (stopped "Python Programming" matching
# "Problem Solving and Programming in C").
ANCHOR_TERMS = {
    "python", "java", "javascript", "sql", "html", "css", "php", "matlab",
    "ruby", "swift", "kotlin", "rust", "perl", "scala", "verilog", "vhdl",
    "autocad", "unix", "linux", "android", "arduino", "tensorflow", "pytorch",
    "hadoop", "spark", "django", "react", "angular", "cobol", "fortran",
    "assembly", "prolog", "lisp", "labview", "solidworks", "opengl",
}

# Single-letter / symbol language names can't survive the [a-zA-Z]{3,}
# tokenizer ("PROGRAMMING IN C" tokenized to just {"programming"} in v1, so
# it could match a Python course on one word). For NAME matching only
# (strict/loose — never topic text, where "Vitamin C" / "Section C" occur),
# these are detected on the raw text and added as synthetic anchor tokens.
_SPECIAL_ANCHORS = [
    (re.compile(r"(?<![\w+#])C\+\+(?![\w+])"), "lang_cpp"),
    (re.compile(r"(?<![\w+#])C#(?!\w)"), "lang_csharp"),
    (re.compile(r"(?<![\w+#.'’-])C(?![\w+#'’-])"), "lang_c"),
    (re.compile(r"(?<![\w+#.'’-])R(?![\w+#'’-])"), "lang_r"),
]


def _special_anchor_tokens(text):
    return {tok for rx, tok in _SPECIAL_ANCHORS if rx.search(text or "")}


MATCH_THRESHOLD = 0.35           # calibrated against real NPTEL/MIT catalogs
MAX_OPENSTAX_BOOKS_TO_CHECK = 3
MAX_PAGES_PER_UNIT = 8           # scope cap (bug class 5), all page-based sources
MAX_OPENSTAX_PAGES_PER_UNIT = MAX_PAGES_PER_UNIT  # v1 name, kept for compatibility
MIN_MATCHED_TOKENS_LONG_QUERY = 2  # a single shared word is never trusted
ANCHOR_TERMS_ALL = ANCHOR_TERMS | {tok for _, tok in _SPECIAL_ANCHORS}


def _idf_weights(token_sets):
    n = len(token_sets)
    df = {}
    for ts in token_sets:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(n / d) + 1 for t, d in df.items()}


# v2 determinism fix: v1 summed float IDF weights while iterating Python
# sets, whose order changes per process (hash randomization). A perfect tie
# then came out as 1.0000000000000002 vs 1.0 in some runs and not others, so
# the documented tie-breaks were skipped and page choice varied run to run
# (measured: 2 of 20 processes gave different MA3151 page selections).
# math.fsum is exactly rounded (order-independent); scores are then rounded
# so genuine ties are compared as ties.
SCORE_DECIMALS = 9


def _wsum(tokens, idf):
    return math.fsum(idf.get(t, 1.0) for t in tokens)


def _weighted_ratio(query_tokens, cand_tokens, idf):
    if not query_tokens:
        return 0.0
    total = _wsum(query_tokens, idf)
    if total == 0:
        return 0.0
    return round(_wsum(query_tokens & cand_tokens, idf) / total, SCORE_DECIMALS)


def _min_coverage(n_tokens):
    # Short queries need near-full coverage ("Engineering Physics" must not
    # match on "physics" alone).
    if n_tokens <= 2:
        return 1.0
    if n_tokens == 3:
        return 0.67
    return 0.5


def _overlap_coefficient(query_tokens, cand_tokens, idf):
    matched = query_tokens & cand_tokens
    if len(matched) < MIN_MATCHED_TOKENS_LONG_QUERY:
        return 0.0
    denom = min(_wsum(query_tokens, idf), _wsum(cand_tokens, idf))
    return round(_wsum(matched, idf) / denom, SCORE_DECIMALS) if denom else 0.0


def _score_candidates(query_text, candidates, mode="strict", singularize=False):
    """Full ranking [(score, key)] best-first, no threshold cutoff.

    mode="strict": short query vs short titles (course names); IDF ratio +
                   length-scaled coverage floor.
    mode="loose":  same ratio, no floor (book titles legitimately miss
                   subject-name words: "Calculus Volume 1" for "Matrices and
                   Calculus").
    mode="topic":  long topic text vs short titles; overlap coefficient gated
                   by >=2 matched tokens (so "Applications" alone never wins).
    Anchor terms are a hard gate in every mode."""
    query_tokens = tokenize(query_text)
    cand_tokens = [(key, tokenize(text)) for key, text in candidates]
    special = set()
    if mode != "topic":
        special = _special_anchor_tokens(query_text)
        query_tokens |= special
        cand_tokens = [(key, ct | _special_anchor_tokens(text))
                       for (key, ct), (_, text) in zip(cand_tokens, candidates)]
    if not query_tokens or not candidates:
        return []
    if singularize:
        query_tokens = _singularize_tokens(query_tokens - special) | special
        cand_tokens = [(key, _singularize_tokens(ct - special) | (ct & special)) for key, ct in cand_tokens]
    idf = _idf_weights([ct for _, ct in cand_tokens])
    required = query_tokens & ANCHOR_TERMS_ALL
    scored = []  # (score, n_matched, n_cand_tokens, key)
    if mode == "topic":
        for key, ct in cand_tokens:
            if required and not required.issubset(ct):
                continue
            matched = query_tokens & ct
            scored.append((_overlap_coefficient(query_tokens, ct, idf), len(matched), len(ct), key))
    else:
        min_cov = _min_coverage(len(query_tokens)) if mode == "strict" else 0.0
        for key, ct in cand_tokens:
            if required and not required.issubset(ct):
                continue
            matched = query_tokens & ct
            if len(matched) / len(query_tokens) < min_cov:
                continue
            # A final (strict) match is never made on a single shared token:
            # a one-word subject name ("ALGORITHMS", or a parse-garbage name
            # like "PCC") must not course-match on that word alone. Loose mode
            # is only a RANKING of an already search-filtered book list; the
            # actual OpenStax decision is the >=2-token page match.
            if mode == "strict" and len(matched) < MIN_MATCHED_TOKENS_LONG_QUERY:
                continue
            scored.append((_weighted_ratio(query_tokens, ct, idf), len(matched), len(ct), key))
    # Tiebreaks (both confirmed real cases): more matched query tokens wins
    # ("Double Integrals in Polar Coordinates" over "Polar Coordinates"),
    # then the tighter title wins ("3.2 Trigonometric Integrals" over
    # "1.7 Integrals Resulting in Inverse Trigonometric Functions").
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [(s, k) for s, _, _, k in scored]


def best_match(query_text, candidates, mode="strict", singularize=False):
    """Winning key or None if nothing clears MATCH_THRESHOLD."""
    scored = _score_candidates(query_text, candidates, mode, singularize)
    if not scored or scored[0][0] < MATCH_THRESHOLD:
        return None
    return scored[0][1]


# ---------------------------------------------------------------------------
# Syllabus text cleaning / splitting
# ---------------------------------------------------------------------------

def clean_subject_name(name):
    # "MATRICES AND CALCULUS L T P C" -> "MATRICES AND CALCULUS"
    return re.sub(r"\bL\s*T\s*P\s*C\b", "", name or "", flags=re.I).strip()


# Stage-1 parse errors: some subjects.json names are the AU course-CATEGORY
# code + credits ("PCC 3", "BSC 2", "ESC 3", "0 0 3 3 1.5") instead of the
# real title. Nothing can be matched correctly from such a name, so these are
# skipped with a warning (and listed in the run summary for fixing upstream).
_CATEGORY_CODE_NAME_RE = re.compile(
    r"^\s*(?:(?:PCC|ESC|BSC|HSMC|HSC|EEC|PEC|OEC|MC|PC|ES|BS|HS|EE|PE|OE)\b)?[\s\d.]*$", re.I)


def is_unusable_subject_name(name):
    cleaned = clean_subject_name(name)
    return bool(_CATEGORY_CODE_NAME_RE.match(cleaned)) or not (tokenize(cleaned) | _special_anchor_tokens(cleaned))


# The LAST unit of every AU syllabus swallows the trailing template
# (TOTAL PERIODS, COURSE OUTCOMES, TEXT BOOKS, REFERENCES, CO-PO table).
_SYLLABUS_BOILERPLATE_RE = re.compile(
    r"^\s*(TOTAL\s*:?\s*\d+\s*PERIODS|COURSE\s+OUTCOMES|TEXT\s*BOOKS?\b|"
    r"REFERENCES?\b|CO\d+\s*:|CO['’‘]?S?\s*-?\s*PO['’‘]?S?)",
    re.I,
)


# BL-044 (30 Sep 2026): the syllabus tail after the last unit - "45 PERIODS" without TOTAL, the lab
# section header, odd TOTAL spellings. Matched only at the START of a line, so real topics such as
# "Ideal and Practical Transformer" or "Reynold's Experiment" are never touched.
_SYLLABUS_TAIL_RE = re.compile(
    r"^\s*(\d+\s*PERIODS\b|PRACTICAL\s*EXERCISES\b|LIST\s+OF\s+(EXPERIMENTS|EXERCISES)\b|"
    r"LAB(ORATORY)?\s+(EXPERIMENTS?|EXERCISES|COMPONENTS?)\b|TOTAL[\s:=(\dLP+)]*PERIODS\b)",
    re.I,
)
# "... accuracy measures 30 PERIODS": a topic line that carries the unit's period count at its end
_TRAILING_PERIODS_RE = re.compile(r"\s+\d+\s*PERIODS\s*$", re.I)


def clean_unit_topics(topics):
    cleaned = []
    for line in topics or []:
        if line.strip().lower() == "all topics (full unit)":
            continue
        if _SYLLABUS_BOILERPLATE_RE.match(line):
            break
        if _SYLLABUS_TAIL_RE.match(line):
            if cleaned:
                break
            continue          # lab subjects (CE3481): a header BEFORE any topic is dropped, not a stop
        cut = _TRAILING_PERIODS_RE.sub("", line)
        if cut != line:
            if cut.strip():
                cleaned.append(cut)
            break
        cleaned.append(line)
    return cleaned


# AU unit lines are dash-separated sub-concepts (sometimes U+FFFD from PDF
# extraction); a dash-segment can itself be a comma-enumerated list.
# v2.1: ";" is also a sub-concept separator — several departments' syllabi
# (e.g. CY3151) use semicolons instead of dashes, which left whole
# multi-concept runs as one phrase and let two generic words from DIFFERENT
# concepts combine into a match ("Properties ... Metal matrix composites" ->
# "Properties of Transition Metals").
_SUBTOPIC_SPLIT_RE = re.compile(r"\s+[-–—�]\s+|,\s+|\s*;\s*")


def _split_into_subtopics(topics):
    joined = " ".join(topics)
    return [p.strip() for p in _SUBTOPIC_SPLIT_RE.split(joined) if p.strip()]


split_into_subtopics = _split_into_subtopics  # public alias


def subtopic_matches(unit_topics, pages):
    """[(sub_concept, winning_key, score)] for every sub-concept of the unit
    that some page clearly matches. pages: [(title, key)]."""
    unit_query = " ".join(unit_topics)
    subtopics = _split_into_subtopics(unit_topics) or [unit_query]
    cands = [(key, title) for title, key in pages]
    out = []
    for phrase in subtopics:
        ranked = _score_candidates(phrase, cands, mode="topic", singularize=True)
        if ranked and ranked[0][0] >= MATCH_THRESHOLD:
            out.append((phrase, ranked[0][1], ranked[0][0]))
    return out


def select_relevant_pages(unit_topics, pages, max_pages=MAX_PAGES_PER_UNIT):
    """Bug class 5 (scope bloat): pick the single best page PER SUB-CONCEPT
    of the unit, never TOC-adjacent siblings and never whole-unit bag-of-
    words matches. pages: [(title, key)] in document order. Returns keys in
    document order, capped at max_pages."""
    winners = {key for _, key, _ in subtopic_matches(unit_topics, pages)}
    return [key for _, key in pages if key in winners][:max_pages]


# ===========================================================================
# 3. TEXT EXTRACTION
# ===========================================================================

# Bug class 4: each <math> carries the SAME expression twice (presentation
# MathML + <annotation-xml> content MathML). Plain get_text() duplicates
# every expression. Also drop any MathJax assistive/preview copies and
# non-content nodes if a page ever ships them server-side.
_DUPLICATE_MATH_TAGS = ["annotation-xml", "annotation", "mjx-assistive-mml"]
_NON_CONTENT_TAGS = ["script", "style", "noscript", "template"]
_DUPLICATE_MATH_CLASSES = ["MathJax_Preview", "MJX_Assistive_MathML", "sr-only-math"]


def _extract_clean_text(tag):
    tag = copy.copy(tag)
    for dup in tag.find_all(_DUPLICATE_MATH_TAGS + _NON_CONTENT_TAGS):
        dup.decompose()
    for cls in _DUPLICATE_MATH_CLASSES:
        for dup in tag.find_all(class_=cls):
            dup.decompose()
    return tag.get_text(" ", strip=True)


def _clean_html_fragment(s):
    return html_module.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _extract_balanced_json(text, start_idx):
    """text[start_idx] must be '{'. Returns the balanced {...} substring."""
    if start_idx is None or start_idx < 0 or start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start_idx, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx:i + 1]
    return None


def _parse_js_object_literal(blob):
    # NPTEL courseOutline is a JS literal with bareword keys.
    fixed = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', blob)
    return json.loads(fixed)


_ISBN_RE = re.compile(r"\bISBN\b", re.I)
BIBLIOGRAPHY_WORDS_PER_CITATION = 150


def _is_bibliography_like(text):
    isbn_count = len(_ISBN_RE.findall(text))
    if isbn_count == 0:
        return False
    return (len(text.split()) / isbn_count) < BIBLIOGRAPHY_WORDS_PER_CITATION


def flatten_toc(node, out=None):
    """Bug class 8: return [(clean_title, slug)] for LEAF content pages only.
    Chapter/unit divider nodes have a slug but no real page (they 404) and a
    short generic title that would out-score their own subsections."""
    if out is None:
        out = []
    title = _clean_html_fragment(node.get("title", ""))
    slug = node.get("slug")
    children = node.get("contents", []) or []
    if slug and not children:
        out.append((title, slug))
    for child in children:
        flatten_toc(child, out)
    return out


# ===========================================================================
# 4. HTTP LAYER — robots.txt, per-source rate limits, retry, failure tracking
# ===========================================================================

class Http:
    """All network access goes through here. `transient_failures` counts
    requests that ultimately FAILED for network/5xx/429 reasons (not 404,
    not robots-disallowed) — the pipeline uses it to tell "no match" apart
    from "couldn't check"."""

    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, sources=None):
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.sources = sources or SOURCES
        self._robots = {}          # domain -> RobotFileParser (only on success)
        self._last_request = {}    # source_key -> clock()
        self.transient_failures = 0
        self.requests_made = 0

    # -- robots ---------------------------------------------------------------
    def _robots_for(self, url):
        p = urlparse(url)
        domain = f"{p.scheme}://{p.netloc}"
        if domain in self._robots:
            return self._robots[domain]
        rp = RobotFileParser()
        rp.set_url(f"{domain}/robots.txt")
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(f"{domain}/robots.txt", headers=HEADERS, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log.warning(f"robots.txt fetch error for {domain} (attempt {attempt+1}): {e}")
                self.sleep(BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)])
                continue
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif 400 <= r.status_code < 500:
                rp.allow_all = True           # no robots.txt => everything allowed
            elif r.status_code >= 500:
                self.sleep(BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)])
                continue
            else:
                rp.parse(r.text.splitlines())
            self._robots[domain] = rp
            return rp
        # Transient: do NOT cache — try again on the next request.
        return None

    def is_allowed(self, url) -> Optional[bool]:
        """True/False, or None when robots.txt itself couldn't be fetched."""
        rp = self._robots_for(url)
        if rp is None:
            return None
        return rp.can_fetch(USER_AGENT, url)

    # -- rate limit -----------------------------------------------------------
    def _throttle(self, source_key):
        delay = self.sources.get(source_key, {}).get("rate_limit_seconds", 1.0)
        last = self._last_request.get(source_key)
        if last is not None:
            wait = delay - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)

    def wait_turn(self, source_key):
        """Public rate-limit hook for non-HTTP-client calls (YouTube API)."""
        self._throttle(source_key)
        self._last_request[source_key] = self.clock()

    def record_transient(self, why=""):
        self.transient_failures += 1
        if why:
            log.warning(f"transient failure: {why}")

    # -- GET ------------------------------------------------------------------
    def get(self, url, source_key, params=None):
        allowed = self.is_allowed(url)
        if allowed is None:
            self.record_transient(f"robots.txt unreachable for {url}")
            return None
        if not allowed:
            log.warning(f"robots.txt disallows {url} — skipping")
            return None

        for attempt in range(MAX_RETRIES):
            self._throttle(source_key)
            try:
                self.requests_made += 1
                resp = self.session.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                self._last_request[source_key] = self.clock()
            except requests.RequestException as e:
                self._last_request[source_key] = self.clock()
                log.warning(f"{source_key} request error (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    self.sleep(BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)])
                continue
            if resp.status_code == 200:
                # Several sites send text/html without charset; requests then
                # assumes ISO-8859-1 and mojibakes UTF-8. Trust sniffing.
                resp.encoding = resp.apparent_encoding
                return resp
            if resp.status_code == 404:
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                log.warning(f"{source_key} HTTP {resp.status_code} for {url} (attempt {attempt+1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    self.sleep(BACKOFF_SCHEDULE[min(attempt, len(BACKOFF_SCHEDULE) - 1)])
                continue
            log.warning(f"{source_key} returned {resp.status_code} for {url}")
            return None
        self.record_transient(f"{source_key} gave up on {url}")
        return None


class CatalogCache:
    """Whole-catalog loader that never poisons: retries with backoff, caches
    ONLY a non-empty result, and after a total failure waits a cooldown
    before trying again (raising SourceUnavailable meanwhile)."""

    def __init__(self, name, loader: Callable[[], list], http: Http,
                 backoff=CATALOG_RETRY_BACKOFF, cooldown=CATALOG_COOLDOWN_SECONDS):
        self.name = name
        self.loader = loader
        self.http = http
        self.backoff = list(backoff)
        self.cooldown = cooldown
        self._value = None
        self._failed_at = None

    def get(self):
        if self._value:
            return self._value
        if self._failed_at is not None and (self.http.clock() - self._failed_at) < self.cooldown:
            raise SourceUnavailable(f"{self.name} catalog unavailable (cooling down)")
        attempts = len(self.backoff) + 1
        for i in range(attempts):
            try:
                value = self.loader()
            except Exception as e:  # malformed page etc. — treat as a failed attempt
                log.warning(f"{self.name} catalog load raised {type(e).__name__}: {e}")
                value = None
            if value:
                self._value = value
                self._failed_at = None
                log.info(f"{self.name} catalog loaded: {len(value)} entries")
                return value
            if i < attempts - 1:
                log.warning(f"{self.name} catalog empty/failed (attempt {i+1}/{attempts}) — retrying in {self.backoff[i]}s")
                self.http.sleep(self.backoff[i])
        self._failed_at = self.http.clock()
        raise SourceUnavailable(f"{self.name} catalog could not be loaded after {attempts} attempts")


# ===========================================================================
# 5. SOURCES — each .search() returns SourceResult or None (= no match),
#    and raises SourceUnavailable when it could not check.
# ===========================================================================

@dataclass
class SourceResult:
    source: str
    text: str
    url: str                   # primary URL (best-matching page / course)
    license: str
    monetizable: bool
    title: str = ""            # matched course/book title (provenance)
    page_urls: list = field(default_factory=list)
    gate_coverage_pct: Optional[float] = None
    page_texts: list = field(default_factory=list)   # aligned with page_urls (page-based sources)
    pages_dropped: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# NPTEL course book reader (v2.3)
# Layout (validated on 106106145, 637 pages): an INDEX ("S.No Topic Page No."
# grouped under "Week N"), then each lecture starts on a new page with a
# course header block, "Week - 01" (sometimes "Module - 05", en-dashes),
# "Lecture - 01", the lecture title, then the transcript with
# "(Refer Slide Time: mm:ss)" markers and a page number at the foot.
# ---------------------------------------------------------------------------
_DASH = r"[-\u2013\u2014]"
NPTEL_BOOK_HEADER_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:Week|Module)[ \t]*" + _DASH + r"?[ \t]*(\d{1,2})[ \t]*\n"
    r"[ \t]*Lecture[ \t]*" + _DASH + r"?[ \t]*(\d{1,3})[ \t]*\n[ \t]*([^\n]+)", re.I)
_REFER_SLIDE_RE = re.compile(r"^[ \t]*\(\s*Refer\s+Slide\s+Time\s*:?[^)]*\)[ \t]*$", re.I | re.M)
_TOC_ENTRY_RE = re.compile(r"^[ \t]*\d{1,3}[ \t]+\S.*?[ \t]+\d{1,4}[ \t]*$", re.M)


class NptelBookLayoutError(ValueError):
    """The PDF is not in the validated NPTEL book layout — never guessed at."""


def _clean_book_page(text):
    text = _REFER_SLIDE_RE.sub("", text or "")
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and re.fullmatch(r"\s*\d{1,4}\s*", lines[-1]):      # page number at the foot
        lines.pop()
    return "\n".join(lines)


# BL-023: pypdf can split a heading's first letter ("W EEK- 5", book 110105076).
_TOC_WEEK_RE = re.compile(r"^\s*(?:W\s?eek|M\s?odule)\s*[-\u2013\u2014:]?\s*(\d{1,2})\s*$", re.I)
_HEADER_LECTURE_LINE_RE = re.compile(r"^\s*Lecture\s*[-\u2013\u2014.:]?\s*0*(\d{1,3})\s*$", re.I)
_LEC_TITLE_PREFIX_RE = re.compile(r"^\s*(?:Lecture|Lec)\s*[-\u2013\u2014.]?\s*\d{1,3}\s*[:.\-\u2013\u2014]\s*", re.I)
_LEC_MARKER_RE = re.compile(r"(?<![a-z])(?:Lecture|Lec)[\s_\-\u2013\u2014.:]*\d{1,3}(?!\d)", re.I)
_WORD_RE = re.compile(r"[a-z]+|\d+")          # "Heap_Demo1" -> heap, demo, 1


def _words(text):
    return [w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 2 or w.isdigit()]


def _page_footer_number(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if lines and re.fullmatch(r"\d{1,4}", lines[-1]):
        return int(lines[-1])
    return None


def _continues_index(lines, next_sno):
    """True if a page opens like an INDEX continuation: one of its first three
    non-empty lines is a week heading or starts with the next entry number."""
    for line in lines[:3]:
        if _TOC_WEEK_RE.match(line):
            return True
        m = re.match(r"^(\d{1,3})\b", line)
        if m and int(m.group(1)) == next_sno:
            return True
    return False


def parse_book_index(pages):
    """INDEX -> ([{sno, week, title, page}], index of the first page after
    the INDEX) or ([], 0) if the book has no recognisable INDEX. Entries
    must be numbered 1, 2, 3, ... in order; a title may wrap over lines."""
    start = next((i for i, p in enumerate(pages[:6]) if re.search(r"^\s*INDEX\b", p or "", re.I | re.M)), None)
    if start is None:
        return [], 0
    entries, week, cur = [], None, None
    i = start
    while i < len(pages):
        page = pages[i] or ""
        lines = [l.strip() for l in page.splitlines() if l.strip()]
        # v2.4.2: a book whose INDEX titles read "Lecture 28 : ..." (106105158)
        # has "Lecture NN" on every INDEX page. A page is still INDEX while its
        # first lines continue the numbering (next S.No) or name the next week.
        if i > start and entries and _LEC_MARKER_RE.search(page[:800]) \
                and not _continues_index(lines, len(entries) + 1):
            break                                      # first lecture page reached
        if i > start and not lines:
            i += 1
            continue
        if i > start and not any(_TOC_WEEK_RE.match(l) or re.match(r"^\d{1,3}\b", l) for l in lines[:3]):
            break
        for line in lines:
            m = _TOC_WEEK_RE.match(line)
            if m:
                week, cur = int(m.group(1)), None
                continue
            nxt = len(entries) + 1
            m = re.match(r"^(\d{1,3})(?:\s+(.*))?$", line)
            if cur is None and m and int(m.group(1)) == nxt and week is not None:
                cur = {"sno": nxt, "week": week, "title": "", "page": None}
                entries.append(cur)
                line = m.group(2) or ""
                if not line:
                    continue
            if cur is None:
                continue                               # header row, page footer, ...
            m = re.match(r"^(.*?)\s*(\d{1,4})$", line)
            if m and (m.group(1) or cur["title"]):
                cur["title"] = (cur["title"] + " " + m.group(1)).strip()
                cur["page"] = int(m.group(2))
                cur = None
            else:
                cur["title"] = (cur["title"] + " " + line).strip()
        i += 1
    entries = [e for e in entries if e["page"] is not None]
    for e in entries:
        # v2.4.2: some books title every entry "Lecture 27 : Dependency ...".
        # The number is not topic text and, being unique per week, it drowned
        # the real words in the week-relevance match (AL3501 Unit I: no hit).
        e["title_raw"] = e["title"]                    # header stripping keeps using the full title
        e["title"] = _LEC_TITLE_PREFIX_RE.sub("", e["title"]).strip() or e["title"]
    return entries, i


def _strip_lecture_header(text, title):
    """Drop the course/professor/lecture header block and the title line.
    Only the short lines before the first line of running prose are
    considered, so the transcript itself is never cut."""
    lines = text.splitlines()
    head = []
    for line in lines[:12]:
        if len(line.strip()) >= 70:
            break
        head.append(line)
    tw = set(_words(title))
    cut = None
    for j, line in enumerate(head):
        lw = set(_words(line))
        if _LEC_MARKER_RE.search(line) or (tw and lw and len(tw & lw) >= max(1, (len(tw) + 1) // 2)):
            cut = j
    if cut is None:
        return text
    return "\n".join(lines[cut + 1:])


def _norm_line(line):
    return " ".join(_words(line))


def _offset_from_lecture_headers(pages, entries, body_start):
    """BL-023: books with NO printed page numbers (110105076). Each lecture page
    opens with a header block whose own line is exactly "Lecture NN" (NN = the
    INDEX S.No). The PDF offset is accepted only if EVERY INDEX lecture has such
    a header and all of them give the SAME offset; otherwise refuse."""
    first = {}
    for i in range(body_start, len(pages)):
        for line in (pages[i] or "").splitlines()[:8]:
            m = _HEADER_LECTURE_LINE_RE.match(line)
            if m:
                first.setdefault(int(m.group(1)), i)
                break
    missing = [e["sno"] for e in entries if e["sno"] not in first]
    offsets = collections.Counter(first[e["sno"]] - e["page"] for e in entries if e["sno"] in first)
    if missing or len(offsets) != 1:
        raise NptelBookLayoutError(
            f"pages carry no page numbers and the lecture headers don't give one consistent offset "
            f"(missing headers for {missing[:5]}, offsets seen {dict(offsets)}) — refusing to guess")
    return next(iter(offsets))


def _parse_by_index(pages, entries, body_start):
    weeks = [e["week"] for e in entries]
    if weeks != sorted(weeks) or any(b - a > 1 for a, b in zip(weeks, weeks[1:])):
        # BL-023: a missed week heading silently files lectures under the previous week
        raise NptelBookLayoutError(f"INDEX weeks are not consecutive ({sorted(set(weeks))}) — "
                                   f"a week heading was probably not read; refusing to guess")
    footers = {i: _page_footer_number(p) for i, p in enumerate(pages) if i >= body_start}
    diffs = [i - n for i, n in footers.items() if n is not None]
    if not diffs:
        offset = _offset_from_lecture_headers(pages, entries, body_start)
    else:
        offset, hits = collections.Counter(diffs).most_common(1)[0]
        if hits < 0.8 * len(diffs):
            raise NptelBookLayoutError(f"page numbering is inconsistent ({hits}/{len(diffs)} pages agree)")
    starts = [e["page"] + offset for e in entries]
    if starts != sorted(starts) or len(set(starts)) != len(starts) or starts[-1] >= len(pages) or starts[0] < body_start:
        raise NptelBookLayoutError("INDEX page numbers don't fit the PDF")
    # A lecture start is verified by its INDEX title, a lecture marker
    # ("Lecture - 03", "Mod03_Lec15"), or the book's own lecture header block
    # (e.g. the professor line) that most lecture-start pages open with.
    heads = {s: [_norm_line(l) for l in (pages[s] or "").splitlines()[:6] if _norm_line(l)] for s in starts}
    common = collections.Counter(l for h in heads.values() for l in set(h))
    signature = {l for l, n in common.items() if n >= 0.6 * len(starts) and len(l) > 8}
    bad = []
    for e, s in zip(entries, starts):
        head = (pages[s] or "")[:900]
        tw = set(_words(e.get("title_raw", e["title"])))
        ok = bool(_LEC_MARKER_RE.search(head)) \
            or (tw and len(tw & set(_words(head))) >= max(1, (len(tw) + 1) // 2)) \
            or bool(signature & set(heads[s]))
        if not ok:
            bad.append(f"{e['sno']} {e['title']!r} (pdf page {s + 1})")
    if bad:
        raise NptelBookLayoutError(f"{len(bad)} of {len(entries)} INDEX lectures don't start where the INDEX "
                                   f"says, e.g. {bad[:3]} — refusing to guess where lectures start")
    out, per_week = [], collections.Counter()
    for k, (e, s) in enumerate(zip(entries, starts)):
        end = starts[k + 1] if k + 1 < len(starts) else len(pages)
        parts = [_clean_book_page(_strip_lecture_header(pages[s] or "", e.get("title_raw", e["title"])))]
        parts += [_clean_book_page(pages[j]) for j in range(s + 1, end)]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(x for x in parts if x.strip())).strip()
        per_week[e["week"]] += 1
        out.append({"week": e["week"], "lecture": per_week[e["week"]], "title": e["title"],
                    "first_page": s + 1, "text": text})
    return out


def _parse_by_headers(pages):
    lectures, cur, toc_entries = [], None, 0
    for i, raw in enumerate(pages):
        m = NPTEL_BOOK_HEADER_RE.search(raw or "")
        if m is None:
            if cur is None:
                toc_entries += len(_TOC_ENTRY_RE.findall(raw or ""))
            else:
                cur["parts"].append(_clean_book_page(raw))
            continue
        cur = {"week": int(m.group(1)), "lecture": int(m.group(2)), "title": m.group(3).strip(),
               "first_page": i + 1, "parts": [_clean_book_page(raw[m.end():])]}
        lectures.append(cur)
    if len(lectures) < 2:
        raise NptelBookLayoutError(f"found {len(lectures)} lecture headers — not an NPTEL course book layout")
    if toc_entries and toc_entries != len(lectures):
        raise NptelBookLayoutError(f"INDEX lists {toc_entries} lectures but {len(lectures)} lecture headers "
                                   f"were found — refusing to guess where lectures start")
    out = []
    for lec in lectures:
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(p for p in lec.pop("parts") if p.strip())).strip()
        out.append(dict(lec, text=text))
    return out


def parse_nptel_book(pages):
    """[page text] -> [{week, lecture, title, text, first_page}] in book order.
    Primary: the book's own INDEX (week, title, page) mapped onto the PDF via
    the printed page numbers, and EVERY lecture start verified against its
    INDEX title or a lecture marker. Books without an INDEX fall back to
    "Week - N / Lecture - N" headers. Anything that doesn't verify raises
    NptelBookLayoutError — lectures are never guessed into the wrong week."""
    entries, body_start = parse_book_index(pages)
    if len(entries) >= 2:
        return _parse_by_index(pages, entries, body_start)
    return _parse_by_headers(pages)


def _extract_pdf_pages(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SourceUnavailable("pypdf is not installed — cannot read NPTEL books. Fix: pip install pypdf")
    try:
        return [(pg.extract_text() or "") for pg in PdfReader(str(pdf_path)).pages]
    except Exception as e:
        raise NptelBookLayoutError(f"PDF could not be read ({type(e).__name__}: {str(e)[:120]})")


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_nptel_book(pdf_path: Path, extract=_extract_pdf_pages):
    """Parsed lectures, cached in <id>.lectures.json next to the PDF and
    re-parsed only if the PDF (sha256) or the parser version changed."""
    cache = pdf_path.with_suffix(".lectures.json")
    sha = _file_sha256(pdf_path)
    if cache.exists():
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("pdf_sha256") == sha and c.get("parser_version") == NPTEL_BOOK_PARSER_VERSION:
                return c["lectures"]
        except (ValueError, KeyError):
            pass
    lectures = parse_nptel_book(extract(pdf_path))
    atomic_write_json(cache, {"pdf_sha256": sha, "parser_version": NPTEL_BOOK_PARSER_VERSION,
                              "pdf": pdf_path.name, "lectures": lectures})
    log.info(f"    NPTEL book {pdf_path.name}: parsed {len(lectures)} lectures "
             f"in {len({l['week'] for l in lectures})} weeks (cached)")
    return lectures


def select_lectures_by_coverage(lectures, unit_topics, min_found=None, max_words=None):
    """BL-012: indices (document order) of the lectures to use for a unit.
    v2.4.1: first, every lecture whose TITLE clearly matches one of the unit's
    sub-topics (subtopic_matches, the same title matcher MIT/OpenStax use) is
    taken, as long as its text FOUNDs at least one sub-topic — a teacher
    expects the "Decision Making" lecture in a unit that names decision making,
    even when other lectures already mention the words.
    Then greedy set cover on coverage_checker FOUND sub-topics: repeatedly take the
    lecture adding the most NEW sub-topics (ties: more FOUND overall, then the
    earlier lecture), skipping lectures that FOUND fewer than min_found on
    their own or would exceed max_words. Stops when nothing adds coverage.
    Deterministic. [] if the unit has no checkable sub-topics or no lecture
    qualifies (caller falls back)."""
    min_found = NPTEL_MIN_FOUND_PER_LECTURE if min_found is None else min_found
    max_words = NPTEL_MAX_WORDS_PER_UNIT if max_words is None else max_words
    cc = _coverage_checker()
    subtopics = cc.scoring_subtopics_from_lines(unit_topics)
    if not subtopics:
        return []
    need = min(min_found, len(subtopics))
    found, words = [], []
    for lec in lectures:
        text = lec.get("text") or ""
        words.append(len(text.split()))
        if not text.strip():
            found.append(frozenset())
            continue
        res = cc.check_unit(text, subtopics)
        found.append(frozenset(t["topic"] for t in res["topics"] if t["status"] == "FOUND"))
    covered, picked, total = set(), [], 0
    titled = {key for _, key, _ in subtopic_matches(unit_topics, [(l.get("title") or "", i)
                                                                  for i, l in enumerate(lectures)])}
    for i in sorted(titled):
        if found[i] and total + words[i] <= max_words:
            picked.append(i)
            covered |= found[i]
            total += words[i]
    while True:
        best = None
        for i, f in enumerate(found):
            if i in picked or len(f) < need or total + words[i] > max_words:
                continue
            gain = len(f - covered)
            if gain == 0:
                continue
            key = (gain, len(f), -i)
            if best is None or key > best[0]:
                best = (key, i)
        if best is None:
            break
        i = best[1]
        picked.append(i)
        covered |= found[i]
        total += words[i]
    return sorted(picked)


def lecture_specificity(lectures, unit_topics):
    """BL-020 (v2.5.0): [(score, found_count)] per lecture. score = share of the
    unit's sub-topic weight the lecture FOUNDs, each sub-topic weighted by how
    RARE it is in this book (idf = log((N+1)/(df+1)), df = lectures that FOUND
    it). Generic sub-topics found everywhere ('Internet protocol' in a networks
    book, 'Motivation', 'Message') weigh ~0, specific ones ('Subnetting',
    'Ricart-Agrawala') weigh most. [] if no checkable sub-topics."""
    cc = _coverage_checker()
    subtopics = cc.scoring_subtopics_from_lines(unit_topics)
    distinct = sorted(set(subtopics))
    if not distinct or not lectures:
        return []
    found = []
    for lec in lectures:
        text = lec.get("text") or ""
        if not text.strip():
            found.append(set())
            continue
        res = cc.check_unit(text, subtopics)
        found.append({t["topic"] for t in res["topics"] if t["status"] == "FOUND"})
    n = len(lectures)
    idf = {s: math.log((n + 1) / (sum(s in f for f in found) + 1)) for s in distinct}
    total = sum(idf.values())
    if total <= 0:
        return [(0.0, len(f)) for f in found]
    return [(sum(idf[s] for s in f) / total, len(f)) for f in found]


def content_gate(lectures, unit_topics, min_score=None, max_words=None):
    """BL-020 (v2.5.0): used ONLY when the week-title gate failed. Indices (document
    order) of the lectures whose specificity >= NPTEL_CONTENT_GATE_MIN_SPECIFICITY
    and that FOUND >= NPTEL_MIN_FOUND_PER_LECTURE sub-topics, most specific first
    within the word cap; [] = the unit does not pass. Measured on 30 units / 6
    books (BL020_DESIGN.md): CS3591 III 0.51, IV 0.50, CS3551 III 0.39 pass;
    CS3551 I 0.26, GE3751 V 0.21, AL3501 V 0.08 stay out."""
    min_score = NPTEL_CONTENT_GATE_MIN_SPECIFICITY if min_score is None else min_score
    max_words = NPTEL_MAX_WORDS_PER_UNIT if max_words is None else max_words
    spec = lecture_specificity(lectures, unit_topics)
    ok = [i for i, (sc, nf) in enumerate(spec) if sc >= min_score and nf >= NPTEL_MIN_FOUND_PER_LECTURE]
    ok.sort(key=lambda i: (-spec[i][0], i))
    picked, total = [], 0
    for i in ok:
        w = len((lectures[i].get("text") or "").split())
        if total + w > max_words:
            continue
        picked.append(i)
        total += w
    return sorted(picked)


class NptelSource:
    key = "nptel"

    def __init__(self, http: Http, transcript_api_factory=None, allow_youtube=False, books_dir=None):
        self.http = http
        self.books_dir = Path(books_dir) if books_dir else None
        self._lectures = {}        # course_id -> parsed lectures (per run)
        self.cfg = SOURCES["nptel"]
        # v2.2: YouTube transcripts are OFF by default — youtube.com/robots.txt
        # disallows /api/ and /youtubei/, which the transcript library calls.
        self.allow_youtube = allow_youtube
        self._book_cache = {}      # course_id -> book dict | None (only after a definite answer)
        self.catalog = CatalogCache("NPTEL", self._load_catalog, http)
        self._transcript_api_factory = transcript_api_factory
        self._transcript_api = None
        self._yt_consecutive_blocks = 0
        self._yt_blocked_at = None

    def _load_catalog(self):
        resp = self.http.get(self.cfg["search_url"], self.key)
        if resp is None:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        courses = []
        for card in soup.find_all("div", class_="course-card"):
            a = card.find("a", href=True)
            name_el = card.find("div", class_="name")
            if a and name_el:
                courses.append((name_el.get_text(strip=True), a["href"]))
        return courses

    def match_course(self, subject_name):
        """(course_name, href) or None. Strict mode + anchor gate."""
        courses = self.catalog.get()
        by_name = {}
        for name, href in courses:
            by_name.setdefault(name, href)
        best = best_match(clean_subject_name(subject_name), [(n, n) for n, _ in courses])
        return (best, by_name[best]) if best is not None else None

    def tied_top_courses(self, subject_name):
        """v2.4.2: every catalog course tied at the winning score, in ranking
        order (the first one is exactly match_course()). [] if no match."""
        courses = self.catalog.get()
        by_name = {}
        for name, href in courses:
            by_name.setdefault(name, href)
        ranked = _score_candidates(clean_subject_name(subject_name), [(n, n) for n in by_name])
        if not ranked or ranked[0][0] < MATCH_THRESHOLD:
            return []
        top = ranked[0][0]
        return [(k, by_name[k]) for sc, k in ranked if sc == top]

    def choose_course(self, subject_name):
        """v2.4.2 (real case AL3501): several NPTEL courses can tie with a
        perfect name score ("Natural Language Processing" 106101007 — no book,
        "NOC:Natural Language Processing" 106105158 — has one). With YouTube
        transcripts off, only a course with an English book can ever be used,
        so prefer the first tied course that has one; if none has, return
        exactly what v2.4.1 chose."""
        tied = self.tied_top_courses(subject_name)
        if not tied:
            return None
        if self.allow_youtube or len(tied) == 1:
            return tied[0]
        for name, href in tied:
            url = href if href.startswith("http") else f"{self.cfg['base']}{href}"
            if self.english_book(href, url, name) is not None:
                if (name, href) != tied[0]:
                    log.info(f"    NPTEL: {len(tied)} courses tie for {subject_name!r}; using {name!r} "
                             f"(has an English book) instead of {tied[0][0]!r}")
                return name, href
        return tied[0]

    def course_by_id(self, course_id):
        """BL-024: (course_name, href) of a catalog course by its id, or None."""
        for name, href in self.catalog.get():
            if self.course_id_from_href(href) == str(course_id):
                return name, href
        return None

    def _api(self):
        if self._transcript_api is None:
            if self._transcript_api_factory is not None:
                self._transcript_api = self._transcript_api_factory()
            else:
                from youtube_transcript_api import YouTubeTranscriptApi
                self._transcript_api = YouTubeTranscriptApi()
        return self._transcript_api

    # Transcript errors that mean "this video simply has no usable transcript"
    # (permanent) vs. everything else (blocked / rate-limited / network).
    _PERMANENT_TRANSCRIPT_ERRORS = {
        "NoTranscriptFound", "TranscriptsDisabled", "VideoUnavailable",
        "VideoUnplayable", "InvalidVideoId", "AgeRestricted", "NotTranslatable",
    }
    # YouTube refusing THIS IP (live 2026-09-23, mid AI&DS batch). Hammering on
    # every lecture only prolongs the ban, so a circuit breaker trips after
    # YOUTUBE_BLOCK_TRIP in a row and NPTEL is reported unavailable (units
    # DEGRADED -> untouched, retried next run) until the cooldown passes.
    _BLOCK_ERRORS = {"IpBlocked", "RequestBlocked", "TooManyRequests", "YouTubeRequestFailed"}

    def _youtube_blocked(self):
        if self._yt_blocked_at is None:
            return False
        if self.http.clock() - self._yt_blocked_at >= YOUTUBE_BLOCK_COOLDOWN_SECONDS:
            self._yt_blocked_at = None
            self._yt_consecutive_blocks = 0
            log.info("YouTube cooldown over — retrying transcripts")
            return False
        return True

    @staticmethod
    def course_id_from_href(href):
        m = re.search(r"/courses/(\d+)", href or "")
        return m.group(1) if m else None

    def english_book(self, href, course_url, course_name):
        """NPTEL's own downloads API (nptel.ac.in, robots-allowed) -> the
        English course book entry, or None if the course has none. A network
        failure is recorded as transient by Http (-> unit DEGRADED) and is
        never cached."""
        cid = self.course_id_from_href(href)
        if cid is None:
            return None
        if cid in self._book_cache:
            return self._book_cache[cid]
        before = self.http.transient_failures
        resp = self.http.get(f"{self.cfg['base']}/api/downloads/{cid}", self.key)
        if resp is None:
            if self.http.transient_failures == before:     # 404 / refused: definite "no book"
                self._book_cache[cid] = None
            return None
        try:
            payload = json.loads(resp.text)
        except ValueError:
            log.warning(f"    NPTEL downloads API for {cid} returned non-JSON — treating as no book")
            self._book_cache[cid] = None
            return None
        books = ((payload or {}).get("data") or {}).get("books") or []
        book = None
        for b in books:
            if isinstance(b, dict) and str(b.get("title", "")).strip().lower() == "english" and b.get("url"):
                book = {"course_id": cid, "course_title": course_name, "course_url": course_url,
                        "book_url": b["url"], "mimetype": b.get("mimetype")}
                break
        self._book_cache[cid] = book
        return book

    def book_lectures(self, book):
        """Parsed lectures of a downloaded book, or None if it isn't downloaded
        yet. A file that is present but unreadable / not in the validated
        layout keeps the unit waiting (never guessed) and is flagged."""
        cid = book["course_id"]
        if cid in self._lectures:
            return self._lectures[cid]
        if self.books_dir is None:
            return None
        pdf = self.books_dir / f"{cid}.pdf"
        if not pdf.exists():
            return None
        try:
            lectures = load_nptel_book(pdf)
        except NptelBookLayoutError as e:
            log.error(f"    NPTEL book {pdf.name} can't be used: {e} — unit keeps waiting; "
                      f"re-download the English book or report the layout")
            raise NptelBookNeeded(dict(book, problem=str(e)))
        self._lectures[cid] = lectures
        return lectures

    def search_book(self, book, lectures, unit_topics, course_name, course_url):
        """Relevance gate (unchanged since v2.3): topic-mode best_match of the
        unit against each week's lecture titles must succeed, else no NPTEL hit.
        v2.4 (BL-012): the lectures themselves are then picked one by one
        across ALL weeks (select_lectures_by_coverage). v2.4.2: if none
        qualifies, NPTEL gives no hit (no whole-week fallback)."""
        weeks = {}
        for lec in lectures:
            weeks.setdefault(lec["week"], []).append(lec)
        top = best_match(" ".join(unit_topics),
                         [(w, " ".join(l["title"] for l in ls)) for w, ls in sorted(weeks.items())], mode="topic")
        if top is None:
            # BL-020 (v2.5.0): a unit whose content is spread over several
            # weeks can fail the week-title gate although whole lectures cover
            # it (CS3591 III: 0.31; IP Addressing I-III FOUND 4/6 each).
            picked = content_gate(lectures, unit_topics)
            if not picked:
                log.info("    NPTEL: week-title gate failed and content gate failed — no NPTEL hit, cascade continues")
                return None
            top = "content gate"
        else:
            picked = select_lectures_by_coverage(lectures, unit_topics)
        if not picked:
            # v2.4.2: no whole-week fallback any more. Real case AL3501 Unit V
            # (discourse): the course has no discourse lectures, the fallback
            # took week 3 (morphology / POS) and it passed the gate on 1/9.
            log.info(f"    NPTEL: no lecture covers >= {NPTEL_MIN_FOUND_PER_LECTURE} of the unit's sub-topics "
                     f"(matched week {top}) — no NPTEL hit, cascade continues")
            return None
        chosen = [lectures[i] for i in picked]
        log.info(f"    NPTEL lectures by coverage (matched week {top}): "
                 + "; ".join(f"wk{l['week']} {l['title']}" for l in chosen))
        text = "\n\n".join(l["text"] for l in chosen if l["text"].strip())
        if not text.strip():
            return None
        used = sorted({l["week"] for l in chosen})
        week_label = f"week {used[0]}" if len(used) == 1 else "weeks " + ", ".join(str(w) for w in used)
        return SourceResult(self.key, text, course_url, self.cfg["license"], self.cfg["monetizable"],
                            title=f"{course_name} (NPTEL course book, {week_label})",
                            page_urls=[f"{book['book_url']}#page={l['first_page']}" for l in chosen])   # the book's own
                            # lecture numbers have typos (e.g. two "Lecture - 02"); the PDF page is unique

    def _outline_week(self, course_url, unit_topics):
        """The course website's best-matching week for the unit, or None
        (no outline / no week clears the threshold). Lesson names are matched
        without their "Lecture N :" prefix (v2.4.3, see _LEC_TITLE_PREFIX_RE)."""
        resp = self.http.get(course_url, self.key)
        if resp is None:
            return None
        idx = resp.text.find("courseOutline:")
        if idx == -1:
            return None
        blob = _extract_balanced_json(resp.text, resp.text.find("{", idx))
        if not blob:
            return None
        try:
            outline = _parse_js_object_literal(blob)
        except ValueError:
            return None
        weeks = outline.get("units", []) or []
        if not weeks:
            return None

        def week_text(w):
            return " ".join(_LEC_TITLE_PREFIX_RE.sub("", lesson.get("name", ""))
                            for lesson in w.get("lessons", []) or [])

        weeks_by_id = {w.get("id"): w for w in weeks}
        top_id = best_match(" ".join(unit_topics), [(w.get("id"), week_text(w)) for w in weeks], mode="topic")
        return None if top_id is None else weeks_by_id[top_id]

    def search(self, subject_name, unit_topics, course_id=None):
        if course_id:
            # BL-024: a person chose this course for the unit — no name matching.
            match = self.course_by_id(course_id)
            if match is None:
                log.warning(f"    NPTEL: chosen course {course_id} is not in the NPTEL catalog — no NPTEL hit")
                return None
            log.info(f"    NPTEL: using the reviewed course choice {match[0]!r} ({course_id})")
        else:
            match = self.choose_course(subject_name)
        if match is None:
            return None
        course_name, href = match
        course_url = href if href.startswith("http") else f"{self.cfg['base']}{href}"

        if not self.allow_youtube:
            book = self.english_book(href, course_url, course_name)
            if book is None:
                log.info(f"    NPTEL course {course_name!r} matched but has no English book — "
                         f"transcripts disabled (robots.txt), cascade continues")
                return None
            lectures = self.book_lectures(book)
            if lectures is not None:
                # v2.4.3: the downloaded book is the evidence — its own weeks
                # decide relevance (search_book). v2.4.2 first required a match
                # against the WEBSITE outline, whose "Lecture N:" lesson names
                # failed every AL3501 unit before the book was even opened.
                return self.search_book(book, lectures, unit_topics, course_name, course_url)
            if course_id:
                # chosen by a person: always wait for its book (the website outline is not asked)
                raise NptelBookNeeded(dict(book, week_title="(reviewed course choice)"))
            week = self._outline_week(course_url, unit_topics)
            if week is None:
                return None                     # book missing and this unit isn't in the course
            raise NptelBookNeeded(dict(book, week_title=week.get("title") or week.get("name") or week.get("id")))

        week = self._outline_week(course_url, unit_topics)
        if week is None:
            return None

        if self._youtube_blocked():
            raise SourceUnavailable("YouTube is blocking transcript requests from this IP (circuit breaker open)")
        api = self._api()
        parts, ids = [], []
        for lesson in week.get("lessons", []) or []:
            vid = lesson.get("youtube_id")
            if not vid:
                continue
            self.http.wait_turn("youtube")
            try:
                transcript = api.fetch(vid, languages=["en-IN", "en"])
                parts.append(" ".join(seg.text for seg in transcript))
                ids.append(vid)
                self._yt_consecutive_blocks = 0
            except Exception as e:
                name = type(e).__name__
                if name in self._PERMANENT_TRANSCRIPT_ERRORS:
                    log.info(f"    NPTEL transcript unavailable for {vid}: {name}")
                    continue
                first_line = str(e).strip().splitlines()[0][:160] if str(e).strip() else ""
                self.http.record_transient(f"YouTube transcript {vid}: {name}: {first_line}")
                if name in self._BLOCK_ERRORS:
                    self._yt_consecutive_blocks += 1
                    if self._yt_consecutive_blocks >= YOUTUBE_BLOCK_TRIP:
                        self._yt_blocked_at = self.http.clock()
                        log.error(f"YouTube is blocking this IP — pausing transcript requests for "
                                  f"{YOUTUBE_BLOCK_COOLDOWN_SECONDS // 60} min; NPTEL units will be "
                                  f"DEGRADED (untouched) and retried on the next run")
                        raise SourceUnavailable("YouTube IP block (circuit breaker tripped)")
        text = "\n\n".join(p for p in parts if p.strip())
        if not text.strip():
            return None
        return SourceResult(self.key, text, course_url, self.cfg["license"], self.cfg["monetizable"],
                            title=course_name,
                            page_urls=[f"https://www.youtube.com/watch?v={v}" for v in ids])


# MIT nav pages that are course administration, never unit content.
_MIT_NON_CONTENT_PAGE_RE = re.compile(
    r"^(syllabus|calendar|schedule|readings?|assignments?|exams?|quizzes|"
    r"download( course)?|instructor insights|related resources|course info|"
    r"projects?|recitations?|labs?|resource index|video lectures|"
    r"lecture videos|problem sets?|tools|study materials)$", re.I)


class MitOcwSource:
    key = "mit_ocw"

    def __init__(self, http: Http):
        self.http = http
        self.cfg = SOURCES["mit_ocw"]
        self.catalog = CatalogCache("MIT OCW", self._load_index, http)

    def _load_index(self):
        resp = self.http.get(self.cfg["search_url"], self.key)
        if resp is None:
            return []
        import warnings
        with warnings.catch_warnings():
            # sitemap.xml via html.parser is deliberate (no lxml dependency).
            warnings.simplefilter("ignore")
            soup = BeautifulSoup(resp.text, "html.parser")
        slugs = []
        for loc in soup.find_all("loc"):
            m = re.match(r".*/courses/([a-zA-Z0-9\-]+)/sitemap\.xml$", loc.get_text(strip=True))
            if m:
                slugs.append(m.group(1))
        return slugs

    def match_course(self, subject_name):
        slugs = self.catalog.get()
        return best_match(clean_subject_name(subject_name), [(s, " ".join(s.split("-"))) for s in slugs])

    @staticmethod
    def select_pages(unit_topics, nav_links):
        """Bug class 2 fix: SAME hardened machinery as OpenStax (IDF-weighted,
        anchor-gated, >=2-token overlap, per-sub-concept winners). No more
        label-priority fallback — a generic 'Readings'/'Syllabus' page can
        never be chosen, so one whole page can't be reused for every unit.
        nav_links: [(title, href)] in page order. Returns hrefs."""
        content = [(t, h) for t, h in nav_links if t and not _MIT_NON_CONTENT_PAGE_RE.match(t.strip())]
        if not content:
            return []
        # Gate: the unit as a whole must clearly match at least one page
        # (same gate OpenStax applies per book).
        unit_query = " ".join(unit_topics)
        gate = _score_candidates(unit_query, [(h, t) for t, h in content], mode="topic")
        if not gate or gate[0][0] < MATCH_THRESHOLD:
            return []
        return select_relevant_pages(unit_topics, content)

    def search(self, subject_name, unit_topics):
        slug = self.match_course(subject_name)
        if slug is None:
            return None
        course_url = f"{self.cfg['base']}/courses/{slug}/"
        resp = self.http.get(course_url, self.key)
        if resp is None:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        nav, seen = [], set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"/courses/{slug}/pages/" in href and href not in seen:
                seen.add(href)
                nav.append((a.get_text(" ", strip=True), href))
        chosen = self.select_pages(unit_topics, nav)
        if not chosen:
            return None
        parts, urls = [], []
        for href in chosen:
            page_url = href if href.startswith("http") else f"{self.cfg['base']}{href}"
            r = self.http.get(page_url, self.key)
            if r is None:
                continue
            main = BeautifulSoup(r.text, "html.parser")
            main = main.find(id="course-content-section") or main.find("main")
            if not main:
                continue
            text = _extract_clean_text(main)
            if not text.strip() or _is_bibliography_like(text):
                continue
            parts.append(text)
            urls.append(page_url)
        if not parts:
            return None
        return SourceResult(self.key, "\n\n".join(parts), urls[0], self.cfg["license"],
                            self.cfg["monetizable"], title=slug, page_urls=urls, page_texts=list(parts))


def choose_openstax_book(unit_topics, books):
    """Bug class 3: volume chosen PER UNIT from each candidate book's own
    TOC, not once per subject. books: [(book_slug, pages)] in book-ranking
    order, pages = [(title, page_slug)] leaf pages. Returns
    (book_slug, pages, top_page_slug) or None. Whole-unit query, topic mode,
    NO singularization (validated: singularizing here picked Vol 3 for an
    intro single-variable unit)."""
    unit_query = " ".join(unit_topics)
    best = None   # (key, slug, pages, top)
    for slug, pages in books:
        if not pages:
            continue
        ranked = _score_candidates(unit_query, [(s, t) for t, s in pages], mode="topic")
        if not ranked or ranked[0][0] < MATCH_THRESHOLD:
            continue
        score, top = ranked[0]
        # Tie-break (v2 fix): v1 kept whichever book the OpenStax API happened
        # to list first when two volumes both scored a perfect 1.0 — i.e. the
        # volume choice depended on API result order (with Vol 1 listed first,
        # MA3151 Unit IV picked precalc "Trigonometric Functions"). Now ties
        # go to the book covering MORE of the unit's sub-concepts, then the
        # higher total sub-concept match strength; only a full tie falls back
        # to ranking order. The primary score is still the validated
        # non-singularized one.
        subs = subtopic_matches(unit_topics, pages)
        key = (score, len(subs), round(math.fsum(sc for _, _, sc in subs), SCORE_DECIMALS))
        if best is None or key > best[0]:
            best = (key, slug, pages, top)
    if best is None:
        return None
    return best[1], best[2], best[3]


_OPENSTAX_LICENSE_RE = re.compile(r"Creative Commons Attribution[\w\- ]*License")


class OpenStaxSource:
    key = "openstax"

    def __init__(self, http: Http):
        self.http = http
        self.cfg = SOURCES["openstax"]
        self._toc_cache = {}   # book slug -> pages (only successful loads)

    def _query(self, q):
        resp = self.http.get(self.cfg["search_url"], self.key,
                             params={"type": "books.Book", "search": q,
                                     "fields": "title,cnx_id,book_uuid", "format": "json"})
        if resp is None:
            return []
        try:
            return resp.json().get("items", []) or []
        except ValueError:
            return []

    def rank_books(self, subject_name):
        query = clean_subject_name(subject_name)
        tokens = tokenize(query)
        if not tokens:
            return []
        # CMS search ANDs every word: query the phrase AND each content word,
        # merge; precision comes from the scorer, not from retrieval.
        seen, items = set(), []
        for q in [query, *sorted(tokens)]:
            for it in self._query(q):
                if it.get("id") not in seen:
                    seen.add(it.get("id"))
                    items.append(it)
        by_title = {it.get("title", ""): it for it in items}
        ranking = _score_candidates(query, [(t, t) for t in by_title], mode="loose")
        out = []
        for score, title in ranking:
            if score <= 0:
                continue
            slug = (by_title[title].get("meta") or {}).get("slug")
            if slug:
                out.append((title, slug))
            if len(out) >= MAX_OPENSTAX_BOOKS_TO_CHECK:
                break
        return out

    def load_toc(self, book_slug):
        if book_slug in self._toc_cache:
            return self._toc_cache[book_slug]
        tree = None
        for boot in ("preface", "1-introduction", "introduction"):
            r = self.http.get(f"{self.cfg['base']}/books/{book_slug}/pages/{boot}", self.key)
            if r is None:
                continue
            idx = r.text.find('"tree":')
            if idx == -1:
                continue
            blob = _extract_balanced_json(r.text, r.text.find("{", idx))
            if not blob:
                continue
            try:
                tree = json.loads(blob)
                break
            except ValueError:
                continue
        if tree is None:
            return []
        pages = flatten_toc(tree)
        if pages:
            self._toc_cache[book_slug] = pages
        return pages

    def search(self, subject_name, unit_topics):
        books = self.rank_books(subject_name)
        if not books:
            return None
        choice = choose_openstax_book(unit_topics, [(slug, self.load_toc(slug)) for _, slug in books])
        if choice is None:
            return None
        slug, pages, top_slug = choice
        title = next((t for t, s in books if s == slug), slug)
        chosen = select_relevant_pages(unit_topics, pages)

        parts, urls = [], []
        license_str, monetizable = self.cfg["license"], self.cfg["monetizable"]
        for page_slug in chosen:
            page_url = f"{self.cfg['base']}/books/{slug}/pages/{page_slug}"
            r = self.http.get(page_url, self.key)
            if r is None:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            main = soup.find(id="main-content") or soup.find("main", class_="page-content")
            if main:
                parts.append(_extract_clean_text(main))
                urls.append(page_url)
            m = _OPENSTAX_LICENSE_RE.search(r.text)
            if m:
                lt = m.group(0)
                license_str = "CC BY" + ("-NC" if "NonCommercial" in lt else "") \
                    + ("-SA" if "ShareAlike" in lt else "") + " 4.0"
                monetizable = "NonCommercial" not in lt
        text = "\n\n".join(p for p in parts if p.strip())
        if not text.strip():
            return None
        # Provenance (v2 fix): v1 always recorded the whole-unit top page as
        # the URL even when that page was NOT among the pages actually fetched
        # (live case: CY3151 Unit III recorded the "Water Properties" appendix
        # while its text came from "10.4 Phase Diagrams"). The primary URL
        # must be a page whose text is in the file.
        top_url = f"{self.cfg['base']}/books/{slug}/pages/{top_slug}"
        primary = top_url if top_url in urls else urls[0]
        return SourceResult(self.key, text, primary, license_str, monetizable, title=title,
                            page_urls=urls, page_texts=list(parts))


# ===========================================================================
# 6. FILE I/O — atomic writes, checkpoint, subjects.json traversal
# ===========================================================================

def _replace_with_retry(src: Path, dst: Path, attempts=5):
    # Windows: os.replace fails if AV / OneDrive / an editor briefly holds dst.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5 * (i + 1))


def atomic_write_text(path: Path, text: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _replace_with_retry(tmp, path)


def atomic_write_json(path: Path, data):
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def unit_file_name(unit_key):
    return f"Unit{ROMAN_TO_INT.get(unit_key, unit_key)}.txt"


def _copy_quality(sub):
    """Rank duplicate copies of one subject code. subjects.json holds a
    curriculum-TABLE copy (name = category code like "PCC 3", no units) and a
    SYLLABUS copy (real title + units) of the same code — see BL-001. v2.0
    used whichever copy came first, so 16 subjects were scraped as "PCC 3"
    with no topics while a complete copy sat next to it. Prefer: has real
    topics > usable name > syllabus-style name ("... L T P C" header)."""
    topics = sub.get("topics") or {}
    has_topics = bool(sub.get("units")) and any(len(clean_unit_topics(v)) > 0 for v in topics.values())
    name = sub.get("name", "") or ""
    return (has_topics, not is_unusable_subject_name(name), bool(re.search(r"\bL\s*T\s*P\s*C\b", name)))


def _best_copy(copies):
    # max() keeps the FIRST of equally-good copies -> identical to v2.0 whenever
    # the first copy was already the best one (e.g. MA3151, PH3151, CY3151).
    return max(copies, key=_copy_quality)


def iter_subject_groups(data, department=None):
    """[(code, canonical_dict, [every dept copy], [dept names])] in first-seen
    order, deduped by code. With department=, only codes that department
    teaches (but ALL dept copies of those codes are still updated)."""
    depts = data.get("universities", {}).get("Anna University", {}).get("departments", {})
    by_code, dept_names, order = {}, {}, []
    for dname, dinfo in depts.items():
        for subs in (dinfo.get("semesters") or {}).values():
            for code, sub in subs.items():
                if code not in by_code:
                    by_code[code] = []
                    dept_names[code] = []
                    order.append(code)
                by_code[code].append(sub)
                dept_names[code].append(dname)
    groups = [(c, _best_copy(by_code[c]), by_code[c], dept_names[c]) for c in order]
    if department is not None:
        groups = [g for g in groups if department in g[3]]
    return groups


def list_departments(data):
    return list(data.get("universities", {}).get("Anna University", {}).get("departments", {}).keys())


class Checkpoint:
    """checkpoint.json = {"version": 2, "scopes": {scope: {"next_index": n,
    "failed": [codes], "updated": iso}}}. Reads v1 {"next_index": n} as the
    "all" scope."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"version": 2, "scopes": {}}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                log.warning(f"checkpoint {self.path} unreadable — starting fresh")
                raw = {}
            if "scopes" in raw:
                self.data = raw
            elif "next_index" in raw:
                self.data["scopes"]["all"] = {"next_index": int(raw["next_index"]), "failed": []}

    def get(self, scope):
        s = self.data["scopes"].get(scope, {})
        return int(s.get("next_index", 0)), list(s.get("failed", []))

    def save(self, scope, next_index, failed):
        self.data["scopes"][scope] = {"next_index": next_index, "failed": sorted(set(failed)),
                                      "updated": datetime.now().isoformat(timespec="seconds")}
        atomic_write_json(self.path, self.data)


# ===========================================================================
# 7. PIPELINE
# ===========================================================================

@dataclass
class Config:
    subjects_json: Path
    output_dir: Path
    coverage_dir: Path
    force: bool = False
    min_words: int = MIN_WORDS_PER_UNIT
    min_write_coverage_pct: Optional[float] = MIN_WRITE_COVERAGE_PCT   # None = gate off
    allow_youtube: bool = False      # v2.2: YouTube transcripts are opt-in (robots.txt)
    redo_units: frozenset = frozenset()   # v2.3: {(code, unit)} to re-source even if already sourced
    review_lower_sources: bool = True     # v2.6 BL-018: OpenStax/MIT hits -> _pending/ (not live)
    rejects: dict = field(default_factory=dict)   # v2.6 BL-018: {(code, unit): {url, ...}}
    course_choices: dict = field(default_factory=dict)   # v2.7 BL-024: {(code, unit|'*'): course_id}


@dataclass
class SubjectOutcome:
    code: str
    counts: dict = field(default_factory=dict)   # outcome -> n
    retry: bool = False                          # needs a retry (degraded/error)
    error: str = ""


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def invalid_existing_units(sources: dict) -> set:
    """Units whose existing entry shares its URL (same source) with a sibling
    unit — the bug-class-2 signature (one coarse page recorded for several
    different units). Such entries are never trusted."""
    by_key = {}
    for unit, entry in (sources or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("source") == "nptel" and entry.get("page_urls"):
            # NPTEL's url is the COURSE page, shared by design by every week
            # drawn from that course; the lectures (page_urls) identify it.
            k = ("nptel", tuple(sorted(entry["page_urls"])))
        else:
            k = (entry.get("source"), entry.get("url"))
        by_key.setdefault(k, []).append(unit)
    bad = set()
    for units in by_key.values():
        if len(units) > 1:
            bad.update(units)
    return bad


def load_reviewed_rejects(output_dir: Path) -> dict:
    """BL-018: {(CODE, UNIT): {url, ...}} from <output_dir>/_reviewed_rejects.json.
    A broken file STOPS the run (SystemExit): silently ignoring it would let
    rejected pages back in."""
    path = Path(output_dir) / REVIEWED_REJECTS_FILE
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}) — fix it before running")
    if not isinstance(entries, list):
        raise SystemExit(f"{path} must be a JSON list of reject entries")
    out = {}
    for i, e in enumerate(entries):
        missing = [k for k in _REJECT_FIELDS if not (isinstance(e, dict) and e.get(k))]
        if missing:
            raise SystemExit(f"{path} entry #{i} is missing {missing} — fix it before running")
        out.setdefault((e["code"].strip().upper(), e["unit"].strip().upper()), set()).add(e["page_url"].strip())
    return out


def load_course_choices(output_dir: Path) -> dict:
    """BL-024: {(CODE, UNIT or '*'): course_id} from <output_dir>/_course_choice.json.
    A broken file, a missing field or two different courses for the same
    (code, unit) STOPS the run (SystemExit) — never guessed."""
    path = Path(output_dir) / COURSE_CHOICE_FILE
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}) — fix it before running")
    if not isinstance(entries, list):
        raise SystemExit(f"{path} must be a JSON list of course-choice entries")
    out = {}
    for i, e in enumerate(entries):
        missing = [k for k in _CHOICE_FIELDS if not (isinstance(e, dict) and str(e.get(k) or "").strip())]
        if missing:
            raise SystemExit(f"{path} entry #{i} is missing {missing} — fix it before running")
        key = (e["code"].strip().upper(), e["unit"].strip().upper())
        cid = str(e["course_id"]).strip()
        if key in out and out[key] != cid:
            raise SystemExit(f"{path}: {key[0]}:{key[1]} has two different courses ({out[key]}, {cid})")
        out[key] = cid
    return out


def load_pending(output_dir: Path) -> dict:
    """BL-018: {"CODE:UNIT": entry} waiting for review (empty if none)."""
    path = Path(output_dir) / PENDING_DIR / PENDING_REGISTRY
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must be a JSON object")
    return data


class Pipeline:
    def __init__(self, cfg: Config, http: Http = None, sources: dict = None):
        self.cfg = cfg
        self.http = http or Http()
        self.books_needed = {}     # course_id -> book dict + needed_by [{subject, unit}]
        self.pending = load_pending(cfg.output_dir)   # BL-018
        self.review_rejections = 0                     # BL-018: whole hits dropped by the rejects list
        self.sources = sources or {
            "nptel": NptelSource(self.http, allow_youtube=cfg.allow_youtube,
                                 books_dir=cfg.output_dir / NPTEL_BOOKS_DIR),
            "mit_ocw": MitOcwSource(self.http),
            "openstax": OpenStaxSource(self.http),
        }

    # ---- per unit ------------------------------------------------------------
    def source_unit(self, subject_name, unit_topics, reject: Callable[[SourceResult], Optional[str]] = None,
                    rejected_urls=frozenset(), nptel_course=None):
        """Cascade. Returns (status, SourceResult|None):
           ("hit", r) | ("no_match", None) | ("degraded", None)
           | ("awaiting_nptel_book", book_dict)
        "degraded" = some check could not be completed; outcome unreliable.
        "awaiting_nptel_book" = NPTEL is the cascade winner but its book must
        be downloaded by hand first; lower sources are NOT consulted."""
        topics = clean_unit_topics(unit_topics)
        if not topics:
            return "no_match", None
        before = self.http.transient_failures
        for key in SOURCE_ORDER:
            src = self.sources.get(key)
            if src is None:
                continue
            try:
                if key == "nptel" and nptel_course:
                    r = src.search(subject_name, topics, course_id=nptel_course)
                else:
                    r = src.search(subject_name, topics)
            except NptelBookNeeded as e:
                return "awaiting_nptel_book", e.book
            except SourceUnavailable as e:
                # A higher-priority source we couldn't check: whatever a
                # lower one returns might not be the true cascade winner.
                log.warning(f"    {key} unavailable: {e}")
                return "degraded", None
            if self.http.transient_failures > before:
                return "degraded", None
            if r is None:
                continue
            r = self.apply_reviewed_rejects(r, rejected_urls)
            if r is None:
                continue
            r = self.page_gate(r, topics)
            if r is None:
                continue
            words = len(r.text.split())
            if words < self.cfg.min_words:
                log.info(f"    {key} hit too thin ({words}w) — trying next source")
                continue
            why = reject(r) if reject else None
            if why:
                log.warning(f"    {key} hit rejected: {why} — trying next source")
                continue
            gate = self.topic_gate(r.text, topics)
            if gate is not None:
                pct, found, total = gate
                r.gate_coverage_pct = pct
                if pct <= self.cfg.min_write_coverage_pct:
                    log.warning(f"    {key} hit REJECTED by write gate: {found}/{total} sub-topics found "
                                f"({pct}% <= {self.cfg.min_write_coverage_pct}%) <- {r.url} — trying next source")
                    continue
            return "hit", r
        return "no_match", None

    def apply_reviewed_rejects(self, r: SourceResult, rejected):
        """BL-018: drop pages a person rejected for THIS unit. Page-based hit
        (page_texts aligned): rejected pages are cut, the rest is kept. Otherwise
        (e.g. NPTEL: url = course page) a rejected url/page rejects the whole hit.
        Returns the (pruned) result, or None -> the cascade continues."""
        if not rejected:
            return r
        aligned = bool(r.page_texts) and len(r.page_texts) == len(r.page_urls)
        if aligned:
            keep = [(u, t) for u, t in zip(r.page_urls, r.page_texts) if u not in rejected]
            nothing_cut = len(keep) == len(r.page_urls)
            if nothing_cut and r.url not in rejected:
                return r
            if not keep or (nothing_cut and r.url not in r.page_urls):
                # every page rejected, or the hit's own url (e.g. a course page) is rejected
                log.warning(f"    {r.source} hit REJECTED: every page is on the reviewed-rejects list <- {r.url}")
                self.review_rejections += 1
                return None
            cut = [u for u in r.page_urls if u in rejected]
            if cut:
                log.warning(f"    {r.source} pages CUT (reviewed-rejects list): {cut}")
            r.page_urls, r.page_texts = [u for u, _ in keep], [t for _, t in keep]
            r.text = "\n\n".join(r.page_texts)
            if r.url not in r.page_urls:
                r.url = r.page_urls[0]
            return r
        if r.url in rejected or any(u in rejected for u in r.page_urls):
            log.warning(f"    {r.source} hit REJECTED: on the reviewed-rejects list <- {r.url}")
            self.review_rejections += 1
            return None
        return r

    def write_pending(self, code, unit_key, r: SourceResult):
        """BL-018: park a lower-source hit for review; subjects.json untouched."""
        fname = unit_file_name(unit_key)
        fpath = self.cfg.output_dir / PENDING_DIR / code / fname
        atomic_write_text(fpath, r.text)
        entry = {"code": code, "unit": unit_key, "source": r.source, "url": r.url, "title": r.title,
                 "page_urls": list(r.page_urls), "license": r.license, "monetizable": r.monetizable,
                 "access_date": date.today().isoformat(), "word_count": len(r.text.split()),
                 "content_sha256": _sha256(r.text), "gate_coverage_pct": r.gate_coverage_pct,
                 "pages_cut_by_gate": list(r.pages_dropped), "file": f"{PENDING_DIR}/{code}/{fname}",
                 "pending_since": date.today().isoformat()}
        self.pending[f"{code}:{unit_key}"] = entry
        atomic_write_json(self.cfg.output_dir / PENDING_DIR / PENDING_REGISTRY,
                          {k: self.pending[k] for k in sorted(self.pending)})
        return entry

    def page_gate(self, r: SourceResult, topics):
        """Per-page write gate (v2.1) for page-based sources (OpenStax, MIT
        OCW): every page must on its own FOUND more than the threshold of the
        unit's sub-topics, or it is cut before anything is written. The
        unit-level gate alone can't catch a junk page riding along with a
        good one (live case: CY3151 Unit III 'Transition Metals' (0/14) was
        written because 'Phase Diagrams' made the unit as a whole pass).
        NPTEL lecture transcripts are NOT page-gated (a single lecture rarely
        states a syllabus phrase verbatim; unvalidated) — they get the
        unit-level gate only. Returns the pruned result, or None if every
        page was cut."""
        if self.cfg.min_write_coverage_pct is None or not r.page_texts \
                or len(r.page_texts) != len(r.page_urls):
            return r
        kept_u, kept_t = [], []
        for url, text in zip(r.page_urls, r.page_texts):
            g = self.topic_gate(text, topics)
            if g is not None and g[0] <= self.cfg.min_write_coverage_pct:
                log.warning(f"    {r.source} page CUT by write gate: {g[1]}/{g[2]} sub-topics found <- {url}")
                r.pages_dropped.append(url)
                continue
            kept_u.append(url)
            kept_t.append(text)
        if not kept_u:
            log.warning(f"    {r.source} hit REJECTED by write gate: no page covers any sub-topic <- {r.url}")
            return None
        if r.pages_dropped:
            r.page_urls, r.page_texts = kept_u, kept_t
            r.text = "\n\n".join(kept_t)
            if r.url not in kept_u:
                r.url = kept_u[0]
        return r

    def topic_gate(self, text, topics):
        """(coverage_pct, found, total) from the SAME coverage_checker logic
        that audits the batch afterwards, or None if the gate is off / the
        unit has no checkable sub-topics."""
        if self.cfg.min_write_coverage_pct is None:
            return None
        cc = _coverage_checker()
        subtopics = cc.scoring_subtopics_from_lines(topics)   # BL-002: no filler, no bare list items
        if not subtopics:
            return None
        res = cc.check_unit(text, subtopics)
        return res["coverage_pct"], res["topics_found"], res["topics_total"]

    # ---- per subject ---------------------------------------------------------
    def process_subject(self, code, canonical, all_dicts) -> SubjectOutcome:
        out = SubjectOutcome(code)
        bump = lambda k: out.counts.__setitem__(k, out.counts.get(k, 0) + 1)  # noqa: E731
        name = canonical.get("name", code)
        units_map = canonical.get("units") or {}
        topics_map = canonical.get("topics") or {}
        if not units_map or not topics_map:
            log.info(f"    {code}: no parsed syllabus units/topics yet — skipping")
            bump("no_topics")
            return out

        if is_unusable_subject_name(name):
            log.warning(f"    {code}: subject name {name!r} is a parse error (category code, not a title) "
                        f"— skipping; fix it in subjects.json / parse_syllabus.py")
            bump("bad_name")
            self.write_registry(code, canonical)
            return out

        subject_dir = self.cfg.output_dir / code
        existing = canonical.get("sources") or {}
        suspect = invalid_existing_units(existing)
        known_bad = set(suspect)          # BL-018: old content that must not stay live
        if suspect:
            log.warning(f"    {code}: units {sorted(suspect)} share one URL across units "
                        f"(old coarse-match bug) — re-sourcing them")
        redo = {u for c, u in self.cfg.redo_units if c == code and u in (units_map or {})}
        if redo:
            log.warning(f"    {code}: units {sorted(redo)} re-sourced on request (--redo-unit)")
            suspect |= redo
        failing = self.existing_files_failing_gate(code, canonical, existing, subject_dir) - suspect
        if failing:
            log.warning(f"    {code}: units {sorted(failing)} were written before the write gate and "
                        f"cover 0 syllabus sub-topics — re-sourcing them (removed if nothing passes)")
            suspect |= failing
            known_bad |= failing

        for unit_key in units_map.keys():
            fpath = subject_dir / unit_file_name(unit_key)
            has_entry = unit_key in (canonical.get("sources") or {})
            pkey = f"{code}:{unit_key}"
            if pkey in self.pending and not self.cfg.force and unit_key not in redo:
                log.info(f"    Unit {unit_key}: waiting for review in {PENDING_DIR}/ — skipping "
                         f"(review_pending.py approve/reject {pkey})")
                bump("skipped_pending_review")
                continue
            if has_entry and not self.cfg.force and unit_key not in suspect:
                if fpath.exists():
                    log.info(f"    Unit {unit_key}: already sourced, skipping (use --force to redo)")
                    bump("skipped_already_sourced")
                    continue
                log.warning(f"    Unit {unit_key}: sources entry exists but {fpath.name} is missing — re-sourcing")

            def reject(r, _uk=unit_key):
                h = _sha256(r.text)
                pages = sorted(r.page_urls or [r.url])
                for other, e in (canonical.get("sources") or {}).items():
                    if other == _uk or not isinstance(e, dict) or other in suspect:
                        continue
                    if e.get("content_sha256") == h:
                        return f"identical text to Unit {other}"
                    if e.get("source") == r.source and sorted(e.get("page_urls") or [e.get("url")]) == pages:
                        return f"same page set as Unit {other}"
                for pk, e in self.pending.items():          # BL-018: siblings waiting for review
                    pc, _, pu = pk.partition(":")
                    if pc != code or pu == _uk:
                        continue
                    if e.get("content_sha256") == h:
                        return f"identical text to Unit {pu} (pending review)"
                    if e.get("source") == r.source and sorted(e.get("page_urls") or [e.get("url")]) == pages:
                        return f"same page set as Unit {pu} (pending review)"
                return None

            rej_before = self.review_rejections
            status, r = self.source_unit(name, topics_map.get(unit_key, []), reject=reject,
                                         rejected_urls=frozenset(self.cfg.rejects.get((code, unit_key), ())),
                                         nptel_course=self.cfg.course_choices.get(
                                             (code, unit_key), self.cfg.course_choices.get((code, "*"))))
            if self.review_rejections > rej_before:
                bump("rejected_by_review")

            if status == "awaiting_nptel_book":
                book = dict(r)
                rec = self.books_needed.setdefault(book["course_id"], {
                    k: v for k, v in book.items() if k != "week_title"})
                rec.setdefault("needed_by", [])
                need = {"subject": code, "unit": unit_key, "week": book.get("week_title")}
                if need not in rec["needed_by"]:
                    rec["needed_by"].append(need)
                bump("awaiting_nptel_book")
                if unit_key in suspect:
                    # Known-bad old content (shared URL / fails the gate):
                    # never keep it while we wait for the book.
                    if fpath.exists():
                        fpath.unlink()
                        log.info(f"    Unit {unit_key}: removed known-bad {fpath.name} while waiting for the NPTEL book")
                    for d in all_dicts:
                        (d.get("sources") or {}).pop(unit_key, None)
                    suspect.discard(unit_key)
                log.warning(f"    Unit {unit_key}: WAITING FOR NPTEL BOOK — course {book['course_id']} "
                            f"({book.get('course_title')}); left untouched, added to nptel_books_needed.json")
                continue

            if status != "degraded":
                suspect.discard(unit_key)   # its entry is now fresh (or gone)

            if status == "degraded":
                log.warning(f"    Unit {unit_key}: DEGRADED (network/source failure) — left untouched, will retry")
                bump("degraded")
                out.retry = True
                continue

            if status == "no_match":
                log.info(f"    Unit {unit_key}: NO MATCH")
                bump("no_match")
                # A previous (now invalid) match must not linger on disk or in
                # subjects.json — it would also make future runs skip the unit.
                if has_entry:
                    if fpath.exists():
                        fpath.unlink()
                        log.info(f"    Unit {unit_key}: removed stale {fpath.name} (no longer matches)")
                    for d in all_dicts:
                        (d.get("sources") or {}).pop(unit_key, None)
                continue

            if self.cfg.review_lower_sources and r.source in LOWER_SOURCES:
                self.write_pending(code, unit_key, r)
                if unit_key in known_bad and has_entry:
                    # known-bad old content (shared URL / 0 FOUND) never stays live while waiting
                    if fpath.exists():
                        fpath.unlink()
                        log.info(f"    Unit {unit_key}: removed known-bad {fpath.name} while waiting for review")
                    for d in all_dicts:
                        (d.get("sources") or {}).pop(unit_key, None)
                bump("pending_review")
                log.warning(f"    Unit {unit_key}: {r.source} hit WAITING FOR REVIEW ({len(r.text.split())} words, "
                            f"gate {r.gate_coverage_pct}%) -> {PENDING_DIR}/{code}/{unit_file_name(unit_key)} <- {r.url}")
                continue

            word_count = len(r.text.split())
            atomic_write_text(fpath, r.text)
            entry = {
                "source": r.source,
                "url": r.url,
                "title": r.title,
                "page_urls": list(r.page_urls),
                "license": r.license,
                "monetizable": r.monetizable,
                "access_date": date.today().isoformat(),
                "word_count": word_count,
                "content_sha256": _sha256(r.text),
                "gate_coverage_pct": r.gate_coverage_pct,
                "pages_cut_by_gate": list(r.pages_dropped),
                "file": f"{code}/{fpath.name}",
            }
            # Scrape once per code; identical entry in every dept's copy.
            for d in all_dicts:
                d.setdefault("sources", {})[unit_key] = dict(entry)
            bump(r.source)
            log.info(f"    Unit {unit_key}: {r.source} ({word_count} words) <- {r.url}")

        self.write_registry(code, canonical)
        return out

    def existing_files_failing_gate(self, code, canonical, existing, subject_dir):
        """v2.1.1: files written by earlier versions (no write gate) are
        re-checked locally with the same gate; failing units are healed.
        Local only — no network."""
        if self.cfg.min_write_coverage_pct is None:
            return set()
        topics_map = canonical.get("topics") or {}
        bad = set()
        for unit in existing:
            f = subject_dir / unit_file_name(unit)
            if not f.exists():
                continue
            g = self.topic_gate(f.read_text(encoding="utf-8"), clean_unit_topics(topics_map.get(unit, [])))
            if g is not None and g[0] <= self.cfg.min_write_coverage_pct:
                bad.add(unit)
        return bad

    # ---- registry ------------------------------------------------------------
    def write_registry(self, code, canonical):
        """Rebuilt from the canonical subjects.json["sources"] (which
        accumulates across runs) — never from this run's local list."""
        sources = canonical.get("sources") or {}
        units_order = list((canonical.get("units") or {}).keys())
        ordered = [u for u in units_order if u in sources] + [u for u in sources if u not in units_order]
        registry = [{"unit": u, **{k: v for k, v in sources[u].items()}} for u in ordered]
        subject_dir = self.cfg.output_dir / code
        reg_path = subject_dir / "sources_registry.json"
        if registry or reg_path.exists():
            atomic_write_json(reg_path, registry)


# ===========================================================================
# 8. BATCH RUNNER
# ===========================================================================

def setup_logging(output_dir: Path, level=logging.INFO):
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "scraper.log"
    log.setLevel(level)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    try:
        sys.stdout.reconfigure(errors="replace")   # Windows cp1252 consoles
    except Exception:
        pass
    log.addHandler(fh)
    log.addHandler(sh)
    return log_path


def preflight(need_nptel=True, books_dir=None):
    problems = []
    if books_dir is not None and Path(books_dir).is_dir() and any(Path(books_dir).glob("*.pdf")):
        try:
            import pypdf  # noqa: F401
        except ImportError:
            problems.append("pypdf is not installed — downloaded NPTEL books can't be read. Fix: pip install pypdf")
    if need_nptel:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            problems.append("youtube_transcript_api is not installed — NPTEL (the #1 source) would be "
                            "silently disabled. Fix: pip install youtube-transcript-api")
        else:
            if not hasattr(YouTubeTranscriptApi, "fetch"):
                problems.append("youtube_transcript_api is too old (no .fetch(); need >= 1.0). "
                                "Fix: pip install -U youtube-transcript-api")
    return problems


def resolve_scope(data, department=None, subject_codes=None, codes_file=None):
    groups = iter_subject_groups(data, department)
    if codes_file:
        wanted = []
        for line in Path(codes_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0]            # everything after '#' is a comment
            wanted += [c.strip().upper() for c in line.replace(",", " ").split() if c.strip()]
        wanted = list(dict.fromkeys(wanted))
        present = {g[0] for g in groups}
        order = {c: i for i, c in enumerate(wanted)}
        groups = sorted([g for g in groups if g[0] in order], key=lambda g: order[g[0]])
        return groups, f"file:{Path(codes_file).name}", [c for c in wanted if c not in present]
    if subject_codes:
        wanted = [c.strip().upper() for c in subject_codes if c.strip()]
        present = {g[0] for g in groups}
        missing = [c for c in wanted if c not in present]
        groups = [g for g in groups if g[0] in wanted]
        return groups, "codes:" + ",".join(wanted), missing
    return groups, (f"dept:{department}" if department else "all"), []


def plan_batch(groups, scope, checkpoint: Checkpoint, start, batch_size, use_checkpoint):
    """Returns (batch_groups, start_index, end_index, retry_codes)."""
    if not use_checkpoint:
        return groups, 0, len(groups), []
    ck_next, ck_failed = checkpoint.get(scope)
    retry_codes = []
    if start is None:
        start = ck_next
        retry_codes = [c for c in ck_failed if any(g[0] == c for g in groups)]
    start = max(0, min(start, len(groups)))
    end = min(start + batch_size, len(groups)) if batch_size else len(groups)
    retry = [g for g in groups if g[0] in retry_codes and not (start <= groups.index(g) < end)]
    return retry + groups[start:end], start, end, retry_codes


class _NoNetwork(Http):
    def get(self, *a, **k):
        raise RuntimeError("network used during dry run")


def dry_run_report(groups, cfg: Config, batch):
    rows, totals = [], {"subjects": 0, "units": 0, "to_source": 0, "skip": 0, "heal": 0, "no_topics": 0}
    pending = load_pending(cfg.output_dir)
    for code, canonical, all_dicts, depts in batch:
        units = list((canonical.get("units") or {}).keys())
        topics = canonical.get("topics") or {}
        srcs = canonical.get("sources") or {}
        suspect = invalid_existing_units(srcs) | {u for c, u in cfg.redo_units if c == code}
        totals["subjects"] += 1
        if not units or not topics:
            totals["no_topics"] += 1
            rows.append((code, 0, 0, 0, 0, "no topics"))
            continue
        if is_unusable_subject_name(canonical.get("name", "")):
            totals["bad_name"] = totals.get("bad_name", 0) + 1
            rows.append((code, len(units), 0, 0, 0, f"BAD NAME {canonical.get('name', '')!r} — skipped"))
            continue
        skip = heal = 0
        failing = Pipeline(cfg, http=_NoNetwork(), sources={}).existing_files_failing_gate(
            code, canonical, srcs, cfg.output_dir / code) if srcs else set()
        for u in units:
            f = cfg.output_dir / code / unit_file_name(u)
            if f"{code}:{u}" in pending and not cfg.force and (code, u) not in cfg.redo_units:
                skip += 1                       # BL-018: waiting for review
                continue
            if u in srcs and not cfg.force and u not in suspect and u not in failing and f.exists():
                skip += 1
            elif u in srcs:
                heal += 1
        todo = len(units) - skip
        totals["units"] += len(units)
        totals["to_source"] += todo
        totals["skip"] += skip
        totals["heal"] += heal
        rows.append((code, len(units), todo, skip, heal, clean_subject_name(canonical.get("name", ""))[:45]))
    return rows, totals


def run(subjects_json, output_dir=None, start=None, batch_size=50, force=False,
        department=None, subject_codes=None, dry_run=False, run_coverage=True, codes_file=None,
        coverage_dir=None, http=None, sources=None, log_level=logging.INFO,
        min_write_coverage_pct=MIN_WRITE_COVERAGE_PCT, allow_youtube=False, redo_units=None,
        review_lower_sources=True):
    """Returns a summary dict (also written to <output_dir>/last_run_summary.json)."""
    subjects_json = Path(subjects_json).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else subjects_json.parent / "sources"
    coverage_dir = Path(coverage_dir).resolve() if coverage_dir else subjects_json.parent / "coverage_reports"
    cfg = Config(subjects_json, output_dir, coverage_dir, force=force,
                 min_write_coverage_pct=min_write_coverage_pct, allow_youtube=allow_youtube,
                 redo_units=frozenset(redo_units or ()), review_lower_sources=review_lower_sources,
                 rejects=load_reviewed_rejects(output_dir), course_choices=load_course_choices(output_dir))

    if dry_run:
        # No log file, no writes, no network.
        log.handlers.clear()
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    else:
        setup_logging(output_dir, log_level)

    data = json.loads(subjects_json.read_text(encoding="utf-8"))
    if department is not None and department not in list_departments(data):
        raise SystemExit(f"Unknown department {department!r}. Known: {list_departments(data)}")
    groups, scope, missing = resolve_scope(data, department, subject_codes, codes_file)
    if missing:
        log.warning(f"Subject code(s) not found in subjects.json: {missing}")
    use_ckpt = not subject_codes
    ckpt = Checkpoint(output_dir / "checkpoint.json")
    batch, s_idx, e_idx, retry_codes = plan_batch(groups, scope, ckpt, start, batch_size, use_ckpt)

    if dry_run:
        rows, totals = dry_run_report(groups, cfg, batch)
        log.info(f"DRY RUN — scope '{scope}': {len(groups)} unique subjects in scope; "
                 f"this batch = indices [{s_idx}:{e_idx}] + {len(retry_codes)} retries = {len(batch)} subjects")
        log.info(f"{'Code':<9}{'Units':>6}{'ToDo':>6}{'Skip':>6}{'Heal':>6}  Name")
        for code, n, todo, skip, heal, nm in rows:
            log.info(f"{code:<9}{n:>6}{todo:>6}{skip:>6}{heal:>6}  {nm}")
        log.info(f"TOTAL: {totals['subjects']} subjects, {totals['units']} units — "
                 f"{totals['to_source']} to source ({totals['heal']} re-source/heal), "
                 f"{totals['skip']} skip, {totals['no_topics']} subjects without topics, "
                 f"{totals.get('bad_name', 0)} subjects with parse-error names (skipped)")
        log.info(f"Rough time estimate: ~{round(totals['to_source'] * 30 / 60)} min at ~30 s/unit")
        return {"dry_run": True, "scope": scope, "batch_codes": [g[0] for g in batch], **totals}

    problems = preflight(need_nptel=allow_youtube and "nptel" in (sources or {"nptel": 1}),
                         books_dir=output_dir / NPTEL_BOOKS_DIR)
    if problems and sources is None:
        for p in problems:
            log.error(p)
        raise SystemExit(2)

    # One backup per run before the first write.
    backup = subjects_json.with_name(subjects_json.name + ".bak")
    shutil.copy2(subjects_json, backup)
    loaded_mtime = subjects_json.stat().st_mtime_ns

    pipeline = Pipeline(cfg, http=http, sources=sources)
    gate_desc = "off" if min_write_coverage_pct is None else f">{min_write_coverage_pct}% sub-topics found"
    log.info(f"=== source_scraper v{__version__} | scope '{scope}' | {len(batch)} subjects "
             f"(indices [{s_idx}:{e_idx}] of {len(groups)}, {len(retry_codes)} retries) | force={force} "
             f"| write gate {gate_desc} | YouTube transcripts {'ON (opt-in)' if allow_youtube else 'off (robots.txt)'} "
             f"| lower-source review {'ON' if review_lower_sources else 'OFF'} "
             f"| reviewed rejects: {sum(len(v) for v in cfg.rejects.values())} pages "
             f"| course choices: {len(cfg.course_choices)}")

    totals = {k: 0 for k in ("nptel", "mit_ocw", "openstax", "no_match", "skipped_already_sourced",
                             "no_topics", "degraded", "awaiting_nptel_book", "bad_name",
                             "pending_review", "skipped_pending_review", "rejected_by_review")}
    failed, zero_match, processed, bad_names = [], [], [], []
    prev_failed = set(ckpt.get(scope)[1]) if use_ckpt else set()
    next_index = s_idx

    for n, (code, canonical, all_dicts, _depts) in enumerate(batch, 1):
        log.info(f"[{n}/{len(batch)}] {code} — {canonical.get('name', code)}")
        try:
            outcome = pipeline.process_subject(code, canonical, all_dicts)
            for k, v in outcome.counts.items():
                totals[k] = totals.get(k, 0) + v
            if outcome.retry:
                failed.append(code)
            else:
                prev_failed.discard(code)
            if not (canonical.get("sources") or {}) and outcome.counts.get("no_match"):
                zero_match.append(code)
            if outcome.counts.get("bad_name"):
                bad_names.append(code)
            processed.append(code)
        except KeyboardInterrupt:
            log.warning("Interrupted — saving state")
            _persist(subjects_json, data, loaded_mtime)
            raise
        except Exception as e:
            log.exception(f"    {code}: UNEXPECTED ERROR {type(e).__name__}: {e} — skipping subject, batch continues")
            failed.append(code)
        # Crash-safe: persist after EVERY subject.
        loaded_mtime = _persist(subjects_json, data, loaded_mtime)
        idx = next((i for i, g in enumerate(groups) if g[0] == code), None)
        if idx is not None and s_idx <= idx < e_idx:
            next_index = idx + 1
        if use_ckpt:
            ckpt.save(scope, next_index, (prev_failed | set(failed)))
        if pipeline.books_needed:
            save_books_needed(output_dir, pipeline.books_needed)

    log.info("--- Batch summary ---")
    for k, v in totals.items():
        log.info(f"{k}: {v}")
    if failed:
        log.warning(f"Subjects queued for retry ({len(failed)}): {failed}")
    if bad_names:
        log.warning(f"Subjects skipped for unusable (parse-error) names ({len(bad_names)}): {bad_names}")
    if pipeline.books_needed:
        n_units = sum(len(b["needed_by"]) for b in pipeline.books_needed.values())
        log.warning(f"NPTEL books to download by hand ({len(pipeline.books_needed)} courses, {n_units} units "
                    f"waiting) — see {output_dir / 'nptel_books_needed.json'}")
        for b in pipeline.books_needed.values():
            log.warning(f"    {b['course_id']}  {b.get('course_title')}  {b['book_url']}  -> save as {b['save_as']}")
    if pipeline.pending:
        log.warning(f"Units WAITING FOR REVIEW ({len(pipeline.pending)}): {sorted(pipeline.pending)} — "
                    f"python review_pending.py --subjects-json <path> list")
    if zero_match:
        log.info(f"Zero-match subjects this batch ({len(zero_match)}): {zero_match}")
        atomic_write_json(output_dir / "zero_match_subjects.json", zero_match)
    if use_ckpt and next_index >= len(groups):
        log.info(f"Scope '{scope}' fully traversed.")

    coverage = None
    if run_coverage and processed:
        coverage = run_coverage_audit(subjects_json, output_dir, coverage_dir, processed)

    summary = {"scope": scope, "version": __version__, "finished": datetime.now().isoformat(timespec="seconds"),
               "subjects_processed": processed, "failed": failed, "zero_match": zero_match,
               "bad_names": bad_names,
               "nptel_books_needed": sorted(pipeline.books_needed),
               "pending_review": sorted(pipeline.pending),
               "totals": totals, "coverage": coverage}
    atomic_write_json(output_dir / "last_run_summary.json", summary)
    return summary


def save_books_needed(output_dir: Path, new: dict):
    """Accumulating download list (merged with what earlier runs recorded).
    A person downloads each book once from its Google Drive link and saves it
    as <output_dir>/_nptel_books/<course_id>.pdf."""
    path = output_dir / "nptel_books_needed.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        old = {}
    merged = dict(old)
    for cid, rec in merged.items():                # refresh every flag, not just this run's
        rec["downloaded"] = (output_dir / NPTEL_BOOKS_DIR / f"{cid}.pdf").exists()
    for cid, b in new.items():
        rec = dict(merged.get(cid) or {})
        needed = list(rec.get("needed_by") or [])
        for k, v in b.items():
            if k != "needed_by":
                rec[k] = v
        for n in b.get("needed_by", []):
            if n not in needed:
                needed.append(n)
        rec["needed_by"] = needed
        rec["save_as"] = f"{NPTEL_BOOKS_DIR}/{cid}.pdf"
        rec["downloaded"] = (output_dir / NPTEL_BOOKS_DIR / f"{cid}.pdf").exists()
        b["save_as"] = rec["save_as"]
        merged[cid] = rec
    atomic_write_json(path, dict(sorted(merged.items())))
    return merged


def _persist(subjects_json: Path, data, loaded_mtime):
    """Atomic write, refusing to clobber an external edit made mid-run."""
    now = subjects_json.stat().st_mtime_ns
    if now != loaded_mtime:
        raise RuntimeError(f"{subjects_json} was modified by another process during the run — "
                           f"refusing to overwrite it (backup at {subjects_json.name}.bak)")
    atomic_write_json(subjects_json, data)
    return subjects_json.stat().st_mtime_ns


def _coverage_checker():
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import coverage_checker
    return coverage_checker


def run_coverage_audit(subjects_json, sources_dir, coverage_dir, codes):
    """Runs coverage_checker for exactly the subjects this batch touched and
    logs a compact per-unit table + aggregate numbers."""
    coverage_checker = _coverage_checker()
    rows = coverage_checker.run(subjects_json, sources_dir, coverage_dir, subject_codes=codes, quiet=True)
    log.info("--- Coverage audit (coverage_checker, automatic) ---")
    log.info(f"{'Subject':<10}{'Unit':<6}{'Coverage':>9}{'Thin':>6}  Status")
    for r in rows:
        log.info(f"{r['code']:<10}{r['unit']:<6}{r['coverage_pct']:>8}%{str(r['thin_topics_flagged']):>6}  {r['status']}")
    with_src = [r for r in rows if r["status"] == "checked"]
    agg = {
        "units_total": len(rows),
        "units_with_source": len(with_src),
        "mean_coverage_sourced_units": round(sum(r["coverage_pct"] for r in with_src) / len(with_src), 1) if with_src else 0.0,
        "mean_coverage_all_units": round(sum(r["coverage_pct"] for r in rows) / len(rows), 1) if rows else 0.0,
        "units_100pct": sum(1 for r in with_src if r["coverage_pct"] >= 100),
    }
    log.info(f"Coverage: {agg['units_with_source']}/{agg['units_total']} units have a source; "
             f"mean coverage {agg['mean_coverage_sourced_units']}% (sourced units), "
             f"{agg['mean_coverage_all_units']}% (all units); {agg['units_100pct']} units at 100%")
    return {"aggregate": agg, "rows": rows}


def build_arg_parser():
    p = argparse.ArgumentParser(description="AcademicsGen source scraper (NPTEL -> MIT OCW -> OpenStax)")
    p.add_argument("--subjects-json", required=True)
    p.add_argument("--output-dir", default=None, help="default: <subjects.json dir>/sources")
    p.add_argument("--coverage-dir", default=None, help="default: <subjects.json dir>/coverage_reports")
    p.add_argument("--start", type=int, default=None, help="omit to auto-resume from checkpoint")
    p.add_argument("--batch-size", type=int, default=50, help="0 = whole scope")
    p.add_argument("--force", action="store_true", help="re-scrape units that already have a source")
    p.add_argument("--subject-code", action="append", default=None,
                   help="target specific subject(s); repeat or comma-separate (bypasses checkpoint)")
    p.add_argument("--department", default=None, help='limit to one department key, e.g. "AI&DS"')
    p.add_argument("--codes-file", default=None,
                   help="text file of subject codes (one per line / comma-separated, # comments); "
                        "resumable via checkpoint, unlike --subject-code")
    p.add_argument("--dry-run", action="store_true", help="preview scope (no network, no writes)")
    p.add_argument("--no-coverage", action="store_true", help="skip the automatic coverage audit")
    p.add_argument("--min-write-coverage", type=float, default=MIN_WRITE_COVERAGE_PCT,
                   help="write gate: a hit is written only if MORE than this %% of the unit's sub-topics "
                        "are FOUND in it (default 0 = at least one)")
    p.add_argument("--no-write-gate", action="store_true", help="disable the pre-write topic gate (not recommended)")
    p.add_argument("--allow-youtube-transcripts", action="store_true",
                   help="fetch NPTEL lecture transcripts from YouTube (NOT recommended: youtube.com "
                        "robots.txt disallows the endpoints used). Default: off; NPTEL units wait for "
                        "the official NPTEL book instead")
    p.add_argument("--redo-unit", action="append", default=None, metavar="CODE:UNIT",
                   help="re-source one unit even if it already has a source, e.g. GE3151:IV "
                        "(repeatable / comma-separated). If nothing passes, its old file is removed")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    codes = None
    if args.subject_code:
        codes = [c for chunk in args.subject_code for c in chunk.split(",")]
    run(args.subjects_json, args.output_dir, args.start, args.batch_size, args.force,
        department=args.department, subject_codes=codes, dry_run=args.dry_run, codes_file=args.codes_file,
        run_coverage=not args.no_coverage, coverage_dir=args.coverage_dir,
        log_level=logging.DEBUG if args.verbose else logging.INFO,
        min_write_coverage_pct=None if args.no_write_gate else args.min_write_coverage,
        allow_youtube=args.allow_youtube_transcripts, redo_units=parse_redo_units(args.redo_unit))


def parse_redo_units(values):
    out = set()
    for chunk in values or []:
        for item in chunk.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise SystemExit(f"--redo-unit expects CODE:UNIT (e.g. GE3151:IV), got {item!r}")
            code, unit = item.split(":", 1)
            out.add((code.strip().upper(), unit.strip().upper()))
    return out


if __name__ == "__main__":
    main()
