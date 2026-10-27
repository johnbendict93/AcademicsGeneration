"""Offline tests for qa_format.py — the per-regulation QA JSON contract."""
import json

import pytest

import qa_format as qf

REG = "R-2021"


def good(reg=REG):
    return {p.key: [{"question": f"{p.key} Q{i}?", "answer": f"A{i}."} for i in range(p.count)]
            for p in qf.get_pattern(reg)}


def test_r2021_pattern_is_official_10x2_5x13_1x15():
    got = [(p.key, p.count, p.marks) for p in qf.get_pattern("R-2021")]
    assert got == [("part_a", 10, 2), ("part_b", 5, 13), ("part_c", 1, 15)]
    assert sum(p.count * p.marks for p in qf.get_pattern("R-2021")) == 100


@pytest.mark.parametrize("raw", ["R2021", "r-2021", "R 2021", "Regulation 2021", " R-2021 "])
def test_regulation_spellings_normalise(raw):
    assert qf.normalize_regulation(raw) == "R-2021"


def test_unconfigured_regulation_fails_clearly():
    with pytest.raises(qf.UnsupportedRegulationError, match="R-2025"):
        qf.get_pattern("R2025")


def test_valid_json_text_passes():
    out = qf.parse_and_validate_qa(json.dumps(good()), REG)
    assert [len(out[k]) for k in ("part_a", "part_b", "part_c")] == [10, 5, 1]


def test_regression_13_instead_of_10_is_rejected():
    d = good()
    d["part_a"] += [{"question": f"Extra {i}?", "answer": "x"} for i in range(3)]
    with pytest.raises(qf.QAValidationError, match="part_a has 13 questions, expected exactly 10"):
        qf.parse_and_validate_qa(d, REG)


def test_missing_part_c_rejected():
    d = good()
    del d["part_c"]
    with pytest.raises(qf.QAValidationError, match="part_c missing"):
        qf.parse_and_validate_qa(d, REG)


@pytest.mark.parametrize("bad", ["", "not json", "[1,2]", json.dumps({"part_a": []})])
def test_malformed_input_rejected(bad):
    with pytest.raises(qf.QAValidationError):
        qf.parse_and_validate_qa(bad, REG)


def test_empty_answer_rejected():
    d = good()
    d["part_a"][3]["answer"] = "   "
    with pytest.raises(qf.QAValidationError, match="empty"):
        qf.parse_and_validate_qa(d, REG)


def test_duplicate_question_rejected():
    d = good()
    d["part_b"][1]["question"] = "  PART_A q0 "
    with pytest.raises(qf.QAValidationError, match="duplicates"):
        qf.parse_and_validate_qa(d, REG)


def test_whitespace_is_trimmed():
    d = good()
    d["part_a"][0] = {"question": "  Q0?  ", "answer": "\nA0.\n"}
    assert qf.parse_and_validate_qa(d, REG)["part_a"][0] == {"question": "Q0?", "answer": "A0."}


def _walk(schema):
    yield schema
    for v in schema.get("properties", {}).values():
        yield from _walk(v)
    if "items" in schema:
        yield from _walk(schema["items"])


@pytest.mark.parametrize("reg", sorted(qf.QA_PATTERNS))
def test_schema_meets_groq_strict_mode_rules(reg):
    rf = qf.response_format(reg)
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    for node in _walk(rf["json_schema"]["schema"]):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


def test_instructions_state_exact_counts_and_marks():
    text = qf.qa_instructions(REG)
    assert "EXACTLY 10 questions (2 marks each)" in text
    assert "EXACTLY 5 questions (13 marks each)" in text
    assert "EXACTLY 1 question (15 marks each)" in text
    assert "LaTeX" in text and "16" not in text
