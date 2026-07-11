"""Project scoring results into the canonical output table and export to XLSX/CSV.

The column layout matches the human-facing scoring documents the sales team already
uses (see Lead_Scoring_Guide.xlsx), plus two transparency columns from the engine
(Confidence, Unknowns). The XLSX/CSV import cleanly into Google Sheets.
"""
from __future__ import annotations

import io

import pandas as pd

from scoring import Lead, ScoringResult

# Canonical, ordered output columns.
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
    """Build the canonical DataFrame from (Lead, ScoringResult) pairs (already ranked)."""
    rows = []
    for i, (lead, result) in enumerate(pairs, start=1):
        f = lead.fields
        rows.append({
            "#": i,
            "Lead Score": result.score,
            "Priority Status": result.category,
            "First Name": f.get("first_name", ""),
            "Last Name": f.get("last_name", ""),
            "Job Title": f.get("job_title", ""),
            "Job Started": f.get("job_started", ""),
            "LinkedIn URL": f.get("linkedin_url", ""),
            "Number of Connections": f.get("connections", ""),
            "Location": f.get("location", ""),
            "Company": f.get("company", ""),
            "Company LinkedIn URL": f.get("company_linkedin_url", ""),
            "Company Website": f.get("company_website", ""),
            "LinkedIn Employees": f.get("company_size_range", ""),
            "LinkedIn Founded Year": f.get("founded_year", ""),
            "LinkedIn Industry": f.get("industry", ""),
            "LinkedIn Specialities": f.get("specialities", ""),
            "ICP Signals": "; ".join(result.signals),
            "Score Reason": result.reason,
            "Score Breakdown": _score_breakdown(result),
            "Confidence": result.confidence,
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
