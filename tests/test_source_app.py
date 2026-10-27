"""P3c-1 of the source-strict app: the app-side decisions, kept out of Streamlit so
they can be tested (APP_SOURCE_STRICT_DESIGN.md sections 5, 8 P3).

source_app.py decides: which variants exist (D1: Notes + QA), whether a unit can be
generated + its coverage table, whether a stored PDF may be served, the storage key,
the Neon row values, and runs a unit end to end (fake Groq here).
"""
import io
import json
import re

import pytest
from pypdf import PdfReader

import source_app as sa
import source_runner as sr

UNIT = dict(subject_code="AL3501", subject_name="Natural Language Processing",
            dept_code="AI&DS", dept_name="Artificial Intelligence and Data Science",
            unit_title="Introduction", regulation="R-2021")
SHA1 = "f034c46d67adb38e092d690a47e14e8958c537cef1d33be35b9b97a1ef09af91"


class Clock:
    t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _sents(text, n):
    return [p for p in re.split(r"(?<=[.?!])\s+", text.strip()) if len(p.split()) >= 6][:n]


_W = "apple bread cloud dance eagle fruit grape house igloo jelly kite lemon mango night".split()


def fake_groq(prompt, *, max_completion_tokens, reasoning_effort=None, response_format=None, attempts=3):
    """Stands in for academicsgen_app._groq_chat (same keyword arguments)."""
    fake_groq.calls.append(dict(max_completion_tokens=max_completion_tokens,
                                reasoning_effort=reasoning_effort, response_format=response_format))
    if response_format and response_format["json_schema"]["name"] == "sentence_verdicts":   # BL-028 verifier
        sents = re.findall(r"^(S\d+): ", prompt.split("SENTENCES", 1)[1], re.M)
        return json.dumps({"verdicts": [{"id": i, "supported": True, "reason": ""} for i in sents]})
    m = re.search(r"^\[(P\d+)\] (.+)$", prompt, re.M)
    pid, text = m.group(1), m.group(2)
    s = _sents(text, 2)
    if response_format is None:
        return f"{s[0]} [{pid}]\n\n- {s[1]} [{pid}]\n"
    topic = re.search(r'Sub-topic: "(.+?)"', prompt).group(1)
    out, k = {}, 0
    for key in response_format["json_schema"]["schema"]["required"]:
        n = int(re.search(rf"- {key}: UP TO (\d+)", prompt).group(1))
        out[key] = [{"question": f"Explain {topic} with {_W[2 * i]} {_W[2 * i + 1]}.",
                     "answer": " ".join(s), "sources": [pid]} for i in range(k, k + n)]
        k += n
    return json.dumps(out)


@pytest.fixture(autouse=True)
def _reset():
    fake_groq.calls = []


# ---------- variants (D1) ----------

def test_only_notes_and_qa():
    assert sa.VARIANTS == ["Notes", "QA"]


# ---------- unit overview ----------

def test_overview_available_unit():
    ov = sa.unit_overview("AL3501", 1)
    assert ov.available and ov.message == ""
    assert ov.source.sha256 == SHA1
    assert (ov.covered, ov.total) == (7, 8)
    assert ov.topics[0] == ("Origins and challenges of NLP", "MISSING")
    assert sa.status_label("FOUND") == "covered"
    assert sa.status_label("PARTIAL") == "partly covered"
    assert sa.status_label("MISSING") == "not in source"


def test_overview_unit_without_source():
    ov = sa.unit_overview("AL3501", 5)
    assert not ov.available and ov.source is None
    assert "No verified source for Unit V yet" in ov.message


def test_overview_unknown_subject_is_unavailable_not_a_crash():
    ov = sa.unit_overview("ZZ9999", 1)
    assert not ov.available and ov.message


# ---------- may a stored PDF be served? ----------

def test_old_general_knowledge_row_is_never_served():
    row = {"storage_key": "AI&DS/AL3501/AL3501_Unit1_Notes.pdf", "storage_bucket": "github",
           "source_sha256": None, "prompt_version": None}
    assert not sa.is_current(row, SHA1, sr.prompt_version())


