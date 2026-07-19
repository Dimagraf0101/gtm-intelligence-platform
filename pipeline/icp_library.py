"""ICP Library — durable persistence for selectable ICPs (Sprint 5.3, storage-backed in 5.5).

An ICP produced in the Workspace (a ``GeneratedICP``) or imported as a PDF can be **selected later**
to qualify leads. Persistence goes through ``pipeline/storage.py`` (LocalStorage for dev, OCI Object
Storage in the container), so it survives restarts in the cloud. Each ICP is three objects under
``icp_library/<id>/``:

    meta.json   — id, name, source, status, created_at, target attributes (for search guidance)
    icp.md      — the ICP as text (legacy load contract; what PDF imports are scored from)
    icp.json    — the full GeneratedICP JSON (round-trips via ``load_generated`` — this is what
                  the Generated-ICP → Engine bridge consumes; generated ICPs only)

Sprint 2A adds the approval gate: ``approve_entry`` applies the IQS-gated human approval act
(``pipeline/icp_approval.py``) to a stored generated ICP, and ``is_ready_for_qualification``
tells the campaign flow whether an entry may qualify leads (generated ICPs require **Approved**
status; PDF imports remain the backward-compatible legacy path — ADR-012).
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
from generated_icp import GeneratedICP, STATUS_APPROVED
import icp_approval

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


def load_generated(entry_id: str) -> GeneratedICP:
    """Rebuild the stored GeneratedICP from ``icp.json``. Raises KeyError when the entry has no
    structured ICP (PDF imports store text only)."""
    store = storage.get_storage()
    if not store.exists(_json_key(entry_id)):
        raise KeyError(f"'{entry_id}' has no stored GeneratedICP (PDF imports are text-only).")
    return GeneratedICP.from_json(store.get_text(_json_key(entry_id)))


def approve_entry(entry_id: str, *, approved_by: str = "user",
                  acknowledge_warnings: bool = False) -> ICPEntry:
    """Apply the human approval act (IQS-gated) to a stored generated ICP and persist it.

    Raises ValueError for PDF imports (they carry no IQS gate — legacy path) and
    :class:`icp_approval.ApprovalError` when the gate refuses.
    """
    entry, _ = load_text(entry_id)
    if entry.source != SOURCE_GENERATED:
        raise ValueError("Only generated ICPs carry the IQS approval gate; "
                         "PDF imports use the backward-compatible legacy path.")
    icp = load_generated(entry_id)
    icp_approval.approve(icp, approved_by=approved_by, acknowledge_warnings=acknowledge_warnings)
    entry.status = icp.metadata.status
    return _write(entry, text=icp.to_markdown(), icp_json=icp.to_json())


def is_ready_for_qualification(entry: ICPEntry) -> bool:
    """May this entry be used to qualify leads? Generated ICPs require **Approved** status
    (PRD §1 / IQS §10); PDF imports remain the supported legacy path (ADR-012)."""
    if entry.source == SOURCE_GENERATED:
        return entry.status == STATUS_APPROVED
    return True


def delete(entry_id: str) -> None:
    storage.get_storage().delete_prefix(f"{ICP_PREFIX}/{entry_id}/")
