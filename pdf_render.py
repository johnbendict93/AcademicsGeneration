"""
pdf_render.py — Markdown / structured-QA -> PDF rendering for AcademicsGen.

Pure module (no Streamlit, no network) so it can be unit-tested offline.

Why this exists (2026-09-25):
  * The old renderer treated every line as plain text, so Markdown tables came
    out as raw "|#|Q|A|" pipes.
  * It deleted every non-ASCII character (the built-in Times font cannot draw
    them), which removed arrows, non-breaking hyphens, quotes and math symbols.
This module parses Markdown with markdown-it-py (CommonMark + GFM tables) and
draws with embedded DejaVu fonts (Bitstream Vera licence, see fonts/).
"""

import io
import os
import re
import unicodedata
import logging
from xml.sax.saxutils import escape as _xml_escape

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable,
    ListItem, Preformatted, KeepTogether,
)
from reportlab.platypus.flowables import HRFlowable

log = logging.getLogger(__name__)

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

BODY_FONT = "AG-Serif"
MONO_FONT = "AG-Mono"
SANS_FONT = "AG-Sans"
SANS_BOLD = "AG-Sans-Bold"

_FONT_FILES = {
    "AG-Serif": "DejaVuSerif.ttf",
    "AG-Serif-Bold": "DejaVuSerif-Bold.ttf",
    "AG-Serif-Italic": "DejaVuSerif-Italic.ttf",
    "AG-Serif-BoldItalic": "DejaVuSerif-BoldItalic.ttf",
    "AG-Sans": "DejaVuSans.ttf",
    "AG-Sans-Bold": "DejaVuSans-Bold.ttf",
    "AG-Mono": "DejaVuSansMono.ttf",
}

RL_DARK = colors.HexColor("#0D0D0D")
RL_SILVER = colors.HexColor("#C0C0C0")
RL_WHITE = colors.HexColor("#F0F0F0")
RL_RULE = colors.HexColor("#9A9A9A")
RL_HEAD_BG = colors.HexColor("#E6E6E6")

_fonts_ready = False
_supported_cps = frozenset()


def register_fonts():
    """Register the embedded Unicode fonts once per process (idempotent)."""
    global _fonts_ready, _supported_cps
    if _fonts_ready:
        return
    from reportlab.lib.fonts import addMapping
    for name, fname in _FONT_FILES.items():
        path = os.path.join(FONT_DIR, fname)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"PDF font missing: {path}")
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
    pdfmetrics.registerFontFamily(
        BODY_FONT, normal="AG-Serif", bold="AG-Serif-Bold",
        italic="AG-Serif-Italic", boldItalic="AG-Serif-BoldItalic")
    for bold, italic, face in ((0, 0, "AG-Serif"), (1, 0, "AG-Serif-Bold"),
                               (0, 1, "AG-Serif-Italic"), (1, 1, "AG-Serif-BoldItalic")):
        addMapping(BODY_FONT, bold, italic, face)
    # A character is "supported" only if every face we draw text with has it.
    faces = ("AG-Serif", "AG-Serif-Bold", "AG-Serif-Italic", "AG-Serif-BoldItalic", "AG-Mono")
    cps = None
    for face in faces:
        cmap = set(pdfmetrics.getFont(face).face.charToGlyph.keys())
        cps = cmap if cps is None else (cps & cmap)
    _supported_cps = frozenset(cps)
    _fonts_ready = True


# ── Text normalisation ───────────────────────────────────────────────────────
# Map look-alike / invisible code points to what the reader expects to see.
_CHAR_MAP = {
    "‐": "-", "‑": "-",          # hyphen, NON-BREAKING hyphen (gpt-oss uses it a lot)
    "­": "",                           # soft hyphen
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "⁠": "", "﻿": "",
    "\t": "    ",
}
_CHAR_TRANS = str.maketrans(_CHAR_MAP)
REPLACEMENT_CHAR = "�"


def normalize_text(text, missing=None):
    """NFC-normalise, map invisible/look-alike characters, and replace any code
    point the embedded fonts cannot draw with U+FFFD (visible, never silent).
    `missing` (optional set) collects the replaced characters for logging."""
    register_fonts()
    text = unicodedata.normalize("NFC", text).translate(_CHAR_TRANS)
    out = []
    for ch in text:
        if ch == "\n" or ord(ch) in _supported_cps:
            out.append(ch)
        else:
            if missing is not None:
                missing.add(ch)
            out.append(REPLACEMENT_CHAR)
    return "".join(out)


