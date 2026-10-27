"""Offline tests for pdf_render.py (no network, no Streamlit)."""
import io

import pytest
from pypdf import PdfReader
from reportlab.platypus import Table, ListFlowable, Preformatted

import pdf_render as pr
from qa_format import display_spec


def pdf_text(flowables):
    buf = pr.build_document(flowables, "Header one", "Header two", "left", "centre")
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(buf.read())).pages)


# ── normalisation ──
def test_non_breaking_hyphen_becomes_normal_hyphen():
    assert pr.normalize_text("part‑of‑speech") == "part-of-speech"


def test_symbols_are_kept_not_deleted():
    s = "connected → connect; x ≤ y ≥ z; “quoted”; en–dash; em—dash; √2; ∑; α β λ; 90°"
    assert pr.normalize_text(s) == s


def test_invisible_and_space_variants():
    assert pr.normalize_text("a b c​d﻿") == "a b cd"


def test_unsupported_char_is_visible_and_reported():
    missing = set()
    out = pr.normalize_text("ok \U0001F600", missing)
    assert out == "ok " + pr.REPLACEMENT_CHAR
    assert missing == {"\U0001F600"}


def test_markup_characters_are_escaped():
    fl = pr.markdown_to_flowables("a < b & c > d")
    assert fl[0].text == "a &lt; b &amp; c &gt; d"


# ── Markdown structure ──
BUGGY_TABLE = """|#|Q|A|
|---|---|---|
| 1 | Define tokenization in NLP. | Tokenization breaks raw text into tokens. |
| 2 | Stem vs lemma? | connected, connecting → connect (lemma). |
"""


def test_markdown_table_becomes_real_table():
    fl = pr.markdown_to_flowables(BUGGY_TABLE)
    tables = [f for f in fl if isinstance(f, Table)]
    assert len(tables) == 1
    t = tables[0]
    assert len(t._cellvalues) == 3 and len(t._cellvalues[0]) == 3
    assert t.repeatRows == 1
    assert sum(t._colWidths) == pytest.approx(pr.A4[0] - 4.0 * pr.cm)


def test_regression_no_raw_pipes_in_pdf():
    text = pdf_text(pr.markdown_to_flowables(BUGGY_TABLE))
    assert "|" not in text
    assert "Define tokenization in NLP." in text
    assert "→" in text


def test_ragged_table_rows_are_padded():
    fl = pr.markdown_to_flowables("|a|b|c|\n|-|-|-|\n|1|2|\n")
    t = [f for f in fl if isinstance(f, Table)][0]
    assert all(len(r) == 3 for r in t._cellvalues)


def test_headings_bold_lists_code():
    md = "## Section\n\n### Sub\n\nText **key** and `x=1`.\n\n- a\n  - b\n\n3. three\n4. four\n\n```\ncode line\n```\n"
    fl = pr.markdown_to_flowables(md)
    assert fl[0].style.name == "AGH2" and fl[1].style.name == "AGH3"
    assert "<b>key</b>" in fl[2].text and pr.MONO_FONT in fl[2].text
    lists = [f for f in fl if isinstance(f, ListFlowable)]
    assert len(lists) == 2
    assert any(isinstance(f, Preformatted) for f in fl)


def test_long_code_line_is_wrapped():
    long_line = "x = " + "a_very_long_identifier + " * 20
    fl = pr.markdown_to_flowables(f"```\n{long_line}\n```")
    pre = [f for f in fl if isinstance(f, Preformatted)][0]
    assert len(pre.lines) > 1


def test_no_latex_or_html_injection():
    fl = pr.markdown_to_flowables("<script>x</script>\n\nplain <b>not bold</b>")
    for f in fl:
        assert "<script>" not in getattr(f, "text", "")
    assert "&lt;b&gt;" in fl[-1].text


def test_empty_content_is_refused():
    with pytest.raises(ValueError):
        pr.build_document([], "a", "b", "c", "d")


