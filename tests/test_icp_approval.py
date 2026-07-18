"""Tests for ICP Approval (pipeline/icp_approval.py).

Proves the Sprint 5.5 gate: approval requires a complete Strategy Review, IQS with no blocking
errors, every IQS warning explicitly acknowledged (scoped to the exact draft fingerprint), a draft
that still matches its strategy, and an explicit named approver. Approved versions are immutable and
adapter-consumable; nothing here mutates knowledge, the reviewed draft, or scoring. Offline, no LLM,
no pytest:

    ./.venv/bin/python tests/test_icp_approval.py
"""
import copy
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import generated_icp as gi              # noqa: E402
import iqs_validator as iqs             # noqa: E402
import icp_project as ip                # noqa: E402
import strategy_review as sr            # noqa: E402
import icp_approval as ap               # noqa: E402
import icp_adapter as ad                # noqa: E402


class FakeDraftClient:
    model = "fake"

    def __init__(self, weights=(40, 60)):
        self.weights = weights

    def complete(self, system, user, *, structured=False):
        w1, w2 = self.weights
        draft = {
            "business_context": {"description": "d", "value_proposition": "v", "business_model": "m"},
            "dimensions": [
                {"name": "Segment fit", "purpose": "p", "weight": w1, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Buyer persona", "purpose": "p", "weight": w2, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": ["x"], "acceptable": [], "non_ideal": ["y"]},
            "generation_notes": "note",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _company(extra=()):
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "name", "Acme"), ("service", "name", "Custom software"),
                           ("industry", "target", "FinTech"), ("buyer", "role", "CTO"),
                           ("geography", "region", "US"), ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing agencies")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    for cat, attr, val in extra:
        c.add_item(cat, attr, val, status=bk.PROPOSED, origin=bk.ORIGIN_AI)
    return c


def _project(name="FinTech", company=None):
    port = ip.ICPPortfolio(company=company or _company())
    return port, port.create_project(name, "hypothesis")


def _full_review(port, project, *, weights=(40, 60), activate=True, decide_exclusions=True):
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient(weights))
    ws.start_review()
    ws.set_weight("Segment fit", weights[0])
    ws.set_weight("Buyer persona", weights[1])
    if decide_exclusions:
        for c in ws.exclusion_candidates():
            (ws.activate_exclusion if activate else ws.decline_exclusion)(c["rule"])
    reviewed, report = ws.generate_reviewed_draft()
    return ws, reviewed, report


def _ack_ids(reviewed):
    return [wid for wid, _ in ap.warnings_with_ids(reviewed)]


def _approve(port, project):
    _, reviewed, _ = _full_review(port, project)
    return ap.approve_icp_version(project, reviewed, approved_by="dana",
                                  acknowledged_warning_ids=_ack_ids(reviewed)), reviewed


# 1. IQS errors block approval.
def test_iqs_errors_block_approval():
    port, project = _project()
    _, reviewed, report = _full_review(port, project, weights=(40, 40))   # totals 80 -> IQS invalid
    assert not report.is_valid
    chk = ap.check_approval_eligibility(project, reviewed, acknowledged_warning_ids=_ack_ids(reviewed))
    assert not chk.can_approve
    assert any("IQS" in r for r in chk.blocking_reasons)


# 2. Incomplete Strategy Review blocks approval.
def test_incomplete_strategy_blocks_approval():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project, decide_exclusions=False)   # exclusion undecided
    assert not sr.is_review_complete(project.strategy, project.strategy.based_on_draft)
    chk = ap.check_approval_eligibility(project, reviewed, acknowledged_warning_ids=_ack_ids(reviewed))
    assert not chk.can_approve and not chk.strategy_complete
    assert any("not complete" in r.lower() for r in chk.blocking_reasons)


