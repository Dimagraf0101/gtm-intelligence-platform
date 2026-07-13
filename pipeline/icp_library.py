"""ICP Library — durable persistence for selectable ICPs (Sprint 5.3, storage-backed in 5.5).

An ICP produced in the Workspace (a ``GeneratedICP``) or imported as a PDF can be **selected later**
to qualify leads. Persistence goes through ``pipeline/storage.py`` (LocalStorage for dev, OCI Object
Storage in the container), so it survives restarts in the cloud. Each ICP is three objects under
``icp_library/<id>/``:

    meta.json   — id, name, source, status, created_at, target attributes (for search guidance)
    icp.md      — the ICP as text (this is what the scoring engine consumes)
    icp.json    — the full GeneratedICP JSON (provenance; generated ICPs only)

Scoring consumes ICP *text* (``pipeline/scoring.score_leads``), so ``icp.md`` is the load contract.
No LLM, no direct filesystem access (the storage layer owns that).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import storage
from storage import ICP_PREFIX
from icp_pdf import extract_icp_from_bytes

SOURCE_GENERATED = "generated"
SOURCE_PDF = "pdf"


@dataclass
class ICPEntry:
    """Metadata describing one stored ICP (mirrors meta.json)."""
    id: str
    name: str
    source: str                       # generated | pdf
    status: str = ""
    created_at: str = ""
    source_files: list[str] = field(default_factory=list)
    n_dimensions: int = 0
    targets: dict = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def target_summary(self) -> dict[str, list[str]]:
        return {k: v for k, v in (self.targets or {}).items() if v}


# --- key helpers -------------------------------------------------------------

def _meta_key(entry_id: str) -> str:
    return f"{ICP_PREFIX}/{entry_id}/meta.json"


def _md_key(entry_id: str) -> str:
    return f"{ICP_PREFIX}/{entry_id}/icp.md"


def _json_key(entry_id: str) -> str:
    return f"{ICP_PREFIX}/{entry_id}/icp.json"


# --- helpers -----------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:60] or "icp"


def _unique_id(name: str) -> str:
    store = storage.get_storage()
    base = f"{_slugify(name)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    candidate, n = base, 2
    while store.exists(_meta_key(candidate)):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _targets_from_icp(icp) -> dict[str, list[str]]:
    """Pull the structured target attributes off a GeneratedICP (used to guide the Vayne search)."""
    tc = getattr(icp, "target_companies", None)
    tb = getattr(icp, "target_buyers", None)

    def g(obj, attr):
        return list(getattr(obj, attr, []) or []) if obj is not None else []

    return {
        "industries": g(tc, "target_industries"),
        "subsegments": g(tc, "target_subsegments"),
        "geographies": g(tc, "target_geographies"),
        "company_types": g(tc, "target_company_types"),
        "preferred_sizes": g(tc, "preferred_employee_ranges"),
        "acceptable_sizes": g(tc, "acceptable_employee_ranges"),
        "primary_roles": g(tb, "primary_buyer_roles"),
        "secondary_roles": g(tb, "secondary_buyer_roles"),
        "excluded_roles": g(tb, "excluded_buyer_roles"),
    }


def _write(entry: ICPEntry, *, text: str, icp_json: Optional[str]) -> ICPEntry:
    store = storage.get_storage()
    store.put_text(_meta_key(entry.id), json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
    store.put_text(_md_key(entry.id), text)
    if icp_json is not None:
        store.put_text(_json_key(entry.id), icp_json)
    return entry


# --- public API --------------------------------------------------------------

def save_generated(icp, *, notes: str = "") -> ICPEntry:
    """Persist a GeneratedICP produced by the Workspace. Returns its ICPEntry."""
    name = (getattr(icp.metadata, "name", "") or "Draft ICP").strip()
    entry = ICPEntry(
        id=_unique_id(name), name=name, source=SOURCE_GENERATED,
        status=getattr(icp.metadata, "status", ""), created_at=_now(),
        source_files=list(getattr(icp.metadata, "source_files", []) or []),
        n_dimensions=len(getattr(icp, "dimensions", []) or []),
        targets=_targets_from_icp(icp), notes=notes)
    return _write(entry, text=icp.to_markdown(), icp_json=icp.to_json())


def import_pdf(name: str, data: bytes, *, notes: str = "") -> ICPEntry:
    """Import an ICP PDF into the library as text. Raises ValueError if no text is extractable."""
    icp = extract_icp_from_bytes(data, (name or "Imported ICP").strip())
    if icp.is_empty():
        raise ValueError("No text could be extracted from the PDF (it may be scanned/image-only).")
    entry = ICPEntry(
        id=_unique_id(icp.name), name=icp.name, source=SOURCE_PDF, status="Imported",
        created_at=_now(), source_files=[name] if name else [], n_dimensions=0,
        targets={}, notes=notes)
    return _write(entry, text=icp.text, icp_json=None)


def list_entries() -> list[ICPEntry]:
    """All stored ICPs, newest first. Partial/corrupt entries are skipped, never fatal."""
    store = storage.get_storage()
    out: list[ICPEntry] = []
    for entry_id in store.list_children(ICP_PREFIX + "/"):
        try:
            if not (store.exists(_meta_key(entry_id)) and store.exists(_md_key(entry_id))):
                continue
            raw = json.loads(store.get_text(_meta_key(entry_id)))
            out.append(ICPEntry(**{k: raw.get(k) for k in ICPEntry.__dataclass_fields__ if k in raw}))
        except (KeyError, json.JSONDecodeError, TypeError, ValueError):
            continue
    out.sort(key=lambda e: e.created_at, reverse=True)
    return out


def load_text(entry_id: str) -> tuple[ICPEntry, str]:
    """Return (entry, icp_text). ``icp_text`` is what the scoring engine consumes."""
    store = storage.get_storage()
    try:
        raw = json.loads(store.get_text(_meta_key(entry_id)))
        text = store.get_text(_md_key(entry_id))
    except KeyError:
        raise KeyError(entry_id)
    entry = ICPEntry(**{k: raw.get(k) for k in ICPEntry.__dataclass_fields__ if k in raw})
    return entry, text


def delete(entry_id: str) -> None:
    storage.get_storage().delete_prefix(f"{ICP_PREFIX}/{entry_id}/")
