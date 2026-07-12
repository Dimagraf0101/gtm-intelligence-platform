"""Tests for the Generator->Engine adapter (pipeline/icp_adapter.py).

Offline, no LLM, no network, no pytest:
    ./.venv/bin/python tests/test_icp_adapter.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi        # noqa: E402
import icp_adapter as ad          # noqa: E402
import icp_profile as ip          # noqa: E402


def _approved(entry=gi.ENTRY_GENERATE_NEW, industry="FinTech") -> gi.GeneratedICP:
    icp = gi.new_icp(f"{industry} — DACH", entry_point=entry)
    icp.metadata.status = gi.STATUS_APPROVED
    icp.metadata.source_files = ["deck.pdf"]
    icp.business_context = gi.BusinessContext(
        description="vendor", product_or_service="SDK", value_proposition="v", business_model="SaaS")
    icp.target_companies = gi.TargetCompanies(
        target_industries=[industry], target_subsegments=["sub"],
        target_company_types=["B2B SaaS"], preferred_employee_ranges=["50-500"],
        acceptable_employee_ranges=["20-1000"], target_geographies=["US", "DACH"])
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
        gi.HardExclusion(rule="Staffing agency", evidence_required="industry=staffing",
                         evaluation_mode=gi.EVAL_DETERMINISTIC, scope=gi.SCOPE_CURRENT_COMPANY),
        gi.HardExclusion(rule="Founder left the company", evidence_required="tenure",
                         evaluation_mode=gi.EVAL_SEMANTIC, scope=gi.SCOPE_CURRENT_PERSON),
    ]
    icp.evidence_requirements = gi.EvidenceRequirements(
        accepted_sources=["LinkedIn export"], current_employment_rules="current only")
    icp.examples = gi.Examples(ideal_leads=["x"], non_ideal_leads=["y"])
    icp.enrichment_fields = ["funding stage"]
    icp.warnings = ["Geography derived from a single source"]
    icp.ambiguous_definitions = ["'mid-market' undefined"]
    return icp


def test_adapts_approved_profile():
    prof = ad.to_engine_profile(_approved())
    assert isinstance(prof, ip.ICPProfile)
    assert prof.name == "FinTech — DACH"
    assert prof.target_industries == ["FinTech"]
    assert prof.target_subsegments == ["sub"]
    assert prof.target_company_types == ["B2B SaaS"]
    assert prof.target_geographies == ["US", "DACH"]
    assert {d.name for d in prof.scoring_dimensions} == {"Subsegment fit", "Buyer persona",
                                                         "Company size"}
    assert prof.scoring_weights["Subsegment fit"] == 40
    assert prof.target_buyer_personas == ["CTO", "CEO"]
    assert prof.buyer_title_tiers == ["C-level"]


def test_employee_ranges_mapped_as_targets():
    prof = ad.to_engine_profile(_approved())
    raws = [r.raw for r in prof.employee_ranges]
    assert "50-500" in raws and "20-1000" in raws
    # target ranges must NOT leak into hard exclusions
    assert not any("50-500" in h for h in prof.hard_exclusions)


def test_category_thresholds_are_operational_bands():
    prof = ad.to_engine_profile(_approved())
    labels = [t.label for t in prof.category_thresholds]
    assert labels[0] == "Priority 1" and "Disqualified" in labels


def test_excluded_company_types_only_from_deterministic_company_scope():
    prof = ad.to_engine_profile(_approved())
    # deterministic + current_company -> excluded_company_types; semantic/current_person -> not.
    assert prof.excluded_company_types == ["Staffing agency"]
    # all hard exclusion rules preserved in hard_exclusions
    assert "Staffing agency" in prof.hard_exclusions
    assert "Founder left the company" in prof.hard_exclusions


def test_enrichment_fields_from_dimension_flags():
    prof = ad.to_engine_profile(_approved())
    assert prof.enrichment_required_fields == ["Company size"]


def test_warnings_and_unknowns_preserved():
    icp = _approved()
    icp.unknown_fields = ["revenue"]
    prof = ad.to_engine_profile(icp)
    assert "Geography derived from a single source" in prof.warnings
    assert prof.unknown_fields == ["revenue"]
    assert prof.ambiguous_definitions == ["'mid-market' undefined"]


def test_universal_exclusions_stay_validity_only():
    prof = ad.to_engine_profile(_approved())
    # adapter must not create global commercial exclusions; default validity set is preserved.
    assert prof.universal_exclusions == list(ip.UNIVERSAL_VALIDITY_EXCLUSIONS)


def test_rejects_draft():
    icp = _approved()
    icp.metadata.status = gi.STATUS_DRAFT
    try:
        ad.to_engine_profile(icp)
        assert False, "expected AdapterError for Draft"
    except ad.AdapterError:
        pass


def test_rejects_needs_info_and_ready():
    for status in (gi.STATUS_NEEDS_INFO, gi.STATUS_READY):
        icp = _approved()
        icp.metadata.status = status
        try:
            ad.to_engine_profile(icp)
            assert False, f"expected AdapterError for {status}"
        except ad.AdapterError:
            pass


def test_rejects_invalid_approved():
    icp = _approved()
    icp.dimensions = [gi.QualificationDimension(name="A", weight=10)]   # weights != 100
    try:
        ad.to_engine_profile(icp)
        assert False, "expected AdapterError for invalid Approved profile"
    except ad.AdapterError as e:
        assert "IQS" in str(e)


def test_can_use_helper():
    assert ad.can_use(_approved()) is True
    draft = _approved()
    draft.metadata.status = gi.STATUS_DRAFT
    assert ad.can_use(draft) is False


def test_entry_point_neutral_output():
    a = ad.to_engine_profile(_approved(gi.ENTRY_GENERATE_NEW))
    b = ad.to_engine_profile(_approved(gi.ENTRY_STANDARDIZE_EXISTING))
    assert a.name == b.name
    assert a.excluded_company_types == b.excluded_company_types
    assert [d.name for d in a.scoring_dimensions] == [d.name for d in b.scoring_dimensions]
    assert a.hard_exclusions == b.hard_exclusions
    assert a.enrichment_required_fields == b.enrichment_required_fields


def test_non_fintech_profile_adapts():
    prof = ad.to_engine_profile(_approved(industry="Healthcare"))
    assert prof.target_industries == ["Healthcare"]
    assert isinstance(prof, ip.ICPProfile)


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