def esc(text, missing=None):
    """Normalise then escape for ReportLab's Paragraph mini-markup."""
    return _xml_escape(normalize_text(text, missing))


# ── Styles ────────────────────────────────────────────────────────────────────
def make_styles():
    """Headings and part titles use keepWithNext so a heading is never left alone
    at the bottom of a page (its first following block moves with it)."""
    register_fonts()
    body = ParagraphStyle("AGBody", fontName=BODY_FONT, fontSize=11, leading=15,
                          alignment=TA_LEFT, spaceAfter=5)
    return {
        "body": body,
        "h1": ParagraphStyle("AGH1", parent=body, fontName="AG-Serif-Bold", fontSize=15,
                             leading=19, textColor=RL_DARK, spaceBefore=10, spaceAfter=6, keepWithNext=1),
        "h2": ParagraphStyle("AGH2", parent=body, fontName="AG-Serif-Bold", fontSize=13.5,
                             leading=17, textColor=RL_DARK, spaceBefore=9, spaceAfter=5, keepWithNext=1),
        "h3": ParagraphStyle("AGH3", parent=body, fontName="AG-Serif-Bold", fontSize=12,
                             leading=16, textColor=RL_DARK, spaceBefore=7, spaceAfter=4, keepWithNext=1),
        "h4": ParagraphStyle("AGH4", parent=body, fontName="AG-Serif-BoldItalic", fontSize=11,
                             leading=15, spaceBefore=6, spaceAfter=3, keepWithNext=1),
        "quote": ParagraphStyle("AGQuote", parent=body, leftIndent=14,
                                textColor=colors.HexColor("#333333"), fontName="AG-Serif-Italic"),
        "code": ParagraphStyle("AGCode", fontName=MONO_FONT, fontSize=8.8, leading=11.5,
                               backColor=colors.HexColor("#F2F2F2"), borderPadding=4,
                               leftIndent=4, rightIndent=4, spaceBefore=4, spaceAfter=8),
        "cell": ParagraphStyle("AGCell", parent=body, fontSize=9.5, leading=12.5, spaceAfter=0),
        "cell_head": ParagraphStyle("AGCellHead", parent=body, fontName="AG-Serif-Bold",
                                    fontSize=9.5, leading=12.5, spaceAfter=0),
        "question": ParagraphStyle("AGQuestion", parent=body, fontName="AG-Serif-Bold",
                                   spaceBefore=8, spaceAfter=3, leftIndent=22, firstLineIndent=-22),
        "part": ParagraphStyle("AGPart", parent=body, fontName="AG-Serif-Bold", fontSize=14,
                               leading=18, textColor=RL_DARK, spaceBefore=12, spaceAfter=6, keepWithNext=1),
    }


# ── Markdown -> flowables ─────────────────────────────────────────────────────
_md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def _inline(node, missing):
    """Render an inline node's children to Paragraph mini-markup."""
    parts = []
    for ch in node.children or []:
        t = ch.type
        if t == "text":
            parts.append(esc(ch.content, missing))
        elif t == "softbreak":
            parts.append(" ")
        elif t == "hardbreak":
            parts.append("<br/>")
        elif t == "code_inline":
            parts.append(f'<font name="{MONO_FONT}">{esc(ch.content, missing)}</font>')
        elif t == "strong":
            parts.append(f"<b>{_inline(ch, missing)}</b>")
        elif t == "em":
            parts.append(f"<i>{_inline(ch, missing)}</i>")
        elif t == "s":
            parts.append(f"<strike>{_inline(ch, missing)}</strike>")
        elif t == "link":
            href = ch.attrs.get("href", "")
            label = _inline(ch, missing)
            if href.startswith(("http://", "https://")):
                parts.append(f'<a href="{_xml_escape(href, {chr(34): "&quot;"})}" color="blue">{label}</a>')
            else:
                parts.append(label)
        elif t == "image":
            parts.append(esc(ch.content or ch.attrs.get("alt", "") or "", missing))
        elif t in ("html_inline",):
            parts.append(esc(ch.content, missing))
        elif ch.children:
            parts.append(_inline(ch, missing))
        elif ch.content:
            parts.append(esc(ch.content, missing))
    return "".join(parts)


