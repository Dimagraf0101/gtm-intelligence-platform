"""ICP Approval — a small deterministic gate over one reviewed GeneratedICP version (Sprint 5.5).

A human may approve one specific reviewed ``GeneratedICP`` only when, deterministically:

  1. Strategy Review is complete;
  2. IQS has no blocking errors;
  3. every IQS warning has been explicitly acknowledged (per exact draft fingerprint);
  4. the draft still matches its StrategyDecisions (not stale, fingerprint matches);
  5. the human performs the explicit approval action with a named approver.

Approval never mutates the reviewed draft, Company/Project Knowledge, StrategyDecisions, ICPProfile,
scoring, or app.py. It deep-copies the reviewed draft, sets ``status=Approved``, appends a
``HistoryEntry``, and stores an append-only ``ApprovalRecord`` plus the immutable Approved version on
the project. The Approved version may later be consumed unchanged by ``icp_adapter`` (verified, not
wired to scoring here).

Everything here is deterministic Python: Python validates, the human approves. No LLM, no new schema —
it reuses ``GeneratedICP`` / ``iqs_validator`` / ``strategy_review``.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, asdict, field
from datetime import date
from typing import Optional

import generated_icp as gi
import iqs_validator as iqs
import strategy_review as sr
import knowledge_gaps as kg
# Canonical ICP identity is owned by the low-level icp_identity module (Sprint 5.7B). Approval and
# strategy_review both depend downward on it, which removes the previous strategy<->approval cycle.
# Re-exported here so existing callers of ``icp_approval.fingerprint_generated_icp`` keep working.
from icp_identity import (       # noqa: F401  (re-exported public identity API)
    fingerprint_generated_icp,
    content_fingerprint,
    warning_id,
    _norm,
)

IQS_VERSION = "1.0"

# ICP statuses a draft may hold and still be a candidate for approval.
_APPROVABLE_STATUSES = (gi.STATUS_DRAFT, gi.STATUS_NEEDS_INFO, gi.STATUS_READY)


def warnings_with_ids(draft: gi.GeneratedICP, iqs_result=None) -> list:
    """[(warning_id, message)] for the draft's IQS warnings, ids scoped to this draft's fingerprint."""
    result = iqs_result if iqs_result is not None else iqs.validate(draft)
    fp = fingerprint_generated_icp(draft)
    return [(warning_id(fp, w), w) for w in result.warnings]


# --- domain structures -------------------------------------------------------

@dataclass
class ApprovalRecord:
    project_id: str = ""
    icp_version: str = ""
    approved_by: str = ""
    approved_at: str = ""
    iqs_version: str = IQS_VERSION
    acknowledged_warning_ids: list = field(default_factory=list)
    approval_note: str = ""
    draft_fingerprint: str = ""
    strategy_revision: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalRecord":
        return cls(
            project_id=d.get("project_id", ""), icp_version=d.get("icp_version", ""),
            approved_by=d.get("approved_by", ""), approved_at=d.get("approved_at", ""),
            iqs_version=d.get("iqs_version", IQS_VERSION),
            acknowledged_warning_ids=list(d.get("acknowledged_warning_ids", [])),
            approval_note=d.get("approval_note", ""),
            draft_fingerprint=d.get("draft_fingerprint", ""),
            strategy_revision=d.get("strategy_revision", 0))


@dataclass
class ApprovalCheck:
    can_approve: bool = False
    blocking_reasons: list = field(default_factory=list)
    unacknowledged_warnings: list = field(default_factory=list)
    iqs_result: Optional[iqs.ValidationResult] = None
    strategy_complete: bool = False
    stale_strategy: bool = False
    draft_fingerprint: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["iqs_result"] = (None if self.iqs_result is None else {
            "is_valid": self.iqs_result.is_valid,
            "blocking_errors": list(self.iqs_result.blocking_errors),
            "warnings": list(self.iqs_result.warnings),
            "completeness_score": self.iqs_result.completeness_score,
        })
        return d


class ApprovalError(ValueError):
    """Raised when approval is attempted while ineligible."""


# --- eligibility -------------------------------------------------------------

def _company_core_conflicts(draft: gi.GeneratedICP) -> list:
    """Core-category conflicts recorded on the draft (from composed knowledge). Surfaced so the user
    is directed to Knowledge Review — Approval never resolves or mutates knowledge."""
    out = []
    for a in draft.ambiguous_definitions:
        category = str(a).split("/", 1)[0].strip().lower()
        if category in kg.CORE_CONFLICT_CATEGORIES:
            out.append(a)
    return out


def check_approval_eligibility(project, draft: gi.GeneratedICP, *,
                               acknowledged_warning_ids=None) -> ApprovalCheck:
    """Deterministically decide whether ``draft`` may be approved for ``project``. Returns ALL
    blocking reasons; never fixes anything. Unknown is treated as blocking, not guessed."""
    ack = set(acknowledged_warning_ids or [])
    reasons: list[str] = []
    fp = fingerprint_generated_icp(draft)
    strategy = getattr(project, "strategy", None)

    # ownership / presence / identity
    in_versions = any(d is draft for d in getattr(project, "draft_versions", []))
    if not in_versions:
        reasons.append("Draft is not one of this ICP Project's versions (wrong project or not a "
                       "stored reviewed version).")
    if not _norm(draft.metadata.version) or not _norm(draft.metadata.name):
        reasons.append("Draft is missing required version metadata (name and version).")
    if draft.metadata.status == gi.STATUS_APPROVED:
        reasons.append("Draft is already Approved.")
    elif draft.metadata.status not in _APPROVABLE_STATUSES:
        reasons.append(f"Draft status '{draft.metadata.status}' cannot be approved.")

    # strategy presence / ownership / completeness / staleness / match
    strategy_complete = False
    stale = False
    if strategy is None:
        reasons.append("No Strategy Review exists for this project.")
    elif getattr(strategy, "project_id", None) != project.project_id:
        reasons.append("Strategy decisions belong to a different project.")
    else:
        base = getattr(strategy, "based_on_draft", None)
        if base is None:
            reasons.append("Strategy Review has no proposed draft to validate completeness against.")
        else:
            strategy_complete = sr.is_review_complete(strategy, base)
            if not strategy_complete:
                reasons.append("Strategy Review is not complete.")
            st = sr.stale_decisions(strategy, base)
            stale = bool(st["dimensions"] or st["exclusions"])
            if stale:
                reasons.append("Strategy has stale decisions; rebase or discard them in Strategy "
                               "Review before approval.")
        # the draft must be the exact reviewed output of this strategy (stable fingerprint match)
        reviewed_fp = getattr(strategy, "reviewed_fingerprint", "")
        if not reviewed_fp:
            reasons.append("This project has no reviewed Draft ICP produced from Strategy Review.")
        elif reviewed_fp != content_fingerprint(draft):
            reasons.append("Draft does not match the current Strategy Review output "
                           "(version/fingerprint mismatch); regenerate the reviewed draft.")

    # IQS
    result = iqs.validate(draft)
    if result.blocking_errors:
        reasons.append("IQS has blocking errors: " + "; ".join(result.blocking_errors))

    # company-owned core conflicts (honest hand-off; never auto-resolved)
    core_conflicts = _company_core_conflicts(draft)
    if core_conflicts:
        reasons.append("Unresolved core conflict(s) remain — resolve them in Knowledge Review: "
                       + "; ".join(core_conflicts))

    # warning acknowledgement (scoped to this exact fingerprint)
    unacked = [w for (wid, w) in warnings_with_ids(draft, result) if wid not in ack]
    if unacked:
        reasons.append(f"{len(unacked)} IQS warning(s) not acknowledged.")

    return ApprovalCheck(
        can_approve=(not reasons), blocking_reasons=reasons, unacknowledged_warnings=unacked,
        iqs_result=result, strategy_complete=strategy_complete, stale_strategy=stale,
        draft_fingerprint=fp)


# --- approve -----------------------------------------------------------------

def approve_icp_version(project, draft: gi.GeneratedICP, *, approved_by: str,
                        acknowledged_warning_ids=None, approval_note: str = "") -> gi.GeneratedICP:
    """Approve one specific reviewed draft. Runs eligibility first and raises ``ApprovalError`` when
    ineligible. Never mutates the input draft: returns a deep-copied Approved version, stores it and
    an append-only ``ApprovalRecord`` on the project, and makes it the active approved version."""
    if not _norm(approved_by):
        raise ApprovalError("Approval requires an explicit approver name (approved_by).")
    ack = list(acknowledged_warning_ids or [])
    check = check_approval_eligibility(project, draft, acknowledged_warning_ids=ack)
    if not check.can_approve:
        raise ApprovalError("Cannot approve: " + " | ".join(check.blocking_reasons))

    strategy = project.strategy
    now = _now()
    approved = copy.deepcopy(draft)
    approved.metadata.status = gi.STATUS_APPROVED
    approved.metadata.updated_date = date.today().isoformat()
    approved.history = list(approved.history) + [gi.HistoryEntry(
        version=approved.metadata.version, date=now, author=approved_by,
        change_summary=(f"Approved by {approved_by} (IQS v{IQS_VERSION}, strategy revision "
                        f"{strategy.revision}, {len(ack)} warning(s) acknowledged)."
                        + (f" Note: {approval_note}" if approval_note else "")))]

    record = ApprovalRecord(
        project_id=project.project_id, icp_version=approved.metadata.version, approved_by=approved_by,
        approved_at=now, iqs_version=IQS_VERSION, acknowledged_warning_ids=sorted(set(ack)),
        approval_note=approval_note, draft_fingerprint=check.draft_fingerprint,
        strategy_revision=strategy.revision)

    project.approved_versions.append(approved)                 # immutable, separate store
    project.approval_records.append(record)                    # append-only audit
    project.active_approved_version = fingerprint_generated_icp(approved)   # approving activates it
    project.touch()
    return approved


# --- active approved version -------------------------------------------------

def get_active_approved_icp(project) -> Optional[gi.GeneratedICP]:
    """The project's active Approved ICP as a SAFE COPY (mutating it cannot affect stored history),
    or None. Selection is explicit by stable fingerprint — never by list order."""
    fp = getattr(project, "active_approved_version", None)
    if not fp:
        return None
    for icp in project.approved_versions:
        if fingerprint_generated_icp(icp) == fp:
            return copy.deepcopy(icp)
    return None


def readiness_state(check: ApprovalCheck) -> str:
    """Derived, display-only status (Sprint 5.5 keeps reviewed artifacts as Draft; 'Ready for Review'
    is a computed UI state, never a stored mutation). Returns a GeneratedICP status label."""
    if check.can_approve:
        return gi.STATUS_READY
    if check.iqs_result is not None and check.iqs_result.blocking_errors:
        return gi.STATUS_NEEDS_INFO
    return gi.STATUS_DRAFT


def _now() -> str:
    import business_knowledge as bk
    return bk._now()


# --- thin workspace ----------------------------------------------------------

class ApprovalWorkspace:
    """Approval workflow over one ICP Project. All rules live here; the page stays a view."""

    def __init__(self, project):
        self.project = project
        # acknowledged warning ids, scoped per draft fingerprint (in-memory, per session)
        self._acks: dict = {}

    def versions(self) -> list:
        """Reviewed Draft versions available to approve (newest last)."""
        return list(self.project.draft_versions)

    def eligibility(self, draft: gi.GeneratedICP) -> ApprovalCheck:
        fp = fingerprint_generated_icp(draft)
        return check_approval_eligibility(self.project, draft,
                                          acknowledged_warning_ids=self._acks.get(fp, set()))

    def warnings(self, draft: gi.GeneratedICP) -> list:
        """[(warning_id, message, acknowledged_bool)] for this draft."""
        fp = fingerprint_generated_icp(draft)
        acked = self._acks.get(fp, set())
        return [(wid, w, wid in acked) for (wid, w) in warnings_with_ids(draft)]

    def acknowledge_warning(self, draft: gi.GeneratedICP, wid: str) -> None:
        fp = fingerprint_generated_icp(draft)
        self._acks.setdefault(fp, set()).add(wid)

    def unacknowledge_warning(self, draft: gi.GeneratedICP, wid: str) -> None:
        fp = fingerprint_generated_icp(draft)
        self._acks.get(fp, set()).discard(wid)

    def approve(self, draft: gi.GeneratedICP, *, approved_by: str,
                approval_note: str = "") -> gi.GeneratedICP:
        fp = fingerprint_generated_icp(draft)
        return approve_icp_version(self.project, draft, approved_by=approved_by,
                                   acknowledged_warning_ids=self._acks.get(fp, set()),
                                   approval_note=approval_note)

    def active_approved(self) -> Optional[gi.GeneratedICP]:
        return get_active_approved_icp(self.project)

    def history(self) -> list:
        return list(self.project.approval_records)
