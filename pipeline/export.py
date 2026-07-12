"""Project scoring results into output tables and export them.

Two layers:

* **Legacy** (unchanged): ``COLUMNS`` + ``build_dataframe`` + ``to_csv_bytes`` + ``to_xlsx_bytes``.
* **Release 0.3.1 lead-generator workbook**: a three-sheet XLSX modelled on the team's reference
  workbook (``data/reference/fintech_scored_output_example.xlsx``) —
  ``Fintech Leads Scored`` (the daily working sheet) · ``AI Details`` (audit/technical) · ``Summary``.

The working sheet stays compact (single-height rows, no wrapped technical columns); all AI audit
fields live on a secondary sheet. Reviewer fields are always emitted empty; no lead is auto-approved;
no Google Sheets API, no chain-of-thought, no raw JSON is exported.
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
    """LEGACY canonical DataFrame. Preserved for backward compatibility."""
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


def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Leads") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Leads")
    return buf.getvalue()


# ===========================================================================
# Release 0.3.1 — lead-generator workbook (3 sheets)
# ===========================================================================

SHEET_MAIN = "Fintech Leads Scored"
SHEET_AI = "AI Details"
SHEET_SUMMARY = "Summary"
REVIEW_STATUS_DEFAULT = "Pending"

# Sheet 1 — the daily working sheet (exact order required by the spec)
MAIN_COLUMNS = [
    "#", "Lead Score", "Priority Status", "First Name", "Last Name", "Job Title", "Job Started",
    "LinkedIn URL", "Number of Connections", "Location", "Company", "Company LinkedIn URL",
    "Company Website", "LinkedIn Employees", "LinkedIn Founded Year", "LinkedIn Industry",
    "LinkedIn Specialities", "ICP Signals", "Score Reason", "Score Breakdown",
    "Review Status", "Human Decision", "Rejection Reason", "Reviewer Comment",
]

# Sheet 2 — technical / audit fields only
AI_COLUMNS = [
    "#", "LinkedIn URL", "Evidence-Adjusted Fit", "Data Coverage", "Decision Confidence",
    "Evidence Summary", "Confirmed Dealbreakers", "Suspected Dealbreakers", "Unknown Fields",
    "Confidence Reasons", "Validation Warnings", "Review Recommendation", "Dealbreaker State",
    "Model Confidence", "Mock Result",
]

_MAIN_URLS = {"LinkedIn URL", "Company LinkedIn URL", "Company Website"}
_MAIN_WIDTHS = {
    "#": 5, "Lead Score": 9, "Priority Status": 14, "First Name": 13, "Last Name": 13,
    "Job Title": 30, "Job Started": 11, "LinkedIn URL": 36, "Number of Connections": 12,
    "Location": 26, "Company": 22, "Company LinkedIn URL": 30, "Company Website": 28,
    "LinkedIn Employees": 14, "LinkedIn Founded Year": 13, "LinkedIn Industry": 22,
    "LinkedIn Specialities": 34, "ICP Signals": 30, "Score Reason": 50, "Score Breakdown": 30,
    "Review Status": 13, "Human Decision": 15, "Rejection Reason": 20, "Reviewer Comment": 22,
}
_AI_URLS = {"LinkedIn URL"}
_AI_WRAP = {"Evidence Summary", "Confirmed Dealbreakers", "Suspected Dealbreakers", "Unknown Fields",
            "Confidence Reasons", "Validation Warnings"}
_AI_WIDTHS = {
    "#": 5, "LinkedIn URL": 34, "Evidence-Adjusted Fit": 16, "Data Coverage": 12,
    "Decision Confidence": 15, "Evidence Summary": 50, "Confirmed Dealbreakers": 34,
    "Suspected Dealbreakers": 34, "Unknown Fields": 30, "Confidence Reasons": 40,
    "Validation Warnings": 40, "Review Recommendation": 18, "Dealbreaker State": 15,
    "Model Confidence": 15, "Mock Result": 13,
}


def _pct(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return f"{int(value)}%"


def _evidence_summary(result: ScoringResult) -> str:
    parts = [f"{name}: {d.evidence}" for name, d in result.dimensions.items() if d.evidence]
    return " | ".join(parts)[:500]


def _get(result, attr, default):
    v = getattr(result, attr, default)
    return default if v is None else v


def build_main_rows(pairs: list[tuple[Lead, ScoringResult]]) -> list[dict]:
    rows = []
    for i, (lead, result) in enumerate(pairs, start=1):
        f = lead.fields
        rows.append({
            "#": i,
            "Lead Score": result.raw_icp_score if result.raw_icp_score is not None else result.score,
            "Priority Status": _get(result, "provisional_priority", None) or result.category,
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
            "ICP Signals": "; ".join(result.signals or []),
            "Score Reason": result.reason, "Score Breakdown": _score_breakdown(result),
            "Review Status": REVIEW_STATUS_DEFAULT,
            "Human Decision": "", "Rejection Reason": "", "Reviewer Comment": "",
        })
    return rows


def build_ai_rows(pairs: list[tuple[Lead, ScoringResult]]) -> list[dict]:
    rows = []
    for i, (lead, result) in enumerate(pairs, start=1):
        rows.append({
            "#": i, "LinkedIn URL": lead.fields.get("linkedin_url", ""),
            "Evidence-Adjusted Fit": _pct(result.evidence_adjusted_fit),
            "Data Coverage": _pct(result.evidence_coverage),
            "Decision Confidence": _get(result, "decision_confidence", ""),
            "Evidence Summary": _evidence_summary(result),
            "Confirmed Dealbreakers": "; ".join(_get(result, "confirmed_dealbreakers", [])),
            "Suspected Dealbreakers": "; ".join(_get(result, "suspected_dealbreakers", [])),
            "Unknown Fields": "; ".join(result.unknowns or []),
            "Confidence Reasons": "; ".join(_get(result, "confidence_reasons", [])),
            "Validation Warnings": "; ".join(_get(result, "validation_warnings", [])),
            "Review Recommendation": _get(result, "review_recommendation", ""),
            "Dealbreaker State": _get(result, "dealbreaker_state", ""),
            "Model Confidence": _get(result, "model_confidence", ""),
            "Mock Result": "MOCK/OFFLINE" if getattr(result, "is_mock", False) else "real",
        })
    return rows


def build_main_dataframe(pairs) -> pd.DataFrame:
    return pd.DataFrame(build_main_rows(pairs), columns=MAIN_COLUMNS)


def build_ai_dataframe(pairs) -> pd.DataFrame:
    return pd.DataFrame(build_ai_rows(pairs), columns=AI_COLUMNS)


def build_summary_rows(pairs, campaign: str, icp_name: str, generated_at: str):
    total = len(pairs)
    results = [r for _, r in pairs]
    success = sum(1 for r in results if r.error is None)
    priorities = Counter((r.provisional_priority or r.category) for r in results)
    excluded = sum(priorities.get(k, 0) for k in ("Disqualified", "Not Relevant"))
    qualified = success - excluded
    mock = sum(1 for r in results if getattr(r, "is_mock", False))

    rows = [
        ("Campaign", campaign), ("ICP", icp_name), ("Generated", generated_at),
        ("", ""), ("Total processed", total), ("Qualified", qualified),
        ("Disqualified / Excluded", excluded), ("Successful", success), ("Failed", total - success),
        ("", ""), ("Category distribution", ""),
    ]
    for label, count in priorities.most_common():
        rows.append((f"  {label}", count))
    rows += [
        ("", ""),
        ("Review — Pending", total), ("Review — Approved", 0),
        ("Review — Rejected", 0), ("Review — Skipped", 0),
        ("", ""), ("Real results", total - mock), ("Mock results", mock),
        ("", ""),
        ("Note", "Human approval is required before any outreach. Filter Human Decision = Approved "
                 "and copy LinkedIn URLs into Linked Helper manually."),
    ]
    return rows


# --- CSV fallbacks ----------------------------------------------------------

def to_main_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def to_ai_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


# --- workbook ---------------------------------------------------------------

def _write_table(ws, columns, rows, widths, url_cols, wrap_cols, *, freeze):
    header_font = Font(bold=True)
    link_font = Font(color="0563C1", underline="single")
    wrap = Alignment(wrap_text=True, vertical="top")
    for c, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = header_font
        ws.column_dimensions[cell.column_letter].width = widths.get(name, 16)
    for r, row in enumerate(rows, start=2):
        for c, name in enumerate(columns, start=1):
            value = row.get(name, "")
            cell = ws.cell(row=r, column=c, value=value)
            if name in url_cols and isinstance(value, str) and value.startswith(("http://", "https://")):
                cell.hyperlink = value
                cell.font = link_font
            if name in wrap_cols:
                cell.alignment = wrap
    ws.freeze_panes = freeze
    last_col = ws.cell(row=1, column=len(columns)).column_letter
    ws.auto_filter.ref = f"A1:{last_col}{max(1, len(rows) + 1)}"


def _write_summary(ws, summary_rows):
    ws.cell(row=1, column=1, value="Metric").font = Font(bold=True)
    ws.cell(row=1, column=2, value="Value").font = Font(bold=True)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 62
    for r, (metric, value) in enumerate(summary_rows, start=2):
        ws.cell(row=r, column=1, value=metric).font = Font(bold=metric in (
            "Campaign", "ICP", "Category distribution", "Total processed"))
        ws.cell(row=r, column=2, value=value).alignment = Alignment(wrap_text=True, vertical="top")


def to_workbook_bytes(pairs, icp_name: str, campaign: str | None = None,
                      generated_at: str | None = None) -> bytes:
    """Build the three-sheet lead-generator workbook: Fintech Leads Scored / AI Details / Summary."""
    campaign = campaign or icp_name
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    main_rows = build_main_rows(pairs)
    ai_rows = build_ai_rows(pairs)
    summary_rows = build_summary_rows(pairs, campaign, icp_name, generated_at)

    wb = Workbook()
    ws1 = wb.active
    ws1.title = SHEET_MAIN
    # main working sheet: single-height rows (no wrap), freeze at D2 so #, Lead Score, Priority stay visible
    _write_table(ws1, MAIN_COLUMNS, main_rows, _MAIN_WIDTHS, _MAIN_URLS, set(), freeze="D2")
    ws2 = wb.create_sheet(SHEET_AI)
    _write_table(ws2, AI_COLUMNS, ai_rows, _AI_WIDTHS, _AI_URLS, _AI_WRAP, freeze="B2")
    ws3 = wb.create_sheet(SHEET_SUMMARY)
    _write_summary(ws3, summary_rows)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
