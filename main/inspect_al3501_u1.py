import os
import psycopg2
from psycopg2.extras import RealDictCursor
from pathlib import Path

def get_connection():
    db_url = os.getenv("DATABASE_URL")
    if db_url and not db_url.startswith("postgresql://username:"):
        return psycopg2.connect(db_url)

    candidate_paths = [
        Path(".streamlit/secrets.toml"),
        Path.home() / ".streamlit" / "secrets.toml"
    ]
    for path in candidate_paths:
        if path.exists():
            try:
                try:
                    import tomllib
                    with open(path, "rb") as f:
                        secrets = tomllib.load(f)
                except ImportError:
                    import toml
                    with open(path, "r", encoding="utf-8") as f:
                        secrets = toml.load(f)
                if "NEON_HOST" in secrets:
                    return psycopg2.connect(
                        host=secrets["NEON_HOST"],
                        dbname=secrets.get("NEON_DATABASE", "neondb"),
                        user=secrets["NEON_USER"],
                        password=secrets["NEON_PASSWORD"],
                        port=secrets.get("NEON_PORT", 5432),
                        sslmode="require"
                    )
            except Exception:
                pass
    raise ValueError("No valid Neon credentials found.")

def reconcile_unit_1():
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Get all rows for Unit 1 ordered by topic_index and id
        cur.execute("""
            SELECT 
                topic_index,
                id,
                topic,
                review_status,
                review_note,
                reviewed_at,
                LENGTH(COALESCE(markdown, '')) as md_len
            FROM topic_texts
            WHERE subject_code = 'AL3501' AND unit_number = 1
            ORDER BY topic_index ASC, id ASC;
        """)
        rows = cur.fetchall()

    from collections import defaultdict
    by_index = defaultdict(list)
    for r in rows:
        by_index[r['topic_index']].append(r)

    print("\n==================================================================")
    print("   AL3501 UNIT 1: COMPLETE TOPIC STATUS & RECONCILIATION AUDIT    ")
    print("==================================================================")

    unresolved_indices = []

    for idx in sorted(by_index.keys()):
        attempts = by_index[idx]
        topic_name = attempts[-1]['topic']
        approved_rows = [a for a in attempts if a['review_status'] == 'approved']
        
        status_summary = ", ".join([f"ID {a['id']}: {a['review_status']}" for a in attempts])
        
        if approved_rows:
            best = approved_rows[-1]
            print(f"Index {idx:2d} | APPROVED (ID: {best['id']}) | {topic_name}")
            print(f"          History: [{status_summary}]")
        else:
            print(f"Index {idx:2d} | >>> NEEDS ACTION <<< | {topic_name}")
            print(f"          History: [{status_summary}]")
            for a in attempts:
                if a['review_note']:
                    print(f"          - ID {a['id']} ({a['review_status']}): {a['review_note']}")
            unresolved_indices.append(idx)
        print("-" * 66)

    print(f"\nTotal indices in Unit 1: {len(by_index)}")
    print(f"Approved indices: {len(by_index) - len(unresolved_indices)}")
    print(f"Indices needing action: {unresolved_indices}")

    conn.close()

if __name__ == "__main__":
    reconcile_unit_1()