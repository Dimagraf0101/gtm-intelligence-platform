"""Human Review → canonical export adapter (Sprint 13b).

A **thin, pure projection** from the Human Review view model into the canonical export rows:

    ReviewRow (view model) → MAIN_COLUMNS / AI_COLUMNS row dicts → existing XLSX / CSV writers

It only maps, formats, and counts. It never reconstructs names, queries a source system, infers a
missing value, reruns qualification, modifies priority, enriches a record, or invents anything —
unknown stays empty. The canonical schema (`export.MAIN_COLUMNS` / `export.AI_COLUMNS`), its column
names and their order are reused verbatim and never redefined here.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import export
import lead_review as lr
import review_view as rv


def _join(values) -> str:
    return "; ".join(str(v) for v in (values or []) if str(v).strip())


def main_row(row, index: int) -> dict:
    """One canonical working-sheet row, keyed by ``export.MAIN_COLUMNS``. Direct mapping + formatting."""
    return {
        "#": index,
        "Lead Score": int(row.score or 0),
        "Priority": row.priority,
        "First Name": row.first_name,
        "Last Name": row.last_name,
        "Job Title": row.title,
        "Job Started": row.job_started,
        "LinkedIn URL": row.linkedin_url,
        "Number of Connections": row.connections,
        "Location": row.location,
        "Company": row.company,
        "Company LinkedIn URL": row.company_linkedin_url,
        "Company Website": row.company_website,
        "LinkedIn Employees": row.company_size,
        "LinkedIn Founded Year": row.founded_year,
        "LinkedIn Industry": row.industry,
        "LinkedIn Specialities": row.specialities,
        "ICP Signals": _join(row.signals),
        "Score Reason": row.reason,
        "Score Breakdown": row.score_breakdown,
        "Review Status": row.review_status,
        "Human Decision": row.human_decision,      # DERIVED from review_status (never stored)
        "Rejection Reason": row.rejection_reason,
        "Reviewer Comment": row.reviewer_comment,
    }


def ai_row(row, index: int) -> dict:
    """One canonical audit-sheet row, keyed by ``export.AI_COLUMNS``."""
    return {
        "#": index,
        "LinkedIn URL": row.linkedin_url,
        "Internal Decision": row.internal_category,
        "Raw ICP Score": row.raw_icp_score,
        "Operational Lead Score": row.operational_lead_score,
        "Evidence-Adjusted Fit": row.evidence_adjusted_fit,
        "Data Coverage": row.evidence_coverage,
        "Decision Confidence": row.decision_confidence,
        "Evidence Summary": _join(row.signals),
        "Confirmed Dealbreakers": _join(row.confirmed_dealbreakers),
        "Suspected Dealbreakers": _join(row.suspected_dealbreakers),
        "Unknown Fields": _join(row.unknown_fields),
        "Confidence Reasons": _join(row.confidence_reasons),
        "Validation Warnings": _join(row.validation_warnings),
        "Review Recommendation": row.review_recommendation,
        "Dealbreaker State": row.dealbreaker_state,
        "Model Confidence": row.model_confidence,
        "Mock Result": "MOCK/OFFLINE" if row.is_mock else "real",
    }


def build_main_rows(rows) -> list:
    return [main_row(r, i) for i, r in enumerate(rv.sort_rows(rows), start=1)]


def build_ai_rows(rows) -> list:
    return [ai_row(r, i) for i, r in enumerate(rv.sort_rows(rows), start=1)]


def build_summary_rows(rows, campaign: str, icp_name: str, generated_at: str) -> list:
    """Deterministic counting only — mirrors the canonical summary layout and fills the review counters
    the legacy path could only leave at zero."""
    ordered = rv.sort_rows(rows)
    total = len(ordered)
    counts = rv.priority_distribution(ordered)
    excluded = counts.get("Disqualified", 0)
    mock = sum(1 for r in ordered if r.is_mock)
    status_counts = {s: sum(1 for r in ordered if r.review_status == s) for s in lr.REVIEW_STATUSES}
    internal = {}
    for r in ordered:
        label = r.internal_category or r.priority
        internal[label] = internal.get(label, 0) + 1

    out = [("Campaign", campaign), ("ICP", icp_name), ("Generated", generated_at),
           ("", ""), ("Total processed", total), ("Qualified", total - excluded),
           ("Disqualified / Excluded", excluded), ("Successful", total), ("Failed", 0),
           ("", ""), ("Priority distribution", "")]
    for label in rv.PRIORITY_ORDER:
        out.append((f"  {label}", counts.get(label, 0)))
    out += [("", ""), ("Internal category distribution", "")]
    for label, count in sorted(internal.items(), key=lambda kv: -kv[1]):
        out.append((f"  {label}", count))
    out += [
        ("", ""),
        ("Review — Pending", status_counts[lr.REVIEW_PENDING]),
        ("Review — Approved", status_counts[lr.REVIEW_APPROVED]),
        ("Review — Rejected", status_counts[lr.REVIEW_REJECTED]),
        ("Review — Skipped", status_counts[lr.REVIEW_SKIPPED]),
        ("", ""), ("Real results", total - mock), ("Mock results", mock),
        ("", ""),
        ("Note", "Human approval is required before any outreach. Filter Human Decision = Approved "
                 "and copy LinkedIn URLs into Linked Helper manually."),
    ]
    return out


# --- export scopes -----------------------------------------------------------

SCOPE_APPROVED = "approved"
SCOPE_SELECTED = "selected"
SCOPE_ALL = "all"


def select_rows(rows, scope: str = SCOPE_APPROVED, *, selected_ids=None) -> list:
    """Resolve the export scope. Default is **Approved only** — the point of Human Review is to ship
    what a human approved."""
    if scope == SCOPE_ALL:
        return list(rows)
    if scope == SCOPE_SELECTED:
        ids = set(selected_ids or [])
        return [r for r in rows if r.lead_id in ids]
    return rv.approved_rows(rows)


# --- serialization (reuses the existing canonical writers) -------------------

def to_workbook_bytes(rows, *, icp_name: str = "", campaign: str = "",
                      generated_at: str | None = None) -> bytes:
    """Serialize review rows into the canonical three-sheet workbook via the existing writers."""
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    campaign = campaign or icp_name
    return export.workbook_bytes_from_rows(
        build_main_rows(rows), build_ai_rows(rows),
        build_summary_rows(rows, campaign, icp_name, generated_at))


def to_main_dataframe(rows) -> pd.DataFrame:
    """Canonical working-sheet DataFrame (columns + order exactly ``export.MAIN_COLUMNS``)."""
    return pd.DataFrame(build_main_rows(rows), columns=export.MAIN_COLUMNS)


def to_main_csv_bytes(rows) -> bytes:
    return export.to_main_csv_bytes(to_main_dataframe(rows))
