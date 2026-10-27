"""
qa_format.py — structured (JSON-schema) Question & Answer generation contract,
per Anna University regulation.

The QA variant asks Groq for strict JSON (constrained decoding, see
https://console.groq.com/docs/structured-outputs) instead of free text, then
validates it here, so the question counts are enforced by code, not by hope.
Pure module (no Streamlit, no network) — unit-tested offline.

Each subject carries its own regulation (subjects.regulation in Neon, e.g.
"R-2021"). Students are examined under the regulation of their admission batch
(2026-27: 1st/2nd year = R-2025, 3rd/4th year = R-2021), so the pattern is
looked up per subject, never hard-coded.
"""

import json
from collections import namedtuple

Part = namedtuple("Part", "key title count marks answer_guide")

# End-semester theory pattern per regulation. Add a regulation here ONLY from an
# official Anna University source (aucoe.annauniv.edu question-paper pattern).
# This is a study question bank, so each exam slot gets one question; the exam's
# either/or choice in Part B is not reproduced.
QA_PATTERNS = {
    "R-2021": (
        Part("part_a", "Part A — Short Answer Questions", 10, 2,
             "2-4 precise sentences (about 40-70 words)"),
        Part("part_b", "Part B — Descriptive Questions", 5, 13,
             "a complete model answer of about 300-400 words, organised with short Markdown "
             "sub-headings (###) and bullet points; include a worked example where relevant"),
        Part("part_c", "Part C — Application / Design Question", 1, 15,
             "a complete model answer of about 400-500 words that applies the unit's concepts "
             "to a case study, problem or design, with clear steps"),
    ),
    # "R-2025": pattern changed for R-2025 — add from the official document (BL-014).
}


class QAValidationError(ValueError):
    """The model's JSON parsed but broke the contract (counts, empty text...)."""


class UnsupportedRegulationError(KeyError):
    """No verified exam pattern is configured for this regulation."""

    def __str__(self):
        return (f"No exam pattern configured for regulation {self.args[0]!r} yet. "
                f"Configured: {', '.join(sorted(QA_PATTERNS))}.")


def normalize_regulation(reg):
    """'R2021', 'r-2021', 'R 2021', 'Regulation 2021' -> 'R-2021'."""
    digits = "".join(ch for ch in str(reg or "") if ch.isdigit())
    return f"R-{digits}" if len(digits) == 4 else str(reg or "").strip()


def get_pattern(regulation):
    reg = normalize_regulation(regulation)
    if reg not in QA_PATTERNS:
        raise UnsupportedRegulationError(reg)
    return QA_PATTERNS[reg]


def _item_schema():
    return {
        "type": "object",
        "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
        "required": ["question", "answer"],
        "additionalProperties": False,
    }


def response_format(regulation):
    parts = get_pattern(regulation)
    schema = {
        "type": "object",
        "properties": {p.key: {"type": "array", "items": _item_schema()} for p in parts},
        "required": [p.key for p in parts],
        "additionalProperties": False,
    }
    return {"type": "json_schema",
            "json_schema": {"name": "unit_question_bank", "strict": True, "schema": schema}}


def display_spec(regulation):
    """[(key, title, marks), ...] in display order, for the PDF renderer."""
    return [(p.key, p.title, p.marks) for p in get_pattern(regulation)]


def qa_instructions(regulation):
    lines = ["Produce a question bank for this unit as JSON matching the given schema."]
    for p in get_pattern(regulation):
        lines.append(f"- {p.key}: EXACTLY {p.count} question{'s' if p.count != 1 else ''} "
                     f"({p.marks} marks each). Each answer: {p.answer_guide}.")
    lines += [
        "- A small Markdown table is allowed inside an answer only when comparing items.",
        "- Cover the whole unit; no two questions may ask the same thing.",
        "- Do NOT number the questions and do NOT write 'Q:'/'A:' labels (the PDF adds them).",
        "- Write formulas in plain text/Unicode (e.g. P(w|c), ≤, →, √). Do NOT use LaTeX or emoji.",
    ]
    return "\n".join(lines)


def _norm_q(q):
    return " ".join(q.lower().split()).rstrip("?.! ")


def parse_and_validate_qa(raw, regulation):
    """raw: JSON text (or already-decoded dict) from the model.
    Returns a clean dict {part_key: [{"question","answer"}...]} or raises QAValidationError."""
    parts = get_pattern(regulation)
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise QAValidationError(f"response is not valid JSON ({e.msg} at char {e.pos})") from None
    else:
        data = raw
    if not isinstance(data, dict):
        raise QAValidationError("response JSON is not an object")

    clean, seen, problems = {}, set(), []
    for p in parts:
        items = data.get(p.key)
        if not isinstance(items, list):
            problems.append(f"{p.key} missing or not a list")
            continue
        if len(items) != p.count:
            problems.append(f"{p.key} has {len(items)} questions, expected exactly {p.count}")
        out = []
        for i, it in enumerate(items, 1):
            if not isinstance(it, dict):
                problems.append(f"{p.key}[{i}] is not an object")
                continue
            q = str(it.get("question", "")).strip()
            a = str(it.get("answer", "")).strip()
            if not q or not a:
                problems.append(f"{p.key}[{i}] has an empty question or answer")
                continue
            nq = _norm_q(q)
            if nq in seen:
                problems.append(f"{p.key}[{i}] duplicates an earlier question")
            seen.add(nq)
            out.append({"question": q, "answer": a})
        clean[p.key] = out
    if problems:
        raise QAValidationError("; ".join(problems))
    return clean
