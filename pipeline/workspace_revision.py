"""Workspace revision tokens (Sprint 5.7B).

A tiny, pure module that computes deterministic revision tokens for the transient Streamlit
workspaces, so a page can rebuild a cached workspace exactly when — and only when — the upstream
authoring state it derives from has changed. No global state manager, no event bus, no observers, no
persistence: just content fingerprints the caller compares.

Tokens are **content-based** (never timestamps): they hash the meaningful fields of the underlying
BusinessKnowledge / strategy state, so a token changes iff the content that could make a workspace
stale changes. Timestamps are metadata elsewhere and are deliberately excluded here.

Scoping (minimal — invalidate no more than necessary):

* ``knowledge_revision`` — company + project knowledge content. The **Knowledge Interview** and
  **Strategy Review** workspaces derive their gaps / base draft from composed knowledge, so they are
  stale exactly when this changes. It is stable under a Strategy Review's own edits (which touch
  ``project.strategy``, not knowledge), so those never self-invalidate.
* ``approval_inputs_revision`` — the inputs the **Approval** workspace depends on: the strategy
  revision, the reviewed-draft fingerprint, and the number of stored reviewed drafts. It deliberately
  excludes the approval *output* (approved versions / active pointer) so approving does not
  self-invalidate the workspace.
"""
from __future__ import annotations

import hashlib


def _fp(*parts) -> str:
    return hashlib.sha256("\x1e".join(repr(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _knowledge_content(kn) -> str:
    """Deterministic content fingerprint of one BusinessKnowledge — meaningful fields only, no
    timestamps or ids, so it changes iff the curated facts/conflicts/unknowns change."""
    if kn is None:
        return "none"
    items = sorted(
        (it.category, it.attribute, it.normalized_value, it.status,
         getattr(it, "temporal_context", ""))
        for it in kn.knowledge_items)
    conflicts = sorted(
        (c.category, c.attribute, c.status, tuple(sorted(str(v) for v in c.conflicting_values)))
        for c in kn.conflicts)
    unknowns = tuple(sorted(kn.unknown_fields))
    return _fp(items, conflicts, unknowns)


def knowledge_revision(company, project) -> str:
    """Token for workspaces derived from composed knowledge (Interview, Strategy Review)."""
    return _fp(_knowledge_content(company), _knowledge_content(project.project_knowledge))


def approval_inputs_revision(project) -> str:
    """Token for the Approval workspace: strategy revision + reviewed-draft fingerprint + number of
    reviewed drafts. Excludes approved output so approving does not self-invalidate."""
    strat = getattr(project, "strategy", None)
    revision = getattr(strat, "revision", -1) if strat is not None else -1
    reviewed_fp = getattr(strat, "reviewed_fingerprint", "") if strat is not None else ""
    return _fp(revision, reviewed_fp, len(project.draft_versions))
