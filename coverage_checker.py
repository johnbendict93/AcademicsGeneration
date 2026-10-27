"""
coverage_checker.py — AcademicsGen automated source-completeness auditor

Replaces manual file-by-file eyeballing with a repeatable, scriptable check.
Answers two questions per unit, automatically, for all 829 subjects:

  1. TOPIC COVERAGE: does the scraped source actually contain every syllabus
     topic for this unit, or are some silently missing?
  2. DEPTH SUFFICIENCY (heuristic, not a guarantee — see caveat below): does
     each covered topic have enough explanatory depth to support a 13-mark
     answer, or does it only support a 2-mark definition-level answer?

Nothing hardcoded: topic lists come from subjects.json, source text comes
from the sources/<code>/Unit<N>.txt files the scraper already writes.

HONEST LIMITATION: depth sufficiency is a heuristic (word count + presence
of examples/derivations near the topic), not a semantic judgment of whether
the content is *actually* good enough for a 13-mark answer. Treat MISSING
and 2-MARK-ONLY flags as "needs human review," not as ground truth on their
own — this replaces blind trust in scraper output, not human judgment
entirely.

Usage:
    python coverage_checker.py --subjects-json subjects.json \
        --sources-dir sources --output-dir coverage_reports

    # single subject (or several, comma-separated), for spot-checking
    python coverage_checker.py --subjects-json subjects.json \
        --sources-dir sources --subject-code MA3151

NOTE: source_scraper.py (v2+) runs this automatically at the end of every
scrape batch for exactly the subjects that batch touched. Running it by hand
is only needed for ad-hoc re-audits.

Outputs (in --output-dir):
  <CODE>_coverage.json   per-subject, per-unit, per-topic detail
  batch_summary.json     rows for the subjects checked in THIS invocation
  overall_summary.json   rebuilt from every <CODE>_coverage.json on disk, so
                         it accumulates across runs instead of being
                         overwritten by the latest subset
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import functools

import source_scraper as _scraper
try:
    # BL-017 (2026-09-26): standard Snowball (Porter2) English stemmer so word
    # FORMS match ("evaluating"/"evaluation", "detecting"/"detection"). Pure
    # Python, BSD licence, free; ships with Anaconda (Sphinx dependency).
    import snowballstemmer as _snowball
except ImportError:                                   # pragma: no cover
    raise ImportError("coverage_checker needs the 'snowballstemmer' package: "
                      "python -m pip install snowballstemmer")
_STEMMER = _snowball.stemmer("english")  # reuse the scraper's own topic-cleaning/splitting

ROMAN_TO_INT = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
                "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}

STOPWORDS = {
    "a", "an", "the", "of", "and", "or", "in", "on", "for", "to", "with",
    "by", "at", "is", "are", "as", "using", "use", "their", "its",
}

# BL-002 (2026-09-23): structural/generic academic words. A syllabus fragment
# made only of these ("Introduction", "Types", "definition and examples.",
# "Steps", "Applications") names no concept, yet it scored FOUND on almost any
# page — which let junk files pass the write gate (live cases: GE3792 matched
# on "Steps"/"Types"/"Style"/"Levels", MX3089 on "Iron"/"water"). Generic
# vocabulary only — nothing subject-specific. Compared AFTER normalize().
GENERIC_TERMS = {
    "introduction", "overview", "definition", "application", "type", "classification",
    "example", "basic", "concept", "principle", "feature", "characteristic",
    "advantage", "disadvantage", "merit", "demerit", "limitation", "benefit",
    "need", "importance", "significance", "scope", "nature", "objective",
    "method", "methodology", "technique", "step", "procedure", "process",
    "function", "role", "level", "style", "factor", "issue", "aspect",
    "general", "various", "different", "simple", "related", "etc", "case",
    "study", "studie", "illustration", "illustrative", "problem", "exercise",
}

EXAMPLE_MARKERS = {"example", "solution", "exercise", "problem", "checkpoint"}
DEPTH_MARKERS = {"theorem", "proof", "derive", "derivation", "prove"}

DEPTH_WINDOW_WORDS = 800     # how far past a topic's first match to look for its content
THIN_THRESHOLD_WORDS = 150   # below this, only a 2-mark answer is realistic
MATCH_RATIO_FOUND = 0.8
MATCH_RATIO_PARTIAL = 0.4


def normalize(word: str) -> str:
    # Delegates to the scraper's own plural/derivation/British-American
    # normalization rather than keeping a second, independently-maintained
    # copy — this script had its own bare "singular vs plural" rule with
    # its own exception list, which didn't include "physics"/"mathematics"/
    # "statistics"/etc. and would have wrongly stripped "physics" ->
    # "physic". Confirmed real case for why this must be shared, not
    # duplicated: this checker is what actually surfaced the "centre" vs
    # "center" gap (AU syllabi are British English, OpenStax/MIT/NPTEL are
    # American) — fixing that in the scraper's matcher alone did nothing
    # for THIS script's own reporting, because it never called the
    # scraper's normalization at all until now.
    return _normalize_cached(word.lower().strip(".,;:()[]"))


@functools.lru_cache(maxsize=200_000)
def _normalize_cached(word: str) -> str:
    # BL-017: stem AFTER the shared singular / British->American step.
    return _STEMMER.stemWord(_scraper._british_to_american(_scraper._singularize(word)))


def key_terms(phrase: str):
    words = re.findall(r"[a-zA-Z']+", phrase)
    return [normalize(w) for w in words if w.lower() not in STOPWORDS and len(w) > 2]


def _unstemmed(word: str) -> str:
    """Singular / American form WITHOUT stemming (BL-002 filler decisions)."""
    w = word.lower().strip(".,;:()[]")
    return _scraper._british_to_american(_scraper._singularize(w))


def distinctive_terms(phrase: str):
    # Filler is decided on UNSTEMMED words: stemming merges "limitation"
    # (generic) with "limit" (a real concept), which would have dropped
    # "Limit of a function" as filler (BL-017 regression, caught by tests).
    words = re.findall(r"[a-zA-Z']+", phrase)
    return [_unstemmed(w) for w in words
            if w.lower() not in STOPWORDS and len(w) > 2 and _unstemmed(w) not in GENERIC_TERMS]


# Marker lists are plain words; compare them in the text's stemmed form (BL-017).
_EXAMPLE_STEMS = frozenset(normalize(w) for w in EXAMPLE_MARKERS)
_DEPTH_STEMS = frozenset(normalize(w) for w in DEPTH_MARKERS)


# Dash / semicolon = separate concepts.
# BL-010 (2026-09-25): many syllabi write the dash with a space on ONE side only
# ("Hazard- Risk", "Project network -Diagram", "Management –Scientific"), which
# left whole lines as one unscorable sub-topic (MX3089 I/II/V scored 0%). Such a
# dash now also separates concepts, but only when a Capitalised word (or an
# acronym) follows — a word broken at a line end continues in lower case
# ("multi- layer", "self- organizing") and is left alone.
# A hyphen with NO space on either side is NEVER split: it cannot be told apart
# from real compounds (Runge-Kutta, Three-Phase, Neuro-Fuzzy, File-System,
# On-site, Time-weighted, What-If) — measured on all 2,373 units, 54 false splits.
_DASH = r"[-–—\ufffd]"
_CAPITALISED_NEXT = r"(?=[A-Z][a-z]{2,}|[A-Z]{2,}\b)"
_CONCEPT_SEP_RE = re.compile(
    rf"\s+{_DASH}\s+"                                          # " - "  (both sides)
    rf"|(?<=[\w)\].:])\s*{_DASH}\s+{_CAPITALISED_NEXT}"       # "X- Y" (space after)
    rf"|(?<=[\w)\].:])\s+{_DASH}{_CAPITALISED_NEXT}"           # "X -Y" (space before)
    r"|\s*;\s*")
_LIST_SEP_RE = re.compile(r",\s+")                              # comma = items of one list


def _is_filler(phrase):
    return not distinctive_terms(phrase)


def scoring_subtopics(topics):
    """BL-002 safety net for an already-split list: drop pure filler only."""
    return [t for t in topics if not _is_filler(t)]


def scoring_subtopics_from_lines(topic_lines):
    """BL-002: the sub-topics that are actually SCORED (coverage % and the
    scraper's write gate), built from a unit's cleaned syllabus lines.
      - dashes / semicolons separate concepts: a one-word concept
        ("Continuity", "Jacobians") stays its own sub-topic;
      - commas separate items of ONE list: a bare one-word item ("product",
        "Iron", "Magnesium") is merged into its neighbour in that list, so it
        only counts together with its context and never FOUND on its own;
      - fragments with no distinctive term ("Introduction", "Types", "Steps",
        "definition and examples.") are dropped.
    Page SELECTION in the scraper is untouched (validated behaviour)."""
    joined = " ".join(topic_lines or [])
    out = []
    for segment in _CONCEPT_SEP_RE.split(joined):
        items = [p.strip() for p in _LIST_SEP_RE.split(segment) if p.strip()]
        items = [p for p in items if not _is_filler(p)]
        if not items:
            continue
        merged = []
        for it in items:
            if merged and len(key_terms(it)) <= 1:
                merged[-1] = f"{merged[-1]}, {it}"          # list item -> joins previous
            elif merged and len(key_terms(merged[-1])) <= 1 and len(items) > 1:
                merged[-1] = f"{merged[-1]}, {it}"          # leading one-word item joins next
            else:
                merged.append(it)
        out.extend(merged)
    return out


def tokenize(text: str):
    return [normalize(w) for w in re.findall(r"[a-zA-Z']+", text)]


PROXIMITY_WINDOW = 250   # words — terms must co-occur within this span to count as a real topic mention
WINDOW_STEP = 25         # slide step; small enough not to skip a real mention


def best_matching_window(text_tokens, terms):
    """Slide a window across the unit text and find where the most topic
    terms co-occur close together. This is the fix for the false-positive
    bug: matching terms anywhere in a 20,000-word file (regardless of
    distance apart) counted unrelated mentions as topic coverage."""
    term_set = set(terms)
    n = len(text_tokens)
    best_ratio = 0.0
    best_start = None
    best_matched = []

    for start in range(0, max(n - PROXIMITY_WINDOW, 1), WINDOW_STEP):
        window = text_tokens[start:start + PROXIMITY_WINDOW]
        window_set = set(window)
        matched = [t for t in terms if t in window_set]
        ratio = len(matched) / len(terms)
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = start
            best_matched = matched

    return best_ratio, best_start, best_matched


def assess_topic(text_tokens, topic: str):
    terms = key_terms(topic)
    if not terms:
        return {"topic": topic, "status": "SKIPPED (no key terms)", "depth": None}

    ratio, start, matched = best_matching_window(text_tokens, terms)

    if ratio >= MATCH_RATIO_FOUND:
        status = "FOUND"
    elif ratio >= MATCH_RATIO_PARTIAL:
        status = "PARTIAL"
    else:
        status = "MISSING"

    depth = None
    if status in ("FOUND", "PARTIAL") and start is not None:
        window = text_tokens[start:start + DEPTH_WINDOW_WORDS]
        window_set = set(window)
        has_example = bool(window_set & _EXAMPLE_STEMS)
        has_derivation = bool(window_set & _DEPTH_STEMS)
        word_count = len(window)

        if word_count < THIN_THRESHOLD_WORDS:
            depth = "2-MARK ONLY"
        elif has_example or has_derivation:
            depth = "13-MARK READY"
        else:
            depth = "MODERATE (definitions only, no worked examples found)"

    return {
        "topic": topic,
        "status": status,
        "match_ratio": round(ratio, 2),
        "matched_terms": matched,
        "missing_terms": [t for t in terms if t not in matched],
        "depth": depth,
    }


def check_unit(unit_text: str, topics: list):
    topics = scoring_subtopics(topics)      # BL-002 safety net: never score filler
    tokens = tokenize(unit_text)
    results = [assess_topic(tokens, t) for t in topics]
    found = sum(1 for r in results if r["status"] == "FOUND")
    coverage_pct = round(100 * found / len(topics), 1) if topics else 0.0
    thin_flags = sum(1 for r in results if r.get("depth") in ("2-MARK ONLY", "MODERATE (definitions only, no worked examples found)"))
    return {
        "coverage_pct": coverage_pct,
        "topics_found": found,
        "topics_total": len(topics),
        "thin_topics_flagged": thin_flags,
        "topics": results,
    }


def iter_subjects(data):
    """Yield (code, subject_dict) once per unique subject code.

    subjects.json is NOT the flat {"subjects": [...]} list this script
    originally assumed — it's nested (universities -> Anna University ->
    departments -> {dept} -> semesters -> {roman numeral} -> {code}), and
    the SAME code repeats once per department that teaches it. source_text
    is identical across those copies (the scraper writes it once per code
    and copies to every department), so checking one copy per code is
    correct and avoids duplicate report rows."""
    # 2026-09-26: yield the scraper's CANONICAL copy of each code (the one
    # with real topics — _best_copy, BL-001). The first copy is often the
    # curriculum-table copy with no units: AL3501's audit read that one and
    # reported "0 of 0 topics" for four units that had just passed the gate
    # at 25-44%. Same order as before (first-seen).
    for code, canonical, _copies, _depts in _scraper.iter_subject_groups(data):
        yield code, canonical


def _summary_rows(subject_report):
    rows = []
    for unit_key, r in subject_report["units"].items():
        rows.append({
            "code": subject_report["code"],
            "unit": unit_key,
            "coverage_pct": r.get("coverage_pct", 0),
            "thin_topics_flagged": r.get("thin_topics_flagged", "N/A"),
            "status": r.get("status", "checked"),
        })
    return rows


def rebuild_overall_summary(output_dir: Path):
    """Accumulating view: every subject report on disk, not just this run's."""
    rows = []
    for f in sorted(Path(output_dir).glob("*_coverage.json")):
        try:
            rows.extend(_summary_rows(json.loads(f.read_text(encoding="utf-8"))))
        except (ValueError, KeyError):
            continue
    _scraper.atomic_write_json(Path(output_dir) / "overall_summary.json", rows)
    return rows


