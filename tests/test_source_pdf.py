"""P4 of the source-strict app: the student PDF (APP_SOURCE_STRICT_DESIGN.md 5, 6, R4, R7).

Decisions (John, 2026-09-27):
  - a 'needs review' topic is LEFT OUT of the student PDF and listed on page 1;
  - [Pn] chunk tags are hidden in the PDF (the code checks already ran).
pdf_render.py is not changed. Offline: fake Groq + fake clock, real AL3501 files.
"""
import io
import json
import re

import pytest
from pypdf import PdfReader

import source_context as sc
import source_pdf as spdf
import source_runner as sr

SUBJ = dict(subject_name="Natural Language Processing", subject_code="AL3501",
            dept_name="Artificial Intelligence and Data Science", unit_title="Introduction",
            regulation="R-2021")


class Clock:
    t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _sents(text, n):
    return [p for p in re.split(r"(?<=[.?!])\s+", text.strip()) if len(p.split()) >= 6][:n]


def _first_passage(prompt):
    m = re.search(r"^\[(P\d+)\] (.+)$", prompt, re.M)
    return m.group(1), m.group(2)


def _topic(prompt):
    return re.search(r'Sub-topic: "(.+?)"', prompt).group(1)


def _notes_ask(bad=()):
    def ask(prompt):
        pid, text = _first_passage(prompt)
        s = _sents(text, 2)
        if _topic(prompt) in bad:                  # invented names every time -> needs_review
            return f"{s[0]} It was shown by Wagner and Fischer, 1974, BERT, GPT. [{pid}]\n"
        if _topic(prompt) == "Regular Expressions" and "fail" in bad:
            return "Nothing cited.\n"
        return f"{s[0]} [{pid}]\n\n- {s[1]} [{pid}, {pid}]\n"
    return ask


_W = "apple bread cloud dance eagle fruit grape house igloo jelly kite lemon mango night".split()


def _qa_ask(bad=()):
    def ask(prompt, rf):
        pid, text = _first_passage(prompt)
        ans = " ".join(_sents(text, 2))
        topic = _topic(prompt)
        if topic in bad:
            ans += " It was shown by Wagner and Fischer, 1974, BERT, GPT."
        out, k = {}, 0
        for key in rf["json_schema"]["schema"]["required"]:
            n = int(re.search(rf"- {key}: UP TO (\d+)", prompt).group(1))
            out[key] = [{"question": f"Explain {topic} with {_W[2 * i]} {_W[2 * i + 1]}.",
                         "answer": ans, "sources": [pid]} for i in range(k, k + n)]
            k += n
        return json.dumps(out)
    return ask


def _notes_run(unit="I", bad=()):
    c = Clock()
    return sr.run_unit_notes("AL3501", unit, SUBJ["subject_name"], SUBJ["unit_title"],
                             ask=_notes_ask(bad), clock=c.now, sleep=c.sleep)


def _qa_run(unit="I", bad=()):
    c = Clock()
    return sr.run_unit_qa("AL3501", unit, SUBJ["subject_name"], SUBJ["unit_title"], "R-2021",
                          ask=_qa_ask(bad), clock=c.now, sleep=c.sleep)


def _pdf_text(buf):
    data = buf.getvalue() if hasattr(buf, "getvalue") else buf
    assert data[:5] == b"%PDF-"
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def _flat(s):
    return " ".join(s.split())


# ---------- small pieces ----------

def test_strip_citations_only_removes_chunk_tags():
    md = "Edit distance counts steps. [P3]\n- Two strings [P3, P7] compared [P4][P5].\nArray a[i] and ref [1] stay."
    out = spdf.strip_citations(md)
    assert "[P" not in out
    assert "a[i]" in out and "[1]" in out
    assert "counts steps." in out and "Two strings compared." in out


def test_unknown_licence_is_refused():
    src = _notes_run().source
    bad = sc.UnitSource(**{**src.__dict__, "license": "All rights reserved"})
    with pytest.raises(spdf.LicenceError):
        spdf.licence_markdown(bad)


