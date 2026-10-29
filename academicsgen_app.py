"""
AcademicsGen — Anna University study material generator (regulation-aware: R-2021; R-2025 planned, BL-014)
Phase 2: Neon PostgreSQL + GitHub (private repo) storage + Groq (openai/gpt-oss-120b, free tier)
Entry point: academicsgen_app.py (NEVER app.py)
"""

import io
import os
import json
import datetime
from pathlib import Path
import time
import base64
import hashlib
import urllib.parse
import logging
import requests
import streamlit as st
import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool

import keep_awake
import pdf_render
import source_app
import source_context
import source_review
import source_runner
import topic_hits
import topic_review
import topic_store
from source_context import SourceRefused
from source_pdf import LicenceError, NothingToPublish
from qa_format import (QAValidationError, UnsupportedRegulationError, display_spec, get_pattern,
                       normalize_regulation, parse_and_validate_qa, qa_instructions,
                       response_format)

log = logging.getLogger("academicsgen")

# ── Brand constants ───────────────────────────────────────────────────────────
DARK_BG     = "#0D0D0D"
CARD_BG     = "#1A1A1A"
BORDER      = "#2A2A2A"
SILVER      = "#C0C0C0"
SILVER_DIM  = "#777777"
WHITE       = "#F0F0F0"

VARIANTS    = source_app.VARIANTS          # D1: ["Notes", "QA"] (VerifiedNotes merged into Notes)

# ── Environment verification ──────────────────────────────────────────────────
REQUIRED_SECRETS = [
    "NEON_HOST", "NEON_DATABASE", "NEON_USER", "NEON_PASSWORD",
    "GITHUB_PDF_TOKEN", "GITHUB_PDF_REPO",
    "SUBJECTS_JSON_PATH", "GROQ_API_KEY",
]

def verify_secrets():
    missing = [k for k in REQUIRED_SECRETS if not st.secrets.get(k)]
    if missing:
        st.error(f"Missing secrets: {', '.join(missing)}")
        st.stop()

# ── DB connection pool ────────────────────────────────────────────────────────
@st.cache_resource
def get_db_pool():
    return pg_pool.SimpleConnectionPool(
        minconn=1, maxconn=5,
        host=st.secrets["NEON_HOST"],
        dbname=st.secrets["NEON_DATABASE"],
        user=st.secrets["NEON_USER"],
        password=st.secrets["NEON_PASSWORD"],
        sslmode="require",
    )

# Neon's free tier drops idle connections (~5 min) — e.g. while a unit is generated with Groq
# (6-10 min). A dead connection is thrown away and the call is retried ONCE on a fresh one.
# Other errors (bad SQL, constraint) are not retried. The only db_execute is an idempotent upsert.
_CONN_LOST = (psycopg2.OperationalError, psycopg2.InterfaceError)


def _safe_rollback(conn):
    if not conn.closed:
        try:
            conn.rollback()
        except Exception:
            pass


def _run_db(work):
    pool = get_db_pool()
    for attempt in (1, 2):
        conn = pool.getconn()
        for _ in range(5):                       # pooled connection already known dead -> fresh one
            if not conn.closed:
                break
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        try:
            result = work(conn)
        except _CONN_LOST:
            lost = bool(conn.closed)
            _safe_rollback(conn)
            pool.putconn(conn, close=lost)
            if lost and attempt == 1:
                continue                         # connection dropped by the server -> retry once
            raise
        except Exception:
            _safe_rollback(conn)
            pool.putconn(conn)
            raise
        pool.putconn(conn)
        return result


def db_query(sql, params=None, fetchone=False):
    def work(conn):
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchone() if fetchone else cur.fetchall()
    return _run_db(work)

def db_execute(sql, params=None):
    """Runs one statement + commit; returns the number of rows it changed (BL-029 step c)."""
    def work(conn):
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            n = cur.rowcount
        conn.commit()
        return n
    return _run_db(work)

# ── GitHub storage (BL-011) ───────────────────────────────────────────────────
# Files live in the PRIVATE repo st.secrets["GITHUB_PDF_REPO"], key layout
# <dept>/<subject>/<file>. Every generated_pdfs row has storage_bucket = "github".
GITHUB_API     = "https://api.github.com"
STORAGE_GITHUB = "github"

