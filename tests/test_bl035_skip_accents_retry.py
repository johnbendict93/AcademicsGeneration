"""BL-035 (John's decisions 2026-09-28, live runs #10/#11). Three changes, one run:
(1) SKIP LIST: garbled transcript spots listed in <resources>/sources/_garbled_spots.json are cut out of the unit
    text before the passages are made (only removes, never adds). Stale / not-found entries refuse loudly.
    First entry: AL3501 Unit I 'men -> lemma men' (runs #6, #9, #10, #11 printed that wrong fact).
(2) ACCENTS: NOT changed - measured: run #11's 'naïve' is not in the sent passages (model's own word).
(3) A topic that fails the checks is re-asked up to TWICE (was once); each re-ask names what the last answer broke.
GUARD tests pass on the old code too; they protect kept behaviour."""
import json
import re
import shutil
import types

import pytest

import source_app as sa
import source_context as sc
import source_prompts as sp
import source_runner as sr
from test_source_prompts import SUBJECT, UNIT_TITLE, _good_answer, _topic

CODE = "AL3501"
GARBLED = "the output you will get is m e n the actual lemma"
NEIGHBOUR = "That is how they can solve the morphological analysis problem."


@pytest.fixture(scope="module")
def ctx():
    return sc.build_unit_context(CODE, "I")


def _res(tmp_path, spots=None):
    res = tmp_path / "res"
    (res / "sources" / CODE).mkdir(parents=True)
    shutil.copy2(sc.RESOURCES_DIR / "subjects.json", res / "subjects.json")
    shutil.copy2(sc.RESOURCES_DIR / "sources" / CODE / "Unit1.txt", res / "sources" / CODE / "Unit1.txt")
    if spots is not None:
        (res / "sources" / sc.GARBLED_SPOTS_FILE).write_text(json.dumps({"spots": spots}), encoding="utf-8")
    return res


def _real_entry():
    return json.loads(sc.garbled_spots_path().read_text(encoding="utf-8"))["spots"][0]


# ---------- (1) skip list ----------

def test_real_list_has_the_men_spot():
    e = _real_entry()
    assert e["code"] == CODE and e["unit"] == "I" and e["source_sha256"].startswith("f034c46d67")
    assert GARBLED in e["text"]


def test_garbled_spot_is_not_in_any_passage(ctx):
    joined = " ".join(p.text for p in ctx.passages)
    assert GARBLED not in joined and NEIGHBOUR in joined          # the neighbouring sentence stays
    assert GARBLED in " ".join(ctx.source.text.split())           # the file itself is untouched (R2)
    assert len(ctx.source.skipped_spots) == 2                      # BL-036: + 'Catherine cook' (was 1)


def test_transducers_topic_no_longer_gets_the_spot(ctx):
    plan = _topic(ctx, "Transducers for lexicon and rules, Tokenization")
    assert plan.passages and all(GARBLED not in p.text for p in plan.passages)


def test_stale_entry_is_refused(tmp_path):
    e = dict(_real_entry(), source_sha256="0" * 64)
    with pytest.raises(sc.SourceRefused, match="out of date"):
        sc.build_unit_context(CODE, "I", _res(tmp_path, [e]))


@pytest.mark.parametrize("text", ["this sentence is not in the lecture at all", "So"])
def test_text_not_found_exactly_once_is_refused(tmp_path, text):
    e = dict(_real_entry(), text=text)
    with pytest.raises(sc.SourceRefused, match="exactly once"):
        sc.build_unit_context(CODE, "I", _res(tmp_path, [e]))


def test_no_list_means_passages_as_before(tmp_path):
    """GUARD (passes before and after)."""
    c = sc.build_unit_context(CODE, "I", _res(tmp_path))
    assert [p.text for p in c.passages] == [p.text for p in sc.split_passages(c.source.text)]


def test_entry_for_another_unit_changes_nothing(tmp_path):
    """An entry for Unit II leaves Unit I untouched (needs the new list file, so it fails on the old code)."""
    c = sc.build_unit_context(CODE, "I", _res(tmp_path, [dict(_real_entry(), unit="II")]))
    assert any(GARBLED in p.text for p in c.passages)


def test_prompt_version_follows_the_list(tmp_path, monkeypatch):
    pv = sr.prompt_version()
    assert re.fullmatch(r"sp:[0-9a-f]{10}\+sq:[0-9a-f]{10}\+gs:[0-9a-f]{10}", pv)
    res = _res(tmp_path, [dict(_real_entry(), reason="changed")])
    monkeypatch.setattr(sc, "RESOURCES_DIR", res)
    assert sr.prompt_version() != pv                                # list changed -> stored PDFs OUT OF DATE


def test_check_log_lists_skipped_spots(ctx):
    run = types.SimpleNamespace(topics=[], gaps=[], verify={}, source=ctx.source, prompt_version="pv", calls=0,
                                needs_review=[], failed=[])
    rep = sa.run_report(run, "Notes", CODE, 1)
    assert rep["skipped_spots"] == ctx.source.skipped_spots and "men" in rep["skipped_spots"][0]


@pytest.fixture(scope="module")
def med(ctx):
    return _topic(ctx, "Minimum Edit Distance")


def test_accents_stay_odd_no_accent_rule(med):
    """GUARD (passes before and after). Measured 28 Sep: 'naive' is NOT in the passages sent for Min Edit
    Distance (only in P5/P16/P44) -> run #11's 'naïve' was the model's own word; the odd-char check was right.
    No accent exception was built; the extra re-ask (3) tells the model which character to drop."""
    assert "naive" not in " ".join(p.text for p in med.passages).lower()
    assert sp._odd_chars("A na\u00efve method.", sp._passage_chars(med)) == ["\u00ef"]


# ---------- (3) up to two re-asks ----------

LATEX_BAD = "- The cost is \\(D(i,j)\\) here [{pid}]\n"


def _run(ctx, plan, answers):
    prompts = []

    def ask(p):
        prompts.append(p)
        return answers[min(len(prompts), len(answers)) - 1]
    return sp.notes_for_topic(ctx, plan, SUBJECT, UNIT_TITLE, ask=ask), prompts


def test_third_try_rescues_a_topic(ctx, med):
    pid = med.passages[0].pid
    first = _good_answer(med) + LATEX_BAD.format(pid=pid)
    second = _good_answer(med) + f"- A naïve ∑ sum [{pid}]\n"          # new problem: odd char
    res, prompts = _run(ctx, med, [first, second, _good_answer(med)])
    assert len(prompts) == 3 and res.status == "ok" and res.used == 2 and res.reask_reason == "not ok"
    assert "LaTeX" in prompts[1] and "U+2211" in prompts[2]         # each re-ask names what the LAST try broke
    log = sa._writer_log(res)
    assert log["used_try"] == 3 and log["reask_reason"] == "not ok"


def test_second_try_ok_stops_there(ctx, med):
    """GUARD (passes before and after): no third call when the second answer is ok."""
    first = _good_answer(med) + LATEX_BAD.format(pid=med.passages[0].pid)
    res, prompts = _run(ctx, med, [first, _good_answer(med)])
    assert len(prompts) == 2 and res.status == "ok"


def test_admin_estimate_mentions_up_to_two_more():
    src = open(sa.__file__.replace("source_app.py", "academicsgen_app.py"), encoding="utf-8").read()
    assert "3 * minutes + 2" in src and "up to two more" in src
