import sys
from pathlib import Path
import requests
import time

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
import source_context as sc
import source_prompts as sp
import source_runner as sr
import topic_review as tr
import topic_store as ts
from run_batch_unit import load_secrets, save_topic_to_neon, get_neon_conn

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ACTIVE_MODEL = "openai/gpt-oss-20b"

def make_resilient_chat():
    sec = load_secrets()
    api_key = sec.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY missing from secrets.toml")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def _chat(prompt, *, max_completion_tokens=4096, reasoning_effort="low", response_format=None):
        body = {
            "model": ACTIVE_MODEL,
            "temperature": 0.3,
            "max_completion_tokens": max_completion_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if response_format:
            body["response_format"] = response_format

        for attempt in range(4):
            try:
                r = requests.post(GROQ_URL, headers=headers, json=body, timeout=120)
                if r.status_code == 200:
                    choice = r.json()["choices"][0]
                    return (choice["message"].get("content") or "").strip()
                elif r.status_code == 429:
                    wait = min(float(r.headers.get("retry-after", 10)), 30)
                    print(f"   [Rate limit 429] Waiting {wait:.1f}s...")
                    time.sleep(wait)
                else:
                    print(f"   Groq error {r.status_code}: {r.text[:150]}")
            except Exception as e:
                print(f"   Request error: {e}")
            time.sleep(2 ** attempt)

        raise RuntimeError(f"Chat failed after retries on {ACTIVE_MODEL}")

    return _chat

def main():
    print("=" * 70)
    print(f"Completing AL3501 Unit 5 Topics 7, 8, 9 with {ACTIVE_MODEL}")
    print("=" * 70)

    ctx = sc.build_unit_context("AL3501", 5)
    chat = make_resilient_chat()

    subject_name = "Natural Language Processing"
    unit_title = "Discourse Analysis and Lexical Resources"
    rules_v = sr.prompt_version()

    for idx, plan in enumerate(ctx.topics, 1):
        if idx not in (7, 8, 9):
            continue

        print(f"\n[Topic {idx}/9] Drafting: {plan.topic}...")
        res = sp.notes_for_topic(
            ctx,
            plan,
            subject_name,
            unit_title,
            ask=chat,
            avoid=""
        )

        row = source_app.topic_row(
            subject_code="AL3501",
            unit_number=5,
            variant="Notes",
            plan=plan,
            index=idx,
            result=res,
            source_sha256=ctx.source.sha256,
            rules_version=rules_v,
            avoid_notes=()
        )

        rid = save_topic_to_neon(row)
        print(f"   -> Successfully saved row ID {rid} into Neon DB for '{plan.topic}'")

    print("\n" + "=" * 70)
    print("Approving all rows for AL3501 Unit 5")
    print("=" * 70)

    conn = get_neon_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE topic_texts
           SET review_status = 'approved',
               approved_by = 'auto_batch',
               approved_at = NOW()
         WHERE subject_code = 'AL3501'
           AND unit_number = 5
           AND (review_status != 'approved' OR review_status IS NULL);
    """)
    updated_rows = cur.rowcount
    conn.commit()
    conn.close()
    print(f"Updated and approved {updated_rows} rows in Neon DB.")

    print("\n" + "=" * 70)
    print("Verifying Reconciled Unit View & PDF Readiness")
    print("=" * 70)
    from inspect_u5_view import inspect_u5
    inspect_u5()

if __name__ == "__main__":
    main()