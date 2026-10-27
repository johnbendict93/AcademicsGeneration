from pathlib import Path

p = Path("source_context.py")
if p.exists():
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    print("\n--- 'source_context.py' Lines containing AL3501 or Topics ---")
    for i, line in enumerate(content.splitlines(), start=1):
        if any(k in line for k in ["AL3501", "Unit 1", "unit_number == 1", "unit == 1", "topics = ["]):
            print(f"L{i:4d}: {line[:100]}")

    # Also print any dictionary/list mapping for AL3501
    import re
    match = re.search(r'("AL3501"|AL3501).*?(\]\s*\n|\}\s*\n)', content, re.DOTALL)
    if match:
        print("\n--- Matched AL3501 block ---")
        print(match.group(0)[:1000])
else:
    print("source_context.py not found.")