"""source_pdf.py - P4 of the source-strict app: the student PDF
(APP_SOURCE_STRICT_DESIGN.md sections 5, 6, 7; rules R4, R7).

  build_notes_pdf(run, ...) -> BytesIO    run = source_runner.UnitNotesRun
  build_qa_pdf(run, ...)    -> BytesIO    run = source_runner.UnitQARun

Page 1 'About this PDF': source, licence, 'Covers X of Y', partly covered topics,
gaps ('Not in the source'), topics left out, QA 'Not the full exam pattern' label.
Body: only topics that passed every check, syllabus order.
Last section 'Source and licence' + licence in every page footer (R7, D4).

Decisions (John, 2026-09-27): a 'needs review' topic is LEFT OUT (listed on page 1;
for QA it is removed BEFORE trimming so good topics fill its slots); [Pn] chunk
tags are hidden in the PDF. pdf_render.py is used as is (not changed).
Pure: builds bytes in memory, writes nothing.
"""
from __future__ import annotations

import re

import pdf_render
import qa_format
import source_context as sc
import source_qa as sq

LICENCES = {   # name -> (deed url, share-alike, non-commercial)
    "CC BY-SA 4.0": ("https://creativecommons.org/licenses/by-sa/4.0/", True, False),
    "CC BY 4.0": ("https://creativecommons.org/licenses/by/4.0/", False, False),
    "CC BY-NC-SA 4.0": ("https://creativecommons.org/licenses/by-nc-sa/4.0/", True, True),
}
_ROMAN_TO_INT = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_CITE_RE = re.compile(r"[^\S\n]*\[\s*P\d+(?:\s*,\s*P\d+)*\s*\]")   # any space but a newline (gpt-oss: U+202F)


class LicenceError(ValueError):
    """Unknown licence: attribution cannot be printed correctly -> no PDF."""


class NothingToPublish(RuntimeError):
    """No topic passed the checks -> no PDF is made or stored."""


def strip_citations(md: str) -> str:
    return _CITE_RE.sub("", md or "")


def _licence(src):
    if src.license not in LICENCES:
        raise LicenceError(f"Unknown licence '{src.license}' for {src.code} Unit {src.unit} - no PDF")
    return LICENCES[src.license]


def _statuses(src):
    """Syllabus sub-topic -> FOUND/PARTIAL/MISSING, recomputed on the verified text."""
    return {r["topic"]: r["status"] for r in sc.unit_topics(src.code, src.unit, src.text)}


def licence_markdown(src) -> str:
    url, share_alike, non_commercial = _licence(src)
    lines = [
        "## Source and licence",
        "",
        f"This PDF is adapted from **{src.title}** ({src.url}), accessed {src.meta.get('access_date', '')}.",
        "",
        f"Source licence: **{src.license}** — {url}",
        "",
        "Changes made by AcademicsGen: the source text was rephrased, reorganised and "
        "grammar-corrected into study material; nothing added.",
    ]
    if share_alike:
        lines += ["", f"This PDF is shared under the same licence ({src.license})."]
    if non_commercial:
        lines += ["", "Not for commercial use."]
    return "\n".join(lines) + "\n"


def _bullets(items):
    return "\n".join(f"- {t}" for t in items)


def coverage_markdown(run, included=None, qa_label=None) -> str:
    """included: topics printed in the body (default: topics with status ok)."""
    src = run.source
    _licence(src)
    status = _statuses(src)
    total = len(status)
    if included is None:
        included = [t.topic for t in run.topics if t.status == "ok"]
    partly = [t for t in included if status.get(t) == "PARTIAL"]
    left_out = list(run.needs_review) + [t for t in run.failed if t not in run.needs_review]
    lines = [
        "## About this PDF",
        "",
        f"Built only from: **{src.title}** — {src.url}",
        "",
        f"Licence: {src.license}. Accessed {src.meta.get('access_date', '')}.",
        "",
        f"Covers {len(included)} of {total} syllabus sub-topics of this unit.",
    ]
    if qa_label:
        lines += ["", f"**{qa_label}**"]
    if partly:
        lines += ["", "**Partly covered in the source:**", "", _bullets(partly)]
    if run.gaps:
        lines += ["", "**Not in the source (not covered in this PDF):**", "", _bullets(run.gaps)]
    if left_out:
        lines += ["", "**Left out — could not be fully checked against the source:**", "",
                  _bullets(left_out)]
    return "\n".join(lines) + "\n"


def notes_body_markdown(run) -> str:
    status = _statuses(run.source)
    parts = []
    for t in run.topics:
        if t.status != "ok":
            continue
        md = strip_citations(t.markdown).strip()
        first, _, rest = md.partition("\n")
        if first.lstrip().startswith("#"):
            head, body = first.strip(), rest.strip()
        else:
            head, body = f"### {t.topic}", md
        note = "\n\n*Partly covered in the source.*" if status.get(t.topic) == "PARTIAL" else ""
        parts.append(f"{head}{note}\n\n{body}")
    return "\n\n".join(parts) + ("\n" if parts else "")


def _unit_int(src):
    return _ROMAN_TO_INT.get(src.unit, src.unit)


def _document(flowables, run, subject_name, subject_code, dept_name, unit_title, regulation, variant):
    src = run.source
    reg = qa_format.normalize_regulation(regulation)
    n = _unit_int(src)
    return pdf_render.build_document(
        flowables,
        header_line1=f"{dept_name}  |  {subject_code} — {subject_name}  |  Anna University {reg}",
        header_line2=f"Unit {n}: {unit_title}  |  {variant}",
        footer_left=f"Unit {n}  |  {variant}",
        footer_centre=f"AcademicsGen — source: {src.source.upper()} — {src.license}",
    )


def build_notes_pdf(run, subject_name, subject_code, dept_name, unit_title, regulation):
    _licence(run.source)
    if not any(t.status == "ok" for t in run.topics):
        raise NothingToPublish("No topic passed the source checks - no PDF made.")
    styles = pdf_render.make_styles()
    md = coverage_markdown(run) + "\n" + notes_body_markdown(run) + "\n" + licence_markdown(run.source)
    flow = pdf_render.markdown_to_flowables(md, styles)
    return _document(flow, run, subject_name, subject_code, dept_name, unit_title, regulation, "Notes")


def student_qa(run, regulation="R-2021"):
    """Re-assemble from topics that passed every check (needs_review/failed removed
    BEFORE trimming, so good topics fill the freed slots)."""
    return sq.assemble_unit_qa([t for t in run.topics if t.status == "ok"], regulation)


def build_qa_pdf(run, subject_name, subject_code, dept_name, unit_title, regulation):
    _licence(run.source)
    qa = student_qa(run, regulation)
    if not any(qa.parts.values()):
        raise NothingToPublish("No question passed the source checks - no PDF made.")
    included = [t.topic for t in run.topics if t.status == "ok"]
    styles = pdf_render.make_styles()
    flow = pdf_render.markdown_to_flowables(coverage_markdown(run, included, qa.label), styles)
    spec = [(k, title, marks) for k, title, marks in qa_format.display_spec(regulation) if qa.parts.get(k)]
    clean = {k: [{"question": strip_citations(it["question"]), "answer": strip_citations(it["answer"])}
                 for it in items] for k, items in qa.parts.items()}
    flow += pdf_render.qa_to_flowables(clean, spec, styles)
    flow += pdf_render.markdown_to_flowables(licence_markdown(run.source), styles)
    return _document(flow, run, subject_name, subject_code, dept_name, unit_title, regulation, "QA")
