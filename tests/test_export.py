"""Offline tests for the lead-generator workbook export (pipeline/export.py, Release 0.3.1).

No network, no API, no pytest dependency:
    ./.venv/bin/python tests/test_export.py
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import pandas as pd                    # noqa: E402
from openpyxl import load_workbook     # noqa: E402
import scoring as sc                   # noqa: E402
import export                          # noqa: E402

# technical fields that must NOT appear on the main working sheet
_TECHNICAL = {"Data Coverage", "Evidence-Adjusted Fit", "Decision Confidence", "Confidence",
              "Unknown Fields", "Confidence Reasons", "Validation Warnings", "Dealbreaker State",
              "Model Confidence", "Mock Result", "Evidence Summary"}


def _lead(idx, **fields):
    return sc.Lead(index=idx, fields=fields, raw={}, previous_roles=[])


def _result(idx, *, priority, raw, coverage=60, fit=83, conf=55, confidence="medium",
            db_state="none", confirmed=None, suspected=None, unknowns=None, is_mock=False, error=None):
    return sc.ScoringResult(
        lead_index=idx, icp="FinTech", score=raw, category=priority,
        dimensions={"Subsegment fit": sc.Dimension(20, 25, "wealth-tech platform")},
        hard_dealbreaker=(db_state == "confirmed"), dealbreaker_reason=None,
        reason="Sample qualification reason.", signals=["1000 connections"],
        confidence=confidence, unknowns=unknowns or [], model="claude-haiku", error=error,
        raw_icp_score=raw, provisional_priority=priority, evidence_coverage=coverage,
        evidence_adjusted_fit=fit, decision_confidence=conf, dealbreaker_state=db_state,
        confirmed_dealbreakers=confirmed or [], suspected_dealbreakers=suspected or [],
        review_recommendation="standard_review", confidence_reasons=[f"coverage {coverage}%"],
        validation_warnings=["enrichment guard: 'Reachability' removed"], model_confidence="low",
        is_mock=is_mock)


def _pairs():
    return [
        (_lead(0, first_name="Aaron", last_name="Wormus", job_title="CTO", company="SMArtX",
               linkedin_url="https://www.linkedin.com/in/aaron", location="FL, US",
               company_size_range="51-200", industry="Financial Services", connections="2206",
               founded_year="2018", specialities="WealthTech",
               company_linkedin_url="https://www.linkedin.com/company/smartx",
               company_website="https://smartx.com", job_started="03/2017"),
         _result(0, priority="A / High", raw=78, coverage=60, fit=92, conf=80, confidence="high",
                 db_state="suspected", suspected=["no hiring signal"],
                 unknowns=["Stage & funding fit", "Eng hiring signal"])),
        (_lead(1, first_name="Ditrick", last_name="Dunn", job_title="BD", company="Servicing Co",
               linkedin_url="https://www.linkedin.com/in/ditrickdunn", location="US",
               company_size_range="51-200", industry="Financial Services"),
         _result(1, priority="Disqualified", raw=32, coverage=60, conf=48, confidence="medium",
                 db_state="confirmed", confirmed=["Dev shop / BPO"], unknowns=["Reachability"])),
        (_lead(2, first_name="Mock", last_name="Lead", job_title="CEO", company="Placeholder",
               linkedin_url="https://www.linkedin.com/in/mock", location="US"),
         _result(2, priority="B / Normal", raw=45, coverage=100, fit=45, conf=40, confidence="low",
                 is_mock=True)),
    ]


def _wb(pairs=None):
    return load_workbook(io.BytesIO(export.to_workbook_bytes(pairs or _pairs(), "FinTech")))


def _sv(rows, key):
    for k, v in rows:
        if k == key:
            return v
    return None


# --- tests ------------------------------------------------------------------

def test_exactly_three_sheet_names():
    assert _wb().sheetnames == ["Fintech Leads Scored", "AI Details", "Summary"]
    assert _wb().sheetnames[0] == "Fintech Leads Scored"      # main sheet is first


def test_main_sheet_24_column_order():
    header = [c.value for c in _wb()["Fintech Leads Scored"][1]]
    assert header == export.MAIN_COLUMNS
    assert len(export.MAIN_COLUMNS) == 24
    assert list(export.build_main_dataframe(_pairs()).columns) == export.MAIN_COLUMNS


def test_main_sheet_freezes_at_d2():
    assert _wb()["Fintech Leads Scored"].freeze_panes == "D2"


def test_technical_fields_absent_from_main_sheet():
    header = set(c.value for c in _wb()["Fintech Leads Scored"][1])
    assert not (header & _TECHNICAL)


def test_technical_fields_present_on_ai_details():
    header = [c.value for c in _wb()["AI Details"][1]]
    assert header == export.AI_COLUMNS
    for col in ("Evidence-Adjusted Fit", "Data Coverage", "Decision Confidence", "Unknown Fields",
                "Confirmed Dealbreakers", "Suspected Dealbreakers", "Mock Result"):
        assert col in header


def test_reviewer_fields_blank_and_status_pending():
    mdf = export.build_main_dataframe(_pairs())
    assert (mdf["Review Status"] == "Pending").all()
    for col in ("Human Decision", "Rejection Reason", "Reviewer Comment"):
        assert (mdf[col] == "").all()


def test_urls_intact_and_clickable():
    ws = _wb()["Fintech Leads Scored"]
    header = [c.value for c in ws[1]]
    url_col = header.index("LinkedIn URL") + 1
    cell = ws.cell(row=2, column=url_col)
    assert cell.value == "https://www.linkedin.com/in/aaron"
    assert cell.hyperlink is not None
    assert ws.auto_filter.ref                               # filters enabled


def test_summary_values_correct():
    rows = export.build_summary_rows(_pairs(), "FinTech Campaign", "FinTech", "2026-07-12 00:00")
    assert _sv(rows, "Campaign") == "FinTech Campaign"
    assert _sv(rows, "ICP") == "FinTech"
    assert _sv(rows, "Total processed") == 3
    assert _sv(rows, "Qualified") == 2                       # 3 total - 1 disqualified
    assert _sv(rows, "Disqualified / Excluded") == 1
    assert _sv(rows, "Review — Pending") == 3
    assert _sv(rows, "Real results") == 2
    assert _sv(rows, "Mock results") == 1
    assert "approval is required" in _sv(rows, "Note")


def test_no_scoring_fields_lost_on_ai_details():
    adf = export.build_ai_dataframe(_pairs())
    assert list(adf.columns) == export.AI_COLUMNS
    assert adf.iloc[0]["Suspected Dealbreakers"] == "no hiring signal"
    assert adf.iloc[1]["Confirmed Dealbreakers"] == "Dev shop / BPO"
    assert "Stage & funding fit" in adf.iloc[0]["Unknown Fields"]
    assert adf.iloc[0]["Data Coverage"] == "60%"
    assert "MOCK/OFFLINE" in set(adf["Mock Result"]) and "real" in set(adf["Mock Result"])


def test_legacy_csv_and_dataframe_compatible():
    df = export.build_dataframe(_pairs())
    assert list(df.columns) == export.COLUMNS and len(df) == 3
    csv_bytes = export.to_csv_bytes(df)
    assert pd.read_csv(io.BytesIO(csv_bytes)).shape[0] == 3
    # new main-sheet CSV round-trips too
    reread = pd.read_csv(io.BytesIO(export.to_main_csv_bytes(export.build_main_dataframe(_pairs()))))
    assert list(reread.columns) == export.MAIN_COLUMNS and len(reread) == 3


def test_main_sheet_rows_single_height_no_wrap():
    ws = _wb()["Fintech Leads Scored"]
    # main working sheet must not force wrapped/oversized rows
    reason_col = [c.value for c in ws[1]].index("Score Reason") + 1
    assert ws.cell(row=2, column=reason_col).alignment.wrap_text in (None, False)


# --- runner -----------------------------------------------------------------

def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
