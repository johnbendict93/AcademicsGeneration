import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Look across core modules for PDF compilation
candidates = ["source_app", "topic_review", "topic_store", "generate_pdf", "compile_pdf"]

for mod_name in candidates:
    try:
        mod = __import__(mod_name)
        fns = [m for m in dir(mod) if "pdf" in m.lower() or "compile" in m.lower()]
        if fns:
            print(f"Module '{mod_name}' has: {fns}")
    except ImportError:
        pass