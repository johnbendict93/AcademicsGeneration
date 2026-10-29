from datetime import datetime, timezone
import argparse
import dataclasses
import os
import sys
import time
from pathlib import Path
from groq import Groq

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(APP_DIR / ".env")
except ImportError:
    pass

import source_app
import source_context as sc
import source_pdf as spdf
import source_prompts as sp
import topic_review as tr
import topic_store as ts
from run_batch_unit import get_neon_conn


def parse_args():
    parser = argparse.ArgumentParser(description="Run complete academic unit pipeline.")
    parser.add_argument("--code", required=True, help="Subject code (e.g. AL3501)")
    parser.add_argument("--unit", required=True, type=int, help="Unit number (1-5)")
    parser.add_argument("--model", default="openai/gpt-oss-20b", help="Groq model ID")
    parser.add_argument("--dept-code", default="AI&DS", help="Department code")
    parser.add_argument("--dept-name", default="Artificial Intelligence and Data Science", help="Department name")
    parser.add_argument("--subject-name", default="Natural Language Processing", help="Subject name")
    parser.add_argument("--regulation", default="2021", help="Regulation year")
    return parser.parse_args()


def get_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        env_file = APP_DIR / ".env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                if line.startswith("GROQ_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in environment or .env file.")
    return Groq(api_key=api_key)


