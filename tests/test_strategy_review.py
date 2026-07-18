"""Tests for Strategy Review (pipeline/strategy_review.py).

Proves the Sprint 5.4 guarantees: strategy is a small audited decision overlay applied onto a NEW
GeneratedICP; previous drafts are immutable; AI weights are marked default and human-reviewed ones
are not; IQS stays the sole ICP authority; exclusions activate only explicitly; stale decisions are
surfaced and block completion; overrides apply only when set; the strategy requirement resolves only
on review completeness; and projects never cross-contaminate. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_strategy_review.py
"""
import copy
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi             # noqa: E402
import iqs_validator as iqs            # noqa: E402
import icp_project as ip               # noqa: E402
import strategy_review as sr           # noqa: E402
import knowledge_interview as ki       # noqa: E402
import business_knowledge as bk        # noqa: E402


class FakeDraftClient:
    """Deterministic draft with two AI-proposed dimensions and one exclusion candidate."""
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "d", "value_proposition": "v", "business_model": "m"},
            "dimensions": [
                {"name": "Segment fit", "purpose": "p", "weight": 40, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Buyer persona", "purpose": "p", "weight": 60, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": ["x"], "acceptable": [], "non_ideal": ["y"]},
            "generation_notes": "note",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _draft(dim_weights=((40, "Segment fit"), (60, "Buyer persona")), exclusions=("Reject staffing",)):
    icp = gi.GeneratedICP(metadata=gi.Metadata(name="FinTech", version="1"))
    icp.dimensions = [gi.QualificationDimension(name=n, weight=w, weight_is_default=True)
                      for w, n in dim_weights]
    icp.hard_exclusions = [gi.HardExclusion(rule=r, evidence_required="stated in source",
                                            evaluation_mode=gi.EVAL_SEMANTIC,
                                            scope=gi.SCOPE_CURRENT_COMPANY) for r in exclusions]
    icp.target_companies = gi.TargetCompanies(target_company_types=["software firms"])
    return icp


def _decisions(draft, *, weights=None, activate=None, decline=None):
    d = sr.StrategyDecisions(project_id="p", based_on_version=draft.metadata.version,
                             based_on_draft=copy.deepcopy(draft))
    for name, w in (weights or {}).items():
        d.dimensions[sr._norm(name)] = sr.DimensionDecision(name=name, weight=w, included=True)
    for rule in (activate or []):
        d.exclusions[sr._norm(rule)] = sr.ExclusionDecision(rule=rule, activated=True)
    for rule in (decline or []):
        d.exclusions[sr._norm(rule)] = sr.ExclusionDecision(rule=rule, activated=False)
    return d


def _complete_decisions(draft):
    return _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60},
                      decline=[e.rule for e in draft.hard_exclusions])


# 1. Applying decisions returns a new GeneratedICP.
def test_apply_returns_new_generated_icp():
    draft = _draft()
    out = sr.apply_strategy_decisions(_complete_decisions(draft), draft)
    assert isinstance(out, gi.GeneratedICP) and out is not draft
    assert out.dimensions is not draft.dimensions


# 2. Previous draft remains unchanged.
def test_previous_draft_unchanged():
    draft = _draft()
    snapshot = json.dumps(draft.to_dict())
    sr.apply_strategy_decisions(_decisions(draft, weights={"Segment fit": 10, "Buyer persona": 90},
                                           decline=["Reject staffing"]), draft)
    assert json.dumps(draft.to_dict()) == snapshot
    assert all(d.weight_is_default for d in draft.dimensions)   # original weights still "default"


# 3. AI-proposed weights are marked default (generator).
def test_ai_weights_marked_default():
    port, project = _project()
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    base = ws.base_draft()
    assert base.dimensions and all(d.weight_is_default for d in base.dimensions)


