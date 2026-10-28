import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

print("=" * 70)
print("Searching source_context.py for Unit 5 / Unit V configuration:")
print("=" * 70)

src = inspect.getsource(sc)
lines = src.splitlines()

# Print lines around 'Unit V' or 'unit_number == 5' or 'Unit 5'
matching_indices = [
    i for i, line in enumerate(lines) 
    if any(k in line.lower() for k in ["unit v", "unit 5", "unit_number == 5", "unit_number == 5"])
]

if matching_indices:
    for idx in matching_indices:
        start = max(0, idx - 10)
        end = min(len(lines), idx + 25)
        print(f"\n--- Around line {idx+1} ---")
        for j in range(start, end):
            print(f"{j+1:4d}: {lines[j]}")
else:
    print("No direct keyword match found. Showing get_source / build_unit_context definition:")
    if hasattr(sc, "build_unit_context"):
        print(inspect.getsource(sc.build_unit_context))

print("\n" + "=" * 70)
print("Checking for existing text files in App directory matching 'unit*.*':")
print("=" * 70)
for p in APP_DIR.glob("*unit*.*"):
    print(f"  {p.name} ({p.stat().st_size:,} bytes)")
for p in APP_DIR.glob("*Unit*.*"):
    print(f"  {p.name} ({p.stat().st_size:,} bytes)")