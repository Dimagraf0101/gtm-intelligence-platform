"""Offline tests for the human-review workbook export (pipeline/export.py).

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


# --- fixtures ---------------------------------------------------------------

def _lead(idx, **fields):
    return sc.Lead(index=idx, fields=fields, raw={}, previous_roles=[])


def _result(idx, *, priority, raw, coverage=60, fit=83, conf=55, confidence="medium",
            db_state="none", confirmed=None, suspected=None, unknowns=None, is_mock=False,
            error=None):
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
        validation_warnings=[], model_confidence="low", is_mock=is_mock)


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


def _load(pairs):
    return load_workbook(io.BytesIO(export.to_workbook_bytes(pairs, "FinTech")))


def _sv(rows, key):
    for k, v in rows:
        if k == key:
            return v
    return None


# --- tests ------------------------------------------------------------------

def test_workbook_has_exactly_three_sheets():
    wb = _load(_pairs())
    assert wb.sheetnames == ["Qualified Leads", "Approved for Outreach", "Summary"]


def test_qualified_column_order():
    wb = _load(_pairs())
    ws = wb["Qualified Leads"]
    header = [c.value for c in ws[1]]
    assert header == export.QUALIFIED_COLUMNS
    assert list(export.build_qualified_dataframe(_pairs()).columns) == export.QUALIFIED_COLUMNS


def test_reviewer_fields_empty_and_status_pending():
    qdf = export.build_qualified_dataframe(_pairs())
    assert (qdf["Review Status"] == "Pending").all()
    for col in ("Human Decision", "Rejection Reason", "Reviewer Comment"):
        assert (qdf[col] == "").all()


def test_approved_sheet_contains_only_approved():
    qdf = export.build_qualified_dataframe(_pairs())
    qdf.loc[0, "Human Decision"] = "Approved"           # simulate a later human approval
    adf = export.build_approved_dataframe(qdf)
    assert len(adf) == 1
    assert list(adf.columns) == export.APPROVED_COLUMNS
    assert adf.iloc[0]["First Name"] == "Aaron"


def test_approved_sheet_empty_when_none_approved():
    # nothing approved at export time -> sheet has only the header row
    wb = _load(_pairs())
    ws = wb["Approved for Outreach"]
    assert [c.value for c in ws[1]] == export.APPROVED_COLUMNS
    assert ws.max_row == 1
    assert len(export.build_approved_dataframe(export.build_qualified_dataframe(_pairs()))) == 0


def test_summary_metrics_correct():
    rows = export.build_summary_rows(_pairs(), "FinTech", "2026-07-12 00:00")
    assert _sv(rows, "Campaign / ICP") == "FinTech"
    assert _sv(rows, "Total processed leads") == 3
    assert _sv(rows, "Successful leads") == 3
    assert _sv(rows, "Failed leads") == 0
    assert _sv(rows, "Disqualified") == 1
    assert _sv(rows, "Confirmed dealbreakers") == 1
    assert _sv(rows, "Suspected dealbreakers") == 1
    assert _sv(rows, "Review — Pending") == 3
    assert _sv(rows, "Mock results") == 1
    assert _sv(rows, "Real results") == 2
    assert "approval is required" in _sv(rows, "Note")


def test_mock_and_real_rows_distinguishable():
    qdf = export.build_qualified_dataframe(_pairs())
    vals = set(qdf["Mock Result"])
    assert "MOCK/OFFLINE" in vals and "real" in vals


def test_confirmed_and_suspected_dealbreakers_separate():
    qdf = export.build_qualified_dataframe(_pairs())
    assert qdf.iloc[0]["Suspected Dealbreakers"] == "no hiring signal"
    assert qdf.iloc[0]["Confirmed Dealbreakers"] == ""
    assert qdf.iloc[1]["Confirmed Dealbreakers"] == "Dev shop / BPO"
    assert qdf.iloc[1]["Suspected Dealbreakers"] == ""


def test_unknown_fields_preserved():
    qdf = export.build_qualified_dataframe(_pairs())
    assert "Stage & funding fit" in qdf.iloc[0]["Unknown Fields"]
    assert "Reachability" in qdf.iloc[1]["Unknown Fields"]


def test_hyperlinks_and_formatting_do_not_corrupt_values():
    wb = _load(_pairs())
    ws = wb["Qualified Leads"]
    header = [c.value for c in ws[1]]
    url_col = header.index("LinkedIn URL") + 1
    company_col = header.index("Company") + 1
    url_cell = ws.cell(row=2, column=url_col)
    assert url_cell.value == "https://www.linkedin.com/in/aaron"     # value intact
    assert url_cell.hyperlink is not None                            # and clickable
    assert ws.cell(row=2, column=company_col).value == "SMArtX"      # text intact
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref            # formatting applied


def test_legacy_build_dataframe_compatible():
    df = export.build_dataframe(_pairs())
    assert list(df.columns) == export.COLUMNS
    assert len(df) == 3


def test_csv_fallbacks():
    qdf = export.build_qualified_dataframe(_pairs())
    csv_bytes = export.to_qualified_csv_bytes(qdf)
    reread = pd.read_csv(io.BytesIO(csv_bytes))
    assert list(reread.columns) == export.QUALIFIED_COLUMNS and len(reread) == 3
    adf = export.build_approved_dataframe(qdf)
    approved_bytes = export.to_approved_csv_bytes(adf)               # empty but valid
    reread_a = pd.read_csv(io.BytesIO(approved_bytes))
    assert list(reread_a.columns) == export.APPROVED_COLUMNS and len(reread_a) == 0


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