# 4. Human-reviewed weights are not marked default.
def test_reviewed_weights_not_default():
    draft = _draft()
    out = sr.apply_strategy_decisions(_complete_decisions(draft), draft)
    assert all(d.weight_is_default is False for d in out.dimensions)
    # an UNreviewed dimension (decision present for only one) keeps default on the other
    d = _decisions(draft, weights={"Segment fit": 40})
    out2 = sr.apply_strategy_decisions(d, draft)
    by_name = {x.name: x for x in out2.dimensions}
    assert by_name["Segment fit"].weight_is_default is False
    assert by_name["Buyer persona"].weight_is_default is True


# 5. Weight totals are never auto-normalized.
def test_weights_never_normalized():
    draft = _draft()
    out = sr.apply_strategy_decisions(
        _decisions(draft, weights={"Segment fit": 30, "Buyer persona": 30}), draft)
    assert [d.weight for d in out.dimensions] == [30, 30]        # 60, left as-is
    assert sum(d.weight for d in out.dimensions) == 60


# 6. IQS remains the authority for invalid totals.
def test_iqs_flags_invalid_total_not_strategy_review():
    draft = _draft()
    out = sr.apply_strategy_decisions(
        _decisions(draft, weights={"Segment fit": 30, "Buyer persona": 30},
                   decline=["Reject staffing"]), draft)
    report = iqs.validate(out)
    assert not report.is_valid
    assert any("total" in e.lower() for e in report.blocking_errors)
    # a correct total passes IQS
    ok = sr.apply_strategy_decisions(_complete_decisions(draft), draft)
    assert not any("total" in e.lower() for e in iqs.validate(ok).blocking_errors)


# 7. Exclusion candidates require explicit activation or decline (for completeness).
def test_exclusion_requires_explicit_decision():
    draft = _draft()
    d = _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60})   # exclusion undecided
    assert not sr.is_review_complete(d, draft)
    d.exclusions[sr._norm("Reject staffing")] = sr.ExclusionDecision(rule="Reject staffing",
                                                                     activated=False)
    assert sr.is_review_complete(d, draft)


# 8. With decisions present, declined exclusions are omitted.
def test_declined_exclusion_omitted():
    draft = _draft(exclusions=("Reject staffing", "Reject agencies"))
    d = _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60},
                   activate=["Reject staffing"], decline=["Reject agencies"])
    out = sr.apply_strategy_decisions(d, draft)
    rules = [e.rule for e in out.hard_exclusions]
    assert rules == ["Reject staffing"]                         # only the activated one


# 9. Without decisions, legacy draft behavior remains unchanged.
def test_no_decisions_is_legacy_passthrough():
    draft = _draft(exclusions=("Reject staffing", "Reject agencies"))
    out = sr.apply_strategy_decisions(None, draft)
    assert out is not draft
    assert [e.rule for e in out.hard_exclusions] == ["Reject staffing", "Reject agencies"]
    assert [d.weight for d in out.dimensions] == [40, 60]
    assert all(d.weight_is_default for d in out.dimensions)


# 10. Activated exclusion keeps evidence/mode/scope.
def test_activated_exclusion_keeps_evidence_mode_scope():
    draft = _draft()
    d = _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60}, activate=["Reject staffing"])
    out = sr.apply_strategy_decisions(d, draft)
    ex = out.hard_exclusions[0]
    assert ex.evidence_required == "stated in source"
    assert ex.evaluation_mode == gi.EVAL_SEMANTIC and ex.scope == gi.SCOPE_CURRENT_COMPANY
    # no exclusion-specific IQS error (the exclusion is well-formed; unrelated section gaps ignored)
    assert not any("exclusion" in e.lower() for e in iqs.validate(out).blocking_errors)
    # an override may refine them, still validated by IQS
    d.exclusions[sr._norm("Reject staffing")] = sr.ExclusionDecision(
        rule="Reject staffing", activated=True, evaluation_mode=gi.EVAL_DETERMINISTIC,
        scope=gi.SCOPE_CURRENT_COMPANY, evidence_required="explicit rule")
    out2 = sr.apply_strategy_decisions(d, draft)
    assert out2.hard_exclusions[0].evaluation_mode == gi.EVAL_DETERMINISTIC