@pytest.mark.parametrize("lic,url,same", [
    ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/", True),
    ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", False),
    ("CC BY-NC-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/", True),
])
def test_licence_section(lic, url, same):
    src = _notes_run().source
    md = spdf.licence_markdown(sc.UnitSource(**{**src.__dict__, "license": lic}))
    assert lic in md and url in md and src.url in md and src.title in md
    assert "nothing added" in md.lower()
    assert ("same licence" in md.lower()) == same


# ---------- Notes ----------

def test_notes_body_has_only_checked_topics_in_order_without_tags():
    run = _notes_run(bad=("Minimum Edit Distance", "fail"))
    body = spdf.notes_body_markdown(run)
    assert "[P" not in body
    assert "Minimum Edit Distance" not in body            # needs_review -> left out
    assert "Wagner" not in body
    assert "Origins and challenges of NLP" not in body    # gap
    ok = [t.topic for t in run.topics if t.status == "ok"]
    pos = [body.find(t) for t in ok]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)


def test_partial_topic_is_labelled():
    run = _notes_run()
    body = spdf.notes_body_markdown(run)
    i = body.find("Finite-State Automata")
    assert i >= 0 and "partly covered in the source" in body[i:i + 200].lower()


def test_coverage_box_lists_gaps_and_left_out():
    run = _notes_run(bad=("Minimum Edit Distance", "fail"))
    md = spdf.coverage_markdown(run)
    assert "Built only from" in md and run.source.title in md and run.source.license in md
    assert "Covers 5 of 8" in md
    gaps = md[md.find("Not in the source"):]
    assert "Origins and challenges of NLP" in gaps
    left = md[md.find("Left out"):]
    assert "Minimum Edit Distance" in left and "Regular Expressions" in left


def test_notes_pdf_end_to_end():
    run = _notes_run(bad=("Minimum Edit Distance",))
    text = _flat(_pdf_text(spdf.build_notes_pdf(run, **SUBJ)))
    assert "Built only from" in text
    assert "CC BY-SA 4.0" in text and "creativecommons.org/licenses/by-sa/4.0" in text
    assert "[P" not in text and "Wagner" not in text
    assert "English Morphology" in text
    assert "Covers 6 of 8" in text


def test_nothing_checked_means_no_pdf():
    c = Clock()
    run = sr.run_unit_notes("AL3501", "I", SUBJ["subject_name"], SUBJ["unit_title"],
                            ask=lambda p: "No citation.\n", clock=c.now, sleep=c.sleep)
    with pytest.raises(spdf.NothingToPublish):
        spdf.build_notes_pdf(run, **SUBJ)


# ---------- QA ----------

def test_qa_full_pattern_pdf_has_no_label_and_right_counts():
    run = _qa_run()
    text = _flat(_pdf_text(spdf.build_qa_pdf(run, **SUBJ)))
    assert "Not the full exam pattern" not in text
    assert text.count("[2 marks]") == 10 and text.count("[13 marks]") == 5 and text.count("[15 marks]") == 1
    assert "Built only from" in text and "CC BY-SA 4.0" in text


def test_qa_needs_review_topic_left_out_before_trimming():
    run = _qa_run(bad=("Minimum Edit Distance",))
    qa = spdf.student_qa(run)
    topics = {it["topic"] for items in qa.parts.values() for it in items}
    assert "Minimum Edit Distance" not in topics
    assert len(qa.parts["part_a"]) == 10                   # other topics filled its slots
    assert len(qa.parts["part_c"]) == 0                    # Part C only came from that topic
    text = _flat(_pdf_text(spdf.build_qa_pdf(run, **SUBJ)))
    assert "Not the full exam pattern" in text and "Wagner" not in text
    assert "Minimum Edit Distance" in text[:text.find("Part A")]   # listed as left out on page 1


def test_qa_nothing_checked_means_no_pdf():
    run = _qa_run(bad=tuple(t.topic for t in sc.build_unit_context("AL3501", "I").topics))
    with pytest.raises(spdf.NothingToPublish):
        spdf.build_qa_pdf(run, **SUBJ)
