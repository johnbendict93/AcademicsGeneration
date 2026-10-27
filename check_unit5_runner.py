import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Check run_batch_unit capabilities
import run_batch_unit as rbu

print("=" * 70)
print("run_batch_unit functions / CLI options:")
print("=" * 70)
for name, obj in inspect.getmembers(rbu, inspect.isfunction):
    if not name.startswith("_"):
        sig = inspect.signature(obj)
        print(f"  {name}{sig}")

# Check finalize_unit5.py if it exists
fin_p = APP_DIR / "finalize_unit5.py"
if fin_p.exists():
    print("\n" + "=" * 70)
    print("Found finalize_unit5.py! First 25 lines:")
    print("=" * 70)
    lines = fin_p.read_text(encoding="utf-8").splitlines()
    for l in lines[:25]:
        print(f"  {l}")