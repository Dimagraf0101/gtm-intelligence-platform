"""Generator → Qualification Bridge (Sprint 5.6).

The single, deterministic place that turns one ICP input — an uploaded document **or** an Approved
GeneratedICP — into a run snapshot the existing scoring engine consumes. Both flows converge on the
unchanged ``scoring.score_leads``; the Approved flow supplies a ready ``ICPProfile`` (from the
adapter) plus semantic text (from ``to_markdown()``), so structured data is never re-parsed.

Invariants (frozen in Sprint 5.6A):

* ``icp_adapter`` stays the ONLY Generator → ICPProfile boundary.
* Only an **Approved**, IQS-valid GeneratedICP may qualify — Drafts never can.
* Exactly one ICP source per run; the two modes are mutually exclusive and never merged.
* The Approved GeneratedICP is never mutated (the adapter reads it; the active copy is a safe copy).
* ``QualificationICPContext`` is an immutable snapshot: it is the source of truth for one run, so
  changing the selected project or active Approved ICP afterward cannot alter a completed run.
* Deterministic Python only — no LLM, no new prompts, no persistence, no cache, no mutable state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import scoring
import icp_adapter
import icp_approval
from generated_icp import GeneratedICP, STATUS_APPROVED
from icp_profile import ICPProfile

SOURCE_UPLOADED = "uploaded_document"
SOURCE_APPROVED = "approved_generated"


class BridgeError(ValueError):
    """Raised when an ICP source cannot be turned into a valid qualification context."""


@dataclass(frozen=True)
class QualificationICPContext:
    """Immutable snapshot of the ICP used for exactly one qualification run.

    ``profile`` is None for the uploaded path (``score_leads`` parses ``semantic_text`` exactly as
    before) and a ready adapter ``ICPProfile`` for the Approved path. Being frozen and value-only, a
    captured context is unaffected by any later change to the project or its active Approved version.
    """
    name: str
    semantic_text: str
    source_type: str
    profile: Optional[ICPProfile] = None
    source_version: Optional[str] = None
    source_fingerprint: Optional[str] = None
    created_at: datetime = None      # set in __post_init__ when not provided

    def __post_init__(self):
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc))

    @property
    def source_label(self) -> str:
        return ("Uploaded Document" if self.source_type == SOURCE_UPLOADED
                else "Approved Generated ICP")


# --- context builders --------------------------------------------------------

def context_from_uploaded(icp_name: str, icp_text: str) -> QualificationICPContext:
    """The existing PDF/text path: profile stays None so ``score_leads`` parses the text as before."""
    return QualificationICPContext(
        name=icp_name, semantic_text=icp_text, source_type=SOURCE_UPLOADED, profile=None)


def context_from_approved_icp(approved: GeneratedICP) -> QualificationICPContext:
    """Build a context from one Approved GeneratedICP via the adapter (the only conversion boundary).

    Raises ``BridgeError`` unless the ICP is Approved and passes the adapter's IQS-valid gate. The
    ICP is read, never mutated; a fingerprint identifies exactly which version scored the run."""
    if approved is None:
        raise BridgeError("No Approved ICP was provided.")
    if approved.metadata.status != STATUS_APPROVED:
        raise BridgeError(
            f"Only Approved ICPs can qualify leads; status is '{approved.metadata.status}'.")
    if not icp_adapter.can_use(approved):
        raise BridgeError("The Approved ICP failed IQS validation and cannot be used for scoring.")
    try:
        profile = icp_adapter.to_engine_profile(approved)
    except icp_adapter.AdapterError as exc:
        raise BridgeError(str(exc)) from exc
    return QualificationICPContext(
        name=approved.metadata.name,
        semantic_text=approved.to_markdown(),
        source_type=SOURCE_APPROVED,
        profile=profile,
        source_version=str(approved.metadata.version),
        source_fingerprint=icp_approval.fingerprint_generated_icp(approved))


def context_from_approved_project(project) -> QualificationICPContext:
    """Build a context from a project's ACTIVE Approved ICP. Refuses (never guesses) when there is
    none — a project with only Drafts, or a broken active pointer, yields a ``BridgeError``."""
    active = icp_approval.get_active_approved_icp(project)   # safe copy, by stable fingerprint
    if active is None:
        raise BridgeError(
            "This project has no active Approved ICP. Approve one on the Approval page first.")
    return context_from_approved_icp(active)


# --- validation + run --------------------------------------------------------

def validate_context(ctx: QualificationICPContext) -> list[str]:
    """Deterministic blocking reasons before scoring ([] when runnable). No LLM, no side effects."""
    reasons: list[str] = []
    if ctx is None:
        return ["No ICP context was provided."]
    if ctx.source_type not in (SOURCE_UPLOADED, SOURCE_APPROVED):
        reasons.append(f"Unknown ICP source type '{ctx.source_type}'.")
    if not (ctx.semantic_text or "").strip():
        reasons.append("The ICP has no text to interpret.")
    if ctx.source_type == SOURCE_APPROVED and ctx.profile is None:
        reasons.append("The Approved ICP produced no scoring profile.")
    return reasons


def score_with_context(leads, ctx: QualificationICPContext, *, client=None,
                       batch_size: int = scoring.BATCH_SIZE, progress_cb=None, stats=None):
    """Score leads against one captured ICP context, converging on the unchanged scoring engine.

    Both source types run the identical ``score_leads`` implementation; only ``profile`` differs
    (None ⇒ parse the text as before; adapter profile ⇒ use it verbatim)."""
    reasons = validate_context(ctx)
    if reasons:
        raise BridgeError("Cannot qualify: " + "; ".join(reasons))
    return scoring.score_leads(
        leads, ctx.semantic_text, ctx.name, profile=ctx.profile,
        client=client, batch_size=batch_size, progress_cb=progress_cb, stats=stats)
