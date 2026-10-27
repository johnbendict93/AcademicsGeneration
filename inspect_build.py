from pathlib import Path

# 1. Inspect build_final_notes_pdf.py
p_build = Path("build_final_notes_pdf.py")
if p_build.exists():
    print("=" * 65)
    print("build_final_notes_pdf.py (Lines 1 to 120):")
    print("=" * 65)
    with open(p_build, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            if i > 120:
                break
            print(f"L{i:3d}: {line.rstrip()}")

# 2. Inspect source_app.py for build_pdf_from_topics
p_app = Path("source_app.py")
if p_app.exists():
    print("\n" + "=" * 65)
    print("source_app.py: Searching for build_pdf_from_topics")
    print("=" * 65)
    with open(p_app, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            if "build_pdf_from_topics" in line or "topic_texts" in line:
                print(f"L{i:3d}: {line.rstrip()[:110]}")