def run(subjects_json_path, sources_dir, output_dir, subject_code_filter=None,
        subject_codes=None, quiet=False):
    """subject_code_filter: single code (v1 API). subject_codes: iterable of
    codes. Neither = every subject. Returns the batch summary rows."""
    wanted = set()
    if subject_code_filter:
        wanted.update(c.strip().upper() for c in str(subject_code_filter).split(",") if c.strip())
    if subject_codes:
        wanted.update(c.strip().upper() for c in subject_codes if c.strip())
    subjects_json_path = Path(subjects_json_path)
    sources_dir = Path(sources_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(subjects_json_path.read_text(encoding="utf-8"))

    batch_summary = []

    for code, subject in iter_subjects(data):
        if wanted and code not in wanted:
            continue

        subject_report = {"code": code, "name": subject.get("name", code), "units": {}}
        subject_dir = sources_dir / code

        units_map = subject.get("units") or {}
        topics_map = subject.get("topics") or {}

        for unit_key in units_map.keys():
            unit_no = ROMAN_TO_INT.get(unit_key, unit_key)
            unit_file = subject_dir / f"Unit{unit_no}.txt"
            # Same topic granularity + boilerplate stripping the scraper
            # itself matched against (dash/comma-split sub-concepts, minus
            # the "All Topics (Full Unit)" UI marker and any trailing
            # syllabus contamination) — checking the raw multi-line,
            # unsplit blobs would count several distinct real topics as
            # one, and would count non-topic boilerplate as a "topic".
            raw_topics = topics_map.get(unit_key, [])
            topics = scoring_subtopics_from_lines(_scraper.clean_unit_topics(raw_topics))   # BL-002

            if not unit_file.exists():
                subject_report["units"][unit_key] = {
                    "status": "NO SOURCE FILE",
                    "coverage_pct": 0.0,
                    "topics_total": len(topics),
                }
                continue

            unit_text = unit_file.read_text(encoding="utf-8")
            result = check_unit(unit_text, topics)
            subject_report["units"][unit_key] = result

        _scraper.atomic_write_json(output_dir / f"{code}_coverage.json", subject_report)
        batch_summary.extend(_summary_rows(subject_report))

    _scraper.atomic_write_json(output_dir / "batch_summary.json", batch_summary)
    rebuild_overall_summary(output_dir)
    if quiet:
        return batch_summary

    # human-readable console summary — the "which subjects need review" view
    print(f"{'Subject':<10} {'Unit':<6} {'Coverage':<10} {'Thin Topics':<12}")
    for row in batch_summary:
        flag = "  <-- REVIEW" if row["coverage_pct"] < 100 or (isinstance(row["thin_topics_flagged"], int) and row["thin_topics_flagged"] > 0) else ""
        print(f"{row['code']:<10} {row['unit']:<6} {row['coverage_pct']:<10} {row['thin_topics_flagged']:<12}{flag}")
    return batch_summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AcademicsGen coverage checker")
    parser.add_argument("--subjects-json", required=True)
    parser.add_argument("--sources-dir", default="sources")
    parser.add_argument("--output-dir", default="coverage_reports")
    parser.add_argument("--subject-code", default=None, help="Check one subject (or comma-separated list) only")
    args = parser.parse_args()

    run(args.subjects_json, args.sources_dir, args.output_dir, args.subject_code)
