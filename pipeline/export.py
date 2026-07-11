"""Project scoring results into output tables and export them.

Two layers:

* **Legacy** (unchanged, backward-compatible): ``COLUMNS`` + ``build_dataframe`` + ``to_csv_bytes``
  + ``to_xlsx_bytes`` — the original single flat table. Existing callers keep working.
* **Release 0.3 human-review workbook**: a three-sheet XLSX (Qualified Leads / Approved for
  Outreach / Summary) shaped to the team's reference workbook, plus matching CSV fallbacks.

Reviewer fields are always emitted empty; no lead is ever auto-approved; Linked Helper import
stays manual. No Google Sheets API, no chain-of-thought, no raw JSON is exported.
"""
from __future__ import annotations

import io
from collections import Counter
from datetime import datetime

import pandas as pd
from openpyxl import Workbook, load_workbook  # noqa: F401  (load_workbook used by tests)
from openpyxl.styles import Alignment, Font

from scoring import Lead, ScoringResult

# ===========================================================================
# Legacy flat table (unchanged — backward compatible)
# ===========================================================================

COLUMNS = [
    "#", "Lead Score", "Priority Status",
    "First Name", "Last Name", "Job Title", "Job Started",
    "LinkedIn URL", "Number of Connections", "Location",
    "Company", "Company LinkedIn URL", "Company Website",
    "LinkedIn Employees", "LinkedIn Founded Year", "LinkedIn Industry",
    "LinkedIn Specialities",
    "ICP Signals", "Score Reason", "Score Breakdown",
    "Confidence", "Unknowns",
]


def _score_breakdown(result: ScoringResult) -> str:
    labels = {"title": "Title", "industry": "Industry", "company_size": "Size",
              "location": "Loc", "signals": "Signals"}
    return " ".join(f"{labels.get(k, k)}:{d.points}" for k, d in result.dimensions.items())


