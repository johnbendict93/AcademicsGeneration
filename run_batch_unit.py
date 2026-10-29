"""
run_batch_unit.py — Production CLI runner for AcademicsGen NPTEL ingestion.
Directly invokes source_app.generate_unit_pdf and connects to Neon and Groq.
"""
import sys
import time
import requests
from pathlib import Path

# Keep imports strictly local to AcademicsGen/App
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    import tomllib
except ImportError:
    import tomli as tomllib

import psycopg2
import source_app
import topic_store as ts

SECRETS_PATH = APP_DIR / ".streamlit" / "secrets.toml"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"


def load_secrets():
    if not SECRETS_PATH.exists():
        raise FileNotFoundError(f"Missing {SECRETS_PATH}")
    return tomllib.loads(SECRETS_PATH.read_text(encoding="utf-8"))


def get_neon_conn():
    sec = load_secrets()
    return psycopg2.connect(
        host=sec["NEON_HOST"],
        dbname=sec["NEON_DATABASE"],
        user=sec["NEON_USER"],
        password=sec["NEON_PASSWORD"],
        port=sec.get("NEON_PORT", 5432),
        sslmode=sec.get("NEON_SSLMODE", "require"),
        connect_timeout=20,
    )


def make_groq_chat():
    sec = load_secrets()
    api_key = sec.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY missing from secrets.toml")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def _groq_chat(prompt, *, max_completion_tokens=4096, reasoning_effort="low",
                   response_format=None, attempts=4):
        body = {
            "model": GROQ_MODEL,
            "temperature": 0.3,
            "max_completion_tokens": max_completion_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if response_format:
            body["response_format"] = response_format

        last_error = None
        for attempt in range(attempts):
            try:
                r = requests.post(GROQ_URL, headers=headers, json=body, timeout=180)
                if r.status_code == 200:
                    data = r.json()
                    choice = data["choices"][0]
                    content = (choice["message"].get("content") or "").strip()
                    if choice.get("finish_reason") == "length":
                        last_error = "answer was cut off (hit token limit)"
                    elif content:
                        return content
                    else:
                        last_error = "empty response"
                else:
                    try:
                        msg = r.json().get("error", {}).get("message", "")
                    except Exception:
                        msg = r.text
                    last_error = f"HTTP {r.status_code}: {str(msg)[:200]}"
                    if r.status_code in (400, 401, 403, 413):
                        break
                    if r.status_code == 429:
                        wait = min(float(r.headers.get("retry-after", 10)), 60)
                        print(f"   [Rate Limit 429] Waiting {wait:.1f}s...")
                        time.sleep(wait)
                        continue
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"

            sleep_time = 2 ** attempt
            time.sleep(sleep_time)

        raise RuntimeError(f"Groq API call failed: {last_error}")

    return _groq_chat


def save_topic_to_neon(row):
    conn = get_neon_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(source_app.TOPIC_INSERT_SQL, source_app.topic_insert_params(row))
            rid = cur.fetchone()[0]
        conn.commit()
        return rid
    finally:
        conn.close()

def run_unit(subject_code: str, unit_no: int):
    print("=" * 65)
    print(f"Starting Headless NPTEL Ingestion: {subject_code} Unit {unit_no}")
    print("=" * 65)

    unit_titles = {
        1: "Introduction & Port-to-Port Communication",
        2: "Transport Layer",
        3: "Network Layer",
        4: "Routing & Forwarding",
        5: "Data Link Layer & Physical Layer",
    }

    groq_chat = make_groq_chat()

    def on_progress(e):
        idx = e.get("index", 0)
        tot = e.get("total", 0)
        topic = e.get("topic", "")
        status = e.get("status", "")
        print(f"[{idx}/{tot}] {topic} -> {status}")

    out = source_app.generate_unit_pdf(
        variant="Notes",
        subject_code=subject_code,
        subject_name="Computer Networks",
        dept_code="CSE",
        dept_name="Computer Science and Engineering",
        unit_number=unit_no,
        unit_title=unit_titles.get(unit_no, f"Unit {unit_no}"),
        regulation="2021",
        groq_chat=groq_chat,
        progress=on_progress,
        save_topic=save_topic_to_neon,
    )

    print("\n" + "=" * 65)
    print(f"Successfully finished ingestion for {subject_code} Unit {unit_no}")
    covered = getattr(out, 'topics_covered', getattr(out, 'covered_topics', 'N/A'))
    total = getattr(out, 'topics_total', getattr(out, 'total_topics', 'N/A'))
    status = getattr(out, 'review_status', getattr(out, 'status', 'completed'))
    print(f"Status: {status} | Topics Covered: {covered}/{total}")
    print("=" * 65)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python run_batch_unit.py <SUBJECT_CODE> <UNIT_NUMBER>")
        sys.exit(1)

    subj = sys.argv[1]
    u = int(sys.argv[2])
    run_unit(subj, u)