# 11. Strategy Review does not convert a target preference into an exclusion.
def test_no_preference_to_exclusion_promotion():
    draft = _draft()
    before_targets = list(draft.target_companies.target_company_types)
    d = _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60}, decline=["Reject staffing"])
    out = sr.apply_strategy_decisions(d, draft)
    assert out.target_companies.target_company_types == before_targets   # untouched
    # exclusions come ONLY from the draft's candidates; a declined one yields zero exclusions
    assert out.hard_exclusions == []
    # there is no API to add an arbitrary rule
    assert not hasattr(sr.StrategyReviewWorkspace, "add_exclusion")


# 12. Regeneration preserves compatible decisions.
def test_regeneration_preserves_compatible_decisions():
    port, project = _project()
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Segment fit", 45)
    ws.set_weight("Buyer persona", 55)
    ws.regenerate_base()                                        # same knowledge -> same dimensions
    out = ws.apply()
    by_name = {d.name: d.weight for d in out.dimensions}
    assert by_name == {"Segment fit": 45, "Buyer persona": 55}  # chosen weights survived
    assert not ws.stale()["dimensions"]


# 13. Missing dimension produces a stale decision.
def test_missing_dimension_is_stale():
    draft = _draft()
    d = _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60})
    d.dimensions[sr._norm("Retired dimension")] = sr.DimensionDecision(name="Retired dimension",
                                                                       weight=10)
    stale = sr.stale_decisions(d, draft)
    assert stale["dimensions"] == ["Retired dimension"]


# 14. Missing exclusion candidate produces a stale decision.
def test_missing_exclusion_is_stale():
    draft = _draft()
    d = _decisions(draft, weights={"Segment fit": 40, "Buyer persona": 60})
    d.exclusions[sr._norm("Reject fintechs")] = sr.ExclusionDecision(rule="Reject fintechs",
                                                                     activated=True)
    stale = sr.stale_decisions(d, draft)
    assert stale["exclusions"] == ["Reject fintechs"]


# 15. Stale decisions are not silently deleted.
def test_stale_decisions_retained_until_explicit_discard():
    port, project = _project()
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Ghost dimension", 10)                        # not in the draft
    assert "Ghost dimension" in ws.stale()["dimensions"]
    assert sr._norm("Ghost dimension") in project.strategy.dimensions   # still stored
    ws.discard_stale_decision(dimension="Ghost dimension")      # explicit, human-invoked
    assert sr._norm("Ghost dimension") not in project.strategy.dimensions
    assert not ws.stale()["dimensions"]


# 16. Stale decisions block completion.
def test_stale_decisions_block_completion():
    draft = _draft()
    d = _complete_decisions(draft)
    d.dimensions[sr._norm("Ghost")] = sr.DimensionDecision(name="Ghost", weight=5)
    assert sr.stale_decisions(d, draft)["dimensions"] == ["Ghost"]
    assert not sr.is_review_complete(d, draft)                  # complete otherwise, but blocked


# 17. Priority-band override None preserves draft bands.
def test_band_override_none_preserves_draft_bands():
    draft = _draft()
    draft.priority_thresholds = gi.standard_priority_bands()
    out = sr.apply_strategy_decisions(_complete_decisions(draft), draft)
    assert [(b.label, b.min_score, b.max_score) for b in out.priority_thresholds] == \
           [(b.label, b.min_score, b.max_score) for b in gi.standard_priority_bands()]


# 18. Explicit band override is applied.
def test_explicit_band_override_applied():
    draft = _draft()
    d = _complete_decisions(draft)
    d.priority_bands_override = [gi.PriorityBand("Top", 50, 100), gi.PriorityBand("Bottom", 0, 49)]
    out = sr.apply_strategy_decisions(d, draft)
    assert [b.label for b in out.priority_thresholds] == ["Top", "Bottom"]
    assert out.priority_thresholds is not d.priority_bands_override        # deep-copied