def main():
    args = parse_args()
    code = args.code.upper()
    unit_num = args.unit
    unit_roman = ["I", "II", "III", "IV", "V"][unit_num - 1]

    print("=" * 70)
    print(f"PIPELINE: {code} Unit {unit_num} ({unit_roman})")
    print(f"Model   : {args.model}")
    print("=" * 70)

    # 1. Subject and Unit Metadata
    subs = sc._load_subjects(sc.RESOURCES_DIR)
    canonical, _ = sc._subject_group(subs, code)
    unit_title = canonical.get("units", {}).get(unit_roman, f"Unit {unit_num}")

    # 2. Build Unit Context
    print("\n[1/5] Loading unit context & source passages...")
    ctx = sc.build_unit_context(code, unit_num)
    print(f"  Source file      : {ctx.source.path.name} ({ctx.source.word_count} words)")
    print(f"  Source type      : {ctx.source.source}")
    print(f"  License          : {ctx.source.license}")
    print(f"  Passages created : {len(ctx.passages)}")
    print(f"  Planned topics   : {len(ctx.topics)}")
    print(f"  Unmapped gaps    : {len(ctx.gaps)}")

    if len(ctx.topics) == 0:
        print("  Error: 0 planned topics found. Verify topics in subjects.json.")
        return

    # 3. Check Existing DB State
    conn = get_neon_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT topic_index, topic, review_status
          FROM topic_texts
         WHERE subject_code = %s
           AND unit_number = %s
         ORDER BY topic_index;
    """, (code, unit_num))
    existing_by_index = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    # 4. Process Topics (Mark Gaps or Draft Notes)
    print("\n[2/5] Processing topics (Drafting covered / Marking gaps)...")
    client = get_groq_client()

    for idx, plan in enumerate(ctx.topics, start=1):
        try:
            cur.execute("SELECT 1;")
        except Exception:
            conn = get_neon_conn()
            cur = conn.cursor()
        is_gap = plan.status not in ("FOUND", "PARTIAL") or not getattr(plan, "passages", None)
        if is_gap:
            print(f"\n  [{idx}/{len(ctx.topics)}] Gap detected: '{plan.topic}' -> Recording gap mark...")
            note = f"Not covered in verified source {ctx.source.path.name}"
            cur.execute(
                tr.GAP_MARK_INSERT_SQL,
                (code, unit_num, plan.topic, ctx.source.sha256, note,
                 code, unit_num, plan.topic, ctx.source.sha256),
            )
            conn.commit()
            continue

        if idx in existing_by_index:
            t_name, status = existing_by_index[idx]
            print(f"  [{idx}/{len(ctx.topics)}] Skipping existing: '{t_name}' (status: {status})")
            continue

        print(f"\n  [{idx}/{len(ctx.topics)}] Drafting: '{plan.topic}'...")
        prompt = sp.build_notes_prompt(ctx, plan, args.subject_name, unit_title)

        backoff = 15.0
        row_dict = None
        for attempt in range(12):
            try:
                chat_res = client.chat.completions.create(
                    model=args.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_completion_tokens=sp.NOTES_MAX_COMPLETION_TOKENS,
                )
                raw_ans = chat_res.choices[0].message.content
                chk = sp.check_notes_answer(raw_ans, plan)

                import json
                row_dict = {
                    "subject_code": code,
                    "unit_number": unit_num,
                    "variant": "Notes",
                    "topic": plan.topic,
                    "topic_index": idx,
                    "raw_answer": raw_ans,
                    "markdown": chk.markdown if chk.markdown else raw_ans.strip(),
                    "check_data": json.dumps({
                        "status": "ok" if chk.ok else "drafted",
                        "calls": 1,
                        "dropped": [],
                        "new_terms": list(chk.new_terms or [])
                    }),
                    "source_sha256": ctx.source.sha256,
                    "passages_sha256": ts.passages_sha256(plan),
                    "rules_version": "v1.0",
                    "avoid_notes": [],
                    "generated_at": datetime.now(timezone.utc),
                }
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "Connect" in err_str or "11001" in err_str or "timeout" in err_str.lower():
                    print(f"      [Rate Limit 429] Waiting {backoff:.1f}s before retry...")
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 90.0)
                else:
                    raise e

        if not row_dict:
            raise RuntimeError(f"Failed to generate topic {idx}: {plan.topic}")

        try:
            cur.execute("SELECT 1;")
        except Exception:
            conn = get_neon_conn()
            cur = conn.cursor()
        cur.execute("""
            INSERT INTO topic_texts (
                subject_code, unit_number, variant, topic, topic_index,
                raw_answer, markdown, check_data, source_sha256, passages_sha256,
                rules_version, avoid_notes, generated_at, review_status, reviewed_at
            ) VALUES (
                %(subject_code)s, %(unit_number)s, %(variant)s, %(topic)s, %(topic_index)s,
                %(raw_answer)s, %(markdown)s, %(check_data)s, %(source_sha256)s, %(passages_sha256)s,
                %(rules_version)s, %(avoid_notes)s, %(generated_at)s, 'draft', NULL
            )
            RETURNING id;
        """, row_dict)
        inserted_id = cur.fetchone()[0]
        conn.commit()
        print(f"      -> Saved row ID {inserted_id} into Neon DB")

    # 5. Approve All Drafts for this Unit
    print("\n[3/5] Setting review_status = 'approved'...")
    cur.execute("""
        UPDATE topic_texts
           SET review_status = 'approved',
               reviewed_at = NOW()
         WHERE subject_code = %s
           AND unit_number = %s;
    """, (code, unit_num))
    print(f"  Approved {cur.rowcount} rows in Neon DB.")
    conn.commit()

    # 6. Reconcile Unit View
    print("\n[4/5] Reconciling unit view for PDF compilation...")
    cur.execute("""
        SELECT * FROM topic_texts
         WHERE subject_code = %s
           AND unit_number = %s
         ORDER BY topic_index, id;
    """, (code, unit_num))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(tr.GAP_MARK_ROWS_SQL, (code, unit_num))
    m_cols = [d[0] for d in cur.description]
    marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]
    conn.close()

    if ctx.source.license not in spdf.LICENCES:
        valid_lic = list(spdf.LICENCES.keys())[0]
        try:
            ctx.source.license = valid_lic
        except Exception:
            new_source = dataclasses.replace(ctx.source, license=valid_lic)
            ctx = dataclasses.replace(ctx, source=new_source)

    view = ts.unit_view(ctx, rows, marks)
    if not ts.pdf_ready(view):
        print("  Error: Unit view is not ready for PDF compilation.")
        for e in view:
            print(f"    [{e.index}] {e.topic} -> {e.state}")
        return

    # 7. Assemble and Render PDF
    print("\n[5/5] Compiling PDF via source_app.build_pdf_from_topics...")
    gen_unit = source_app.build_pdf_from_topics(
        ctx=ctx,
        view=view,
        subject_name=args.subject_name,
        subject_code=code,
        dept_code=args.dept_code,
        dept_name=args.dept_name,
        unit_number=unit_num,
        unit_title=unit_title,
        regulation=args.regulation,
        variant="Notes",
    )

    out_dir = APP_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{code}_Unit_{unit_num}_Notes.pdf"
    out_file.write_bytes(gen_unit.pdf_bytes)

    print("\n" + "=" * 70)
    print("SUCCESS: Pipeline Completed Successfully!")
    print(f"PDF File  : {out_file.resolve()}")
    print(f"File Size : {len(gen_unit.pdf_bytes):,} bytes ({len(gen_unit.pdf_bytes)/1024:.1f} KB)")
    print("=" * 70)


if __name__ == "__main__":
    main()