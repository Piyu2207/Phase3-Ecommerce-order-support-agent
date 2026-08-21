from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = str(DATA_DIR / "checkpoints.sqlite")

REFUND_THRESHOLD = float(os.getenv("REFUND_APPROVAL_THRESHOLD", "1000"))

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
