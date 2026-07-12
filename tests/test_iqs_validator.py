"""Tests for the IQS v1.0 validator (pipeline/iqs_validator.py).

Offline, no LLM, no network, no pytest:
    ./.venv/bin/python tests/test_iqs_validator.py
"""
import sys
import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi        # noqa: E402
import iqs_validator as iqs        # noqa: E402


def _min_valid_draft() -> gi.GeneratedICP:
    """Smallest ICP that passes IQS (no blocking errors): name + product + one full-weight dim."""
    icp = gi.new_icp("Minimal")
    icp.business_context.product_or_service = "Consulting services"
    icp.dimensions = [gi.QualificationDimension(name="Fit", weight=100)]
    return icp


def _complete(entry=gi.ENTRY_GENERATE_NEW) -> gi.GeneratedICP:
    icp = gi.new_icp("FinTech — DACH", entry_point=entry)
    icp.metadata.status = gi.STATUS_APPROVED
    icp.metadata.source_files = ["deck.pdf"]
    icp.business_context = gi.BusinessContext(
        description="B2B analytics vendor.", product_or_service="Embedded analytics SDK",
        value_proposition="Faster reporting", business_model="SaaS")
    icp.target_companies = gi.TargetCompanies(
        target_industries=["FinTech"], target_subsegments=["embedded finance"],
        target_company_types=["B2B SaaS"], preferred_employee_ranges=["50-500"],
        target_geographies=["US", "DACH"])
    icp.target_buyers = gi.TargetBuyers(
        primary_buyer_roles=["CTO"], secondary_buyer_roles=["CEO"], title_tiers=["C-level"])
    icp.dimensions = [
        gi.QualificationDimension(name="Subsegment fit", weight=40, purpose="p",
                                  scoring_guidance="g", required_evidence_attributes=["a"]),
        gi.QualificationDimension(name="Buyer persona", weight=30, purpose="p",
                                  scoring_guidance="g", required_evidence_attributes=["b"]),
        gi.QualificationDimension(name="Company size", weight=30, purpose="p",
                                  scoring_guidance="g", required_evidence_attributes=["c"],
                                  external_enrichment_required=True),
    ]
    icp.hard_exclusions = [
        gi.HardExclusion(rule="Staffing agency", reason="not buyer",
                         evidence_required="industry=staffing",
                         evaluation_mode=gi.EVAL_DETERMINISTIC, scope=gi.SCOPE_CURRENT_COMPANY),
    ]
    icp.evidence_requirements = gi.EvidenceRequirements(
        accepted_sources=["LinkedIn export"], current_employment_rules="current only",
        previous_employment_restrictions="no", conflict_handling="record",
        evidence_quality_rules="direct")
    icp.examples = gi.Examples(ideal_leads=["x"], non_ideal_leads=["y"])
    icp.enrichment_fields = ["funding stage"]
    return icp


def test_minimal_valid_draft_passes():
    r = iqs.validate(_min_valid_draft())
    assert r.is_valid, r.blocking_errors
    assert r.blocking_errors == []


def test_complete_approved_is_valid():
    r = iqs.validate(_complete())
    assert r.is_valid, r.blocking_errors
    assert r.completeness_score >= 80


def test_entry_points_validate_identically():
    a = iqs.validate(_complete(gi.ENTRY_GENERATE_NEW))
    b = iqs.validate(_complete(gi.ENTRY_STANDARDIZE_EXISTING))
    assert a.is_valid == b.is_valid
    assert a.blocking_errors == b.blocking_errors
    assert a.completeness_score == b.completeness_score


def test_missing_name_blocks():
    icp = _min_valid_draft()
    icp.metadata.name = ""
    r = iqs.validate(icp)
    assert not r.is_valid
    assert any("name" in e.lower() for e in r.blocking_errors)


def test_missing_product_service_blocks():
    icp = _min_valid_draft()
    icp.business_context.product_or_service = ""
    icp.business_context.description = ""
    r = iqs.validate(icp)
    assert any("product/service" in e.lower() for e in r.blocking_errors)


def test_no_dimensions_blocks():
    icp = _min_valid_draft()
    icp.dimensions = []
    r = iqs.validate(icp)
    assert any("no qualification dimensions" in e.lower() for e in r.blocking_errors)


def test_duplicate_dimensions_block():
    icp = _min_valid_draft()
    icp.dimensions = [gi.QualificationDimension(name="Fit", weight=50),
                      gi.QualificationDimension(name="fit", weight=50)]
    r = iqs.validate(icp)
    assert any("duplicate" in e.lower() for e in r.blocking_errors)


def test_weights_below_100_block():
    icp = _min_valid_draft()
    icp.dimensions = [gi.QualificationDimension(name="A", weight=40),
                      gi.QualificationDimension(name="B", weight=40)]
    r = iqs.validate(icp)
    assert any("total 80" in e for e in r.blocking_errors)


