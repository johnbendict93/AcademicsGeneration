import glob

keywords = ["AL3501", "Natural Language Processing", "Grammar-based LM"]

for path in glob.glob("*.py"):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        matches = [f"  L{i+1}: {line.strip()[:110]}" for i, line in enumerate(lines) if any(k in line for k in keywords)]
        if matches:
            print(f"\n=== File: {path} ({len(matches)} matches) ===")
            for m in matches[:10]:
                print(m)
    except Exception:
        pass