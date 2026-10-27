"""P1 of the source-strict app (APP_SOURCE_STRICT_DESIGN.md sections 2, 3, 8).

Tests for App/source_context.py: offline and pure (no Groq, no DB, no network,
writes nothing). Runs on the REAL AL3501 files in the resources repo.
Decision (John, 2026-09-27): option (a) - the app imports normalize() and the
topic/status logic from the scripts folder's coverage_checker.py (one copy,
cannot drift - the BL-017 lesson).
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

import source_context as sc

CODE = "AL3501"
UNIT1_SHA = "f034c46d67adb38e092d690a47e14e8958c537cef1d33be35b9b97a1ef09af91"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def unit1():
    return sc.build_unit_context(CODE, "I")


def _copy_resources(tmp_path):
    """Minimal private copy of the repo: subjects.json + AL3501 Unit1.txt."""
    res = tmp_path / "res"
    (res / "sources" / CODE).mkdir(parents=True)
    shutil.copy2(sc.RESOURCES_DIR / "subjects.json", res / "subjects.json")
    shutil.copy2(sc.RESOURCES_DIR / "sources" / CODE / "Unit1.txt",
                 res / "sources" / CODE / "Unit1.txt")
    return res


def _edit_al3501_copies(res, fn, only_first=False):
    """Apply fn(sources_entry_for_unit_I) to the AL3501 copies in subjects.json."""
    p = res / "subjects.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for dept in data["universities"]["Anna University"]["departments"].values():
        for sem in dept["semesters"].values():
            if CODE in sem and "I" in (sem[CODE].get("sources") or {}):
                fn(sem[CODE]["sources"]["I"])
                if only_first:
                    p.write_text(json.dumps(data), encoding="utf-8")
                    return
    p.write_text(json.dumps(data), encoding="utf-8")


# ---------- setup / option (a) ----------

def test_default_folders_found():
    assert (sc.RESOURCES_DIR / "subjects.json").is_file()
    assert (sc.SCRIPTS_DIR / "coverage_checker.py").is_file()


def test_uses_the_checkers_own_normalize():
    import coverage_checker
    assert sc.normalize is coverage_checker.normalize


def test_app_modules_not_shadowed_by_scripts_folder():
    # generate_all_pdfs.py exists in BOTH folders; the App's copy must win.
    import generate_all_pdfs
    assert Path(generate_all_pdfs.__file__).resolve().parent == Path(sc.__file__).resolve().parent


# ---------- R2 / R3: load + refuse ----------

def test_load_unit1_sha_matches_subjects_json():
    src = sc.load_unit_source(CODE, "I")
    assert src.sha256 == UNIT1_SHA
    assert _sha(src.path) == UNIT1_SHA
    assert src.source == "nptel"
    assert src.license == "CC BY-SA 4.0"
    assert src.url == "https://nptel.ac.in/courses/106105158"
    assert src.title.startswith("NOC:Natural Language Processing")
    assert len(src.text.split()) > 10000


def test_unit_without_source_is_refused():
    with pytest.raises(sc.SourceRefused, match="No verified source for Unit V yet"):
        sc.load_unit_source(CODE, "V")


def test_unknown_subject_is_refused():
    with pytest.raises(sc.SourceRefused):
        sc.load_unit_source("ZZ9999", "I")


def test_tampered_file_is_refused(tmp_path):
    res = _copy_resources(tmp_path)
    f = res / "sources" / CODE / "Unit1.txt"
    f.write_bytes(f.read_bytes() + b" extra")
    with pytest.raises(sc.SourceRefused, match="sha256"):
        sc.load_unit_source(CODE, "I", resources_dir=res)


def test_department_copies_that_disagree_are_refused(tmp_path):
    res = _copy_resources(tmp_path)
    _edit_al3501_copies(res, lambda s: s.update(content_sha256="0" * 64), only_first=True)
    with pytest.raises(sc.SourceRefused, match="disagree"):
        sc.load_unit_source(CODE, "I", resources_dir=res)


@pytest.mark.parametrize("bad", ["_pending/AL3501/Unit1.txt", "../AL3501/Unit1.txt", "/etc/Unit1.txt"])
def test_file_outside_live_sources_is_refused(tmp_path, bad):
    res = _copy_resources(tmp_path)
    (res / "sources" / "_pending" / CODE).mkdir(parents=True)
    shutil.copy2(res / "sources" / CODE / "Unit1.txt", res / "sources" / "_pending" / CODE / "Unit1.txt")
    _edit_al3501_copies(res, lambda s: s.update(file=bad))
    with pytest.raises(sc.SourceRefused):
        sc.load_unit_source(CODE, "I", resources_dir=res)


# ---------- passages ----------

def test_passages_keep_every_word_in_order(unit1):
    # BL-035 (updated): every word in order EXCEPT the garbled spots on the skip list
    spots = sc.unit_garbled_spots(unit1.source)
    words = sc.remove_garbled_spots(unit1.source.text, spots).split()
    assert [w for p in unit1.passages for w in p.text.split()] == words


def test_passages_are_numbered_and_sized(unit1):
    ids = [p.pid for p in unit1.passages]
    assert ids == [f"P{i}" for i in range(1, len(ids) + 1)]
    sizes = [p.word_count for p in unit1.passages]
    assert max(sizes) <= sc.PASSAGE_MAX_WORDS
    assert 200 <= sum(sizes) / len(sizes) <= 400


def test_one_huge_sentence_is_still_split():
    text = " ".join(["word"] * 1000)            # no punctuation at all
    ps = sc.split_passages(text)
    assert all(p.word_count <= sc.PASSAGE_MAX_WORDS for p in ps)
    assert sum(p.word_count for p in ps) == 1000


# ---------- topics, selection, gaps ----------

def test_topics_and_status_match_saved_report(unit1):
    # Fresh check on the sha-verified text == what coverage_checker saved.
    rep = json.loads((sc.RESOURCES_DIR / "coverage_reports" / f"{CODE}_coverage.json")
                     .read_text(encoding="utf-8"))["units"]["I"]["topics"]
    assert [(t.topic, t.status) for t in unit1.topics] == [(r["topic"], r["status"]) for r in rep]


def test_found_and_partial_topics_get_passages_within_budget(unit1):
    known = {p.pid for p in unit1.passages}
    covered = [t for t in unit1.topics if t.status in ("FOUND", "PARTIAL")]
    assert len(covered) == 7
    for t in covered:
        assert t.passages, t.topic
        assert sum(p.word_count for p in t.passages) <= sc.PASSAGE_BUDGET_WORDS
        terms = set(sc.key_terms(t.topic))
        for p in t.passages:
            assert p.pid in known
            assert terms & set(sc.tokenize(p.text)), (t.topic, p.pid)


def test_regular_expressions_best_passage_has_both_terms(unit1):
    t = next(t for t in unit1.topics if t.topic == "Regular Expressions")
    assert {"regular", "express"} <= set(sc.tokenize(t.passages[0].text))


def test_missing_topic_goes_to_gaps_with_no_passages(unit1):
    t = next(t for t in unit1.topics if t.topic == "Origins and challenges of NLP")
    assert t.status == "MISSING" and t.passages == []
    assert unit1.gaps == [t.topic for t in unit1.topics if t.status == "MISSING"]
    assert "Origins and challenges of NLP" in unit1.gaps


def test_building_context_writes_nothing():
    watched = [sc.RESOURCES_DIR / "subjects.json",
               sc.RESOURCES_DIR / "sources" / CODE / "Unit1.txt",
               sc.RESOURCES_DIR / "coverage_reports" / f"{CODE}_coverage.json"]
    before = [_sha(p) for p in watched]
    sc.build_unit_context(CODE, "I")
    assert [_sha(p) for p in watched] == before