def test_weights_above_100_block():
    icp = _min_valid_draft()
    icp.dimensions = [gi.QualificationDimension(name="A", weight=70),
                      gi.QualificationDimension(name="B", weight=60)]
    r = iqs.validate(icp)
    assert any("total 130" in e for e in r.blocking_errors)


def test_negative_weight_blocks():
    icp = _min_valid_draft()
    icp.dimensions = [gi.QualificationDimension(name="A", weight=-10),
                      gi.QualificationDimension(name="B", weight=110)]
    r = iqs.validate(icp)
    assert any("invalid weight" in e.lower() for e in r.blocking_errors)


def test_overlapping_thresholds_block():
    icp = _min_valid_draft()
    icp.priority_thresholds = [gi.PriorityBand("High", 50, 100),
                               gi.PriorityBand("Low", 0, 60)]
    r = iqs.validate(icp)
    assert any("overlap" in e.lower() for e in r.blocking_errors)


def test_threshold_gap_blocks():
    icp = _min_valid_draft()
    icp.priority_thresholds = [gi.PriorityBand("Low", 0, 40),
                               gi.PriorityBand("High", 60, 100)]
    r = iqs.validate(icp)
    assert any("gap" in e.lower() for e in r.blocking_errors)


def test_threshold_malformed_min_gt_max_blocks():
    icp = _min_valid_draft()
    icp.priority_thresholds = [gi.PriorityBand("Bad", 80, 20),
                               gi.PriorityBand("Rest", 0, 100)]
    r = iqs.validate(icp)
    assert any("malformed" in e.lower() for e in r.blocking_errors)


def test_target_exclusion_contradiction_blocks():
    icp = _complete()
    icp.target_companies.target_company_types = ["Staffing agency"]
    icp.hard_exclusions = [gi.HardExclusion(
        rule="Staffing agency", evidence_required="x",
        evaluation_mode=gi.EVAL_DETERMINISTIC, scope=gi.SCOPE_CURRENT_COMPANY)]
    r = iqs.validate(icp)
    assert any("contradiction" in e.lower() for e in r.blocking_errors)


def test_hard_exclusion_without_evidence_blocks():
    icp = _complete()
    icp.hard_exclusions[0].evidence_required = ""
    r = iqs.validate(icp)
    assert any("evidence_required" in e for e in r.blocking_errors)


def test_hard_exclusion_without_eval_mode_blocks():
    icp = _complete()
    icp.hard_exclusions[0].evaluation_mode = ""
    r = iqs.validate(icp)
    assert any("evaluation_mode" in e for e in r.blocking_errors)


def test_hard_exclusion_without_scope_blocks():
    icp = _complete()
    icp.hard_exclusions[0].scope = ""
    r = iqs.validate(icp)
    assert any("scope" in e for e in r.blocking_errors)


def test_malformed_employee_range_blocks():
    icp = _complete()
    icp.target_companies.preferred_employee_ranges = ["fifty to five hundred"]
    r = iqs.validate(icp)
    assert any("malformed employee range" in e.lower() for e in r.blocking_errors)


def test_preferred_range_is_not_an_exclusion():
    # A preferred size range must never produce a hard-exclusion / rejection blocking error.
    icp = _complete()
    icp.target_companies.preferred_employee_ranges = ["50-500"]
    icp.hard_exclusions = []                      # no explicit hard exclusions at all
    r = iqs.validate(icp)
    assert r.is_valid, r.blocking_errors
    assert not any("exclusion" in e.lower() for e in r.blocking_errors)


def test_approved_with_errors_is_flagged():
    icp = _complete()
    icp.metadata.name = ""                        # inject a blocking error while Approved
    r = iqs.validate(icp)
    assert not r.is_valid
    assert any("approved" in e.lower() for e in r.blocking_errors)


def test_unknown_fields_are_warning_not_blocking():
    icp = _complete()
    icp.unknown_fields = ["funding stage", "engineering headcount"]
    r = iqs.validate(icp)
    assert r.is_valid, r.blocking_errors
    assert any("unknown" in w.lower() for w in r.warnings)


def test_missing_geography_and_hierarchy_warn():
    icp = _complete()
    icp.target_companies.target_geographies = []
    icp.target_buyers.secondary_buyer_roles = []
    r = iqs.validate(icp)
    assert any("geography" in w.lower() for w in r.warnings)
    assert any("hierarchy" in w.lower() for w in r.warnings)


def test_completeness_is_deterministic_and_bounded():
    icp = _complete()
    a = iqs.validate(icp).completeness_score
    b = iqs.validate(copy.deepcopy(icp)).completeness_score
    assert a == b
    assert 0 <= a <= 100


def test_completeness_rewards_more_complete_profiles():
    full = iqs.validate(_complete()).completeness_score
    minimal = iqs.validate(_min_valid_draft()).completeness_score
    assert full > minimal


def test_section_completeness_sums_to_total():
    r = iqs.validate(_complete())
    assert sum(r.section_completeness.values()) == r.completeness_score


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