def build_dataframe(pairs: list[tuple[Lead, ScoringResult]]) -> pd.DataFrame:
    """LEGACY: the original canonical DataFrame. Preserved for backward compatibility."""
    rows = []
    for i, (lead, result) in enumerate(pairs, start=1):
        f = lead.fields
        rows.append({
            "#": i, "Lead Score": result.score, "Priority Status": result.category,
            "First Name": f.get("first_name", ""), "Last Name": f.get("last_name", ""),
            "Job Title": f.get("job_title", ""), "Job Started": f.get("job_started", ""),
            "LinkedIn URL": f.get("linkedin_url", ""), "Number of Connections": f.get("connections", ""),
            "Location": f.get("location", ""), "Company": f.get("company", ""),
            "Company LinkedIn URL": f.get("company_linkedin_url", ""),
            "Company Website": f.get("company_website", ""),
            "LinkedIn Employees": f.get("company_size_range", ""),
            "LinkedIn Founded Year": f.get("founded_year", ""),
            "LinkedIn Industry": f.get("industry", ""),
            "LinkedIn Specialities": f.get("specialities", ""),
            "ICP Signals": "; ".join(result.signals), "Score Reason": result.reason,
            "Score Breakdown": _score_breakdown(result), "Confidence": result.confidence,
            "Unknowns": "; ".join(result.unknowns),
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Qualified Leads") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Leads")
    return buf.getvalue()


# ===========================================================================
# Release 0.3 — human-review-ready workbook
# ===========================================================================

REVIEW_STATUS_DEFAULT = "Pending"
DECISION_APPROVED = "Approved"

QUALIFIED_MAIN = [
    "#", "Priority Status", "Raw ICP Score", "Evidence-Adjusted Fit", "Data Coverage",
    "Confidence", "Review Status", "First Name", "Last Name", "Job Title", "Job Started",
    "LinkedIn URL", "Location", "Company", "Company LinkedIn URL", "Company Website",
    "LinkedIn Employees", "LinkedIn Industry", "Qualification Reason", "Evidence Summary",
    "Confirmed Dealbreakers", "Suspected Dealbreakers", "Unknown Fields",
    "Human Decision", "Rejection Reason", "Reviewer Comment",
]
QUALIFIED_SECONDARY = [
    "Number of Connections", "LinkedIn Founded Year", "LinkedIn Specialities", "ICP Signals",
    "Score Breakdown", "Confidence Reasons", "Validation Warnings", "Review Recommendation",
    "Dealbreaker State", "Model Confidence", "Mock Result",
]
QUALIFIED_COLUMNS = QUALIFIED_MAIN + QUALIFIED_SECONDARY

APPROVED_COLUMNS = [
    "First Name", "Last Name", "Job Title", "Company", "LinkedIn URL", "Location",
    "Priority Status", "Raw ICP Score", "Human Decision",
]

_URL_COLUMNS = {"LinkedIn URL", "Company LinkedIn URL", "Company Website"}
_WRAP_COLUMNS = {"Qualification Reason", "Evidence Summary", "Confirmed Dealbreakers",
                 "Suspected Dealbreakers", "Unknown Fields", "LinkedIn Specialities", "ICP Signals",
                 "Score Breakdown", "Confidence Reasons", "Validation Warnings", "Reviewer Comment"}
_WIDTHS = {
    "#": 5, "Priority Status": 26, "Raw ICP Score": 11, "Evidence-Adjusted Fit": 16,
    "Data Coverage": 12, "Confidence": 11, "Review Status": 13, "First Name": 14, "Last Name": 14,
    "Job Title": 26, "Job Started": 11, "LinkedIn URL": 38, "Location": 24, "Company": 22,
    "Company LinkedIn URL": 34, "Company Website": 30, "LinkedIn Employees": 15,
    "LinkedIn Industry": 22, "Qualification Reason": 48, "Evidence Summary": 48,
    "Confirmed Dealbreakers": 30, "Suspected Dealbreakers": 30, "Unknown Fields": 26,
    "Human Decision": 15, "Rejection Reason": 22, "Reviewer Comment": 28, "Number of Connections": 13,
    "LinkedIn Founded Year": 13, "LinkedIn Specialities": 40, "ICP Signals": 26, "Score Breakdown": 30,
    "Confidence Reasons": 40, "Validation Warnings": 40, "Review Recommendation": 18,
    "Dealbreaker State": 15, "Model Confidence": 15, "Mock Result": 13,
}


def _pct(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value                       # e.g. "unknown"
    return f"{int(value)}%"


def _evidence_summary(result: ScoringResult) -> str:
    """Short, human-readable evidence per dimension — no raw JSON, no chain-of-thought."""
    parts = [f"{name}: {d.evidence}" for name, d in result.dimensions.items() if d.evidence]
    return " | ".join(parts)[:500]


def _get(result: ScoringResult, attr, default):
    v = getattr(result, attr, default)
    return default if v is None else v


def build_qualified_rows(pairs: list[tuple[Lead, ScoringResult]]) -> list[dict]:
    """Native-Python row dicts for the Qualified Leads sheet (ranked order preserved)."""
    rows = []
    for i, (lead, result) in enumerate(pairs, start=1):
        f = lead.fields
        rows.append({
            "#": i,
            "Priority Status": _get(result, "provisional_priority", None) or result.category,
            "Raw ICP Score": _get(result, "raw_icp_score", None) if result.raw_icp_score is not None else result.score,
            "Evidence-Adjusted Fit": _pct(result.evidence_adjusted_fit),
            "Data Coverage": _pct(result.evidence_coverage),
            "Confidence": result.confidence,
            "Review Status": REVIEW_STATUS_DEFAULT,
            "First Name": f.get("first_name", ""), "Last Name": f.get("last_name", ""),
            "Job Title": f.get("job_title", ""), "Job Started": f.get("job_started", ""),
            "LinkedIn URL": f.get("linkedin_url", ""), "Location": f.get("location", ""),
            "Company": f.get("company", ""), "Company LinkedIn URL": f.get("company_linkedin_url", ""),
            "Company Website": f.get("company_website", ""),
            "LinkedIn Employees": f.get("company_size_range", ""),
            "LinkedIn Industry": f.get("industry", ""),
            "Qualification Reason": result.reason,
            "Evidence Summary": _evidence_summary(result),
            "Confirmed Dealbreakers": "; ".join(_get(result, "confirmed_dealbreakers", [])),
            "Suspected Dealbreakers": "; ".join(_get(result, "suspected_dealbreakers", [])),
            "Unknown Fields": "; ".join(result.unknowns or []),
            "Human Decision": "", "Rejection Reason": "", "Reviewer Comment": "",
            # secondary / internal
            "Number of Connections": f.get("connections", ""),
            "LinkedIn Founded Year": f.get("founded_year", ""),
            "LinkedIn Specialities": f.get("specialities", ""),
            "ICP Signals": "; ".join(result.signals or []),
            "Score Breakdown": _score_breakdown(result),
            "Confidence Reasons": "; ".join(_get(result, "confidence_reasons", [])),
            "Validation Warnings": "; ".join(_get(result, "validation_warnings", [])),
            "Review Recommendation": _get(result, "review_recommendation", ""),
            "Dealbreaker State": _get(result, "dealbreaker_state", ""),
            "Model Confidence": _get(result, "model_confidence", ""),
            "Mock Result": "MOCK/OFFLINE" if getattr(result, "is_mock", False) else "real",
        })
    return rows


def build_qualified_dataframe(pairs: list[tuple[Lead, ScoringResult]]) -> pd.DataFrame:
    return pd.DataFrame(build_qualified_rows(pairs), columns=QUALIFIED_COLUMNS)


def build_approved_dataframe(qdf: pd.DataFrame) -> pd.DataFrame:
    """Rows whose Human Decision is already 'Approved' (empty at export time — no auto-approval)."""
    approved = qdf[qdf["Human Decision"] == DECISION_APPROVED]
    return approved.reindex(columns=APPROVED_COLUMNS).reset_index(drop=True)


def build_summary_rows(pairs, icp_name: str, generated_at: str) -> list[tuple[str, object]]:
    total = len(pairs)
    results = [r for _, r in pairs]
    success = sum(1 for r in results if r.error is None)
    priorities = Counter((r.provisional_priority or r.category) for r in results)
    covs = [r.evidence_coverage for r in results if r.evidence_coverage is not None]
    confs = [r.decision_confidence for r in results if r.decision_confidence is not None]
    mock = sum(1 for r in results if getattr(r, "is_mock", False))

    rows: list[tuple[str, object]] = [
        ("Campaign / ICP", icp_name),
        ("Generated", generated_at),
        ("Total processed leads", total),
        ("Successful leads", success),
        ("Failed leads", total - success),
        ("", ""),
        ("Priority distribution", ""),
    ]
    for label, count in priorities.most_common():
        rows.append((f"  {label}", count))
    rows += [
        ("", ""),
        ("Disqualified", priorities.get("Disqualified", 0)),
        ("A+ Candidate — Enrichment Required", priorities.get("A+ Candidate — Enrichment Required", 0)),
        ("Confirmed dealbreakers", sum(1 for r in results if r.dealbreaker_state == "confirmed")),
        ("Suspected dealbreakers", sum(1 for r in results if r.dealbreaker_state == "suspected")),
        ("", ""),
        ("Review — Pending", total), ("Review — Approved", 0),
        ("Review — Rejected", 0), ("Review — Skipped", 0),
        ("", ""),
        ("Average Data Coverage", f"{round(sum(covs) / len(covs))}%" if covs else "n/a"),
        ("Average Decision Confidence", round(sum(confs) / len(confs)) if confs else "n/a"),
        ("Mock results", mock), ("Real results", total - mock),
        ("", ""),
        ("Note", "Human approval is required before any outreach. Linked Helper import is manual."),
    ]
    return rows


# --- CSV fallbacks ----------------------------------------------------------

def to_qualified_csv_bytes(qdf: pd.DataFrame) -> bytes:
    return qdf.to_csv(index=False).encode("utf-8-sig")


def to_approved_csv_bytes(adf: pd.DataFrame) -> bytes:
    return adf.to_csv(index=False).encode("utf-8-sig")


# --- workbook ---------------------------------------------------------------

def _write_table(ws, columns: list[str], rows: list[dict], *, freeze: bool, autofilter: bool):
    header_font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")
    link_font = Font(color="0563C1", underline="single")

    for c, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = header_font
        ws.column_dimensions[cell.column_letter].width = _WIDTHS.get(name, 16)

    for r, row in enumerate(rows, start=2):
        for c, name in enumerate(columns, start=1):
            value = row.get(name, "")
            cell = ws.cell(row=r, column=c, value=value)
            if name in _URL_COLUMNS and isinstance(value, str) and value.startswith(("http://", "https://")):
                cell.hyperlink = value
                cell.font = link_font
            cell.alignment = wrap if name in _WRAP_COLUMNS else top

    if freeze:
        ws.freeze_panes = "A2"
    if autofilter and rows is not None:
        last_col = ws.cell(row=1, column=len(columns)).column_letter
        ws.auto_filter.ref = f"A1:{last_col}{max(1, len(rows) + 1)}"


def _write_summary(ws, summary_rows: list[tuple[str, object]]):
    ws.cell(row=1, column=1, value="Metric").font = Font(bold=True)
    ws.cell(row=1, column=2, value="Value").font = Font(bold=True)
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 60
    for r, (metric, value) in enumerate(summary_rows, start=2):
        ws.cell(row=r, column=1, value=metric)
        ws.cell(row=r, column=2, value=value).alignment = Alignment(wrap_text=True, vertical="top")


def to_workbook_bytes(pairs: list[tuple[Lead, ScoringResult]], icp_name: str,
                      generated_at: str | None = None) -> bytes:
    """Build the three-sheet human-review workbook (Qualified Leads / Approved for Outreach / Summary)."""
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    qrows = build_qualified_rows(pairs)
    approved_rows = [{k: row.get(k, "") for k in APPROVED_COLUMNS}
                     for row in qrows if row.get("Human Decision") == DECISION_APPROVED]
    summary_rows = build_summary_rows(pairs, icp_name, generated_at)

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Qualified Leads"
    _write_table(ws1, QUALIFIED_COLUMNS, qrows, freeze=True, autofilter=True)
    ws2 = wb.create_sheet("Approved for Outreach")
    _write_table(ws2, APPROVED_COLUMNS, approved_rows, freeze=True, autofilter=True)
    ws3 = wb.create_sheet("Summary")
    _write_summary(ws3, summary_rows)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
