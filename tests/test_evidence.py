"""Unit tests for the Evidence Engine (pipeline/evidence.py).

Offline, no API, no pytest dependency: run with `./.venv/bin/python tests/test_evidence.py`.
(Also discoverable by pytest if installed — functions are named test_*.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import evidence as ev  # noqa: E402


# --- fixtures (no external data / API) --------------------------------------

def _current_fields():
    return {
        "job_title": "Chief Technology Officer",
        "company": "SMArtX Advisory Solutions",
        "industry": "Financial Services",
        "company_size_range": "51-200",
        "employee_count": "108",
        "location": "West Palm Beach, Florida, United States",
        "specialities": "UMA, SMA, WealthTech",
        "founded_year": "2018",
        "company_description": "Cloud-native managed accounts platform.",
        "company_website": "https://smartxadvisory.com/",
        "connections": "2206",
        "summary": "I lead engineering at SMArtX.",
        "headline": "CTO at SMArtX",
        "skills": "Cloud, Engineering Leadership",
        "linkedin_url": "https://www.linkedin.com/in/example",
    }

def _get(items, attribute):
    return [i for i in items if i.attribute == attribute]


# --- tests ------------------------------------------------------------------

def test_confirmed_items_have_source_and_provenance():
    items = ev.extract_evidence(_current_fields())
    title = _get(items, ev.ATTR_JOB_TITLE)
    assert len(title) == 1
    it = title[0]
    assert it.status == ev.CONFIRMED
    assert it.source_field == "job_title"
    assert it.observed_value == "Chief Technology Officer"
    assert "job_title" in it.provenance and it.provenance  # non-empty, sourced
    assert it.employment_scope == ev.SCOPE_CURRENT


def test_conflicting_size_is_flagged_not_rejected():
    fields = _current_fields()
    fields["employee_count"] = "4"          # conflicts with range 51-200 (Sprint-2 lead-6 case)
    items = ev.extract_evidence(fields)
    size = _get(items, ev.ATTR_COMPANY_SIZE)
    assert len(size) == 1
    assert size[0].status == ev.CONFLICTING          # flagged...
    assert size[0].status != ev.UNKNOWN
    # ...and it is NOT turned into a negative/rejection — status is simply 'conflicting'
    assert "review" in size[0].explanation.lower()


def test_consistent_size_is_confirmed():
    items = ev.extract_evidence(_current_fields())   # 108 within 51-200
    size = _get(items, ev.ATTR_COMPANY_SIZE)
    assert size and size[0].status == ev.CONFIRMED


def test_missing_data_is_unknown_never_negative():
    # required rubric attributes include facts the source cannot supply
    required = [ev.ATTR_JOB_TITLE, ev.ATTR_INDUSTRY, ev.ATTR_COMPANY_SIZE,
                "funding_stage", "eng_hiring_signal"]
    items = ev.extract_evidence(_current_fields())
    unknowns = ev.enumerate_unknowns(items, required)
    attrs = {u.attribute for u in unknowns}
    assert attrs == {"funding_stage", "eng_hiring_signal"}
    for u in unknowns:
        assert u.status == ev.UNKNOWN          # unknown, not a negative/dealbreaker
        assert u.observed_value is None


def test_previous_employment_never_becomes_current_company_evidence():
    fields = {"job_title": "Advisor", "company": "Freight Trade Association",
              "industry": "Transportation"}
    previous = [{"title": "COO", "company": "PayCargo", "industry": "Financial Services",
                 "started": "2018", "ended": "2022"}]
    items = ev.extract_evidence(fields, previous_roles=previous)

    # previous role is captured, but with PREVIOUS scope
    prev = _get(items, ev.ATTR_PREVIOUS_ROLE)
    assert len(prev) == 1 and prev[0].employment_scope == ev.SCOPE_PREVIOUS

    # the current-company industry evidence is the CURRENT one, never the previous fintech one
    ind = _get(items, ev.ATTR_INDUSTRY)
    assert len(ind) == 1
    assert ind[0].employment_scope == ev.SCOPE_CURRENT
    assert "Financial Services" not in (ind[0].observed_value or "")   # not the PayCargo industry

    # coverage of a current-company attribute is NOT satisfied by the previous role
    cov = ev.compute_coverage(items, ["company_industry"])
    assert cov.confirmed == 1  # from current 'Transportation', not from previous


def test_coverage_math_and_unknown_list():
    items = ev.extract_evidence(_current_fields())
    required = [ev.ATTR_JOB_TITLE, ev.ATTR_INDUSTRY, ev.ATTR_COMPANY_SIZE,
                "funding_stage", "eng_hiring_signal"]   # 3 present, 2 unearnable
    cov = ev.compute_coverage(items, required)
    assert cov.required == 5
    assert cov.confirmed == 3
    assert cov.unknown == 2
    assert cov.coverage_pct == 60
    assert set(cov.unknown_attributes) == {"funding_stage", "eng_hiring_signal"}


def test_conflicting_counts_separately_in_coverage():
    fields = _current_fields()
    fields["employee_count"] = "4"
    items = ev.extract_evidence(fields)
    cov = ev.compute_coverage(items, [ev.ATTR_COMPANY_SIZE])
    assert cov.conflicting == 1 and cov.confirmed == 0
    assert cov.conflicting_attributes == [ev.ATTR_COMPANY_SIZE]


def test_statuses_and_scopes_are_valid_vocabulary():
    items = ev.extract_evidence(_current_fields(),
                                previous_roles=[{"title": "X", "company": "Y"}])
    for it in items:
        assert it.status in ev.EVIDENCE_STATUSES
        assert it.employment_scope in ev.EMPLOYMENT_SCOPES
        assert it.confidence in (ev.HIGH, ev.MEDIUM, ev.LOW)


# --- runner (no pytest needed) ----------------------------------------------

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
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run()
    sys.exit(0 if ok else 1)
