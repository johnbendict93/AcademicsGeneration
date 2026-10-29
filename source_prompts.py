"""source_prompts.py - P2 of the source-strict app (APP_SOURCE_STRICT_DESIGN.md).

Notes for ONE sub-topic, built only from the passages source_context chose:
  build_notes_prompt(ctx, plan, subject_name, unit_title) -> prompt text (R5, R6)
  check_notes_answer(answer, plan)                        -> NotesCheck (code checks, step 5)
  notes_for_topic(ctx, plan, subject_name, unit_title, ask) -> TopicResult
    ask(prompt) -> answer text is injected (Groq in the app, a fake in tests).

Pure: no network, no DB, writes nothing.

Honest limit (design section 3, 5c): the new-term check is LEXICAL. It catches
invented names, numbers and acronyms; it cannot catch a wrong explanation built
from words that are in the source. The first units are spot-checked by John.
The first word of each sentence is not checked (it is capitalised anyway), so a
new name that STARTS a sentence slips through.

BL-038 (John, 29 Sep): organised layout for students - '### <topic>' then '#### Definition / Explanation / Example /
Algorithm / Steps / Key points', each section ONLY if the passages contain it (a missing one is left out). Heading
rule (closes a hole: heading text was never checked): the title is always '### <syllabus topic>'; section headings
only from SECTIONS (written the one way); any other heading is dropped ('heading not allowed'); a section left with
no text is removed; order / repeats are only logged (NotesCheck.sections / .layout).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import source_context as sc

GROQ_TOKENS_PER_MINUTE = sc.GROQ_TOKENS_PER_MINUTE              # BL-039: one copy, in source_context
NOTES_MAX_COMPLETION_TOKENS = sc.NOTES_MAX_COMPLETION_TOKENS
estimate_tokens = sc.estimate_tokens


class CallTooBig(ValueError):
    """BL-039: one Groq call would need more than the per-minute budget - waiting never helps."""
LEADIN_MAX_WORDS = 12               # BL-033 part 2: longest untagged lead-in ('...:') that may be kept
LOST_LINES_LISTED = 8               # BL-033 part 2: lost lines named in a re-ask (each cut to 80 chars)
LOST_LINE_CHARS = 80
NOT_OK_RETRIES = 2                  # BL-035: a topic that fails the checks may be re-asked twice (was once)
NEW_TERM_LIMIT = 3                   # more new names/numbers/acronyms than this -> re-ask once
SECTIONS = ("Definition", "Explanation", "Example", "Algorithm / Steps", "Key points")    # BL-038, in this order
SECTION_HINTS = ("what it is", "how or why it works", "only a concrete example that is in the passages",
                 "numbered steps, procedure or formula lines, only if the passages give them",
                 "2 to 5 bullets that repeat only what is written above")
HEADING_NOT_ALLOWED = "heading not allowed"

_CITE_RE = re.compile(r"\[\s*(P\d+(?:\s*,\s*P\d+)*)\s*\]")
# BL-033 (data run #8): gpt-oss sometimes writes its tags as 【P22】 (U+3010/U+3011) or fullwidth ［P22］;
# the ids inside are right. Rewritten to [P22] BEFORE every check - the checks themselves are unchanged.
_WIDE_CITE_RE = re.compile(r"[\u3010\uff3b]\s*(P\d+(?:\s*[,\uff0c]\s*P\d+)*)\s*[\u3011\uff3d]")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']*|\d+(?:\.\d+)?|[.?!:;]")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# Plain typography a model may use although the source spells it differently (the PDF font draws
# all of them): hyphen + non-breaking hyphen (gpt-oss), en/em dash, curly quotes, ellipsis, arrow,
# times, bullet, minus, middle dot (BL-034, John 28 Sep: U+00B7 used as a multiply sign adds no fact).
# LaTeX formula code (BL-031, live run #6 printed '\text{cost}', '\times', '(i^{th})'). Measured 28 Sep:
# none of these marks occurs in any of the 58 source files. '$' is NOT a marker (sources are full of money).
_LATEX_RE = re.compile(r"\\[A-Za-z]+|\\[()\[\]]|[\^_]\{")
PLAIN_TYPOGRAPHY = set("\u2010\u2011\u2013\u2014\u2018\u2019\u201c\u201d\u2026\u2192\u00d7\u2022\u2212\u00b7")


def build_notes_prompt(ctx, plan, subject_name, unit_title) -> str:
    if plan.status not in ("FOUND", "PARTIAL") or not plan.passages:
        raise ValueError(f"'{plan.topic}' is not covered by the source - it is a gap, no prompt")
    src = ctx.source
    cover = "covered" if plan.status == "FOUND" else "only partly covered"
    passages = "\n\n".join(f"[{p.pid}] {p.text}" for p in plan.passages)
    return (
        "You are writing study notes for Anna University students.\n"
        f"Subject: {subject_name} ({src.code}). Unit {src.unit}: {unit_title}.\n"
        f"Sub-topic: \"{plan.topic}\" ({cover} in the source).\n"
        f"Source: {src.title} ({src.license}).\n\n"
        "RULES\n"
        "1. Use ONLY the numbered passages below. Do not add facts, numbers, examples, formulas, "
        "names or dates that are not in them. If the passages say little about the sub-topic, write little.\n"
        "2. The passages are lecture transcripts: you may rephrase, reorder and fix grammar.\n"
        "3. Cite: end EVERY paragraph and EVERY bullet with the passage id(s) it came from, "
        "like [P<n>] or [P<n>, P<m>]. Only cite ids given below. Text without a citation is deleted. "
        "This includes formula lines, numbered steps and any line ending with ':'. Put an introduction "
        "inside a tagged sentence instead of a separate line. EVERY step and EVERY formula line ends with its "
        "own tag, e.g. \"1. <first step> [P3]\" then \"2. <next step> [P3]\".\n"
        f"4. Format: Markdown. Start with \"### {plan.topic}\". Then organise the notes under these section "
        "headings, in this order:\n"
        + "".join(f"   #### {n}\n" for n in SECTIONS)
        + "   Write each heading exactly as shown, with nothing after it. What goes in each section:\n"
        + "".join(f"   {n}: {h}\n" for n, h in zip(SECTIONS, SECTION_HINTS))
        + "   Leave a section OUT if the passages do not contain it - never write a definition, example or "
        "algorithm that is not in the passages. Use no other headings. Paragraphs, bullet and numbered lists "
        "only - no tables, no code blocks, no LaTeX, HTML or emoji. Formulas in plain text/Unicode.\n"
        "5. Do not mention the passages, the lecturer or \"this course\" in the notes.\n\n"
        "PASSAGES\n"
        f"{passages}\n"
    )


@dataclass
class Dropped:
    text: str
    reason: str


@dataclass
class NotesCheck:
    markdown: str
    kept_blocks: int
    dropped: list = field(default_factory=list)
    new_terms: list = field(default_factory=list)
    odd_chars: list = field(default_factory=list)
    latex: list = field(default_factory=list)
    kept_leadins: list = field(default_factory=list)
    raw: str = ""                  # BL-037 d: the model's answer exactly as received (re-checked later, Q2)
    sections: list = field(default_factory=list)     # BL-038: section names kept, in the order written
    list_tagged: list = field(default_factory=list)  # BL-042: untagged list items kept by the list-tag rule
    layout: list = field(default_factory=list)       # BL-038: layout notes (title replaced, empty removed, order)

    @property
    def ok(self) -> bool:
        return (self.kept_blocks > 0 and len(self.new_terms) <= NEW_TERM_LIMIT and not self.odd_chars
                and not self.latex)


def _blocks(answer: str):
    """[(text, kind, blank_before)] kind = heading | item | para."""
    out, cur, blank = [], None, False
    for line in answer.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
                cur = None
            blank = True
            continue
        if _HEADING_RE.match(line) or _LIST_RE.match(line):
            if cur:
                out.append(cur)
            kind = "heading" if _HEADING_RE.match(line) else "item"
            cur = [line.rstrip(), kind, blank or not out]
            if kind == "heading":
                out.append(cur)
                cur = None
        elif cur:
            cur[0] += "\n" + line.rstrip()
        else:
            cur = [line.rstrip(), "para", True]
        blank = False
    if cur:
        out.append(cur)
    return [tuple(b) for b in out]


def _allowed(plan):
    toks, nums = set(), set()
    for p in plan.passages:
        toks.update(sc.tokenize(p.text))
        nums.update(_NUMBER_RE.findall(p.text))
    toks.update(sc.key_terms(plan.topic))
    return toks, nums


def _new_terms(text, toks, nums):
    text = _CITE_RE.sub(" ", text)
    text = _LIST_RE.sub("", text)
    found, start = [], True
    for m in _TOKEN_RE.finditer(text):
        t = m.group(0)
        if t in ".?!:;":
            start = True
            continue
        was_start, start = start, False
        if t[0].isdigit():
            if t not in nums:
                found.append(t)
            continue
        if len(t) < 2 or not t[0].isupper():
            continue
        acronym = sum(ch.isupper() for ch in t) >= 2
        if was_start and not acronym:
            continue
        if sc.normalize(t) not in toks:
            found.append(t)
    return found


def _passage_chars(plan):
    return set("".join(p.text for p in plan.passages))


def _odd_chars(text, passage_chars):
    """Characters that are in none of the sent passages and are not plain ASCII / typography
    (e.g. U+FFFD, an invented symbol). R5: nothing added. Live run #2 printed '-\ufffd'."""
    out = []
    for ch in _CITE_RE.sub(" ", str(text)):
        if ch.isspace() or " " <= ch <= "~" or ch in PLAIN_TYPOGRAPHY or ch in passage_chars:
            continue
        if ch not in out:
            out.append(ch)
    return out


