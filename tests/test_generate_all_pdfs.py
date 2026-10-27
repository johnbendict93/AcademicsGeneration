"""Offline tests for generate_all_pdfs.py (the interactive AL3501 teaching-PDF tool).
No network: the Groq client is faked. PDFs are built into a temp folder."""
import json
import types

import pytest
from pypdf import PdfReader

import generate_all_pdfs as g

SYMBOLS = "P(w|c) ≤ 1 → “quoted” – dash — α β √2"


def text_of(path):
    return " ".join(" ".join(p.extract_text() for p in PdfReader(str(path)).pages).split())


# ── clean_text (was: forced ASCII and deleted every "?") ──
def test_clean_text_keeps_question_marks_and_symbols():
    assert g.clean_text("What is NLP?") == "What is NLP?"
    assert g.clean_text(SYMBOLS) == SYMBOLS.replace("&", "&amp;")


def test_clean_text_escapes_markup():
    assert g.clean_text("if x < y & z > 0") == "if x &lt; y &amp; z &gt; 0"


def test_clean_dict_recurses_and_keeps_numbers():
    d = g.clean_dict({"a": ["x?", {"b": "p‑q"}], "n": 3})
    assert d == {"a": ["x?", {"b": "p-q"}], "n": 3}


# ── exam pattern ──
def test_pattern_is_r2021_official():
    assert (g.PART_A.count, g.PART_A.marks) == (10, 2)
    assert (g.PART_B.count, g.PART_B.marks) == (5, 13)
    assert (g.PART_C.count, g.PART_C.marks) == (1, 15)


def test_no_16_mark_left_in_prompts():
    for name in ("QA_PART_A_PROMPT", "QA_PART_B_PROMPT", "QA_PART_C_PROMPT",
                 "NOTES_TOPIC_PROMPT", "VERIFIED_NOTES_PROMPT", "NOTES_META_PROMPT"):
        assert "16-mark" not in getattr(g, name).lower(), name
        assert "16-MARK" not in getattr(g, name), name


def test_all_prompts_still_format():
    kw = dict(subject_code="AL3501", subject_name="NLP", unit_num="I", unit_title="T",
              syllabus_text="s", topic_num="1", topic_name="t", topic_syllabus="s",
              source_context="c", unit_syllabus="s")
    for name in ("QA_PART_A_PROMPT", "QA_PART_B_PROMPT", "QA_PART_C_PROMPT", "NOTES_TOPIC_PROMPT",
                 "VERIFIED_NOTES_PROMPT", "NOTES_META_PROMPT", "BEYOND_PROMPT"):
        assert "JSON" in getattr(g, name).format(**kw)


# ── Groq calls (faked) ──
class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = self.completions = self

    def create(self, **kw):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        msg = types.SimpleNamespace(content=r)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(g.time, "sleep", lambda s: None)


def part(key, n):
    return json.dumps({key: [{"q_num": f"Q{i}", "question": f"Q {i}?", "star": "*", "answer": "a"}
                             for i in range(1, n + 1)]})


def test_call_json_expect_reasks_on_wrong_count():
    c = FakeClient([part("part_a", 13), part("part_a", 10)])
    assert len(g.call_json_expect(c, "p", "part_a", 10)["part_a"]) == 10
    assert c.replies == []


def test_call_json_expect_gives_up():
    c = FakeClient([part("part_a", 13)] * 3)
    with pytest.raises(RuntimeError, match="expected exactly 10"):
        g.call_json_expect(c, "p", "part_a", 10)


def test_call_gemini_tolerates_fences_and_retries_bad_json():
    c = FakeClient(["not json", "```json\n{\"x\": \"y?\"}\n```"])
    assert g.call_gemini(c, "p") == {"x": "y?"}


def test_groq_shim_sends_json_mode_and_refuses_truncation(monkeypatch):
    sent = {}

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}

    def post(url, timeout, headers, json):
        sent.update(json)
        return R()

    monkeypatch.setattr(g.requests, "post", post)
    with pytest.raises(RuntimeError, match="cut off"):
        g._GroqClient("k").chat.completions.create(model="m", messages=[], max_tokens=10, temperature=0)
    assert sent["response_format"] == {"type": "json_object"}


# ── PDF builders ──
def long_q(n):
    return [{"q_num": f"Q{i}", "question": f"Explain topic {i} with P(w|c)?", "star": "**",
             "introduction": "Intro → start.", "sections": [{"heading": "Idea", "content": SYMBOLS}],
             "points": ["x < y & z"], "conclusion": "Done."} for i in range(1, n + 1)]


def test_build_qa_pdf_has_all_three_parts(tmp_path):
    data = g.clean_dict({
        "part_a": [{"q_num": f"Q{i}", "question": f"Define term {i}?", "star": "*", "answer": SYMBOLS}
                   for i in range(1, 11)],
        "part_b": long_q(5), "part_c": long_q(1)})
    out = tmp_path / "qa.pdf"
    g.build_qa_pdf(str(out), "AL3501", "Natural Language Processing", "IV", "Semantics", data, g.get_styles())
    t = text_of(out)
    assert "PART B - 13 MARK QUESTIONS" in t and "PART C - 15 MARK QUESTIONS" in t
    assert "(13 Marks)" in t and "(15 Marks)" in t and "16" not in t.replace("Page 16", "")
    assert "Define term 10?" in t                      # question marks survive
    assert "P(w|c) ≤ 1 → “quoted”" in t and "x < y & z" in t
    assert "past papers" in t and "AI estimate" in t   # honest legend
    assert "&amp;" not in t and "&lt;" not in t


