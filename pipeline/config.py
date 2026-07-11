"""Shared configuration for pipeline scripts."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent.resolve()
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
ICP_DIR = BASE_DIR / "icp"

VAYNE_API_TOKEN = os.getenv("VAYNE_API_TOKEN")
VAYNE_BASE_URL = "https://www.vayne.io"
VAYNE_WEBHOOK_URL = os.getenv("VAYNE_WEBHOOK_URL")  # optional — Vayne POSTs completed CSV URL here
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "60"))


def require_vayne_token():
    if not VAYNE_API_TOKEN:
        print("Error: VAYNE_API_TOKEN not set. Copy .env.example to .env and fill in your token.")
        sys.exit(1)


def vayne_headers():
    return {"Authorization": f"Bearer {VAYNE_API_TOKEN}"}
