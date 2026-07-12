"""Tests for the GeneratedICP model (pipeline/generated_icp.py).

Offline, no LLM, no network, no pytest:
    ./.venv/bin/python tests/test_generated_icp.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi        # noqa: E402


def _complete_approved(entry=gi.ENTRY_GENERATE_NEW) -> gi.GeneratedICP:
    """A fully populated, Approved, IQS-valid ICP (weights total 100)."""
    return gi.GeneratedICP(
        metadata=gi.Metadata(name="FinTech — DACH", status=gi.STATUS_APPROVED,
                             entry_point=entry, source_files=["deck.pdf", "catalogue.pdf"]),
        business_context=gi.BusinessContext(
            description="B2B analytics vendor.",
            product_or_service="Embedded analytics SDK",
            value_proposition="Shorter reporting cycles",
            business_model="SaaS subscription"),
        target_companies=gi.TargetCompanies(
            target_industries=["FinTech"], target_subsegments=["embedded finance"],
            target_company_types=["B2B SaaS"], preferred_employee_ranges=["50-500"],
            acceptable_employee_ranges=["20-1000"], target_geographies=["US", "DACH"]),
        target_buyers=gi.TargetBuyers(
            primary_buyer_roles=["VP Engineering", "CTO"],
            secondary_buyer_roles=["Founder/CEO"], title_tiers=["C-level", "VP"]),
        dimensions=[
            gi.QualificationDimension(name="Subsegment fit", weight=40, purpose="segment match",
                                      scoring_guidance="score by subsegment",
                                      required_evidence_attributes=["company_industry"]),
            gi.QualificationDimension(name="Buyer persona", weight=30, purpose="role match",
                                      scoring_guidance="score by title",
                                      required_evidence_attributes=["contact_title"]),
            gi.QualificationDimension(name="Company size", weight=30, purpose="size match",
                                      scoring_guidance="score by headcount",
                                      required_evidence_attributes=["company_size"]),
        ],
        hard_exclusions=[
            gi.HardExclusion(rule="Staffing agency", reason="not our buyer",
                             evidence_required="company industry = staffing",
                             evaluation_mode=gi.EVAL_DETERMINISTIC,
                             scope=gi.SCOPE_CURRENT_COMPANY),
        ],
        evidence_requirements=gi.EvidenceRequirements(
            accepted_sources=["LinkedIn export"],
            current_employment_rules="Only current employment confirms company fit.",
            previous_employment_restrictions="Previous employment never confirms exclusions.",
            conflict_handling="Record conflicts; do not resolve silently.",
            evidence_quality_rules="Direct statements only."),
        examples=gi.Examples(ideal_leads=["150-person embedded-finance platform, US"],
                             acceptable_leads=["400-person paytech, UK"],
                             non_ideal_leads=["3-person crypto startup"]),
        enrichment_fields=["funding stage"],
        history=[gi.HistoryEntry(version="1", date="2026-07-12", author="ops",
                                 change_summary="initial")],
    )


def test_defaults_are_draft_and_standard_bands():
    icp = gi.new_icp("X")
    assert icp.metadata.status == gi.STATUS_DRAFT
    assert icp.metadata.entry_point == gi.ENTRY_GENERATE_NEW
    labels = [b.label for b in icp.priority_thresholds]
    assert labels == ["Priority 1", "Priority 2", "Priority 3", "Priority 4",
                      "Priority 5", "Disqualified"]


def test_to_dict_roundtrips_nested_sections():
    icp = _complete_approved()
    d = icp.to_dict()
    assert d["metadata"]["name"] == "FinTech — DACH"
    assert d["dimensions"][0]["weight"] == 40
    assert d["hard_exclusions"][0]["scope"] == gi.SCOPE_CURRENT_COMPANY
    assert isinstance(d["priority_thresholds"], list)


def test_to_json_is_serializable_and_stable():
    icp = _complete_approved()
    j1 = icp.to_json()
    j2 = icp.to_json()
    assert j1 == j2                                   # deterministic
    parsed = json.loads(j1)                           # valid JSON
    assert parsed["metadata"]["name"] == "FinTech — DACH"
    # UTF-8 serializable: the em dash and non-ASCII survive without escaping.
    assert "FinTech — DACH" in j1
    j1.encode("utf-8")


def test_markdown_contains_all_required_sections():
    md = _complete_approved().to_markdown()
    for heading in ["# ICP:", "## Business Context", "## Target Companies", "## Target Buyers",
                    "## Qualification Dimensions", "## Priority Thresholds", "## Hard Exclusions",
                    "## Evidence Requirements", "## Unknown Fields", "## Enrichment Fields",
                    "## Examples"]:
        assert heading in md, f"missing section: {heading}"


def test_markdown_is_concise():
    # A single ICP rendering should be a few pages, not a novel. Guard against runaway output.
    md = _complete_approved().to_markdown()
    assert len(md.splitlines()) < 120, "markdown unexpectedly long"
    assert len(md) < 8000


def test_markdown_has_no_chain_of_thought_markers():
    md = _complete_approved().to_markdown().lower()
    for banned in ["chain-of-thought", "let's think", "system prompt", "reasoning:"]:
        assert banned not in md


def test_both_entry_points_are_structurally_identical():
    a = _complete_approved(gi.ENTRY_GENERATE_NEW).to_dict()
    b = _complete_approved(gi.ENTRY_STANDARDIZE_EXISTING).to_dict()
    a["metadata"].pop("entry_point")
    b["metadata"].pop("entry_point")
    assert a == b, "entry point must not change the profile beyond the origin marker"


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