def _node_inline_markup(node, missing):
    """Paragraph-like block (paragraph, heading, th/td): markup of its inline child."""
    return "".join(_inline(c, missing) for c in (node.children or []) if c.type == "inline")


def _table(node, styles, avail_width, missing):
    rows, header_rows = [], 0
    for section in node.children:            # thead / tbody
        for tr in section.children:
            cells = []
            for cell in tr.children:          # th / td
                style = styles["cell_head"] if cell.type == "th" else styles["cell"]
                cells.append(Paragraph(_node_inline_markup(cell, missing) or "&nbsp;", style))
            rows.append(cells)
            if section.type == "thead":
                header_rows += 1
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    for r in rows:                            # ragged rows -> pad
        r.extend(Paragraph("&nbsp;", styles["cell"]) for _ in range(ncols - len(r)))
    # Column widths proportional to content length, clamped so no column starves.
    weights = []
    for c in range(ncols):
        longest = max(len(re.sub(r"<[^>]+>", "", rows[r][c].text or "")) for r in range(len(rows)))
        weights.append(min(max(longest, 6), 60))
    total = float(sum(weights))
    widths = [avail_width * w / total for w in weights]
    t = Table(rows, colWidths=widths, repeatRows=header_rows, hAlign="LEFT")
    ts = [
        ("GRID", (0, 0), (-1, -1), 0.5, RL_RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header_rows:
        ts.append(("BACKGROUND", (0, 0), (-1, header_rows - 1), RL_HEAD_BG))
    t.setStyle(TableStyle(ts))
    return t


def _blocks(nodes, styles, avail_width, missing, depth=0):
    out = []
    for node in nodes:
        t = node.type
        if t == "heading":
            level = min(int(node.tag[1]), 4)
            out.append(Paragraph(_node_inline_markup(node, missing), styles[f"h{level}"]))
        elif t == "paragraph":
            markup = _node_inline_markup(node, missing)
            if markup.strip():
                out.append(Paragraph(markup, styles["body"]))
        elif t in ("bullet_list", "ordered_list"):
            items = []
            for li in node.children:
                inner = _blocks(li.children, styles, avail_width - 18, missing, depth + 1)
                if inner:
                    items.append(ListItem(inner if len(inner) > 1 else inner[0]))
            if not items:
                continue
            if t == "bullet_list":
                lf = ListFlowable(items, bulletType="bullet", start="•" if depth % 2 == 0 else "–",
                                  leftIndent=16, bulletFontName=BODY_FONT, bulletFontSize=10)
            else:
                start = int(node.attrs.get("start", 1) or 1)
                lf = ListFlowable(items, bulletType="1", start=start, leftIndent=18,
                                  bulletFontName=BODY_FONT, bulletFontSize=10, bulletFormat="%s.")
            out.append(lf)
        elif t == "blockquote":
            for fl in _blocks(node.children, styles, avail_width - 14, missing, depth):
                if isinstance(fl, Paragraph):
                    out.append(Paragraph(fl.text, styles["quote"]))
                else:
                    out.append(fl)
        elif t in ("fence", "code_block"):
            code = normalize_text(node.content.rstrip("\n"), missing)
            max_chars = max(20, int(avail_width / (styles["code"].fontSize * 0.61)))
            out.append(Preformatted(code, styles["code"], maxLineLength=max_chars, newLineChars=""))
        elif t == "hr":
            out.append(HRFlowable(width="100%", thickness=0.6, color=RL_RULE,
                                  spaceBefore=4, spaceAfter=6))
        elif t == "table":
            tbl = _table(node, styles, avail_width, missing)
            if tbl is not None:
                out.append(tbl)
                out.append(Spacer(1, 0.25 * cm))
        elif t == "html_block":
            out.append(Paragraph(esc(node.content, missing), styles["body"]))
        elif node.children:
            out.extend(_blocks(node.children, styles, avail_width, missing, depth))
    return out


def markdown_to_flowables(md_text, styles=None, avail_width=None, missing=None):
    styles = styles or make_styles()
    avail_width = avail_width or (A4[0] - 4.0 * cm)
    tree = SyntaxTreeNode(_md.parse(md_text or ""))
    return _blocks(tree.children, styles, avail_width, missing if missing is not None else set())


# ── Structured QA -> flowables ────────────────────────────────────────────────
def qa_to_flowables(qa, spec, styles=None, avail_width=None, missing=None):
    """qa = {"part_a": [{"question","answer"}...], "part_b": [...]} (already validated).
    spec = list of (key, title, marks) in display order."""
    styles = styles or make_styles()
    avail_width = avail_width or (A4[0] - 4.0 * cm)
    missing = missing if missing is not None else set()
    out = []
    n = 0
    for key, title, marks in spec:
        out.append(Paragraph(esc(f"{title} ({marks} marks each)", missing), styles["part"]))
        out.append(HRFlowable(width="100%", thickness=0.6, color=RL_RULE, spaceAfter=4))
        for item in qa[key]:
            n += 1
            q = Paragraph(f"{n}.&nbsp;&nbsp;{_inline_only(item['question'], missing)}"
                          f'&nbsp;&nbsp;<font name="AG-Serif-Italic" size="9">[{marks} marks]</font>',
                          styles["question"])
            answer = markdown_to_flowables(item["answer"], styles, avail_width - 22, missing)
            label = Paragraph("<b>Answer:</b>", styles["body"])
            head = [q, label] + answer[:1]
            out.append(KeepTogether(head))       # never strand a question at a page bottom
            out.extend(answer[1:])
    return out


def _inline_only(text, missing):
    """Question text: allow inline Markdown (bold/code) but flatten any blocks."""
    tree = SyntaxTreeNode(_md.parse(text or ""))
    parts = []
    for block in tree.children:
        m = _node_inline_markup(block, missing) if block.type in ("paragraph", "heading") else esc(block.content or "", missing)
        if m:
            parts.append(m)
    return " ".join(parts) if parts else esc(text or "", missing)


# ── Document assembly ─────────────────────────────────────────────────────────
def fit_line(text, font, size, max_width, min_size=6.5):
    """Largest font size (<= size, >= min_size) at which text fits max_width;
    if it still does not fit, trim with an ellipsis. Returns (text, size)."""
    register_fonts()
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size = round(size - 0.25, 2)
    if pdfmetrics.stringWidth(text, font, size) > max_width:
        while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
            text = text[:-1]
        text = text.rstrip() + "…"
    return text, size


def build_document(flowables, header_line1, header_line2, footer_left, footer_centre):
    """Lay out flowables on A4 with the AcademicsGen header/footer. Returns BytesIO."""
    register_fonts()
    buffer = io.BytesIO()
    h1, h2 = normalize_text(header_line1), normalize_text(header_line2)
    fl, fc = normalize_text(footer_left), normalize_text(footer_centre)

    def header_footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(RL_DARK)
        canvas.rect(0, A4[1] - 2.2 * cm, A4[0], 2.2 * cm, fill=True, stroke=False)
        max_w = A4[0] - 2.0 * cm
        canvas.setFillColor(RL_WHITE)
        t1, s1 = fit_line(h1, SANS_BOLD, 10.5, max_w)
        canvas.setFont(SANS_BOLD, s1)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 1.1 * cm, t1)
        t2, s2 = fit_line(h2, SANS_FONT, 9, max_w)
        canvas.setFont(SANS_FONT, s2)
        canvas.setFillColor(RL_SILVER)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 1.75 * cm, t2)
        canvas.setStrokeColor(RL_SILVER)
        canvas.setLineWidth(1.5)
        canvas.line(1.5 * cm, 1.4 * cm, A4[0] - 1.5 * cm, 1.4 * cm)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.setFont(SANS_FONT, 8)
        canvas.drawString(1.5 * cm, 0.8 * cm, fl)
        canvas.drawCentredString(A4[0] / 2, 0.8 * cm, fc)
        canvas.drawRightString(A4[0] - 1.5 * cm, 0.8 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2.8 * cm, bottomMargin=2.0 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=h2, author="AcademicsGen")
    if not flowables:
        raise ValueError("Nothing to render: the generated content is empty.")
    doc.build(list(flowables), onFirstPage=header_footer, onLaterPages=header_footer)
    buffer.seek(0)
    return buffer
