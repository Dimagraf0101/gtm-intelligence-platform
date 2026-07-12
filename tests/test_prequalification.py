"""Unit tests for deterministic Python pre-qualification (pipeline/prequalification.py).

Offline, no LLM, no network, no pytest dependency:
    ./.venv/bin/python tests/test_prequalification.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import scoring as sc          # noqa: E402  (Lead)
import evidence as ev         # noqa: E402
import icp_profile as ip      # noqa: E402
import prequalification as pq  # noqa: E402


def _profile(**over):
    defn = {
        "dimensions": [{"name": "Subsegment fit", "weight": 100}],
        "category_thresholds": [{"label": "A+ / Hot", "min": 85, "max": 110},
                                {"label": "Not Relevant", "min": 0, "max": 39}],
    }
    defn.update(over)
    return ip.build_profile_from_definition("Test", defn)


def _lead(idx=0, previous=None, **fields):
    fields.setdefault("linkedin_url", f"https://www.linkedin.com/in/test{idx}")
    return sc.Lead(index=idx, fields=fields, raw={}, previous_roles=previous or [])


def _pq(profile, lead, **kw):
    evs = ev.extract_evidence(lead.fields, lead.previous_roles)
    return pq.prequalify(profile, lead, evs, **kw)


# --- ICP-specific deterministic exclusions ----------------------------------

def test_explicit_hard_employee_exclusion_bypasses_claude():
    prof = _profile(hard_exclusions=["Companies with fewer than 50 employees must be rejected",
                                     "Companies with more than 500 employees must be rejected"])
    r = _pq(prof, _lead(job_title="CTO", company="Tiny", company_size_range="2-10"))
    assert r.is_disqualified and not r.should_call_model
    assert r.matched_rule == "icp:hard_employee_size" and r.confidence == "high"
    r2 = _pq(prof, _lead(job_title="CTO", company="Big", company_size_range="1001-5000"))
    assert r2.is_disqualified and not r2.should_call_model


def test_preferred_employee_range_does_not_bypass_claude():
    # a TARGET/preferred size range is NOT a hard exclusion -> must reach Claude (model may lower score)
    prof = _profile(employee_ranges=["50-500"])
    r = _pq(prof, _lead(job_title="CTO", company="Tiny", company_size_range="2-10"))
    assert r.should_call_model and not r.is_disqualified
    r2 = _pq(prof, _lead(job_title="CTO", company="Huge", company_size_range="5001-10000"))
    assert r2.should_call_model and not r2.is_disqualified


def test_exact_excluded_geography_bypasses_claude():
    prof = _profile(target_geographies=["United States", "United Kingdom"])
    out = _pq(prof, _lead(job_title="CTO", company="X", location="Toronto, Ontario, Canada"))
    assert out.is_disqualified and out.matched_rule == "icp:geography"
    inside = _pq(prof, _lead(job_title="CTO", company="X",
                             location="San Francisco, California, United States"))
    assert inside.should_call_model and not inside.is_disqualified


def test_exact_excluded_current_title_bypasses_claude():
    prof = _profile(excluded_company_types=["Recruiter"])
    r = _pq(prof, _lead(job_title="Technical Recruiter", company="X", industry="Software"))
    assert r.is_disqualified and r.matched_rule == "icp:excluded_title"
    assert r.evidence_source_field == "job title"


def test_explicit_excluded_company_type_bypasses_claude():
    prof = _profile(excluded_company_types=["Bank"])
    r = _pq(prof, _lead(job_title="RM", company="Studio Bank", industry="Financial Services"))
    assert r.is_disqualified and r.matched_rule == "icp:excluded_company_type"


# --- current vs previous / missing / conflicting ----------------------------

def test_previous_employment_exclusion_does_not_bypass_claude():
    prof = _profile(excluded_company_types=["Bank"])
    lead = _lead(job_title="CTO", company="Acme SaaS", industry="Software",
                 previous=[{"title": "COO", "company": "Mega Bank", "industry": "Banking"}])
    r = _pq(prof, lead)
    assert r.should_call_model and not r.is_disqualified     # previous 'Bank' must NOT disqualify


def test_missing_employee_count_does_not_reject():
    prof = _profile(hard_exclusions=["fewer than 50 employees must be rejected"])
    r = _pq(prof, _lead(job_title="CTO", company="X"))       # no size at all
    assert r.should_call_model and not r.is_disqualified


def test_conflicting_employee_values_do_not_reject():
    prof = _profile(hard_exclusions=["fewer than 50 employees must be rejected"])
    # range says 51-200, count says 4 -> evidence marks CONFLICTING -> must go to Claude
    r = _pq(prof, _lead(job_title="CTO", company="X", company_size_range="51-200", employee_count="4"))
    assert r.should_call_model and not r.is_disqualified


def test_ambiguous_company_type_goes_to_claude():
    prof = _profile(excluded_company_types=["Crypto", "Bank"])
    r = _pq(prof, _lead(job_title="CTO", company="Belvo", industry="Financial Services"))
    assert r.should_call_model and not r.is_disqualified     # broad 'Financial Services' is ambiguous


def test_absence_of_hiring_or_funding_goes_to_claude():
    prof = _profile(employee_ranges=["50-500"])
    r = _pq(prof, _lead(job_title="CTO", company="InScope", company_size_range="51-200",
                        industry="Software"))
    assert r.should_call_model and not r.is_disqualified     # no funding/hiring pre-filter exists


def test_icp_specific_exclusion_does_not_affect_another_icp():
    lead = _lead(job_title="CTO", company="Crypto Exchange", industry="Software",
                 company_size_range="51-200")
    prof_crypto = _profile(excluded_company_types=["crypto"])
    prof_other = _profile(excluded_company_types=[])          # different ICP, no crypto exclusion
    assert _pq(prof_crypto, lead).is_disqualified
    assert _pq(prof_other, lead).should_call_model


# --- universal validity -----------------------------------------------------

def test_duplicate_record_bypasses_claude():
    prof = _profile()
    lead = _lead(0, job_title="CTO", company="X")
    r = _pq(prof, lead, seen_slugs={"test0"})
    assert r.is_disqualified and r.matched_rule == "universal:duplicate"


def test_invalid_missing_identifier_bypasses_claude():
    prof = _profile()
    lead = sc.Lead(index=0, fields={"job_title": "CTO", "company": "X"}, raw={}, previous_roles=[])
    r = _pq(prof, lead)
    assert r.is_disqualified and r.matched_rule == "universal:missing_identifier"


def test_no_current_employment_bypasses_claude():
    prof = _profile()
    r = _pq(prof, _lead(job_title="", company=""))           # identifier present, but no employment
    assert r.is_disqualified and r.matched_rule == "universal:no_current_employment"


def test_valid_in_scope_lead_goes_to_claude():
    prof = _profile(employee_ranges=["50-500"], excluded_company_types=["Bank"])
    r = _pq(prof, _lead(job_title="CTO", company="Acme SaaS", industry="Software",
                        company_size_range="51-200", location="United States"))
    assert r.should_call_model and not r.is_disqualified
    assert r.current_employment_only is True


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