def _passage_text(plan):
    return "\n".join(p.text for p in plan.passages)


def _latex_marks(text, passage_text=""):
    """LaTeX formula code (\\word, \\( \\) \\[ \\], ^{ _{) that is not in the sent passages (BL-031).
    Every character is plain ASCII, so _odd_chars cannot see it; the PDF would print it raw."""
    out = []
    for m in _LATEX_RE.findall(_CITE_RE.sub(" ", str(text))):
        if m not in passage_text and m not in out:
            out.append(m)
    return out


def describe_chars(chars):
    return ", ".join(f"'{c}' (U+{ord(c):04X})" for c in chars)


def normalize_tags(text) -> str:
    """【P1】 / 【P1, P2】 / ［P1］ -> [P1] / [P1, P2] (BL-033). Anything else is left exactly as it is."""
    return _WIDE_CITE_RE.sub(lambda m: "[" + ", ".join(p.strip() for p in re.split(r"[,\uff0c]", m.group(1))) + "]",
                             str(text))


def _leadin_ok(text, toks, nums, pchars, ptext) -> bool:
    """BL-033 part 2: the ONE R6 exception - a short, fact-free lead-in ending ':' (no digits, no new terms,
    no odd chars, no LaTeX). check_notes_answer also demands that the very next block is a KEPT bullet."""
    t = text.strip()
    return (t.endswith(":") and "\n" not in t and len(t.split()) <= LEADIN_MAX_WORDS
            and not any(ch.isdigit() for ch in t) and not _new_terms(t, toks, nums)
            and not _odd_chars(t, pchars) and not _latex_marks(t, ptext))