# 19. Evidence override None preserves draft evidence requirements.
def test_evidence_override_none_preserves_draft():
    draft = _draft()
    draft.evidence_requirements = gi.EvidenceRequirements(accepted_sources=["deck"],
                                                          evidence_quality_rules="strict")
    out = sr.apply_strategy_decisions(_complete_decisions(draft), draft)
    assert out.evidence_requirements.accepted_sources == ["deck"]
    assert out.evidence_requirements.evidence_quality_rules == "strict"
    # an explicit override replaces it
    d = _complete_decisions(draft)
    d.evidence_requirements_override = gi.EvidenceRequirements(accepted_sources=["interview"])
    out2 = sr.apply_strategy_decisions(d, draft)
    assert out2.evidence_requirements.accepted_sources == ["interview"]


# 20. Strategy requirement resolves only when review is complete.
def test_strategy_requirement_resolves_only_when_complete():
    port, project = _project()
    assert ki.strategy_requirements(project)[0].status == "unresolved"    # no review yet
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    assert ki.strategy_requirements(project)[0].status == "unresolved"    # started, not complete
    ws.set_weight("Segment fit", 40)
    ws.set_weight("Buyer persona", 60)
    for c in ws.exclusion_candidates():
        ws.decline_exclusion(c["rule"])
    assert ws.is_complete()
    assert ki.strategy_requirements(project)[0].status == "resolved"      # now resolved


# 21. Project A strategy does not affect Project B.
def test_project_strategy_isolated():
    port, project_a = _project(name="FinTech")
    project_b = port.create_project("Healthcare", "h")
    wsa = sr.StrategyReviewWorkspace(port.company, project_a, draft_client=FakeDraftClient())
    wsa.start_review()
    wsa.set_weight("Segment fit", 45)
    assert project_b.strategy is None
    assert ki.strategy_requirements(project_b)[0].status == "unresolved"
    # A's decisions are only on A
    assert sr._norm("Segment fit") in project_a.strategy.dimensions


# Versioning: reviewed result creates a new immutable version.
def test_reviewed_draft_versions_and_immutability():
    port, project = _project()
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Segment fit", 40)
    ws.set_weight("Buyer persona", 60)
    for c in ws.exclusion_candidates():
        ws.decline_exclusion(c["rule"])
    reviewed, report = ws.generate_reviewed_draft()
    assert len(project.draft_versions) == 1 and project.draft_versions[0] is reviewed
    assert reviewed.metadata.status == gi.STATUS_DRAFT          # never approved here
    assert reviewed.metadata.version == "1"
    first_snapshot = json.dumps(reviewed.to_dict())
    ws.set_weight("Segment fit", 50)
    ws.set_weight("Buyer persona", 50)
    reviewed2, _ = ws.generate_reviewed_draft()
    assert len(project.draft_versions) == 2
    assert json.dumps(project.draft_versions[0].to_dict()) == first_snapshot   # v1 immutable
    assert reviewed2.metadata.version == "2"
    assert isinstance(report.is_valid, bool)                    # IQS returned honestly


def test_weight_total_is_live_and_not_normalized():
    port, project = _project()
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    assert ws.weight_total() == 100                            # proposed 40 + 60
    ws.set_weight("Segment fit", 30)
    assert ws.weight_total() == 90                             # 30 + proposed 60, never rebalanced
    ws.exclude_dimension("Buyer persona")
    assert ws.weight_total() == 30


# --- helpers -----------------------------------------------------------------

def _company():
    c = bk.BusinessKnowledge()
    c.add_item("company", "name", "Acme", status=bk.CONFIRMED, evidence_excerpt="Acme")
    c.add_item("service", "name", "Custom software", status=bk.CONFIRMED, evidence_excerpt="x")
    c.add_item("industry", "target", "FinTech", status=bk.CONFIRMED, evidence_excerpt="FinTech")
    c.add_item("buyer", "role", "CTO", status=bk.CONFIRMED, evidence_excerpt="CTO")
    return c


def _project(name="FinTech"):
    port = ip.ICPPortfolio(company=_company())
    project = port.create_project(name, "hypothesis")
    return port, project


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
