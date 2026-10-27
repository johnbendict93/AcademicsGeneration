"""BL-041 b (BL041B_DESIGN.md; John Q1-Q5, 2026-09-30): key-word warning before Generate.
- Q1 WARN ONLY: a covered topic whose key word has 0 hits in the unit text gets '⚠️ Not found in the source: w (0)'.
  Nothing is blocked, skipped or marked; Generate / buttons / prompt_version / PDF unchanged.
- Q4/Q5 alias list sources/_topic_aliases.json: spelling / word form / abbreviation only. A word counts as found
  if it OR one of its aliases has a hit (multi-word alias = consecutive words).
- A bad alias file never stops the app: a note is shown and the check runs without aliases.
Every test FAILS on the old code (no topic_hits module / no warning); GUARD tests pass on the old code too."""
import json

import pytest

import source_context as sc
from test_app_ui import _all_text, _app, _box, _unlock, _undo_driver_patches  # noqa: F401 (autouse fixture)

ALIASES = {"automata": ["automaton"], "pos": ["part of speech"], "wsd": ["word sense disambiguation"]}


def th():
    import topic_hits
    return topic_hits


def test_zero_hit_word_is_flagged():
    hits = th().topic_word_hits("An automaton reads the input tape. The finite state machine.", "Finite-State Automata")
    assert dict(hits)["automata"] == 0
    assert th().flagged(hits) == [("automata", 0)]
    assert th().warning_text(th().flagged(hits)) == "Not found in the source: automata (0)"


def test_alias_counts_as_found():
    text = "A finite state automaton reads the input. Another automaton stops."
    hits = th().topic_word_hits(text, "Finite-State Automata", ALIASES)
    assert dict(hits)["automata"] == 2 and th().flagged(hits) == []


def test_multi_word_alias_needs_consecutive_words():
    ok = th().topic_word_hits("Issues: we do part of speech tagging here.", "Issues in PoS tagging", ALIASES)
    assert th().flagged(ok) == []
    apart = th().topic_word_hits("issues: part of the speech is tagging", "Issues in PoS tagging", ALIASES)
    assert th().flagged(apart) == [("pos", 0)]


def test_word_forms_match_like_the_checker():
    hits = th().topic_word_hits("Grammars and treebanks are used.", "Grammar rules, Treebanks")
    assert dict(hits)["treebanks"] == 1 and dict(hits)["grammar"] == 1 and dict(hits)["rules"] == 0


def test_no_warning_when_nothing_flagged():
    assert th().warning_text([]) is None


def test_load_aliases(tmp_path):
    (tmp_path / "sources").mkdir()
    assert th().load_aliases("AL3501", tmp_path) == ({}, None)                      # no file -> no aliases
    f = tmp_path / "sources" / th().ALIASES_FILE
    f.write_text(json.dumps({"AL3501": {"Automata": ["automaton"]}, "CS3491": {"x": ["y"]}}), encoding="utf-8")
    assert th().load_aliases("al3501", tmp_path) == ({"automata": ["automaton"]}, None)
    assert th().load_aliases("GE3751", tmp_path) == ({}, None)
    f.write_text("{not json", encoding="utf-8")
    aliases, note = th().load_aliases("AL3501", tmp_path)
    assert aliases == {} and "alias list" in note and "unreadable" in note
    f.write_text(json.dumps({"AL3501": {"automata": "automaton"}}), encoding="utf-8")   # not a list
    aliases, note = th().load_aliases("AL3501", tmp_path)
    assert aliases == {} and "unreadable" in note


@pytest.mark.parametrize("unit, expected", [
    ("I", set()),
    ("II", {"Stochastic and Transformation-based tagging"}),
    ("III", {"Grammar rules for English, Treebanks", "Syntactic Parsing, Ambiguity", "DynamicProgramming parsing",
             "Shallow parsing", "Probabilistic CYK,Probabilistic Lexicalized CFGs", "Unification of feature structures."}),
    ("IV", {"First-Order Logic", "Syntax-Driven Semantic analysis", "Semantic attachments", "selectional restrictions"}),
])
def test_real_al3501_units_with_the_three_aliases(unit, expected):
    """Measured 30 Sep: the flags on the real verified sources (covered topics only)."""
    ctx = sc.build_unit_context("AL3501", unit)
    flags = th().unit_flags(ctx, ALIASES)
    assert {t for t, w in flags.items() if w} == expected
    assert set(flags) == {p.topic for p in ctx.topics if p.status in ("FOUND", "PARTIAL")}


def test_unit_text_is_the_text_the_passages_are_made_from():
    ctx = sc.build_unit_context("AL3501", "I")
    assert th().unit_text(ctx) == " ".join(p.text for p in ctx.passages)


def test_ui_shows_the_warning_before_generate(tmp_path):
    at, log = _app(tmp_path)
    unit = _box(at, "Unit")
    unit.select(next(o for o in unit.options if o.startswith("Unit 2"))).run()
    _unlock(at)
    assert not at.exception
    text = _all_text(at)
    assert "Not found in the source: stochastic (0)" in [w.value for w in at.warning]
    assert any("key word that is not in the source" in w.value and "Stochastic" in w.value for w in at.warning)
    assert all(w.icon == "⚠️" for w in at.warning if "source" in w.value)
    assert "Word Classes" in text and "not found in the source: class" not in text.lower()
    assert not at.button(key="generate").disabled                    # warn only: Generate still works


def test_guard_generate_button_still_there(tmp_path):
    """GUARD: the Admin Notes screen still offers Generate on Unit 1 (passes on the old code too)."""
    at, _ = _app(tmp_path)
    _unlock(at)
    assert not at.exception and at.button(key="generate") is not None
