import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app

print("=" * 70)
print("source_app.build_pdf_from_topics signature & docstring:")
print("=" * 70)
print(inspect.signature(source_app.build_pdf_from_topics))
print(inspect.getdoc(source_app.build_pdf_from_topics))
print("-" * 70)
print(inspect.getsource(source_app.build_pdf_from_topics))