# 3. Stale strategy decisions block approval.
def test_stale_strategy_blocks_approval():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    project.strategy.dimensions[sr._norm("Ghost")] = sr.DimensionDecision(name="Ghost", weight=5)
    chk = ap.check_approval_eligibility(project, reviewed, acknowledged_warning_ids=_ack_ids(reviewed))
    assert not chk.can_approve and chk.stale_strategy
    assert any("stale" in r.lower() for r in chk.blocking_reasons)


# 4. Missing StrategyDecisions block approval.
def test_missing_strategy_blocks_approval():
    port, project = _project()
    draft = gi.new_icp("Manual")
    project.draft_versions.append(draft)                    # a draft with no Strategy Review
    chk = ap.check_approval_eligibility(project, draft)
    assert not chk.can_approve
    assert any("no strategy review" in r.lower() for r in chk.blocking_reasons)


# 5. Unacknowledged IQS warning blocks approval.
def test_unacknowledged_warning_blocks_approval():
    port, project = _project()
    _, reviewed, report = _full_review(port, project)
    assert report.warnings                                  # there are warnings to acknowledge
    chk = ap.check_approval_eligibility(project, reviewed)   # none acknowledged
    assert not chk.can_approve and chk.unacknowledged_warnings
    assert any("not acknowledged" in r.lower() for r in chk.blocking_reasons)


# 6. Individually acknowledged warnings permit approval.
def test_acknowledged_warnings_permit_approval():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    chk = ap.check_approval_eligibility(project, reviewed, acknowledged_warning_ids=_ack_ids(reviewed))
    assert chk.can_approve and not chk.blocking_reasons and not chk.unacknowledged_warnings


# 7. Warning acknowledgement is scoped to one draft fingerprint.
def test_ack_scoped_to_fingerprint():
    port, project = _project()
    ws, reviewed_a, _ = _full_review(port, project)
    ack_a = _ack_ids(reviewed_a)
    # produce a different reviewed draft (new content -> new fingerprint)
    ws.set_weight("Segment fit", 30)
    ws.set_weight("Buyer persona", 70)
    reviewed_b, _ = ws.generate_reviewed_draft()
    assert ap.fingerprint_generated_icp(reviewed_a) != ap.fingerprint_generated_icp(reviewed_b)
    chk = ap.check_approval_eligibility(project, reviewed_b, acknowledged_warning_ids=ack_a)
    assert chk.unacknowledged_warnings                      # A's acks do not satisfy B
    assert set(ack_a).isdisjoint({wid for wid, _ in ap.warnings_with_ids(reviewed_b)})


# 8. A changed warning is not inherited as acknowledged.
def test_changed_warning_not_inherited():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    before = dict(ap.warnings_with_ids(reviewed))
    reviewed2 = copy.deepcopy(reviewed)
    reviewed2.target_companies.target_geographies = []      # drops -> changes the warning set
    after_ids = {wid for wid, _ in ap.warnings_with_ids(reviewed2)}
    assert set(before).isdisjoint(after_ids)                # old ids never carry to the changed draft


# 9. Approval requires explicit approved_by.
def test_approval_requires_named_approver():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    for bad in ("", "   "):
        raised = False
        try:
            ap.approve_icp_version(project, reviewed, approved_by=bad,
                                   acknowledged_warning_ids=_ack_ids(reviewed))
        except ap.ApprovalError:
            raised = True
        assert raised


# 10/11/12/13/14/15. Approval returns a new, Approved object; reviewed unchanged; audited.
def test_approval_produces_audited_immutable_version():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    reviewed_snapshot = json.dumps(reviewed.to_dict())
    approved = ap.approve_icp_version(project, reviewed, approved_by="dana",
                                      acknowledged_warning_ids=_ack_ids(reviewed), approval_note="ship")
    assert approved is not reviewed                                     # 10
    assert json.dumps(reviewed.to_dict()) == reviewed_snapshot         # 11: reviewed unchanged
    assert reviewed.metadata.status == gi.STATUS_DRAFT
    assert approved.metadata.status == gi.STATUS_APPROVED              # 12
    assert approved.history[-1].author == "dana" and "Approved" in approved.history[-1].change_summary
    assert len(project.approval_records) == 1                         # 14
    rec = project.approval_records[0]
    assert rec.approved_by == "dana" and rec.approval_note == "ship"
    assert rec.draft_fingerprint == ap.fingerprint_generated_icp(reviewed)   # 15
    assert rec.strategy_revision == project.strategy.revision         # 15
    assert rec.iqs_version == ap.IQS_VERSION