_SECTION_KEYS = {n.lower(): n for n in SECTIONS}
_SECTION_KEYS.update({"examples": "Example", "algorithm": "Algorithm / Steps", "steps": "Algorithm / Steps",
                      "algorithm / step": "Algorithm / Steps", "algorithms / steps": "Algorithm / Steps"})


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


def section_name(heading_line):
    """BL-038: '#### definition:' / '### KEY POINTS' / '#### Algorithm/Steps' -> the SECTIONS name, else None.
    BL-038 c: a section name followed by EXACTLY its own hint from the prompt ('#### Definition - what it is',
    any dash / ':' / brackets) is that section too. Any other extra text -> None (heading not allowed)."""
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", str(heading_line)).strip().strip("*").strip()
    t = re.sub(r"\s*/\s*", " / ", t.rstrip(":").strip())
    name = _SECTION_KEYS.get(" ".join(t.split()).lower())
    if name:
        return name
    words = _words(t)
    for n, h in zip(SECTIONS, SECTION_HINTS):
        head = _words(n)
        if words[:len(head)] == head and words[len(head):] == _words(h):
            return n
    return None


def check_notes_answer(answer: str, plan) -> NotesCheck:
    raw = str(answer)
    answer = normalize_tags(answer)
    sent = {p.pid for p in plan.passages}
    toks, nums = _allowed(plan)
    pchars = _passage_chars(plan)
    ptext = _passage_text(plan)
    blocks = _blocks(answer)
    fate = []                              # per block: None = kept, else the drop reason
    title = f"### {plan.topic}"
    for i, (text, kind, _b) in enumerate(blocks):
        if kind == "heading":                 # BL-038: title (first, not a section) / a section / not allowed
            fate.append(None if i == 0 or section_name(text) else HEADING_NOT_ALLOWED)
            continue
        cited = [pid.strip() for tag in _CITE_RE.findall(text) for pid in tag.split(",")]
        bad = [c for c in cited if c not in sent]
        fate.append("no citation" if not cited else
                    f"cites passage not sent: {', '.join(bad)}" if bad else None)
    list_tagged = _apply_list_tag(blocks, fate, toks, nums, pchars, ptext)     # BL-042 (John's Q1)
    leadins = set()                        # BL-033 part 2: untagged lead-in kept only before a KEPT bullet
    for i, (text, kind, _b) in enumerate(blocks):
        if (kind == "para" and fate[i] == "no citation" and i + 1 < len(blocks)
                and blocks[i + 1][1] == "item" and fate[i + 1] is None
                and _leadin_ok(text, toks, nums, pchars, ptext)):
            leadins.add(i)
    kept, dropped, new, odd, latex, kept_leadins, layout = [], [], [], [], [], [], []
    for i, (text, kind, blank_before) in enumerate(blocks):
        if i in leadins:
            kept.append((text, kind, blank_before))
            kept_leadins.append(text)
            continue
        if kind == "heading":
            if fate[i]:
                dropped.append(Dropped(text, fate[i]))
                continue
            name = section_name(text)
            if name:
                kept.append((f"#### {name}", kind, blank_before))
            else:                             # i == 0: the title is always the syllabus topic
                if text.strip() != title:
                    layout.append("title replaced by the syllabus topic")
                kept.append((title, kind, blank_before))
            continue
        if fate[i]:
            dropped.append(Dropped(text, fate[i]))
            continue
        kept.append((text, kind, blank_before))
        for t in _new_terms(text, toks, nums):
            if t not in new:
                new.append(t)
        for c in _odd_chars(text, pchars):
            if c not in odd:
                odd.append(c)
        for mk in _latex_marks(text, ptext):
            if mk not in latex:
                latex.append(mk)
    layout += _incomplete_lists(blocks, fate, leadins)
    kept = _renumber(_drop_empty_sections(kept, layout))
    sections = [section_name(t) for t, k, _b in kept if k == "heading" and section_name(t)]
    if sections != sorted(sections, key=SECTIONS.index):
        layout.append("sections out of order")
    for name in dict.fromkeys(n for n in sections if sections.count(n) > 1):
        layout.append(f"section repeated: {name}")
    parts = []
    for i, (text, kind, blank_before) in enumerate(kept):
        if i:
            tight = kind == "item" and kept[i - 1][1] == "item" and not blank_before
            parts.append("\n" if tight else "\n\n")
        parts.append(text)
    body_blocks = sum(1 for _t, k, _b in kept if k != "heading") - len(kept_leadins)
    return NotesCheck(markdown="".join(parts) + ("\n" if parts else ""),
                      kept_blocks=body_blocks, dropped=dropped, new_terms=new, odd_chars=odd,
                      latex=latex, kept_leadins=kept_leadins, raw=raw, sections=sections, layout=layout,
                      list_tagged=[blocks[i][0] for i in sorted(list_tagged)])


