"""Local workspace persistence (Sprint 6).

One explicit, deterministic save/load path for a ``CompanyWorkspace`` using a single JSON file. No
database, no ORM, no repository framework, no migrations — just ``to_dict`` / ``from_dict`` wrapped in
a small versioned envelope:

    {"schema_version": 1, "kind": "gtm_company_workspace", "workspace": { ... }}

Loading refuses — with a clear ``WorkspacePersistenceError`` — on corrupt JSON, a missing/foreign
envelope, or an unsupported ``schema_version``. Unknown is better than guessed: nothing is silently
migrated or repaired.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import icp_project as ip

# Bump ONLY when the persisted shape changes incompatibly. A loader that sees a newer version refuses
# rather than guessing.
SCHEMA_VERSION = 1
_KIND = "gtm_company_workspace"


class WorkspacePersistenceError(ValueError):
    """Raised when a workspace payload cannot be safely loaded (corrupt, foreign, or unsupported)."""


def to_envelope(workspace: ip.CompanyWorkspace) -> dict:
    """The full, versioned persistence payload for a workspace (deterministic)."""
    return {"schema_version": SCHEMA_VERSION, "kind": _KIND, "workspace": workspace.to_dict()}


def from_envelope(payload: dict) -> ip.CompanyWorkspace:
    """Reconstruct a CompanyWorkspace from an envelope, refusing anything unsupported."""
    if not isinstance(payload, dict):
        raise WorkspacePersistenceError("Workspace payload is not a JSON object.")
    if payload.get("kind") != _KIND:
        raise WorkspacePersistenceError(
            f"Not a GTM company workspace file (kind={payload.get('kind')!r}).")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise WorkspacePersistenceError(
            f"Unsupported workspace schema_version {version!r}; this build supports {SCHEMA_VERSION}.")
    body = payload.get("workspace")
    if not isinstance(body, dict):
        raise WorkspacePersistenceError("Workspace payload is missing its 'workspace' body.")
    try:
        return ip.CompanyWorkspace.from_dict(body)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspacePersistenceError(f"Malformed workspace body: {exc}") from exc


def save_workspace(workspace: ip.CompanyWorkspace, path: Union[str, Path]) -> Path:
    """Serialize a workspace to a JSON file (UTF-8, deterministic key order). Returns the path."""
    p = Path(path)
    p.write_text(json.dumps(to_envelope(workspace), ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")
    return p


def load_workspace(path: Union[str, Path]) -> ip.CompanyWorkspace:
    """Load a workspace from a JSON file. Refuses corrupt JSON or unsupported payloads explicitly."""
    p = Path(path)
    if not p.exists():
        raise WorkspacePersistenceError(f"No workspace file at {p}.")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WorkspacePersistenceError(f"Workspace file is not valid JSON: {exc}") from exc
    return from_envelope(payload)


def dumps(workspace: ip.CompanyWorkspace) -> str:
    """In-memory JSON string of the envelope (for tests / round-trips without touching disk)."""
    return json.dumps(to_envelope(workspace), ensure_ascii=False, sort_keys=True)


def loads(text: str) -> ip.CompanyWorkspace:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkspacePersistenceError(f"Workspace text is not valid JSON: {exc}") from exc
    return from_envelope(payload)