# 16. Approved version is immutable (re-approval blocked; stored object protected).
def test_approved_version_is_immutable():
    port, project = _project()
    approved, _ = _approve(port, project)
    # cannot approve the Approved artifact again
    raised = False
    try:
        ap.approve_icp_version(project, approved, approved_by="x",
                               acknowledged_warning_ids=[])
    except ap.ApprovalError:
        raised = True
    assert raised
    # a returned copy cannot mutate the stored version
    active = ap.get_active_approved_icp(project)
    active.dimensions[0].weight = 999
    assert project.approved_versions[0].dimensions[0].weight != 999


# 17/18. Editing/regeneration creates a new Draft and preserves the active Approved version.
def test_regeneration_preserves_active_approved():
    port, project = _project()
    approved, _ = _approve(port, project)
    active_fp = project.active_approved_version
    n_drafts = len(project.draft_versions)
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Segment fit", 55)
    ws.set_weight("Buyer persona", 45)
    for c in ws.exclusion_candidates():
        ws.activate_exclusion(c["rule"])
    ws.generate_reviewed_draft()
    assert len(project.draft_versions) == n_drafts + 1                 # 17: new Draft version
    assert project.active_approved_version == active_fp               # 18: active approved unchanged
    assert ap.get_active_approved_icp(project).metadata.status == gi.STATUS_APPROVED


# 19/20/25. Approving a newer version changes the active approved; older stays in history.
def test_approving_newer_version_changes_active():
    port, project = _project()
    approved1, _ = _approve(port, project)
    fp1 = project.active_approved_version
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Segment fit", 55)
    ws.set_weight("Buyer persona", 45)
    for c in ws.exclusion_candidates():
        ws.activate_exclusion(c["rule"])
    reviewed2, _ = ws.generate_reviewed_draft()
    approved2 = ap.approve_icp_version(project, reviewed2, approved_by="dana",
                                       acknowledged_warning_ids=_ack_ids(reviewed2))
    assert project.active_approved_version != fp1                     # 19
    assert len(project.approved_versions) == 2                       # 20: older retained
    active = ap.get_active_approved_icp(project)                     # 25
    weights = {d.name: d.weight for d in active.dimensions}
    assert weights == {"Segment fit": 55, "Buyer persona": 45}


# 21. Project A approval does not affect Project B.
def test_project_a_approval_isolated_from_b():
    port, project_a = _project("FinTech")
    project_b = port.create_project("Healthcare", "h")
    _approve(port, project_a)
    assert project_b.approved_versions == [] and project_b.approval_records == []
    assert project_b.active_approved_version is None
    assert ap.get_active_approved_icp(project_b) is None


# 22. A draft from another project cannot be approved.
def test_draft_from_other_project_rejected():
    port, project_a = _project("FinTech")
    project_b = port.create_project("Healthcare", "h")
    _, reviewed_b, _ = _full_review(port, project_b)
    chk = ap.check_approval_eligibility(project_a, reviewed_b,
                                        acknowledged_warning_ids=_ack_ids(reviewed_b))
    assert not chk.can_approve
    assert any("project" in r.lower() for r in chk.blocking_reasons)
    raised = False
    try:
        ap.approve_icp_version(project_a, reviewed_b, approved_by="x",
                               acknowledged_warning_ids=_ack_ids(reviewed_b))
    except ap.ApprovalError:
        raised = True
    assert raised


# 23. An already-Approved version cannot be approved again.
def test_already_approved_cannot_reapprove():
    port, project = _project()
    approved, _ = _approve(port, project)
    chk = ap.check_approval_eligibility(project, approved)
    assert not chk.can_approve
    assert any("already approved" in r.lower() for r in chk.blocking_reasons)


