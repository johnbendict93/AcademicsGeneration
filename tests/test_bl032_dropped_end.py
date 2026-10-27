"""BL-032: the check log keeps the END of every dropped text (where the [Pn] tag belongs) and what the
FIRST try got wrong. Live run #7 (2026-09-28): 3 topics lost every paragraph 'no citation' on both tries,
but the log kept only the first 160 chars, so nobody could see HOW the tags were written.
3 tests FAIL on source_app 5ae76bf3c2; the 2 labelled GUARD pass before and after."""
import json
import types

import source_app as sa

LONG = "Regular expressions define regular languages. " * 6 + "Any expression becomes an FSA [P1–P3]"


def _chk(dropped, **kw):
    base = dict(new_terms=[], odd_chars=[], latex=[], dropped=dropped)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _drop(text, reason="no citation"):
    return types.SimpleNamespace(text=text, reason=reason)


def test_dropped_entry_keeps_the_end_with_the_tag():
    t = types.SimpleNamespace(calls=1, checks=[_chk([_drop(LONG)])])
    d = sa._writer_log(t)["dropped"][0]
    assert d["end"].endswith("[P1–P3]") and len(d["end"]) == sa.DROPPED_END_CHARS
    assert d["text"] == LONG[:160]                       # GUARD part: the start is kept as before


def test_short_text_end_is_the_whole_text():
    t = types.SimpleNamespace(calls=1, checks=[_chk([_drop("Two scenarios are considered:")])])
    assert sa._writer_log(t)["dropped"][0]["end"] == "Two scenarios are considered:"


def test_first_try_is_logged_when_there_was_a_reask():
    first = _chk([_drop(LONG)], latex=["\\text"], odd_chars=["₀"])
    last = _chk([])
    w = sa._writer_log(types.SimpleNamespace(calls=2, checks=[first, last]))
    assert w["first_try"]["latex"] == ["\\text"] and w["first_try"]["odd_chars"] == ["U+2080"]
    assert w["first_try"]["dropped"][0]["end"].endswith("[P1–P3]")
    assert w["dropped"] == []


def test_no_first_try_block_for_a_single_call():
    """GUARD (passes before and after)."""
    w = sa._writer_log(types.SimpleNamespace(calls=1, checks=[_chk([])]))
    assert "first_try" not in w


def test_log_stays_json():
    """GUARD (passes before and after)."""
    w = sa._writer_log(types.SimpleNamespace(calls=2, checks=[_chk([_drop(LONG)]), _chk([_drop("x [P9]")])]))
    assert json.loads(json.dumps(w, ensure_ascii=False)) == w