def test_current_row_is_served_stale_rows_are_not():
    pv = sr.prompt_version()
    row = {"storage_key": "k", "storage_bucket": "github", "source_sha256": SHA1, "prompt_version": pv}
    assert sa.is_current(row, SHA1, pv)
    assert not sa.is_current({**row, "source_sha256": "0" * 64}, SHA1, pv)      # source changed
    assert not sa.is_current({**row, "prompt_version": "sp:x+sq:y"}, SHA1, pv)   # prompts changed
    assert not sa.is_current(None, SHA1, pv)
    assert not sa.is_current({**row, "storage_key": ""}, SHA1, pv)               # no file


def test_storage_key_is_in_a_new_folder_never_the_old_files():
    k = sa.storage_key("AI&DS", "AL3501", 1, "Notes")
    assert k == "source-strict/AI&DS/AL3501/AL3501_Unit1_Notes.pdf"
    assert not k.startswith("AI&DS/")
    with pytest.raises(ValueError):
        sa.storage_key("AI&DS", "AL3501", 1, "VerifiedNotes")


# ---------- end to end (fake Groq, fake clock) ----------

def _generate(variant, progress=None):
    c = Clock()
    return sa.generate_unit_pdf(variant=variant, unit_number=1, groq_chat=fake_groq,
                                progress=progress, clock=c.now, sleep=c.sleep, **UNIT)


def test_generate_notes_pdf_and_row_values():
    events = []
    out = _generate("Notes", events.append)
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(out.pdf_bytes)).pages)
    assert "Built only from" in text and "CC BY-SA 4.0" in text
    assert len(fake_groq.calls) == 7                                # 7 writer, verifier OFF (BL-029)
    assert sum(c["response_format"] is None for c in fake_groq.calls) == 7
    assert all(c["reasoning_effort"] == "low" for c in fake_groq.calls)
    assert {e["stage"] for e in events} >= {"start", "done"}
    v = out.row_values
    assert v["source_sha256"] == SHA1 and v["prompt_version"] == sr.prompt_version()
    assert (v["topics_covered"], v["topics_total"]) == (7, 8)
    assert json.loads(v["gap_topics"]) == ["Origins and challenges of NLP"]
    assert json.loads(v["needs_review_topics"]) == [] and json.loads(v["failed_topics"]) == []
    assert out.storage_key == "source-strict/AI&DS/AL3501/AL3501_Unit1_Notes.pdf"


def test_generate_qa_uses_strict_json_and_counts_only_checked_topics():
    out = _generate("QA")
    assert len(fake_groq.calls) == 7                                # 7 writer, verifier OFF (BL-029)
    assert all(c["response_format"]["json_schema"]["strict"] for c in fake_groq.calls)
    assert out.row_values["topics_covered"] == 7
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(out.pdf_bytes)).pages)
    assert " ".join(text.split()).count("[2 marks]") == 10     # flatten: a label can wrap


def test_generate_refuses_unit_without_source_before_any_call():
    c = Clock()
    with pytest.raises(sa.SourceRefused):
        sa.generate_unit_pdf(variant="Notes", unit_number=5, groq_chat=fake_groq,
                             clock=c.now, sleep=c.sleep, **UNIT)
    assert fake_groq.calls == []


def test_generate_refuses_unknown_variant_before_any_call():
    with pytest.raises(ValueError):
        _generate("VerifiedNotes")
    assert fake_groq.calls == []


def test_row_values_count_left_out_topics_correctly():
    def groq(prompt, **kw):
        if 'Sub-topic: "Minimum Edit Distance"' in prompt:
            pid = re.search(r"^\[(P\d+)\]", prompt, re.M).group(1)
            return f"It was shown by Wagner and Fischer, 1974, BERT, GPT. [{pid}]\n"
        return fake_groq(prompt, **kw)
    c = Clock()
    out = sa.generate_unit_pdf(variant="Notes", unit_number=1, groq_chat=groq,
                               clock=c.now, sleep=c.sleep, **UNIT)
    v = out.row_values
    assert v["topics_covered"] == 6
    assert json.loads(v["needs_review_topics"]) == ["Minimum Edit Distance"]