# 24. A fingerprint mismatch blocks approval.
def test_fingerprint_mismatch_blocks_approval():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    reviewed.dimensions[0].name = "Renamed dimension"       # mutate after generation -> mismatch
    chk = ap.check_approval_eligibility(project, reviewed, acknowledged_warning_ids=_ack_ids(reviewed))
    assert not chk.can_approve
    assert any("mismatch" in r.lower() for r in chk.blocking_reasons)


# 26. A returned active Approved ICP cannot mutate stored history.
def test_returned_active_is_a_safe_copy():
    port, project = _project()
    _approve(port, project)
    a1 = ap.get_active_approved_icp(project)
    a1.metadata.name = "HACKED"
    a1.history.append(gi.HistoryEntry(version="x", author="attacker"))
    a2 = ap.get_active_approved_icp(project)
    assert a2.metadata.name != "HACKED"
    assert all(h.author != "attacker" for h in a2.history)


# 27. icp_adapter accepts the valid Approved ICP and preserves the strategy.
def test_adapter_accepts_approved_icp():
    port, project = _project()
    _approve(port, project)
    active = ap.get_active_approved_icp(project)
    assert ad.can_use(active)
    profile = ad.to_engine_profile(active)
    assert {(d.name, d.weight) for d in profile.scoring_dimensions} == \
        {("Segment fit", 40), ("Buyer persona", 60)}
    assert "Reject staffing agencies" in profile.hard_exclusions
    assert "FinTech" in profile.target_industries and "CTO" in profile.target_buyer_personas
    assert [(b.label, b.min_score, b.max_score) for b in profile.category_thresholds] == \
        [(b.label, b.min_score, b.max_score) for b in gi.standard_priority_bands()]


# 28. icp_adapter rejects an IQS-invalid ICP even if marked Approved.
def test_adapter_rejects_invalid_icp():
    bad = gi.GeneratedICP(metadata=gi.Metadata(name="Bad", status=gi.STATUS_APPROVED))  # no dimensions
    assert not ad.can_use(bad)
    raised = False
    try:
        ad.to_engine_profile(bad)
    except ad.AdapterError:
        raised = True
    assert raised


# 29. A company-owned unresolved core conflict is surfaced honestly.
def test_company_core_conflict_surfaced():
    # two differing, unconfirmed industry targets in company knowledge -> a core conflict
    company = _company(extra=[("industry", "target", "Healthcare")])
    assert company.conflicts                                # conflict was auto-recorded
    port, project = _project(company=company)
    _, reviewed, _ = _full_review(port, project)
    assert any("industry" in a for a in reviewed.ambiguous_definitions)
    chk = ap.check_approval_eligibility(project, reviewed, acknowledged_warning_ids=_ack_ids(reviewed))
    assert not chk.can_approve
    assert any("knowledge review" in r.lower() for r in chk.blocking_reasons)


# 30. No approval path mutates Company Knowledge.
def test_approval_never_mutates_company_knowledge():
    port, project = _project()
    before = port.company.to_json()
    _approve(port, project)
    assert port.company.to_json() == before


# 25 (fingerprint identity) + get_active correctness with no approval.
def test_get_active_none_when_no_approval():
    port, project = _project()
    assert ap.get_active_approved_icp(project) is None
    assert project.active_approved_version is None


def test_fingerprint_deterministic_and_content_based():
    port, project = _project()
    _, reviewed, _ = _full_review(port, project)
    assert ap.fingerprint_generated_icp(reviewed) == ap.fingerprint_generated_icp(copy.deepcopy(reviewed))
    # version + status participate in the identity fingerprint; content fp ignores them
    approved = copy.deepcopy(reviewed)
    approved.metadata.status = gi.STATUS_APPROVED
    assert ap.fingerprint_generated_icp(reviewed) != ap.fingerprint_generated_icp(approved)
    assert ap.content_fingerprint(reviewed) == ap.content_fingerprint(approved)


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
