import sys
from pathlib import Path
import requests

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import load_secrets
import run_batch_unit as rbu

print(f"Default GROQ_MODEL in run_batch_unit: {getattr(rbu, 'GROQ_MODEL', 'Not set')}")

sec = load_secrets()
api_key = sec.get("GROQ_API_KEY")
headers = {"Authorization": f"Bearer {api_key}"}

resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers)
if resp.status_code == 200:
    models = sorted([m["id"] for m in resp.json().get("data", [])])
    print(f"\nAvailable models ({len(models)}):")
    for m in models:
        print(f"  - {m}")
else:
    print(f"Error {resp.status_code}: {resp.text}")