def _gh_headers(raw=False):
    return {
        "Authorization": f"Bearer {st.secrets['GITHUB_PDF_TOKEN']}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _gh_url(storage_key):
    repo = st.secrets["GITHUB_PDF_REPO"]
    return f"{GITHUB_API}/repos/{repo}/contents/{urllib.parse.quote(storage_key, safe='/')}"

def _gh_error(r):
    try:
        msg = r.json().get("message", "")
    except ValueError:
        msg = r.text
    return f"GitHub {r.status_code}: {str(msg)[:200]}"

def _git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

def _gh_existing_sha(storage_key):
    r = requests.get(_gh_url(storage_key), headers=_gh_headers(), timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(_gh_error(r))
    return r.json().get("sha")

def upload_to_github(pdf_buffer, storage_key, retries=3):
    """Upload (or replace) one PDF. Returns (bucket, size_bytes, pdf_bytes).
    Verifies GitHub stored exactly our bytes (git blob sha) before returning,
    so the DB row is only written after a confirmed upload."""
    pdf_bytes = pdf_buffer.read()
    content_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    last_error = ""
    for attempt in range(1, retries + 1):
        sha = _gh_existing_sha(storage_key)
        body = {"message": f"{'Update' if sha else 'Add'} {storage_key}", "content": content_b64}
        if sha:
            body["sha"] = sha
        r = requests.put(_gh_url(storage_key), headers=_gh_headers(), json=body, timeout=60)
        if r.status_code in (200, 201):
            stored_sha = r.json().get("content", {}).get("sha")
            if stored_sha != _git_blob_sha(pdf_bytes):
                raise RuntimeError("GitHub upload hash mismatch — not saving DB row")
            return STORAGE_GITHUB, len(pdf_bytes), pdf_bytes
        last_error = _gh_error(r)
        if r.status_code in (409, 422) and attempt < retries:  # file changed meanwhile -> retry
            time.sleep(attempt)
            continue
        break
    raise RuntimeError(f"GitHub upload failed: {last_error}")

def download_pdf(storage_bucket, storage_key):
    if storage_bucket != STORAGE_GITHUB:
        raise RuntimeError(f"Unknown storage '{storage_bucket}' for {storage_key}")
    r = requests.get(_gh_url(storage_key), headers=_gh_headers(raw=True), timeout=60)
    if r.status_code != 200:
        raise RuntimeError(_gh_error(r))
    return r.content

# ── Check logs (live run #3): why each topic was kept / removed / left out ──────
# One JSON file per generation, written before the PDF is built. No secrets inside.
RUN_LOG_DIR = Path(__file__).resolve().parent / "run_logs"


def run_log_dir():
    """App/run_logs, or ACADEMICSGEN_RUN_LOG_DIR (tests point it at a temp folder)."""
    return Path(os.environ.get("ACADEMICSGEN_RUN_LOG_DIR") or RUN_LOG_DIR)


def save_run_log(report):
    log_dir = run_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"check_{report['subject_code']}_U{report['unit']}_{report['variant']}_{stamp}"
    for n in range(100):                                  # never overwrite
        path = log_dir / (base + (f"_{n}" if n else "") + ".json")
        try:
            with open(path, "x", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            return path
        except FileExistsError:
            continue
    return None


# ── Groq (free tier, no card) ─────────────────────────────────────────────────
# Same model as before (gpt-oss-120b), served by Groq's OpenAI-compatible API.
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_departments():
    return db_query("SELECT id, code, name FROM departments ORDER BY name")

@st.cache_data(ttl=300)
def load_subjects_for_dept(dept_id):
    return db_query(
        """
        SELECT s.id, s.code, s.name, s.regulation
        FROM subjects s
        JOIN subject_departments sd ON sd.subject_code = s.code
        JOIN departments d ON d.code = sd.department_code
        WHERE d.id = %s AND s.status = 'available'
        ORDER BY s.code
        """,
        (dept_id,),
    )

@st.cache_data(ttl=300)
def load_units_for_subject(subject_code):
    return db_query(
        """
        SELECT id, unit_number, title
        FROM units
        WHERE subject_code = %s
        ORDER BY unit_number
        """,
        (subject_code,),
    )

def check_existing_pdf(subject_code, unit_number, variant):
    return db_query(
        """
        SELECT id, storage_key, storage_bucket, source_sha256, prompt_version, generated_at,
               review_status, reviewed_at, review_note
        FROM generated_pdfs
        WHERE subject_code = %s AND unit_number = %s AND variant = %s
        """,
        (subject_code, unit_number, variant),
        fetchone=True,
    )

def upsert_generated_pdf(subject_code, unit_number, variant, storage_key, storage_bucket, file_size_kb,
                         row_values):
    """row_values = source_app.row_values(run): the migration_005 columns."""
    v = row_values
    db_execute(
        """
        INSERT INTO generated_pdfs (subject_code, unit_number, variant, storage_key, storage_bucket,
                                    file_size_kb, source_sha256, prompt_version, topics_covered,
                                    topics_total, gap_topics, needs_review_topics, failed_topics,
                                    generated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
        ON CONFLICT (subject_code, unit_number, variant)
        DO UPDATE SET storage_key         = EXCLUDED.storage_key,
                      storage_bucket      = EXCLUDED.storage_bucket,
                      file_size_kb        = EXCLUDED.file_size_kb,
                      source_sha256       = EXCLUDED.source_sha256,
                      prompt_version      = EXCLUDED.prompt_version,
                      topics_covered      = EXCLUDED.topics_covered,
                      topics_total        = EXCLUDED.topics_total,
                      gap_topics          = EXCLUDED.gap_topics,
                      needs_review_topics = EXCLUDED.needs_review_topics,
                      failed_topics       = EXCLUDED.failed_topics,
                      generated_at        = NOW()
        """,
        (subject_code, unit_number, variant, storage_key, storage_bucket, file_size_kb,
         v["source_sha256"], v["prompt_version"], v["topics_covered"], v["topics_total"],
         v["gap_topics"], v["needs_review_topics"], v["failed_topics"]),
    )


def check_existing_notes_pdf(subject_code, unit_number, variant):
    """BL-037 f: like check_existing_pdf + topic_row_ids (migration 007) - which approved topics the PDF was built from."""
    return db_query(
        """
        SELECT id, storage_key, storage_bucket, source_sha256, prompt_version, generated_at,
               review_status, reviewed_at, review_note, topic_row_ids
        FROM generated_pdfs
        WHERE subject_code = %s AND unit_number = %s AND variant = %s
        """,
        (subject_code, unit_number, variant),
        fetchone=True,
    )


def existing_pdf(subject_code, unit_number, variant):
    return (check_existing_notes_pdf if variant == "Notes" else check_existing_pdf)(subject_code, unit_number, variant)


# BL-037 f: the Notes PDF built from approved topics. Same columns as upsert_generated_pdf + topic_row_ids; the
# 006/007 trigger resets review_status to 'draft' whenever the file or topic_row_ids change (John's final OK).
TOPIC_PDF_UPSERT_SQL = """
INSERT INTO generated_pdfs (subject_code, unit_number, variant, storage_key, storage_bucket,
                            file_size_kb, source_sha256, prompt_version, topics_covered,
                            topics_total, gap_topics, needs_review_topics, failed_topics,
                            topic_row_ids, generated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
ON CONFLICT (subject_code, unit_number, variant)
DO UPDATE SET storage_key         = EXCLUDED.storage_key,
              storage_bucket      = EXCLUDED.storage_bucket,
              file_size_kb        = EXCLUDED.file_size_kb,
              source_sha256       = EXCLUDED.source_sha256,
              prompt_version      = EXCLUDED.prompt_version,
              topics_covered      = EXCLUDED.topics_covered,
              topics_total        = EXCLUDED.topics_total,
              gap_topics          = EXCLUDED.gap_topics,
              needs_review_topics = EXCLUDED.needs_review_topics,
              failed_topics       = EXCLUDED.failed_topics,
              topic_row_ids       = EXCLUDED.topic_row_ids,
              generated_at        = NOW()
"""


def upsert_topic_pdf(subject_code, unit_number, variant, storage_key, storage_bucket, file_size_kb, row_values,
                     topic_row_ids):
    v = row_values
    db_execute(TOPIC_PDF_UPSERT_SQL,
               (subject_code, unit_number, variant, storage_key, storage_bucket, file_size_kb,
                v["source_sha256"], v["prompt_version"], v["topics_covered"], v["topics_total"],
                v["gap_topics"], v["needs_review_topics"], v["failed_topics"], json.dumps(list(topic_row_ids))))


def current_topic_ids(subject_code, unit_number):
    """BL-037 f: ids of the current approved topics when EVERY covered topic is approved, else None.
    BL-041: topics John marked 'not really in the source' do not count (their ids are not in the list)."""
    try:
        view = topic_store.unit_view(load_unit_context(subject_code, unit_number),
                                     load_topic_rows(subject_code, unit_number, "Notes"),
                                     load_gap_marks(subject_code, unit_number))
    except Exception:                                           # noqa: BLE001 - then nothing is current
        return None
    return topic_store.pdf_row_ids(view) if topic_store.pdf_ready(view) else None


def save_topic_row(row):
    """BL-037 d: INSERT one written topic into topic_texts (always a draft - migration_007 trigger); returns its id.
    One statement + commit; a dead Neon connection is retried once (_run_db) - the INSERT was not committed then."""
    def work(conn):
        with conn.cursor() as cur:
            cur.execute(source_app.TOPIC_INSERT_SQL, source_app.topic_insert_params(row))
            rid = cur.fetchone()[0]
        conn.commit()
        return rid
    return _run_db(work)


def _topics_saved_note(n):
    """BL-037 d: say honestly what WAS saved when the PDF was not made."""
    return (f" {n} written topic(s) were saved as drafts for per-topic review; no PDF was saved."
            if n else " Nothing was saved.")


def set_review(params):
    """BL-029: Approve / Reject = ONE update of review_status/reviewed_at/review_note only (the
    migration_006 trigger leaves it alone). Returns rows changed: 0 = the PDF changed meanwhile."""
    return db_execute(source_review.REVIEW_SQL, params)


def load_topic_rows(subject_code, unit_number, variant):
    """BL-037 e: every saved attempt (topic_texts) of this unit + variant; topic_store picks each topic's current one."""
    return db_query(topic_review.TOPIC_ROWS_SQL, (subject_code, unit_number, variant))


def set_topic_review(params):
    """BL-037 e: Approve / Reject ONE topic = one guarded UPDATE of the review columns only.
    Returns rows changed: 0 = the topic changed since the page loaded."""
    return db_execute(topic_review.TOPIC_REVIEW_SQL, params)


def load_gap_marks(subject_code, unit_number):
    """BL-041: every 'not really in the source' mark of this unit (topic_gap_marks, migration_008)."""
    return db_query(topic_review.GAP_MARK_ROWS_SQL, (subject_code, unit_number))


def mark_topic(params):
    """BL-041: ONE guarded INSERT of an active mark. Returns rows added: 0 = already marked (e.g. double click)."""
    return db_execute(topic_review.GAP_MARK_INSERT_SQL, params)


def unmark_topic(params):
    """BL-041: ONE guarded UPDATE (unmarked_at) of exactly the mark John saw. 0 = it was already undone."""
    return db_execute(topic_review.GAP_MARK_UNDO_SQL, params)


def load_unit_context(subject_code, unit_number):
    """BL-037 e: the unit's topics + passages exactly as the writer gets them (re-read every time: ~0.3 s)."""
    return source_context.build_unit_context(subject_code, unit_number)


@st.cache_data(ttl=300)
def load_unit_overview(subject_code, unit_number):
    """Source-strict: is there a verified source for this unit + its coverage table."""
    return source_app.unit_overview(subject_code, unit_number)

# ── Groq content generation ──────────────────────────────────────────────────
# Formatting contract for the free-text (Markdown) variants.
MARKDOWN_RULES = (
    " Format the answer in Markdown: ## for main sections, ### for sub-sections, "
    "bullet or numbered lists, **bold** for key terms, and a Markdown table only when comparing items. "
    "Write formulas in plain text/Unicode (e.g. P(w|c), ≤, →, √). Do NOT use LaTeX, HTML or emoji. "
    "Do not add a title line; the PDF header already shows the subject and unit."
)

def build_prompt(subject_name, subject_code, unit_number, unit_title, variant, regulation):
    reg = normalize_regulation(regulation)
    base = (
        f"You are an expert Anna University {reg} lecturer. "
        f"Subject: {subject_name} ({subject_code}). "
        f"Unit {unit_number}: {unit_title}. "
        f"Regulation: {reg}. "
    )
    if variant == "Notes":
        return base + (
            "Write detailed lecture notes covering all key concepts in this unit. "
            "Include definitions, explanations, examples, and diagrams described in text. "
            "Structure with clear headings and subheadings."
        ) + MARKDOWN_RULES
    elif variant == "QA":
        return base + qa_instructions(regulation)
    else:
        return base + (
            "Write concise verified notes aligned strictly to Anna University exam patterns. "
            "Focus on definitions, important theorems, key points, and frequently asked topics. "
            "Keep it exam-focused and precise."
        ) + MARKDOWN_RULES


class GenerationError(RuntimeError):
    """Generation failed in a way the user should see (not a code bug)."""


# Per-variant request settings. Groq free tier ~8k tokens/min for this model:
# prompt + max_completion_tokens must stay under that.
GEN_SETTINGS = {
    "Notes":         {"max_completion_tokens": 4096},
    "VerifiedNotes": {"max_completion_tokens": 4096},
    "QA":            {"max_completion_tokens": 6500, "reasoning_effort": "low"},   # + response_format per regulation
}

def _groq_chat(prompt, *, max_completion_tokens, reasoning_effort=None,
               response_format=None, attempts=3):
    """One chat completion with retry on 429/5xx/network. Returns message text.
    Refuses truncated answers (finish_reason == 'length') instead of storing them."""
    headers = {"Authorization": f"Bearer {st.secrets['GROQ_API_KEY']}",
               "Content-Type": "application/json"}
    body = {"model": GROQ_MODEL, "temperature": 0.3,
            "max_completion_tokens": max_completion_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    if response_format:
        body["response_format"] = response_format
    last_error = None
    for attempt in range(attempts):
        try:
            r = requests.post(GROQ_URL, headers=headers, json=body, timeout=180)
            if r.status_code == 200:
                choice = r.json()["choices"][0]
                content = (choice["message"].get("content") or "").strip()
                if choice.get("finish_reason") == "length":
                    last_error = "answer was cut off (hit the token limit)"
                elif content:
                    return content
                else:
                    last_error = "empty response"
            else:
                try:
                    msg = r.json().get("error", {}).get("message", "")
                except ValueError:
                    msg = r.text
                last_error = f"HTTP {r.status_code}: {str(msg)[:200]}"
                if r.status_code in (400, 401, 403, 413):   # retrying the same request won't help
                    break
                if r.status_code == 429:                    # free-tier rate limit: wait as told
                    time.sleep(min(float(r.headers.get("retry-after", 10)), 60))
                    continue
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        time.sleep(2 ** attempt)
    raise GenerationError(f"Groq failed after {attempt + 1} attempt(s): {last_error}")

def generate_content(subject_name, subject_code, unit_number, unit_title, variant, regulation):
    """Returns Markdown text (Notes / VerifiedNotes) or a validated QA dict (QA).
    The QA exam pattern comes from the SUBJECT's regulation (R-2021 / R-2025 ...)."""
    if variant == "QA":
        get_pattern(regulation)             # fail fast, before spending a Groq request
    prompt = build_prompt(subject_name, subject_code, unit_number, unit_title, variant, regulation)
    settings = dict(GEN_SETTINGS[variant])
    if variant != "QA":
        return _groq_chat(prompt, **settings)
    settings["response_format"] = response_format(regulation)
    last_problem = None
    for _ in range(3):                      # re-ask if the JSON breaks the contract
        raw = _groq_chat(prompt, **settings)
        try:
            return parse_and_validate_qa(raw, regulation)
        except QAValidationError as e:
            last_problem = str(e)
            log.warning("QA output rejected: %s", last_problem)
    raise GenerationError(f"QA output failed validation 3 times: {last_problem}")

# ── PDF assembly (rendering lives in pdf_render.py) ──────────────────────────
def build_pdf(subject_name, subject_code, dept_name, unit_number, unit_title, variant, content,
              regulation):
    reg = normalize_regulation(regulation)
    missing = set()
    styles = pdf_render.make_styles()
    if variant == "QA":
        flowables = pdf_render.qa_to_flowables(content, display_spec(reg), styles, missing=missing)
    else:
        flowables = pdf_render.markdown_to_flowables(content, styles, missing=missing)
    if missing:
        log.warning("Characters without a glyph (shown as U+FFFD): %s",
                    " ".join(f"U+{ord(c):04X}" for c in sorted(missing)))
    return pdf_render.build_document(
        flowables,
        header_line1=f"{dept_name}  |  {subject_code} — {subject_name}  |  Anna University {reg}",
        header_line2=f"Unit {unit_number}: {unit_title}  |  {variant}",
        footer_left=f"Unit {unit_number}  |  {variant}",
        footer_centre=f"AcademicsGen — Anna University {reg}",
    )

# ── CSS ───────────────────────────────────────────────────────────────────────
def inject_css():
    st.markdown(f"""
    <style>
    /* Force everything dark */
    html, body, .stApp, .main, [data-testid="stAppViewContainer"],
    [data-testid="stAppViewBlockContainer"], .block-container,
    [data-testid="stHeader"], header, [data-testid="stToolbar"] {{
        background-color: {DARK_BG} !important;
        color: {SILVER} !important;
    }}

    /* Streamlit top toolbar */
    [data-testid="stHeader"] {{
        background-color: {DARK_BG} !important;
        border-bottom: 1px solid {BORDER} !important;
    }}
    [data-testid="stToolbar"] * {{
        color: {SILVER} !important;
        fill: {SILVER} !important;
    }}

    /* Sidebar — fully dark */
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] > div > div {{
        background-color: {DARK_BG} !important;
        border-right: 1px solid {BORDER} !important;
    }}
    section[data-testid="stSidebar"] * {{
        color: {SILVER} !important;
    }}
    section[data-testid="stSidebar"] h2 {{
        color: {WHITE} !important;
        font-size: 18px !important;
    }}
    section[data-testid="stSidebar"] p {{
        color: {SILVER_DIM} !important;
        font-size: 12px !important;
    }}
    section[data-testid="stSidebar"] hr {{
        border: none !important;
        border-top: 1px solid {BORDER} !important;
        margin: 0.5rem 0 !important;
    }}

    /* Sidebar selectbox labels */
    section[data-testid="stSidebar"] label {{
        color: {SILVER_DIM} !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 1px !important;
    }}

    /* Dropdown control */
    div[data-baseweb="select"] > div {{
        background-color: {CARD_BG} !important;
        border: 1px solid {BORDER} !important;
        border-radius: 6px !important;
        color: {WHITE} !important;
        cursor: pointer !important;
    }}
    div[data-baseweb="select"] > div:hover {{
        border-color: {SILVER} !important;
    }}
    div[data-baseweb="select"] * {{
        cursor: pointer !important;
        color: {SILVER} !important;
        background-color: transparent !important;
    }}
    div[data-baseweb="select"] svg {{
        fill: {SILVER_DIM} !important;
    }}

    /* Dropdown menu popup */
    ul[data-baseweb="menu"],
    [data-baseweb="popover"] {{
        background-color: {CARD_BG} !important;
        border: 1px solid {BORDER} !important;
    }}
    li[role="option"] {{
        background-color: {CARD_BG} !important;
        color: {SILVER} !important;
    }}
    li[role="option"]:hover {{
        background-color: {BORDER} !important;
        color: {WHITE} !important;
    }}

    /* Generate button */
    section[data-testid="stSidebar"] .stButton > button {{
        background-color: {SILVER} !important;
        color: {DARK_BG} !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 800 !important;
        font-size: 14px !important;
        width: 100% !important;
        padding: 0.65rem 1rem !important;
        cursor: pointer !important;
        letter-spacing: 0.5px !important;
        transition: all 0.2s ease !important;
    }}
    section[data-testid="stSidebar"] .stButton > button:hover {{
        background-color: {WHITE} !important;
        color: {DARK_BG} !important;
    }}

    /* Download button */
    .stDownloadButton > button {{
        background-color: {SILVER} !important;
        color: {DARK_BG} !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 800 !important;
        font-size: 15px !important;
        width: 100% !important;
        padding: 0.7rem 1rem !important;
        cursor: pointer !important;
        transition: all 0.2s ease !important;
        margin-top: 12px !important;
    }}
    .stDownloadButton > button:hover {{
        background-color: {WHITE} !important;
    }}

    /* Metric cards */
    div[data-testid="metric-container"] {{
        background-color: {CARD_BG} !important;
        border: 1px solid {BORDER} !important;
        border-radius: 8px !important;
        padding: 14px 18px !important;
    }}
    div[data-testid="metric-container"] label,
    div[data-testid="metric-container"] [data-testid="stMetricLabel"] * {{
        color: {SILVER_DIM} !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.8px !important;
    }}
    div[data-testid="metric-container"] [data-testid="stMetricValue"] * {{
        color: {WHITE} !important;
        font-size: 20px !important;
        font-weight: 700 !important;
    }}

    /* Alerts */
    div[data-testid="stAlert"] {{
        background-color: {CARD_BG} !important;
        border: 1px solid {BORDER} !important;
        border-left: 3px solid {SILVER} !important;
        border-radius: 8px !important;
        color: {SILVER} !important;
    }}
    div[data-testid="stAlert"] * {{
        color: {SILVER} !important;
    }}

    /* Progress bar */
    div[data-testid="stProgress"] > div {{
        background-color: {BORDER} !important;
        border-radius: 4px !important;
    }}
    div[data-testid="stProgress"] > div > div {{
        background-color: {SILVER} !important;
        border-radius: 4px !important;
    }}
    div[data-testid="stProgress"] p {{
        color: {SILVER_DIM} !important;
        font-size: 12px !important;
    }}

    /* Divider */
    hr {{
        border: none !important;
        border-top: 1px solid {BORDER} !important;
    }}

    /* Spinner */
    .stSpinner * {{
        color: {SILVER} !important;
        border-top-color: {SILVER} !important;
    }}

    /* Scrollbar */
    ::-webkit-scrollbar {{ width: 5px; height: 5px; }}
    ::-webkit-scrollbar-track {{ background: {DARK_BG}; }}
    ::-webkit-scrollbar-thumb {{ background: {BORDER}; border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: {SILVER_DIM}; }}

    /* Remove default Streamlit padding/white gaps */
    .block-container {{ padding-top: 1rem !important; padding-bottom: 1rem !important; }}
    [data-testid="stVerticalBlock"] {{ gap: 0.5rem !important; }}
    </style>
    """, unsafe_allow_html=True)

# ── Main app ──────────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="AcademicsGen", page_icon="🎓", layout="wide")
    inject_css()
    verify_secrets()

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(f"<h2 style='color:{WHITE};margin-bottom:0;'>🎓 AcademicsGen</h2>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{SILVER_DIM};font-size:12px;margin-top:2px;'>Anna University</p>", unsafe_allow_html=True)
        st.markdown("---")

        departments = load_departments()
        if not departments:
            st.error("No departments found in DB.")
            st.stop()
        dept_options = {f"{d['code']} — {d['name']}": d for d in departments}
        dept_label = st.selectbox("Department", list(dept_options.keys()))
        selected_dept = dept_options[dept_label]

        subjects = load_subjects_for_dept(selected_dept["id"])
        if not subjects:
            st.warning("No subjects found for this department.")
            st.stop()
        subj_options = {f"{s['code']} — {s['name']}": s for s in subjects}
        subj_label = st.selectbox("Subject", list(subj_options.keys()))
        selected_subj = subj_options[subj_label]

        units = load_units_for_subject(selected_subj["code"])
        if not units:
            st.info("No units available yet. Coming soon.")
            st.stop()
        overviews = {u["unit_number"]: load_unit_overview(selected_subj["code"], u["unit_number"])
                     for u in units}
        unit_options = {
            f"Unit {u['unit_number']} — {u['title']}"
            + ("" if overviews[u["unit_number"]].available else "  (no verified source yet)"): u
            for u in units}
        unit_label = st.selectbox("Unit", list(unit_options.keys()))
        selected_unit = unit_options[unit_label]
        overview = overviews[selected_unit["unit_number"]]

        variant = st.selectbox("Variant", VARIANTS)

    # ── Main area ─────────────────────────────────────────────────────────────
    st.markdown(f"""
    <div style='background:{CARD_BG};border:1px solid {BORDER};padding:16px 24px;
                border-radius:10px;margin-bottom:20px;'>
        <h2 style='color:{WHITE};margin:0;font-size:22px;letter-spacing:0.3px;'>AcademicsGen</h2>
        <p style='color:{SILVER_DIM};margin:3px 0 0 0;font-size:12px;'>
            Anna University — AI-Powered Study Material Generator
        </p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Department", selected_dept["code"])
    col2.metric("Subject", selected_subj["code"])
    col3.metric("Regulation", normalize_regulation(selected_subj["regulation"]))
    col4.metric("Unit", f"Unit {selected_unit['unit_number']}")
    col5.metric("Variant", variant)

    st.markdown("---")

    tab_students, tab_admin = st.tabs(["🎓 Students", "🔒 Admin"])
    with tab_students:
        students_tab(selected_subj, selected_unit, variant, overview)
    with tab_admin:
        admin_tab(selected_dept, selected_subj, selected_unit, variant, overview)


# ── Tab 1: Students (BL-029, R9) — only approved AND current PDFs, no Generate ──
def students_tab(selected_subj, selected_unit, variant, overview):
    if not overview.available:
        st.info(f"Not available yet — {overview.message}.")
        return
    src = overview.source
    row = existing_pdf(selected_subj["code"], selected_unit["unit_number"], variant)
    topic_ids = (current_topic_ids(selected_subj["code"], selected_unit["unit_number"])
                 if row and row.get("topic_row_ids") is not None else None)
    if not source_app.is_servable(row, src.sha256, source_runner.prompt_version(), topic_ids):
        st.info(f"Not available yet — the {variant} PDF for this unit has not been checked and approved.")
        return
    file_name = row["storage_key"].rsplit("/", 1)[-1]
    st.success("✅ Ready — checked and approved.")
    st.markdown(f"**Built only from:** {src.title} — {src.url}")
    st.caption(f"Licence: {src.license}  |  Covers {overview.covered} of {overview.total} "
               f"syllabus sub-topics of this unit")
    if st.button(f"⬇️ Get {file_name}", key="student_get"):
        try:
            with st.spinner("Fetching the PDF..."):
                pdf_bytes = download_pdf(row["storage_bucket"], row["storage_key"])
        except Exception as e:                                   # noqa: BLE001
            st.error(f"Could not fetch the PDF right now: {e}")
            return
        st.download_button(label=f"⬇️ Download {file_name}", data=pdf_bytes, file_name=file_name,
                           mime="application/pdf", key="student_download")


# ── Tab 2: Admin (BL-029) — password, Generate, review panel ──────────────────
def admin_tab(selected_dept, selected_subj, selected_unit, variant, overview):
    secret = st.secrets.get("ADMIN_PASSWORD")
    if not secret:
        st.warning("Admin is locked: ADMIN_PASSWORD is not set. Add it to "
                   "AcademicsGen\\App\\.streamlit\\secrets.toml and restart the app.")
        return
    typed = st.text_input("Admin password", type="password", key="admin_pw")
    if not typed:
        st.caption("Enter the admin password to generate and review PDFs.")
        return
    if not source_review.password_ok(typed, secret):
        st.error("Wrong password.")
        return
    if variant == "Notes" and overview.available:       # BL-037 e: Notes are written + reviewed topic by topic
        notes_admin(selected_dept, selected_subj, selected_unit, overview)
        return

    generate_clicked = st.button("⚡ Generate PDF", key="generate", disabled=not overview.available)
    if not overview.available:
        st.warning(f"{overview.message}. This unit cannot be generated until a verified "
                   f"source (NPTEL / MIT OCW / OpenStax) is added.")
        return
    src = overview.source
    st.markdown(f"**Built only from:** {src.title} — {src.url}")
    st.caption(f"Licence: {src.license}  |  Covers {overview.covered} of {overview.total} "
               f"syllabus sub-topics of this unit")
    st.dataframe([{"Sub-topic": t, "In the verified source": source_app.status_label(s_)}
                  for t, s_ in overview.topics], hide_index=True, use_container_width=True)
    minutes = max(1, overview.covered)
    st.caption(f"Generating takes about {minutes}-{3 * minutes + 2} minutes (one Groq call per covered "
               f"sub-topic, plus up to two more for a topic whose answer must be fixed; about one per minute "
               f"on the free tier). Keep this tab open.")
    if generate_clicked:
        generate_flow(selected_dept, selected_subj, selected_unit, variant, src)
    st.markdown("---")
    review_panel(selected_subj, selected_unit, variant, src)


def generate_flow(selected_dept, selected_subj, selected_unit, variant, src):
    # ── Generate flow ─────────────────────────────────────────────────────────
    unit_no = selected_unit["unit_number"]
    storage_key = source_app.storage_key(selected_dept["code"], selected_subj["code"], unit_no, variant)
    file_name = storage_key.rsplit("/", 1)[-1]

    existing = check_existing_pdf(selected_subj["code"], unit_no, variant)
    state = source_review.review_state(existing, src.sha256, source_runner.prompt_version())
    if state in ("draft", "approved"):          # same source + same prompts: nothing new to make
        st.info(f"A PDF from this verified source with these prompts already exists ({state.upper()}). "
                f"Nothing was generated — review it below.")
        return

    progress = st.progress(0, text="Starting...")
    awake_ctx = keep_awake.keep_awake()          # live run #4: the PC slept mid-run -> stay awake meanwhile
    if awake_ctx.__enter__():
        st.caption("This PC is kept awake while generating. Closing the lid or pressing Sleep still stops it.")
    saved_logs = []

    def on_run(report):
        path = save_run_log(report)
        if path:
            saved_logs.append(path)
    uploaded = None                 # (storage_key, pdf_bytes) once the PDF is safely in GitHub
    topic_ids = []                  # BL-037 d: topic_texts rows saved so far (Notes)

    def save_topic(row):
        rid = save_topic_row(row)
        topic_ids.append(rid)
        return rid

    def on_progress(e):
        pct = int(85 * e["index"] / max(e["total"], 1))
        progress.progress(pct, text=f"Sub-topic {e['index']}/{e['total']}: {e['topic']} — {e['status']}")

    try:
        out = source_app.generate_unit_pdf(
            variant=variant,
            subject_code=selected_subj["code"],
            subject_name=selected_subj["name"],
            dept_code=selected_dept["code"],
            dept_name=selected_dept["name"],
            unit_number=unit_no,
            unit_title=selected_unit["title"],
            regulation=selected_subj["regulation"],
            groq_chat=_groq_chat,
            progress=on_progress,
            on_run=on_run,
            save_topic=save_topic if variant == "Notes" else None,
        )
        progress.progress(90, text="Uploading to GitHub storage...")
        storage_bucket, file_size_bytes, pdf_bytes = upload_to_github(io.BytesIO(out.pdf_bytes),
                                                                      out.storage_key)
        uploaded = (out.storage_key, pdf_bytes)
        progress.progress(95, text="Saving metadata to Neon PostgreSQL...")
        upsert_generated_pdf(
            subject_code=selected_subj["code"],
            unit_number=unit_no,
            variant=variant,
            storage_key=out.storage_key,
            storage_bucket=storage_bucket,
            file_size_kb=round(file_size_bytes / 1024, 2),
            row_values=out.row_values,
        )
        progress.progress(100, text="Done!")
        run = out.run
        st.success(f"✅ PDF ready — saved as a DRAFT, built only from the verified source "
                   f"({out.row_values['topics_covered']} of {out.row_values['topics_total']} sub-topics). "
                   f"Students cannot get it until you approve it below.")
        left_out = list(run.needs_review) + [t for t in run.failed if t not in run.needs_review]
        if left_out:
            st.warning("Left out — could not be fully checked against the source: " + "; ".join(left_out))
        st.download_button(label=f"⬇️ Download {file_name}", data=pdf_bytes,
                           file_name=file_name, mime="application/pdf")
        if topic_ids:
            st.caption(f"{len(topic_ids)} written topic(s) also saved as drafts for per-topic review.")

    except (SourceRefused, UnsupportedRegulationError, LicenceError) as e:   # expected refusals
        progress.empty()
        st.error(f"Cannot generate: {e}")
    except NothingToPublish as e:
        progress.empty()
        st.warning(f"{e}{_topics_saved_note(len(topic_ids))}")
    except source_runner.RunAborted as e:
        progress.empty()
        st.error(f"Generation stopped — {e}.{_topics_saved_note(len(topic_ids))} {source_runner.abort_hint(e)}")
    except Exception as e:
        progress.empty()
        if uploaded:
            st.error(f"Saving failed: {e}. The PDF was uploaded to GitHub ({uploaded[0]}) but its "
                     f"database row was NOT saved, so the app will not serve it yet. Generating again "
                     f"replaces that file. You can still download this PDF now:")
            st.download_button(label=f"⬇️ Download {file_name}", data=uploaded[1],
                               file_name=file_name, mime="application/pdf")
            if topic_ids:
                st.caption(f"{len(topic_ids)} written topic(s) were saved as drafts for per-topic review.")
        else:
            st.error(f"Generation failed: {e}.{_topics_saved_note(len(topic_ids))}")
        st.exception(e)
    finally:
        awake_ctx.__exit__(None, None, None)      # always let Windows sleep again
    if saved_logs:
        st.caption(f"Check log saved: run_logs/{saved_logs[-1].name} (why each topic was kept, removed or left out).")




def review_panel(selected_subj, selected_unit, variant, src, topic_ids=None):
    """Read the draft, see where the checks flagged something, then Approve / Reject (+ note).
    topic_ids (BL-037 f): current approved topic ids, for a PDF built from topics."""
    pv = source_runner.prompt_version()
    code, unit_no = selected_subj["code"], selected_unit["unit_number"]
    row = existing_pdf(code, unit_no, variant)
    st.markdown(f"#### Review — {code} Unit {unit_no} {variant}")
    status_box = st.empty()
    if source_review.can_decide(row, src.sha256, pv, topic_ids):
        file_name = row["storage_key"].rsplit("/", 1)[-1]
        if st.button(f"⬇️ Get this PDF to read ({file_name})", key="review_get"):
            try:
                pdf_bytes = download_pdf(row["storage_bucket"], row["storage_key"])
                st.download_button(label=f"⬇️ Download {file_name}", data=pdf_bytes, file_name=file_name,
                                   mime="application/pdf", key="review_download")
            except Exception as e:                               # noqa: BLE001
                st.error(f"Could not fetch the PDF right now: {e}")
        if row.get("topic_row_ids") is not None:
            st.caption(f"Built from your {len(row['topic_row_ids'])} approved topics - each was checked and approved "
                       f"one by one above. Read the whole PDF once more before the final OK.")
        else:
            show_check_log(code, unit_no, variant, row)
        note = st.text_area("Note (needed to reject)", key="review_note", max_chars=source_review.NOTE_MAX)
        c1, c2 = st.columns(2)
        approve = c1.button("✅ Approve — students can download it", key="approve")
        reject = c2.button("❌ Reject", key="reject")
        if approve or reject:
            try:
                params = source_review.review_params("approved" if approve else "rejected", note, row,
                                                     src.sha256, pv, topic_ids)
                changed = set_review(params)
            except source_review.ReviewError as e:
                st.error(f"Not saved: {e}")
            except Exception as e:                               # noqa: BLE001
                st.error(f"Saving the review failed: {e}. Nothing changed.")
            else:
                if changed == 1:
                    st.success("Saved.")
                    row = existing_pdf(code, unit_no, variant)
                else:
                    st.error("Not saved: this PDF changed since the page loaded (maybe it was generated "
                             "again). Reload the page and review the new one.")
    state = source_review.review_state(row, src.sha256, pv, topic_ids)
    status_box.info(source_review.state_text(state, row))
    if state == "stale" and row and row.get("topic_row_ids") is not None:
        st.caption("A topic or the source changed since this PDF was built - build it again from the approved "
                   "topics above.")


# ── BL-037 e: Admin per-topic screen (Notes) ──────────────────────────────────
TOPIC_ACTION = "topic_action"          # session_state key: the button John clicked (handled on the next run)


def _queue_topic_action(action):
    st.session_state[TOPIC_ACTION] = action


def _seen(row):
    return {"id": row["id"], "review_status": row["review_status"], "generated_at": row["generated_at"]}


def notes_admin(selected_dept, selected_subj, selected_unit, overview):
    """Main Generate writes every topic that is not approved (no PDF); each topic can be read, approved, rejected
    (note needed; sent to the writer next time) or regenerated on its own. Approved topics are never touched."""
    code, unit_no = selected_subj["code"], selected_unit["unit_number"]
    src = overview.source
    try:
        ctx = load_unit_context(code, unit_no)
        rows = load_topic_rows(code, unit_no, "Notes")
        marks = load_gap_marks(code, unit_no)                   # BL-041
    except SourceRefused as e:
        st.error(f"Cannot use this unit's source: {e}")
        return
    except Exception as e:                                      # noqa: BLE001
        st.error(f"Could not read the saved topics: {e}")
        return

    action = st.session_state.pop(TOPIC_ACTION, None)
    if action:
        run_topic_action(action, ctx, rows, selected_dept, selected_subj, selected_unit, marks)
        rows = load_topic_rows(code, unit_no, "Notes")
        marks = load_gap_marks(code, unit_no)
    view = topic_store.unit_view(ctx, rows, marks)
    topics, _avoid = topic_review.generation_plan(view, rows)

    st.button(topic_review.generate_label(view), key="generate", disabled=not topics,
              on_click=_queue_topic_action, args=({"kind": "generate"},))
    st.markdown(f"**Built only from:** {src.title} — {src.url}")
    st.caption(f"Licence: {src.license}  |  Covers {overview.covered} of {overview.total} "
               f"syllabus sub-topics of this unit")
    st.dataframe([{"Sub-topic": t, "In the verified source": source_app.status_label(s_)}
                  for t, s_ in overview.topics], hide_index=True, use_container_width=True)
    n = max(1, len(topics))
    st.caption(f"Generate writes only the topics that are not approved yet and makes no PDF (the PDF is built "
               f"once every covered topic is approved). About {n}-{3 * n + 2} minutes (one Groq call per covered "
               f"sub-topic, plus up to two more for a topic whose answer must be fixed; about one per minute on "
               f"the free tier). Keep this tab open.")
    st.markdown("---")
    topics_panel(code, unit_no, ctx, view)
    st.markdown("---")
    topic_ids = topic_store.pdf_row_ids(view) if topic_store.pdf_ready(view) else None
    pdf_section(code, unit_no, ctx, view, topic_ids)
    review_panel(selected_subj, selected_unit, "Notes", src, topic_ids=topic_ids)


def run_topic_action(action, ctx, rows, selected_dept, selected_subj, selected_unit, marks=()):
    """Handle the Generate / Approve / Reject / Regenerate / Mark / Undo click BEFORE the page is drawn (so every
    label shows the result), against the rows as they are NOW (not as they were shown)."""
    view = topic_store.unit_view(ctx, rows, marks)
    if action["kind"] == "build_pdf":                      # BL-037 f
        build_topics_pdf_flow(selected_dept, selected_subj, selected_unit, ctx, view)
        return
    if action["kind"] == "generate":
        topics, avoid = topic_review.generation_plan(view, rows)
        if topics:
            topics_generate_flow(selected_subj, selected_unit, topics, avoid)
        else:
            st.info("Nothing to write: every covered topic is approved or waiting for your review.")
        return
    entry = next((e for e in view if e.topic == action["topic"]), None)
    if action["kind"] in ("mark", "unmark"):                # BL-041
        mark_action(action, entry, ctx, selected_subj, selected_unit)
        return
    if action["kind"] == "remove":                          # BL-043
        remove_action(action, entry)
        return
    if action["kind"] == "recheck":                         # BL-042
        plan = next((p for p in ctx.topics if p.topic == action["topic"]), None)
        new = (source_app.rechecked_topic_row(entry, plan, subject_code=selected_subj["code"],
                                              unit_number=selected_unit["unit_number"],
                                              rules_version=source_runner.prompt_version())
               if entry is not None and plan is not None and entry.row is not None
               and entry.row["id"] == action["seen"]["id"] else None)
        if new is None:
            st.error("Not saved: this topic changed since the page loaded, or the re-check no longer helps. "
                     "Look at it again below.")
            return
        try:
            save_topic_row(new)
        except Exception as e:                              # noqa: BLE001
            st.error(f"Saving the re-checked text failed: {e}. Nothing changed.")
            return
        st.success(f"Saved the re-checked text of '{action['topic']}' as a DRAFT - read it below, then approve or "
                   f"reject.")
        return
    if action["kind"] == "regenerate":
        try:
            topics, avoid = topic_review.generation_plan(view, rows, only=action["topic"])
        except ValueError as e:
            st.error(f"Not written again: {e}.")
            return
        topics_generate_flow(selected_subj, selected_unit, topics, avoid)
        return
    note_key = f"tnote_{action['index']}"
    try:
        params = topic_review.review_params(action["kind"], st.session_state.get(note_key, ""), entry,
                                            action["seen"])
        changed = set_topic_review(params)
    except source_review.ReviewError as e:
        st.error(f"Not saved: {e}")
    except Exception as e:                                      # noqa: BLE001
        st.error(f"Saving the review failed: {e}. Nothing changed.")
    else:
        if changed == 1:
            st.success(f"Saved: '{action['topic']}' is now {action['kind'].upper()}.")
            st.session_state[note_key] = ""
        else:
            st.error("Not saved: this topic changed since the page loaded (maybe it was written again or "
                     "reviewed in another tab). Look at it again below.")


def mark_action(action, entry, ctx, selected_subj, selected_unit):
    """BL-041: 'Not really in the source' (note required) / 'Undo this mark'."""
    note_key = f"tmarknote_{action['index']}"
    try:
        if action["kind"] == "mark":
            params = topic_review.mark_params(st.session_state.get(note_key, ""), entry,
                                              subject_code=selected_subj["code"],
                                              unit_number=selected_unit["unit_number"],
                                              source_sha256=ctx.source.sha256)
            changed = mark_topic(params)
        else:
            changed = unmark_topic(topic_review.unmark_params(entry, action.get("mark_id")))
    except source_review.ReviewError as e:
        st.error(f"Not saved: {e}")
        return
    except Exception as e:                                      # noqa: BLE001
        st.error(f"Saving the mark failed: {e}. Nothing changed.")
        return
    if changed != 1:
        st.error("Not saved: this topic changed since the page loaded (maybe it was marked or unmarked in "
                 "another tab). Look at it again below.")
    elif action["kind"] == "mark":
        st.session_state[note_key] = ""
        st.success(f"Saved: '{action['topic']}' is marked NOT REALLY IN THE SOURCE - it is listed as a gap in the "
                   f"PDF and Generate skips it. Build the PDF again once every other covered topic is approved.")
    else:
        st.success(f"Mark undone: '{action['topic']}' is back as it was.")


def remove_action(action, entry):
    """BL-043: save the topic without the ticked lines as a NEW draft (note required; no Groq call)."""
    seen_id = action["seen"]["id"]
    row = entry.row if entry is not None else None
    blocks = topic_store.content_blocks(row["markdown"]) if row is not None and row["id"] == seen_id else []
    texts = [t for i, t in enumerate(blocks) if st.session_state.get(f"trm_{seen_id}_{i}")]
    parts = [{"in_line": t, "text": s} for i, t in enumerate(blocks)            # BL-045: ticked sentences
             for j, s in enumerate(topic_store.block_sentences(t)) if st.session_state.get(f"trs_{seen_id}_{i}_{j}")]
    note_key = f"tremnote_{action['index']}"
    try:
        new = topic_review.removal_row(entry, seen_id, texts, st.session_state.get(note_key, ""), parts=parts)
    except source_review.ReviewError as e:
        st.error(f"Not saved: {e}")
        return
    try:
        save_topic_row(new)
    except Exception as e:                                      # noqa: BLE001
        st.error(f"Saving the trimmed text failed: {e}. Nothing changed.")
        return
    st.session_state[note_key] = ""
    st.success(f"Saved: {len(texts)} line(s) removed from '{action['topic']}'. It is a new DRAFT - read it below, "
               f"then approve it.")


def pdf_section(code, unit_no, ctx, view, topic_ids):
    """BL-037 f: 'Build PDF' only when every covered topic is approved and the stored PDF is not already built
    from exactly these topics."""
    cov = [e for e in view if not topic_store.is_gap(e)]          # BL-041: marked topics are not needed
    approved = sum(1 for e in cov if e.state == topic_store.APPROVED)
    if topic_ids is None:
        st.caption(f"The PDF can be built when every covered topic is approved ({approved} of {len(cov)} approved).")
        return
    row = check_existing_notes_pdf(code, unit_no, "Notes")
    if (row and row.get("topic_row_ids") is not None and list(row["topic_row_ids"]) == list(topic_ids)
            and row.get("source_sha256") == ctx.source.sha256):
        st.caption(f"The PDF is built from these approved topics ({str(row.get('review_status') or 'draft').upper()})."
                   " Review it below.")
        return
    st.button(f"📄 Build PDF from the {len(topic_ids)} approved topics", key="build_pdf",
              on_click=_queue_topic_action, args=({"kind": "build_pdf"},))
    st.caption("Builds the PDF from exactly the texts you approved (no Groq call) and saves it as a DRAFT for your "
               "final OK.")


def build_topics_pdf_flow(selected_dept, selected_subj, selected_unit, ctx, view):
    unit_no = selected_unit["unit_number"]
    uploaded = None
    try:
        with st.spinner("Building the PDF from the approved topics..."):
            out = source_app.build_pdf_from_topics(
                ctx=ctx, view=view, subject_name=selected_subj["name"], subject_code=selected_subj["code"],
                dept_code=selected_dept["code"], dept_name=selected_dept["name"], unit_number=unit_no,
                unit_title=selected_unit["title"], regulation=selected_subj["regulation"])
            bucket, size, pdf_bytes = upload_to_github(io.BytesIO(out.pdf_bytes), out.storage_key)
            uploaded = (out.storage_key, pdf_bytes)
            upsert_topic_pdf(subject_code=selected_subj["code"], unit_number=unit_no, variant="Notes",
                             storage_key=out.storage_key, storage_bucket=bucket,
                             file_size_kb=round(size / 1024, 2), row_values=out.row_values,
                             topic_row_ids=out.topic_row_ids)
    except (ValueError, LicenceError, NothingToPublish) as e:
        st.error(f"Not built: {e}. Nothing was saved.")
        return
    except Exception as e:                                      # noqa: BLE001
        file_name = (uploaded[0] if uploaded else "").rsplit("/", 1)[-1]
        if uploaded:
            st.error(f"Saving failed: {e}. The PDF was uploaded to GitHub ({uploaded[0]}) but its database row was "
                     f"NOT saved, so the app will not serve it. Building again replaces that file.")
            st.download_button(label=f"⬇️ Download {file_name}", data=uploaded[1], file_name=file_name,
                               mime="application/pdf", key="built_download_failed")
        else:
            st.error(f"Building the PDF failed: {e}. Nothing was saved.")
        return
    file_name = out.storage_key.rsplit("/", 1)[-1]
    st.success(f"✅ PDF built from the {len(out.topic_row_ids)} approved topics — saved as a DRAFT. Read it below "
               f"and give the final OK; students get it only after that.")
    st.download_button(label=f"⬇️ Download {file_name}", data=pdf_bytes, file_name=file_name,
                       mime="application/pdf", key="built_download")


def topics_generate_flow(selected_subj, selected_unit, topics, avoid):
    unit_no = selected_unit["unit_number"]
    progress = st.progress(0, text="Starting...")
    awake_ctx = keep_awake.keep_awake()          # live run #4: the PC slept mid-run -> stay awake meanwhile
    if awake_ctx.__enter__():
        st.caption("This PC is kept awake while generating. Closing the lid or pressing Sleep still stops it.")
    saved_logs, topic_ids = [], []

    def on_run(report):
        path = save_run_log(report)
        if path:
            saved_logs.append(path)

    def save_topic(row):
        rid = save_topic_row(row)
        topic_ids.append(rid)
        return rid

    def on_progress(e):
        pct = int(100 * e["index"] / max(e["total"], 1))
        progress.progress(min(pct, 100), text=f"Topic {e['index']}/{e['total']}: {e['topic']} — {e['status']}")

    try:
        out = source_app.generate_topics(
            subject_code=selected_subj["code"], subject_name=selected_subj["name"], unit_number=unit_no,
            unit_title=selected_unit["title"], groq_chat=_groq_chat, topics=topics, avoid=avoid,
            save_topic=save_topic, progress=on_progress, on_run=on_run)
        progress.progress(100, text="Done!")
        run = out.run
        st.success(f"✅ {len(out.topic_row_ids)} topic(s) written and saved as DRAFTS — read them below and "
                   f"approve or reject each one. No PDF is made in this step; the PDF is built once every "
                   f"covered topic is approved.")
        bad = list(run.needs_review) + [t for t in run.failed if t not in run.needs_review]
        if bad:
            st.warning("Did not pass the checks (not usable; Generate writes them again): " + "; ".join(bad))
    except SourceRefused as e:                                  # expected refusal
        progress.empty()
        st.error(f"Cannot generate: {e}")
    except source_runner.RunAborted as e:
        progress.empty()
        st.error(f"Generation stopped — {e}.{_topics_saved_note(len(topic_ids))} {source_runner.abort_hint(e)}")
    except Exception as e:                                      # noqa: BLE001
        progress.empty()
        st.error(f"Generation failed: {e}.{_topics_saved_note(len(topic_ids))}")
        st.exception(e)
    finally:
        awake_ctx.__exit__(None, None, None)      # always let Windows sleep again
    if saved_logs:
        st.caption(f"Check log saved: run_logs/{saved_logs[-1].name} (why each topic was kept or dropped).")


def topics_panel(code, unit_no, ctx, view):
    st.markdown(f"#### Topics — {code} Unit {unit_no} Notes")
    st.caption(topic_review.counts_line(view))
    plans = {p.topic: p for p in ctx.topics}
    aliases, alias_note = topic_hits.load_aliases(code)                # BL-041 b: advice only, never blocks
    if alias_note:
        st.caption(alias_note)
    unit_flags = topic_hits.unit_flags(ctx, aliases)
    hit_flags = {e.topic: unit_flags[e.topic] for e in view
                 if unit_flags.get(e.topic) and e.state not in topic_store.GAP_STATES}
    summary = topic_hits.summary_text(hit_flags)
    if summary:
        st.warning(summary, icon="⚠️")
    for e in view:
        with st.expander(f"{e.index}. {e.topic} — {topic_review.LABELS[e.state]}",
                         expanded=e.state in (topic_store.DRAFT, topic_store.NEEDS_REVIEW)):
            st.markdown(topic_review.state_text(e))
            if hit_flags.get(e.topic):                          # BL-041 b: key word not in the source
                st.warning(hit_flags[e.topic], icon="⚠️")
            if e.old_mark:                                      # BL-041: made for an older source file
                st.caption(topic_review.old_mark_text(e))
            if e.row:
                _topic_details(e, plans.get(e.topic))
            if topic_review.can_approve(e) or topic_review.can_reject(e):
                st.text_area("Note (needed to reject; sent to the writer as 'Do NOT write this')",
                             key=f"tnote_{e.index}", max_chars=source_review.NOTE_MAX)
            offer = (source_app.rechecked_topic_row(e, plans[e.topic], subject_code=code, unit_number=unit_no,
                                                    rules_version=source_runner.prompt_version())
                     if e.row and e.topic in plans else None)
            if offer:                                           # BL-042: today's checks keep more of the saved answer
                st.caption("Today's checks keep more of the saved answer (e.g. steps that were dropped before). "
                           "'Use re-checked text' saves it as a new draft - no Groq call.")
            c1, c2, c3, c4 = st.columns(4)
            if topic_review.can_approve(e):
                c1.button("✅ Approve topic", key=f"tapprove_{e.index}", on_click=_queue_topic_action,
                          args=({"kind": "approved", "topic": e.topic, "index": e.index, "seen": _seen(e.row)},))
            if topic_review.can_reject(e):
                c2.button("❌ Reject topic", key=f"treject_{e.index}", on_click=_queue_topic_action,
                          args=({"kind": "rejected", "topic": e.topic, "index": e.index, "seen": _seen(e.row)},))
            if offer:
                c4.button("✅ Use re-checked text (no Groq call)", key=f"trecheck_{e.index}",
                          on_click=_queue_topic_action,
                          args=({"kind": "recheck", "topic": e.topic, "index": e.index, "seen": _seen(e.row)},))
            if topic_review.can_regenerate(e):
                c3.button("🔁 Regenerate this topic", key=f"tregen_{e.index}", on_click=_queue_topic_action,
                          args=({"kind": "regenerate", "topic": e.topic, "index": e.index},))
            if topic_review.can_remove_lines(e):                # BL-043
                _remove_controls(e)
            _mark_controls(e)


def _remove_controls(e):
    """BL-043: tick lines of a DRAFT / APPROVED topic and remove them (removal only; saved as a new draft).
    A tick box opens it (Streamlit does not allow an expander inside the topic's expander)."""
    if not st.checkbox("✂️ Remove lines", key=f"trmopen_{e.index}"):
        return
    st.caption("Tick the lines to remove - removing only, nothing is added. A section whose lines all go "
               "disappears; steps are renumbered. A line with several sentences also lists them (↳): tick one "
               "to remove just that sentence. Saved as a new DRAFT; then approve it.")
    rid = e.row["id"]
    for i, text in enumerate(topic_store.content_blocks(e.row["markdown"])):
        st.checkbox(text, key=f"trm_{rid}_{i}")
        for j, sent in enumerate(topic_store.block_sentences(text)):            # BL-045
            st.checkbox("↳ " + sent, key=f"trs_{rid}_{i}_{j}")
    st.text_area("Why are these lines removed? (needed; shown only here, never in the PDF)",
                 key=f"tremnote_{e.index}", max_chars=source_review.NOTE_MAX)
    st.button("✂️ Remove ticked lines", key=f"tremove_{e.index}", on_click=_queue_topic_action,
              args=({"kind": "remove", "topic": e.topic, "index": e.index, "seen": _seen(e.row)},))


def _mark_controls(e):
    """BL-041: mark a covered topic 'not really in the source' (note required) or undo John's mark."""
    if topic_review.can_mark(e):
        st.text_area("Why is this topic not really in the source? (needed to mark it; shown only here)",
                     key=f"tmarknote_{e.index}", max_chars=source_review.NOTE_MAX)
        st.button("🚫 Not really in the source", key=f"tmark_{e.index}", on_click=_queue_topic_action,
                  args=({"kind": "mark", "topic": e.topic, "index": e.index},))
    elif topic_review.can_unmark(e):
        st.button("↩️ Undo this mark", key=f"tunmark_{e.index}", on_click=_queue_topic_action,
                  args=({"kind": "unmark", "topic": e.topic, "index": e.index, "mark_id": e.mark["id"]},))


def _topic_details(e, plan):
    row = e.row
    s_ = topic_review.check_summary(row)
    bits = [f"Written {source_review.local_naive(row.get('generated_at')) or row.get('generated_at')}"]
    if s_["calls"]:
        bits.append(f"{s_['calls']} call(s)")
    if s_["reask_reason"]:
        bits.append(f"re-asked: {s_['reask_reason']} (used try {s_['used_try']})")
    if s_["dropped"]:
        bits.append(f"{s_['dropped']} sentence(s) dropped")
    if s_["kept_leadins"]:
        bits.append(f"{s_['kept_leadins']} lead-in(s) kept")
    if s_.get("list_tagged"):
        bits.append(f"{s_['list_tagged']} step(s) kept by the list-tag rule")
    st.caption(" · ".join(bits))
    if s_.get("trimmed"):                                       # BL-039
        st.caption(f"{len(s_['trimmed'])} passage(s) left out to fit Groq's free limit (least relevant): "
                   + ", ".join(s_["trimmed"]))
    if s_.get("note"):
        st.warning(s_["note"])
    if s_.get("sections"):                                      # BL-038 b
        st.caption("Sections: " + ", ".join(s_["sections"]))
    for note in s_.get("layout") or []:                         # BL-042
        if note.startswith("steps incomplete"):
            st.error("Steps incomplete: " + note.split(":", 1)[1].strip()
                     + " - read this section carefully before approving.")
    if row.get("avoid_notes"):
        st.caption("Written with your earlier notes: " + " | ".join(row["avoid_notes"]))
    if topic_review.removed_text(row):                          # BL-043
        st.caption(topic_review.removed_text(row))
    if s_["new_terms"] or s_["odd_chars"] or s_["latex"]:
        st.markdown("**Read these spots first** — flagged by the checks: "
                    + "; ".join(x for x in (
                        "new words: " + ", ".join(s_["new_terms"]) if s_["new_terms"] else "",
                        "odd characters: " + ", ".join(s_["odd_chars"]) if s_["odd_chars"] else "",
                        "formula code: " + ", ".join(s_["latex"]) if s_["latex"] else "") if x))
    st.markdown("**Text** (each [Pn] tag = the source passage that part came from; hidden in the student PDF):")
    st.markdown(row["markdown"])
    if plan is not None and st.checkbox("Show the source passages for this topic", key=f"tsrc_{e.index}"):
        for p in plan.passages:
            st.text(f"[{p.pid}] {p.text}")


def show_check_log(code, unit_no, variant, row):
    found = source_review.latest_check_log(run_log_dir(), code, unit_no, variant, row,
                                           made_at=source_review.local_naive(row.get("generated_at")))
    if not found:
        st.caption("No check log on this PC for this PDF version — read the whole PDF against the source.")
        return
    path, report = found
    s_ = source_review.check_summary(report)
    st.caption(f"Check log: run_logs/{path.name} — {len(s_['in_pdf'])} sub-topics in the PDF.")
    if s_["left_out"]:
        st.warning("Left out (could not be fully checked): " + "; ".join(s_["left_out"]))
    if s_["gaps"]:
        st.caption("Not in the source (listed as gaps): " + "; ".join(s_["gaps"]))
    if s_["flagged"]:
        st.markdown("**Read these spots first** — words or characters the checks flagged:")
        st.dataframe([{"Sub-topic": f["topic"], "Status": f["status"],
                       "New words": ", ".join(f["new_terms"]), "Odd characters": ", ".join(f["odd_chars"]),
                       "Formula code": ", ".join(f.get("latex") or []),
                       "Sentences dropped": f["dropped"]} for f in s_["flagged"]],
                     hide_index=True, use_container_width=True)
    else:
        st.caption("The checks flagged nothing — still read it against the source.")


if __name__ == "__main__":
    main()