def _lists(blocks):
    """BL-042: runs of consecutive list items (blank lines allowed; a paragraph or heading ends the list)."""
    out, cur = [], []
    for i, (_t, kind, _b) in enumerate(blocks):
        if kind == "item":
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _list_item_ok(text, toks, nums, pchars, ptext) -> bool:
    body = _LIST_RE.sub("", _CITE_RE.sub(" ", text), count=1)
    return (not _new_terms(text, toks, nums) and not _odd_chars(text, pchars) and not _latex_marks(text, ptext)
            and all(n in nums for n in _NUMBER_RE.findall(body)))


def _apply_list_tag(blocks, fate, toks, nums, pchars, ptext) -> set:
    """BL-042 (John, 29 Sep, the second narrow R6 exception): an untagged list item is kept when a LATER item of the
    SAME list has a valid tag and the item passes every other check. Changes fate in place; returns kept indexes."""
    kept = set()
    for run in _lists(blocks):
        for pos, i in enumerate(run):
            if fate[i] == "no citation" and any(fate[j] is None and j not in kept for j in run[pos + 1:]) \
                    and _list_item_ok(blocks[i][0], toks, nums, pchars, ptext):
                fate[i] = None
                kept.add(i)
    return kept


def _incomplete_lists(blocks, fate, leadins) -> list:
    notes, section = [], None
    runs = {r[0]: r for r in _lists(blocks)}
    for i, (text, kind, _b) in enumerate(blocks):
        if kind == "heading" and section_name(text):
            section = section_name(text)
        if i in runs:
            lost = sum(1 for j in runs[i] if fate[j] and j not in leadins)
            if lost:
                notes.append(f"steps incomplete: {section or 'a list'} lost {lost} of {len(runs[i])} items")
    return notes