def test_navybar_and_code_box_show_real_characters(tmp_path):
    from reportlab.platypus import SimpleDocTemplate
    st = g.get_styles()
    story = [g.NavyBar(g.clean_text("1  Tokens & Types")), g.code_box(g.clean_text("if a < b: print('?')"), st),
             g.algo_box(g.clean_text("A & B"), [g.clean_text("Step 1: x ≤ y")], st)]
    out = tmp_path / "x.pdf"
    SimpleDocTemplate(str(out)).build(story)
    t = text_of(out)
    assert "Tokens & Types" in t and "if a < b: print('?')" in t and "x ≤ y" in t
    assert "&amp;" not in t and "&lt;" not in t


def test_build_notes_pdf_smoke(tmp_path):
    sub = {"num": "1.1", "name": "Tokens?", "explanation": SYMBOLS, "definition": "d", "real_world_example_title": "t",
           "real_world_example_text": "x", "algorithm_steps": ["Step 1: a < b"], "worked_example": "w",
           "complexity": [{"case": "Best", "time": "O(n)", "space": "O(1)", "reason": "r"}],
           "advantages": ["a"], "disadvantages": ["d"], "ptr_points": ["p"]}
    data = g.clean_dict({"unit_title": "Intro", "ltpc": "3 0 0 3",
                         "topics": [{"topic_num": "1", "topic_name": "Tokens & more", "subtopics": [sub],
                                     "video_refs": ["Search: 'x'"]}],
                         "master_points": ["m"],
                         "exam_questions": {"part_a": [{"num": "Q1.", "question": "Define X?"}],
                                            "part_b": [{"num": "Q1.", "question": "Explain?"}],
                                            "part_c": [{"num": "Q1.", "question": "Design?"}]}})
    out = tmp_path / "n.pdf"
    g.build_notes_pdf(str(out), "AL3501", "NLP", "I", "core", data, g.get_styles())
    t = text_of(out)
    assert "Tokens & more" in t and "Define X?" in t and "→" in t


def test_build_verified_notes_pdf_smoke(tmp_path):
    topic = {"topic_num": "1", "topic_name": "WSD & senses", "what_is": SYMBOLS, "why_needed": "w?",
             "simple_definition": "d", "key_features": [{"feature": "f", "explanation": "e", "example": "x"}],
             "types_or_variants": [{"type": "t", "description": "d", "example": "x"}],
             "algorithm": {"name": "Lesk", "input": "i", "output": "o", "steps": ["Step 1: a < b"],
                           "worked_example": "w"},
             "mathematical_foundation": "P(s|w) = c(s,w)/c(w)", "advantages": ["a"], "disadvantages": ["d"],
             "real_world_applications": [{"application": "a", "how": "h", "tool": "NLTK"}],
             "connections": "c", "exam_focus": "e", "tips": "t", "ptr_points": ["p?"]}
    data = g.clean_dict({"unit_title": "Semantics", "ltpc": "3 0 0 3", "topics": [topic], "master_points": ["m"],
                         "exam_questions": {"part_a": [{"num": "Q1.", "question": "Define WSD?"}],
                                            "part_b": [], "part_c": [{"num": "Q1.", "question": "Design?"}]}})
    out = tmp_path / "v.pdf"
    g.build_verified_notes_pdf(str(out), "AL3501", "NLP", "IV", "core", data, g.get_styles())
    t = text_of(out)
    assert "WSD & senses" in t and "P(s|w) = c(s,w)/c(w)" in t and "15-Mark Questions" in t
    assert "16-Mark" not in t


def test_build_beyond_pdf_smoke_long_outcome_wraps(tmp_path):
    unit = {"unit_num": "I", "unit_title": "Intro & basics", "hours": 9, "syllabus_text": "s?",
            "beyond_syllabus": [{"topic": "t", "explanation": "e →"}],
            "exam_focus": [{"priority": "High Priority", "topic": "t", "note": "n"}]}
    long_outcome = "Apply n‑gram models & smoothing to real corpora and compare results " * 3
    data = g.clean_dict({"course_outcomes": [{"co": "CO1", "outcome": long_outcome}],
                         "co_po_mapping": [{"co": "CO1", "po1": 3, "po2": 2}], "units": [unit],
                         "textbooks": [{"authors": "A", "title": "T", "publisher": "P", "year": "2020",
                                        "coverage": "c"}]})
    out = tmp_path / "b.pdf"
    g.build_beyond_pdf(str(out), "AL3501", "NLP", data, g.get_styles())
    t = text_of(out)
    assert "n-gram models & smoothing" in t and "&amp;" not in t and "Intro & basics" in t
