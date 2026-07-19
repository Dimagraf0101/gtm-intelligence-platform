"""Tests for Sprint 2A — the approval gate + the Generated-ICP → Engine bridge.

Covers: GeneratedICP.from_dict/from_json round-trip, icp_approval.approve (IQS-gated human act,
recorded in history), icp_library.load_generated/approve_entry/is_ready_for_qualification (against
a temporary LocalStorage root), scoring.score_leads(profile=...) (the additive bridge entrypoint),
and campaign.load_icp_for_scoring (refuses Drafts; adapts Approved ICPs).

Offline, no LLM, no network, no pytest:
    ./.venv/bin/python tests/test_icp_approval.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi        # noqa: E402
import icp_approval as ap         # noqa: E402
import icp_adapter as ad          # noqa: E402
import iqs_validator as iqs       # noqa: E402
import storage as storage_mod     # noqa: E402
import icp_library as lib         # noqa: E402
import campaign                   # noqa: E402
import scoring as sc              # noqa: E402


# --- fixtures ----------------------------------------------------------------

def _draft(warning_free: bool = True) -> gi.GeneratedICP:
    """A complete, IQS-valid Draft ICP. ``warning_free=True`` fills every field the validator
    warns about; ``False`` leaves unknown fields so IQS emits warnings."""
    icp = gi.new_icp("Test ICP — Bridge")
    icp.metadata.source_files = ["deck.pdf"]
    icp.business_context = gi.BusinessContext(
        description="vendor", product_or_service="SDK", value_proposition="v",
        business_model="SaaS")
    icp.target_companies = gi.TargetCompanies(
        target_industries=["FinTech"], target_subsegments=["embedded finance"],
        target_company_types=["B2B SaaS"], preferred_employee_ranges=["50-500"],
        acceptable_employee_ranges=["20-1000"], target_geographies=["US", "DACH"])
    icp.target_buyers = gi.TargetBuyers(
        primary_buyer_roles=["CTO"], secondary_buyer_roles=["CEO"], title_tiers=["C-level"])
    icp.dimensions = [
        gi.QualificationDimension(name="Subsegment fit", weight=40, purpose="p",
                                  scoring_guidance="g", required_evidence_attributes=["a"]),
        gi.QualificationDimension(name="Buyer persona", weight=30, purpose="p",
                                  scoring_guidance="g", required_evidence_attributes=["b"]),
        gi.QualificationDimension(name="Funding stage", weight=30, purpose="p",
                                  scoring_guidance="g", required_evidence_attributes=["c"],
                                  external_enrichment_required=True),
    ]
    icp.hard_exclusions = [
        gi.HardExclusion(rule="Staffing agency", reason="r", evidence_required="industry=staffing",
                         evaluation_mode=gi.EVAL_DETERMINISTIC, scope=gi.SCOPE_CURRENT_COMPANY),
    ]
    icp.evidence_requirements = gi.EvidenceRequirements(
        accepted_sources=["LinkedIn export"], current_employment_rules="current only")
    icp.examples = gi.Examples(ideal_leads=["x"], non_ideal_leads=["y"])
    if not warning_free:
        icp.unknown_fields = ["revenue"]
    return icp


def _fresh_store() -> str:
    """Point the storage singleton at a fresh temporary LocalStorage root."""
    tmp = tempfile.mkdtemp(prefix="icp-lib-test-")
    storage_mod._STORE = storage_mod.LocalStorage(tmp)
    return tmp


def _valid_row(idx: int = 0) -> dict:
    return {"first name": "F", "last name": f"L{idx}", "job title": "CTO",
            "company": f"C{idx}", "linkedin url": f"https://www.linkedin.com/in/lead{idx}",
            "linkedin industry": "FinTech", "linkedin employees": "51-200"}


# --- from_dict / from_json round-trip ----------------------------------------

def test_roundtrip_preserves_everything():
    icp = _draft(warning_free=False)
    icp.warnings = ["w1"]
    icp.ambiguous_definitions = ["ambiguous"]
    icp.history = [gi.HistoryEntry(version="1", date="2026-07-01", author="a", change_summary="c")]
    back = gi.GeneratedICP.from_json(icp.to_json())
    assert back.to_dict() == icp.to_dict()


def test_from_dict_ignores_unknown_keys_and_defaults_bands():
    d = _draft().to_dict()
    d["future_section"] = {"x": 1}
    d["metadata"]["future_field"] = "y"
    d["priority_thresholds"] = []
    back = gi.GeneratedICP.from_dict(d)
    assert back.metadata.name == "Test ICP — Bridge"
    assert [b.label for b in back.priority_thresholds] == \
        [b.label for b in gi.standard_priority_bands()]


# --- approval gate -----------------------------------------------------------

def test_approve_valid_warning_free_draft():
    icp = _draft()
    assert iqs.validate(icp).warnings == [], "fixture must be warning-free for this test"
    ap.approve(icp, approved_by="tester")
    assert icp.metadata.status == gi.STATUS_APPROVED
    assert icp.history and icp.history[-1].author == "tester"
    assert "Approved" in icp.history[-1].change_summary


def test_approve_requires_warning_acknowledgment():
    icp = _draft(warning_free=False)
    assert iqs.validate(icp).warnings, "fixture must produce IQS warnings for this test"
    try:
        ap.approve(icp)
        assert False, "expected ApprovalError without acknowledgment"
    except ap.ApprovalError as e:
        assert "acknowledg" in str(e)
    assert icp.metadata.status == gi.STATUS_DRAFT      # unchanged after refusal
    ap.approve(icp, acknowledge_warnings=True)
    assert icp.metadata.status == gi.STATUS_APPROVED


def test_approve_refuses_iqs_invalid_draft():
    icp = _draft()
    icp.dimensions = [gi.QualificationDimension(name="A", weight=10)]   # weights != 100
    try:
        ap.approve(icp, acknowledge_warnings=True)
        assert False, "expected ApprovalError for IQS-invalid draft"
    except ap.ApprovalError as e:
        assert "IQS" in str(e)
    assert icp.metadata.status == gi.STATUS_DRAFT


def test_approve_is_idempotent_on_approved():
    icp = _draft()
    ap.approve(icp)
    n_history = len(icp.history)
    ap.approve(icp)                                     # no-op, no duplicate history entry
    assert icp.metadata.status == gi.STATUS_APPROVED
    assert len(icp.history) == n_history


# --- library: persistence round-trip + approval ------------------------------

def test_library_load_generated_roundtrip():
    _fresh_store()
    icp = _draft()
    entry = lib.save_generated(icp)
    assert entry.status == gi.STATUS_DRAFT
    back = lib.load_generated(entry.id)
    assert back.to_dict() == icp.to_dict()


def test_library_approve_entry_updates_all_objects():
    _fresh_store()
    entry = lib.save_generated(_draft())
    assert lib.is_ready_for_qualification(entry) is False
    updated = lib.approve_entry(entry.id, approved_by="tester", acknowledge_warnings=True)
    assert updated.status == gi.STATUS_APPROVED
    assert lib.is_ready_for_qualification(updated) is True
    # all three stored objects reflect the approval
    reloaded, text = lib.load_text(entry.id)
    assert reloaded.status == gi.STATUS_APPROVED
    assert "Status: Approved" in text
    back = lib.load_generated(entry.id)
    assert back.metadata.status == gi.STATUS_APPROVED
    assert back.history and back.history[-1].author == "tester"


def test_library_approve_entry_refuses_invalid():
    _fresh_store()
    icp = _draft()
    icp.dimensions = [gi.QualificationDimension(name="A", weight=10)]
    entry = lib.save_generated(icp)
    try:
        lib.approve_entry(entry.id, acknowledge_warnings=True)
        assert False, "expected ApprovalError"
    except ap.ApprovalError:
        pass
    reloaded, _ = lib.load_text(entry.id)
    assert reloaded.status == gi.STATUS_DRAFT           # nothing was persisted


# --- campaign bridge ---------------------------------------------------------

def test_bridge_refuses_draft_entry():
    _fresh_store()
    entry = lib.save_generated(_draft())
    try:
        campaign.load_icp_for_scoring(entry.id)
        assert False, "expected CampaignError for a Draft ICP"
    except campaign.CampaignError as e:
        assert "Approved" in str(e)


def test_bridge_adapts_approved_entry():
    _fresh_store()
    entry = lib.save_generated(_draft())
    lib.approve_entry(entry.id, acknowledge_warnings=True)
    loaded = campaign.load_icp_for_scoring(entry.id)
    prof = loaded["profile"]
    assert prof is not None
    assert {d.name for d in prof.scoring_dimensions} == \
        {"Subsegment fit", "Buyer persona", "Funding stage"}
    assert prof.excluded_company_types == ["Staffing agency"]
    assert "Status: Approved" in loaded["text"]         # markdown context, regenerated


def test_scoring_uses_prebuilt_profile_not_text_parsing():
    icp = _draft()
    ap.approve(icp)
    prof = ad.to_engine_profile(icp)
    leads = [sc.normalize_lead(_valid_row(0), 0)]
    results = sc.score_leads(leads, icp.to_markdown(), icp.metadata.name,
                             client=sc.MockClient(), profile=prof)
    assert len(results) == 1
    r = results[0]
    assert r.error is None and r.category != "Disqualified"
    # dimensions come from the structured profile, not from parsing the markdown text
    assert set(r.dimensions) == {"Subsegment fit", "Buyer persona", "Funding stage"}
    assert r.dimensions["Subsegment fit"].max == 40
    # the enrichment-required dimension stays unknown offline (guard active through the bridge)
    assert "Funding stage" in r.unknowns


def test_campaign_score_rows_passes_profile_through():
    icp = _draft()
    ap.approve(icp)
    prof = ad.to_engine_profile(icp)
    pairs = campaign.score_rows([_valid_row(0), _valid_row(1)], icp.to_markdown(),
                                icp.metadata.name, client=sc.MockClient(), profile=prof)
    assert len(pairs) == 2
    for _lead, r in pairs:
        assert set(r.dimensions) == {"Subsegment fit", "Buyer persona", "Funding stage"}


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