def test_missing_font_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "FONT_DIR", str(tmp_path))
    monkeypatch.setattr(pr, "_fonts_ready", False)
    monkeypatch.setattr(pr.pdfmetrics, "getRegisteredFontNames", lambda: [])
    with pytest.raises(FileNotFoundError):
        pr.register_fonts()


def test_header_symbols_render():
    buf = pr.build_document(pr.markdown_to_flowables("body"),
                            "AI&DS | AL3501 — Natural Language Processing", "Unit 2: Word‑Level Analysis | QA",
                            "l", "c")
    text = PdfReader(io.BytesIO(buf.read())).pages[0].extract_text()
    assert "AL3501 — Natural Language Processing" in text
    assert "Word-Level Analysis" in text


# ── structured QA ──
def make_qa(a=10, b=5, c=1):
    return {
        "part_c": [{"question": "Design a pipeline.", "answer": "Step 1 → step 2."} for _ in range(c)],
        "part_a": [{"question": f"Define term {i}?", "answer": f"Term {i} → meaning."} for i in range(1, a + 1)],
        "part_b": [{"question": f"Explain topic {i}.", "answer": f"### Idea\n\n- point {i}\n\n|x|y|\n|-|-|\n|1|2|"}
                   for i in range(1, b + 1)],
    }


def test_qa_pdf_numbering_parts_and_marks():
    text = " ".join(pdf_text(pr.qa_to_flowables(make_qa(), display_spec("R-2021"))).split())
    assert "Part A — Short Answer Questions (2 marks each)" in text
    assert "Part B — Descriptive Questions (13 marks each)" in text
    assert "Part C — Application / Design Question (15 marks each)" in text
    assert "16. Design a pipeline. [15 marks]" in text
    assert "1. Define term 1?" in text and "10. Define term 10?" in text
    assert "11. Explain topic 1." in text and "15. Explain topic 5." in text
    assert text.count("Answer:") == 16
    assert "|" not in text and "→" in text


# ── header fitting ──
def test_long_header_shrinks_to_fit():
    text = "Artificial Intelligence and Data Science  |  AL3501 — Natural Language Processing  |  Anna University R-2021"
    out, size = pr.fit_line(text, pr.SANS_BOLD, 10.5, pr.A4[0] - 2.0 * pr.cm)
    assert out == text and size < 10.5
    assert pr.pdfmetrics.stringWidth(out, pr.SANS_BOLD, size) <= pr.A4[0] - 2.0 * pr.cm


def test_absurd_header_is_trimmed_with_ellipsis():
    out, size = pr.fit_line("X" * 500, pr.SANS_BOLD, 10.5, 200)
    assert out.endswith("…") and pr.pdfmetrics.stringWidth(out, pr.SANS_BOLD, size) <= 200


# ── pagination ──
def test_all_heading_styles_keep_with_next():
    st = pr.make_styles()
    for name in ("h1", "h2", "h3", "h4", "part"):
        assert st[name].keepWithNext == 1, name


def test_heading_is_never_last_on_a_page():
    # Regression (AL3501 Unit 4 QA p.3): '### Challenges' was stranded at a page bottom.
    md_parts = []
    for i in range(1, 40):
        md_parts.append(f"### Heading {i}\n\n" + ("Body text sentence. " * (8 + (i * 7) % 23)))
    buf = pr.build_document(pr.markdown_to_flowables("\n\n".join(md_parts)), "h1", "h2", "l", "c")
    pages = [p.extract_text() for p in PdfReader(io.BytesIO(buf.read())).pages]
    assert len(pages) > 3
    for text in pages:
        body = [ln for ln in text.splitlines()
                if ln.strip() and ln.strip() not in ("h1", "h2", "l", "c") and not ln.startswith("Page ")]
        assert not body[-1].startswith("Heading"), f"stranded heading: {body[-1]!r}"