_TOP_NUMBER_RE = re.compile(r"^(\d+)([.)])(\s)")


def _renumber(kept):
    """BL-042: top-level numbered items of each kept list become 1, 2, 3 ... (formatting only)."""
    out, n = [], 0
    for text, kind, b in kept:
        if kind != "item":
            n = 0
        elif _TOP_NUMBER_RE.match(text):
            n += 1
            text = _TOP_NUMBER_RE.sub(lambda m: f"{n}{m.group(2)}{m.group(3)}", text, count=1)
        out.append((text, kind, b))
    return out


def _drop_empty_sections(kept, layout):
    """BL-038: a section heading with no kept text before the next heading (or the end) is removed."""
    out = []
    for i, (text, kind, b) in enumerate(kept):
        name = section_name(text) if kind == "heading" else None
        if name and (i + 1 == len(kept) or kept[i + 1][1] == "heading"):
            layout.append(f"empty section removed: {name}")
            continue
        out.append((text, kind, b))
    return out


@dataclass
class TopicResult:
    topic: str
    status: str            # ok | needs_review | failed | gap
    markdown: str
    checks: list = field(default_factory=list)
    calls: int = 0
    used: int = -1              # BL-033 part 2: index in checks of the answer that was used
    reask_reason: str = ""      # "" | "not ok" | "lost lines" | "not ok, then lost lines" (BL-037 a)
    trimmed: list = field(default_factory=list)   # BL-039: passage ids left out of the plan to fit the budget
    note: str = ""              # BL-039: 're-ask skipped ...' / 'too big for Groq's free limit ...'

    @property
    def raw_answer(self) -> str:
        """BL-037 d: the model answer that was USED (the one markdown came from); '' for a gap."""
        return self.checks[self.used].raw if self.checks else ""


LATEX_RETRY = ("It used LaTeX formula code: {marks}. No LaTeX: write formulas in plain text, "
               "e.g. D(i,j), n × m, i-th.")


