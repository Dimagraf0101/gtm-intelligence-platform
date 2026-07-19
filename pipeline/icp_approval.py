"""Draft → Approved gate (Sprint 2A) — the human approval act the IQS standard requires.

Approval is a **human act** (IQS v1.0 §10, PRD §1): the system never approves an ICP by itself.
This module implements the deterministic *gate* around that act:

  - only an ICP whose IQS validation passes (no blocking errors) may be approved;
  - IQS **warnings must be explicitly acknowledged** by the approver (they are never hidden);
  - the approval is recorded in the ICP's append-only history (who, when, what was acknowledged);
  - nothing else is changed — approval never edits facts, weights, or thresholds.

Pure Python: no LLM, no network, no persistence (``pipeline/icp_library.py`` persists the result).
"""
from __future__ import annotations

from datetime import date

import iqs_validator
from generated_icp import GeneratedICP, HistoryEntry, STATUS_APPROVED


class ApprovalError(ValueError):
    """Raised when an ICP cannot be approved (IQS failure or unacknowledged warnings)."""


def approve(icp: GeneratedICP, *, approved_by: str = "user",
            acknowledge_warnings: bool = False) -> GeneratedICP:
    """Apply the human approval act to ``icp`` (in place) and return it.

    Raises :class:`ApprovalError` when IQS has blocking errors, or when IQS warnings exist and
    ``acknowledge_warnings`` is not set. Approving an already-Approved ICP is a no-op.
    """
    if icp.metadata.status == STATUS_APPROVED:
        return icp

    report = iqs_validator.validate(icp)
    if not report.is_valid:
        raise ApprovalError(
            "Cannot approve — IQS blocking errors: " + "; ".join(report.blocking_errors))
    if report.warnings and not acknowledge_warnings:
        raise ApprovalError(
            f"Cannot approve — {len(report.warnings)} IQS warning(s) must be explicitly "
            "acknowledged by the approver first.")

    today = date.today().isoformat()
    icp.metadata.status = STATUS_APPROVED
    icp.metadata.updated_date = today
    summary = f"Approved (IQS v1.0 passed; {len(report.warnings)} warning(s) acknowledged)."
    icp.history.append(HistoryEntry(version=icp.metadata.version, date=today,
                                    author=approved_by, change_summary=summary))
    return icp
