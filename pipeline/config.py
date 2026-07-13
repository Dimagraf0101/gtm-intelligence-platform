"""Shared configuration for pipeline scripts."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent.resolve()
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
ICP_DIR = BASE_DIR / "icp"
ICP_LIBRARY_DIR = BASE_DIR / "data" / "icp_library"   # persisted, selectable ICPs (generated + imported)

# --- persistence / storage ---------------------------------------------------
# LocalStorage (default, dev) or OCI Object Storage (containerised deployment).
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")           # local | oci
STORAGE_LOCAL_ROOT = Path(os.getenv("STORAGE_LOCAL_ROOT", str(DATA_DIR)))
OCI_NAMESPACE = os.getenv("OCI_NAMESPACE")
OCI_BUCKET = os.getenv("OCI_BUCKET")
OCI_REGION = os.getenv("OCI_REGION")
OCI_AUTH = os.getenv("OCI_AUTH", "instance_principal")            # instance_principal | config_file
OCI_CONFIG_FILE = os.getenv("OCI_CONFIG_FILE", "~/.oci/config")
OCI_CONFIG_PROFILE = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")

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