def _retry_prompt(prompt, chk):
    lines = ["", "YOUR PREVIOUS ANSWER WAS REJECTED."]
    if chk.new_terms:
        lines.append("It used words/numbers that are NOT in the passages: " + ", ".join(chk.new_terms)
                     + ". Leave them out.")
    if chk.odd_chars:
        lines.append("It used characters that are NOT in the passages: " + describe_chars(chk.odd_chars)
                     + ". Write plain text; use only characters that appear in the passages.")
    if getattr(chk, "latex", None):
        lines.append(LATEX_RETRY.format(marks=", ".join(chk.latex)))
    heads = [d for d in chk.dropped if d.reason == HEADING_NOT_ALLOWED]      # BL-038
    lost = [d for d in chk.dropped if d.reason != HEADING_NOT_ALLOWED]
    if heads:
        lines.append("It used headings that are not allowed (deleted): "
                     + "; ".join(" ".join(str(d.text).split())[:LOST_LINE_CHARS] for d in heads[:LOST_LINES_LISTED])
                     + ". Use only these section headings: " + ", ".join(f"#### {n}" for n in SECTIONS) + ".")
    if lost:
        lines.append(f"{len(lost)} paragraph(s)/bullet(s) had no valid citation and were deleted:")
        for d in lost[:LOST_LINES_LISTED]:
            lines.append("  - " + " ".join(str(d.text).split())[:LOST_LINE_CHARS])
        lines.append("Add the tag of the passage each line came from, or leave the line out if no passage "
                     "supports it.")
        if any(_LIST_RE.match(str(d.text)) for d in lost):                     # BL-042
            lines.append("Some of them were steps or formula lines: put the passage tag at the END of EACH step, "
                         "e.g. \"2. <next step> [P3]\".")
    lines.append("Write the notes again, following every rule.")
    return prompt + "\n".join(lines) + "\n"


def notes_for_topic(ctx, plan, subject_name, unit_title, ask, avoid="") -> TopicResult:
    """avoid (BL-037 d, Q5): topic_store.avoid_block(John's reject notes) - added to THIS topic's prompt
    (and so to its re-asks). It only asks the writer to leave things out; every check below is unchanged.
    BL-039: a call that does not fit the per-minute budget (CallTooBig from the pacer) never stops the run:
    first call -> this topic fails with a clear note; a re-ask -> skipped, the best answer so far is kept."""
    trimmed = list(getattr(plan, "trimmed", []) or [])
    if plan.status not in ("FOUND", "PARTIAL") or not plan.passages:
        return TopicResult(plan.topic, "gap", "")
    prompt = build_notes_prompt(ctx, plan, subject_name, unit_title) + (avoid or "")
    try:
        chk = check_notes_answer(ask(prompt), plan)
    except CallTooBig as e:
        return TopicResult(plan.topic, "failed", "", trimmed=trimmed,
                           note=f"too big for Groq's free limit ({e}) - not written; shorten the reject notes "
                                f"or report it")
    checks, used, reason, skipped = [chk], 0, "", []

    def reask(chk_):
        try:
            return check_notes_answer(ask(_retry_prompt(prompt, chk_)), plan)
        except CallTooBig as e:
            skipped.append(str(e))
            return None
    if not chk.ok:
        reason = "not ok"
        for _try in range(NOT_OK_RETRIES):  # BL-035: up to 2 re-asks, each naming what the last answer broke
            nxt = reask(chk)
            if nxt is None:
                break
            chk = nxt
            checks.append(chk)
            used = len(checks) - 1
            if chk.ok:
                break
        if not skipped and chk.ok and chk.dropped and len(checks) < 1 + NOT_OK_RETRIES:
            # BL-037 a: ok after a re-ask but lost lines -> one 'lost lines' re-ask if a call is left, never worse
            reason = "not ok, then lost lines"
            last = reask(chk)
            if last is not None:
                checks.append(last)
                if last.ok and len(last.dropped) < len(chk.dropped):
                    chk, used = last, len(checks) - 1
    elif chk.dropped:                      # BL-033 part 2: ok but lost lines -> one re-ask, never worse
        reason = "lost lines"
        second = reask(chk)
        if second is not None:
            checks.append(second)
            if second.ok and len(second.dropped) < len(chk.dropped):
                chk, used = second, 1
    if len(checks) == 1:
        reason = ""                        # no re-ask was actually sent
    if chk.ok:
        status = "ok"
    elif chk.kept_blocks:
        status = "needs_review"
    else:
        status = "failed"
    note = f"re-ask skipped: too big for Groq's free limit ({skipped[0]})" if skipped else ""
    return TopicResult(plan.topic, status, chk.markdown if status != "failed" else "",
                       checks=checks, calls=len(checks), used=used, reask_reason=reason,
                       trimmed=trimmed, note=note)
