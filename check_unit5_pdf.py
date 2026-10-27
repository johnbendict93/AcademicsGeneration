import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        import fitz  # PyMuPDF
        PdfReader = None

res_dir = Path(sc.RESOURCES_DIR)
unit5_pdf = res_dir / "AL3501" / "NLP AL3501_UnitV_VerifiedNotes.pdf"

print("=" * 70)
print(f"Inspecting: {unit5_pdf}")
print("=" * 70)

if not unit5_pdf.exists():
    print(f"File not found at {unit5_pdf}")
    # Check alternate PDFs in AL3501
    for p in (res_dir / "AL3501").glob("*UnitV*"):
        print(f"  Found alternate: {p.name}")
else:
    print(f"File size: {unit5_pdf.stat().st_size:,} bytes")
    extracted_text = []
    
    if PdfReader is not None:
        reader = PdfReader(str(unit5_pdf))
        num_pages = len(reader.pages)
        print(f"Total pages: {num_pages}")
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            extracted_text.append(f"--- PAGE {i+1} ---\n" + text)
    else:
        doc = fitz.open(str(unit5_pdf))
        print(f"Total pages: {len(doc)}")
        for i, page in enumerate(doc):
            extracted_text.append(f"--- PAGE {i+1} ---\n" + page.get_text())

    full_text = "\n\n".join(extracted_text)
    print(f"Extracted {len(full_text.split()):,} words ({len(full_text):,} characters)")
    print("\nSnippet of Page 1 & 2:")
    print("-" * 50)
    print("\n".join(full_text.splitlines()[:40]))