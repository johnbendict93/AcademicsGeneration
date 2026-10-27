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

def inspect_direct_topics():
    conn = get_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. Query topics table directly for unit_id IN (427, 428, 429, 430, 431)
        cur.execute("""
            SELECT t.id, t.unit_id, t.topic_text, t.position, u.unit_number, u.title
            FROM topics t
            JOIN units u ON t.unit_id = u.id
            WHERE u.subject_code = 'AL3501'
            ORDER BY u.unit_number, t.position;
        """)
        db_topics = cur.fetchall()
        print(f"Total topics found in 'topics' table for AL3501: {len(db_topics)}")
        for t in db_topics:
            print(f"Unit {t['unit_number']} | Pos {t['position']} | ID {t['id']} | {t['topic_text']}")

        # 2. Check distinct topic strings already in topic_texts for Unit 1
        print("\n--- Distinct topics in topic_texts for AL3501 Unit 1 ---")
        cur.execute("""
            SELECT DISTINCT topic_index, topic 
            FROM topic_texts 
            WHERE subject_code = 'AL3501' AND unit_number = 1
            ORDER BY topic_index;
        """)
        for r in cur.fetchall():
            print(f"Index {r['topic_index']}: {r['topic']}")

    conn.close()

if __name__ == "__main__":
    inspect_direct_topics()