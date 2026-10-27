"""BL-041 b (BL041B_DESIGN.md, John Q1-Q5, 2026-09-30): key-word warning - ADVICE ONLY.

For each covered topic: how often each key word of the topic title appears in the unit text the passages are
made from (after the garbled-spot cut). A word with 0 hits (and 0 hits for all its aliases) is flagged.
Nothing is blocked, skipped or marked here; the app only shows the warning. Pure: no Streamlit, network or DB.
Word forms use coverage_checker's normalize / tokenize (one shared copy, BL-017) via source_context.
Aliases (sources/_topic_aliases.json): spelling / word form / abbreviation only - never 'similar meaning' (Q5).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import source_context

ALIASES_FILE = "_topic_aliases.json"
COVERED = ("FOUND", "PARTIAL")
_cc = source_context._cc


def load_aliases(code, resources_dir=None):
    """-> (aliases for this subject {word: [alias, ...]}, note or None). Missing file / subject -> ({}, None).
    A bad file never stops the app: ({}, note) and the check runs without aliases."""
    path = Path(resources_dir or source_context.RESOURCES_DIR).resolve() / "sources" / ALIASES_FILE
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mine = {str(k).upper(): v for k, v in data.items()}.get(str(code).strip().upper()) or {}
        if not isinstance(mine, dict) or not all(isinstance(v, list) and all(isinstance(a, str) for a in v)
                                                 for v in mine.values()):
            raise ValueError("each entry must be a list of words")
        return {str(k).strip().lower(): [a for a in v if a.strip()] for k, v in mine.items()}, None
    except Exception as e:                                           # noqa: BLE001 - advice only, never fatal
        return {}, f"The alias list ({ALIASES_FILE}) is unreadable ({type(e).__name__}); checked without aliases."


def unit_text(ctx) -> str:
    """The text the writer's passages are made from (garbled spots already cut out)."""
    return " ".join(p.text for p in ctx.passages)


def _title_words(topic):
    """Key words of the title as written (lower case), same filter as coverage_checker.key_terms, one per stem."""
    seen, out = set(), []
    for w in re.findall(r"[a-zA-Z']+", topic):
        if w.lower() in _cc.STOPWORDS or len(w) <= 2:
            continue
        stem = _cc.normalize(w)
        if stem not in seen:
            seen.add(stem)
            out.append((w.lower(), stem))
    return out


def _phrase_hits(tokens, phrase_tokens):
    n = len(phrase_tokens)
    if n == 0:
        return 0
    return sum(1 for i in range(len(tokens) - n + 1) if tokens[i:i + n] == phrase_tokens)


def _hits(tokens, counts, topic, aliases):
    out = []
    for word, stem in _title_words(topic):
        n = counts[stem]
        for alias in (aliases or {}).get(word, []):
            n += _phrase_hits(tokens, _cc.tokenize(alias))
        out.append((word, n))
    return out


def topic_word_hits(text, topic, aliases=None):
    """-> [(word as written, hits incl. alias hits)] in title order."""
    tokens = _cc.tokenize(text)
    return _hits(tokens, Counter(tokens), topic, aliases)


def flagged(hits):
    return [(w, n) for w, n in hits if n == 0]


def warning_text(flags):
    if not flags:
        return None
    return "Not found in the source: " + ", ".join(f"{w} ({n})" for w, n in flags)


def unit_flags(ctx, aliases=None):
    """{covered topic: warning text or None} for one unit (tokenised once)."""
    tokens = _cc.tokenize(unit_text(ctx))
    counts = Counter(tokens)
    return {p.topic: warning_text(flagged(_hits(tokens, counts, p.topic, aliases)))
            for p in ctx.topics if p.status in COVERED}


def summary_text(flags):
    """One line above the topic list, or None (the app shows it with a ⚠️ icon)."""
    names = [t for t, w in flags.items() if w]
    if not names:
        return None
    what = "1 topic has a key word" if len(names) == 1 else f"{len(names)} topics have a key word"
    return (f"{what} that is not in the source: {'; '.join(names)}. Check it before Generate - "
            f"if it is not really in the source, mark it (advice only; nothing is blocked).")
