"""Human Review domain — append-only review decisions (Sprint 13b).

The human-approval layer of the pipeline:

    QualifiedLeadBatch (immutable AI proposal) → ReviewedLeadBatch (immutable human decisions)

A ``ReviewedLeadBatch`` is owned by exactly one Market Hypothesis and references exactly one
``QualifiedLeadBatch``. It **never mutates** the Lead or the QualifiedLead — the AI proposal and the
business entity stay untouched; the human verdict lives here.

Append-only: every decision is a frozen ``LeadReviewDecision`` appended to the batch. Re-deciding a
lead appends a NEW decision; the **latest decision for a lead_id wins**, and the earlier ones remain as
audit history. Nothing is ever overwritten or deleted.

``review_status`` is the single workflow authority. The canonical export "Human Decision" value is
**derived** from it (``human_decision_for``) — it is never stored separately, so the two can never drift.
Deterministic Python only — no LLM, no scoring, no priority logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import business_knowledge as bk

# --- review workflow statuses (the single authority) -------------------------

REVIEW_PENDING = "Pending"
REVIEW_APPROVED = "Approved"
REVIEW_REJECTED = "Rejected"
REVIEW_SKIPPED = "Skipped"
REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED, REVIEW_SKIPPED)
_DECIDED = (REVIEW_APPROVED, REVIEW_REJECTED, REVIEW_SKIPPED)

# Deterministic projection of the workflow status into the canonical export "Human Decision" column.
# Pending = the human has not decided yet, so the export carries no final decision (empty).
_HUMAN_DECISION = {
    REVIEW_PENDING: "",
    REVIEW_APPROVED: "Approved",
    REVIEW_REJECTED: "Rejected",
    REVIEW_SKIPPED: "Skipped",
}


class LeadReviewError(ValueError):
    """Raised for an unknown review status or a structurally invalid review artifact."""


def human_decision_for(review_status: str) -> str:
    """Derive the canonical export ``Human Decision`` from ``review_status`` (the sole authority).
    Never stored — always derived, so the two cannot drift."""
    return _HUMAN_DECISION.get(review_status, "")


def _norm(value) -> str:
    return " ".join(str(value or "").split()).strip()


@dataclass(frozen=True)
class LeadReviewDecision:
    """One immutable human decision about one lead. Frozen: a decision is never edited — a change is a
    NEW decision appended after it (append-only audit history).

    Normalization: ``rejection_reason`` is only meaningful for a Rejected lead, so it is cleared for any
    other status — a contradictory state (Approved + "Wrong industry") cannot be represented."""
    lead_id: str = ""
    review_status: str = REVIEW_PENDING
    rejection_reason: str = ""
    reviewer_comment: str = ""
    decided_at: str = ""
    decided_by: str = ""

    def __post_init__(self):
        if self.review_status not in REVIEW_STATUSES:
            raise LeadReviewError(f"Unknown review status {self.review_status!r}.")
        object.__setattr__(self, "lead_id", _norm(self.lead_id))
        object.__setattr__(self, "reviewer_comment", _norm(self.reviewer_comment))
        # deterministic normalization: a rejection reason only survives on a Rejected decision
        reason = _norm(self.rejection_reason) if self.review_status == REVIEW_REJECTED else ""
        object.__setattr__(self, "rejection_reason", reason)
        if not self.decided_at:
            object.__setattr__(self, "decided_at", bk._now())

    @property
    def human_decision(self) -> str:
        """Derived export value — never a stored authority."""
        return human_decision_for(self.review_status)

    def to_dict(self) -> dict:
        return {"lead_id": self.lead_id, "review_status": self.review_status,
                "rejection_reason": self.rejection_reason, "reviewer_comment": self.reviewer_comment,
                "decided_at": self.decided_at, "decided_by": self.decided_by}

    @classmethod
    def from_dict(cls, d: dict) -> "LeadReviewDecision":
        d = d or {}
        return cls(lead_id=d.get("lead_id", ""),
                   review_status=d.get("review_status", REVIEW_PENDING),
                   rejection_reason=d.get("rejection_reason", ""),
                   reviewer_comment=d.get("reviewer_comment", ""),
                   decided_at=d.get("decided_at", ""), decided_by=d.get("decided_by", ""))


@dataclass
class ReviewedLeadBatch:
    """An append-only set of human decisions over exactly one QualifiedLeadBatch, owned by one Market
    Hypothesis. Mirrors the LeadBatch/QualifiedLeadBatch ownership pattern: no mutators beyond the
    append-only ``record`` below; source artifacts are never touched."""
    batch_id: str = ""
    hypothesis_id: str = ""
    derived_from_qualified_batch: str = ""      # the source QualifiedLeadBatch.batch_id
    reviewed_by: str = ""
    created_at: str = ""
    decisions: list = field(default_factory=list)     # append-only list[LeadReviewDecision]

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = bk._new_id("rv")
        if not self.created_at:
            self.created_at = bk._now()

    # --- append-only recording ---------------------------------------------

    def record(self, lead_id: str, review_status: str, *, rejection_reason: str = "",
               reviewer_comment: str = "", decided_by: str = "") -> LeadReviewDecision:
        """Append a NEW immutable decision (never edits an existing one). The latest decision for a
        lead wins; earlier decisions remain as audit history."""
        decision = LeadReviewDecision(
            lead_id=lead_id, review_status=review_status, rejection_reason=rejection_reason,
            reviewer_comment=reviewer_comment, decided_by=decided_by or self.reviewed_by)
        self.decisions.append(decision)
        return decision

    # --- resolution ---------------------------------------------------------

    def current_decisions(self) -> dict:
        """lead_id -> the LATEST decision (last write wins; history preserved in ``decisions``)."""
        latest = {}
        for d in self.decisions:                       # ordered append-only, so the last one wins
            latest[d.lead_id] = d
        return latest

    def decision_for(self, lead_id: str):
        return self.current_decisions().get(_norm(lead_id))

    def history_for(self, lead_id: str) -> list:
        lid = _norm(lead_id)
        return [d for d in self.decisions if d.lead_id == lid]

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id, "hypothesis_id": self.hypothesis_id,
            "derived_from_qualified_batch": self.derived_from_qualified_batch,
            "reviewed_by": self.reviewed_by, "created_at": self.created_at,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewedLeadBatch":
        d = d or {}
        return cls(batch_id=d.get("batch_id", ""), hypothesis_id=d.get("hypothesis_id", ""),
                   derived_from_qualified_batch=d.get("derived_from_qualified_batch", ""),
                   reviewed_by=d.get("reviewed_by", ""), created_at=d.get("created_at", ""),
                   decisions=[LeadReviewDecision.from_dict(x) for x in d.get("decisions", [])])


# --- statistics --------------------------------------------------------------

def review_statistics(total_leads: int, reviewed_batch) -> dict:
    """Deterministic review progress over ``total_leads`` qualified leads. Leads with no decision are
    Pending. Pure counting — no scoring, no priority logic."""
    current = reviewed_batch.current_decisions() if reviewed_batch is not None else {}
    counts = {s: 0 for s in REVIEW_STATUSES}
    for d in current.values():
        counts[d.review_status] = counts.get(d.review_status, 0) + 1
    decided = sum(counts[s] for s in _DECIDED)
    counts[REVIEW_PENDING] = max(total_leads - decided, 0)      # undecided leads are Pending
    return {
        "total": total_leads,
        "pending": counts[REVIEW_PENDING], "approved": counts[REVIEW_APPROVED],
        "rejected": counts[REVIEW_REJECTED], "skipped": counts[REVIEW_SKIPPED],
        "reviewed": decided, "remaining": max(total_leads - decided, 0),
        "progress_pct": round(100 * decided / total_leads) if total_leads else 0,
    }


# --- hypothesis-level helpers (thin; no extra service layer) -----------------

def get_or_create_review(hypothesis, qualified_batch_id: str, *, reviewed_by: str = ""):
    """Resolve the (single) ReviewedLeadBatch for a QualifiedLeadBatch within this hypothesis, creating
    and appending it on first use. One review artifact per qualified batch; decisions accumulate
    append-only inside it."""
    if not _norm(qualified_batch_id):
        raise LeadReviewError("A source QualifiedLeadBatch id is required.")
    existing = next((r for r in getattr(hypothesis, "reviewed_batches", [])
                     if r.derived_from_qualified_batch == qualified_batch_id), None)
    if existing is not None:
        return existing
    review = ReviewedLeadBatch(hypothesis_id=hypothesis.project_id,
                               derived_from_qualified_batch=qualified_batch_id,
                               reviewed_by=reviewed_by)
    hypothesis.reviewed_batches.append(review)
    hypothesis.touch()
    return review


def record_decision(hypothesis, qualified_batch_id: str, lead_id: str, review_status: str, *,
                    rejection_reason: str = "", reviewer_comment: str = "", decided_by: str = ""):
    """Record ONE human decision (append-only). Never mutates the Lead or the QualifiedLead."""
    review = get_or_create_review(hypothesis, qualified_batch_id, reviewed_by=decided_by)
    decision = review.record(lead_id, review_status, rejection_reason=rejection_reason,
                             reviewer_comment=reviewer_comment, decided_by=decided_by)
    hypothesis.touch()
    return decision


def record_bulk(hypothesis, qualified_batch_id: str, lead_ids, review_status: str, *,
                rejection_reason: str = "", decided_by: str = "") -> int:
    """Record the same decision for an explicit set of leads (append-only). Returns the count applied.
    Only the ids passed in are affected — never a hidden or unrelated row."""
    ids = [i for i in (lead_ids or []) if _norm(i)]
    if not ids:
        return 0
    review = get_or_create_review(hypothesis, qualified_batch_id, reviewed_by=decided_by)
    for lead_id in ids:
        review.record(lead_id, review_status, rejection_reason=rejection_reason,
                      decided_by=decided_by)
    hypothesis.touch()
    return len(ids)
