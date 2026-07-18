"""Tests for Search Strategy (Sprint 9).

Proves: a SearchStrategy is hypothesis-owned, derived from the hypothesis's approved Adapted ICP,
records provenance, is deterministically validated and versioned, follows its own forward-only
lifecycle without touching ICP approval/fingerprints, persists, and never leaks across hypotheses.
Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_search_strategy.py
"""
import copy
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import generated_icp as gi              # noqa: E402
import icp_project as ip                # noqa: E402
import icp_identity as idy              # noqa: E402
import icp_approval as ap               # noqa: E402
import strategy_review as sr            # noqa: E402
import general_icp as gicp             # noqa: E402
import adapted_icp as aicp             # noqa: E402
import search_strategy as ss           # noqa: E402
import workspace_store as store         # noqa: E402


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "Acme software", "value_proposition": "v",
                                 "business_model": "services"},
            "dimensions": [
                {"name": "Fit", "purpose": "p", "weight": 60, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Eng", "purpose": "p", "weight": 40, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": [], "acceptable": [], "non_ideal": []},
            "generation_notes": "n",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _company():
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "overview", "Acme software"),
                           ("service", "name", "Custom delivery"), ("industry", "target", "FinTech"),
                           ("buyer", "role", "CTO"), ("geography", "region", "US"),
                           ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing agencies")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    return c


def _hypothesis_with_approved_icp(ws, name="Healthcare", target="Healthcare"):
    h = ws.create_hypothesis(name, f"Sell to {name}")
    h.project_knowledge.add_item("industry", "target", target, status=bk.CONFIRMED,
                                 evidence_excerpt=target)
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    srw = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    srw.start_review()
    srw.set_weight("Fit", 60)
    srw.set_weight("Eng", 40)
    for cand in srw.exclusion_candidates():
        srw.activate_exclusion(cand["rule"])
    reviewed, _ = srw.generate_reviewed_draft()
    ackids = [w for w, _ in ap.warnings_with_ids(reviewed)]
    ap.approve_icp_version(h, reviewed, approved_by="dana", acknowledged_warning_ids=ackids)
    return h


def _workspace():
    ws = ip.CompanyWorkspace(company=_company(), name="Hygge")
    gicp.generate_and_append(ws, client=FakeDraftClient())
    return ws


# 1. Many hypotheses own independent strategies.
def test_many_hypotheses_independent_strategies():
    ws = _workspace()
    a = _hypothesis_with_approved_icp(ws, "Healthcare", "Healthcare")
    b = _hypothesis_with_approved_icp(ws, "Logistics", "Logistics")
    ss.generate_search_strategy(a, client=FakeDraftClient())
    ss.generate_search_strategy(b, client=FakeDraftClient())
    assert len(a.list_search_strategies()) == 1 and len(b.list_search_strategies()) == 1
    assert a.list_search_strategies()[0].strategy_id != b.list_search_strategies()[0].strategy_id


# 2 & 3. Generated from the correct hypothesis + Adapted ICP; provenance recorded.
def test_strategy_provenance_and_scope():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    adapted_id = idy.artifact_identity_str(ap.get_active_approved_icp(h))
    res = ss.generate_search_strategy(h, client=FakeDraftClient())
    assert res.ok
    s = res.strategy
    assert s.derived_from_adapted_icp == adapted_id
    assert idy.parse_artifact_identity(s.derived_from_adapted_icp).artifact_type == idy.ARTIFACT_ADAPTED_ICP
    assert s.hypothesis_id == h.project_id
    # reflects the adapted ICP targeting (company + hypothesis composed)
    assert "FinTech" in s.company_criteria.industries and "US" in s.geography.included


# 4. Refused without an approved Adapted ICP.
def test_refused_without_approved_icp():
    ws = _workspace()
    h = ws.create_hypothesis("NoICP", "x")
    res = ss.generate_search_strategy(h, client=FakeDraftClient())
    assert not res.ok and "approved Adapted ICP" in res.refusal_reason
    assert h.list_search_strategies() == []
    # a Draft (unapproved) adapted ICP is not enough
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    assert not ss.generate_search_strategy(h, client=FakeDraftClient()).ok


# 5. Company Business Knowledge is not modified.
def test_company_knowledge_unmodified():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    before = ws.company.to_json()
    ss.generate_search_strategy(h, client=FakeDraftClient())
    assert ws.company.to_json() == before


# 6. Source Adapted ICP is not modified.
def test_adapted_icp_unmodified():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    src = ap.get_active_approved_icp(h)
    before, before_id = json.dumps(src.to_dict()), idy.artifact_identity_str(src)
    ss.generate_search_strategy(h, client=FakeDraftClient())
    src2 = ap.get_active_approved_icp(h)
    assert json.dumps(src2.to_dict()) == before and idy.artifact_identity_str(src2) == before_id


# 7 & 8. Versions append; historical versions immutable.
def test_versions_append_and_are_immutable():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    ss.generate_search_strategy(h, client=FakeDraftClient())
    v1 = json.dumps(h.list_search_strategies()[0].to_dict())
    ss.generate_search_strategy(h, client=FakeDraftClient())
    assert [s.version for s in h.list_search_strategies()] == ["1", "2"]
    assert json.dumps(h.list_search_strategies()[0].to_dict()) == v1
    assert h.list_search_strategies()[0] is not h.list_search_strategies()[1]


# 9. Latest draft and latest approved resolve correctly.
def test_latest_resolution():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s1 = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    s2 = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    assert h.latest_search_strategy() is s2
    assert h.latest_approved_search_strategy() is None            # none approved yet
    ss.set_status(h, s1.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s1.strategy_id, ss.STRATEGY_APPROVED, approved_by="dana")
    assert h.latest_approved_search_strategy() is s1


# 10 & 12. Round-trip; provenance survives save/load.
def test_roundtrip_and_provenance_survives():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    res = ss.generate_search_strategy(h, client=FakeDraftClient())
    ss.set_status(h, res.strategy.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, res.strategy.strategy_id, ss.STRATEGY_APPROVED, approved_by="dana")
    ref = res.strategy.derived_from_adapted_icp
    ws2 = store.loads(store.dumps(ws))
    h2 = ws2.get_hypothesis(h.project_id)
    assert len(h2.list_search_strategies()) == 1
    s2 = h2.latest_search_strategy()
    assert s2.derived_from_adapted_icp == ref and s2.status == ss.STRATEGY_APPROVED
    assert s2.to_dict() == res.strategy.to_dict()                # lossless


# 11. Old workspace JSON without strategies loads safely.
def test_old_json_without_strategies_loads():
    envelope = {
        "schema_version": store.SCHEMA_VERSION, "kind": "gtm_company_workspace",
        "workspace": {
            "workspace_id": "ws1", "name": "Legacy", "created_at": "t", "updated_at": "t",
            "metadata": {}, "company": bk.BusinessKnowledge().to_dict(), "general_icp_versions": [],
            "hypotheses": [{
                "project_id": "p1", "name": "H", "hypothesis": "", "status": "active",
                "created_at": "t", "updated_at": "t",
                "project_knowledge": bk.BusinessKnowledge().to_dict(),
                "draft_versions": [], "not_applicable": {}, "strategy": None,
                "approved_versions": [], "approval_records": [], "active_approved_version": None,
                # search_strategies intentionally ABSENT
            }],
        },
    }
    ws = store.from_envelope(envelope)
    assert ws.get_hypothesis("p1").list_search_strategies() == []   # safe default


# 13. Invalid filter structures are rejected deterministically.
def test_invalid_structures_rejected():
    base = ss.SearchStrategy(objective="obj", derived_from_adapted_icp="adapted_icp:1:deadbeef")
    base.company_criteria.company_sizes = ["not-a-range"]
    issues = ss.validate_search_strategy(base)
    assert any("Malformed company-size range" in i for i in issues)
    dup = ss.SearchStrategy(objective="obj", derived_from_adapted_icp="adapted_icp:1:deadbeef")
    dup.company_criteria.industries = ["FinTech", "FinTech"]      # not normalized
    assert any("not normalized" in i for i in ss.validate_search_strategy(dup))
    empt = ss.SearchStrategy(objective="obj", derived_from_adapted_icp="adapted_icp:1:deadbeef")
    empt.person_criteria.job_titles = ["CTO", "  "]
    assert any("empty value" in i for i in ss.validate_search_strategy(empt))
    badref = ss.SearchStrategy(objective="obj", derived_from_adapted_icp="general_icp:1:deadbeef")
    assert any("not an Adapted ICP" in i for i in ss.validate_search_strategy(badref))
    noref = ss.SearchStrategy(objective="obj")
    assert any("no source Adapted ICP" in i for i in ss.validate_search_strategy(noref))


# 14. Include/exclude conflicts are surfaced explicitly.
def test_include_exclude_conflicts_surfaced():
    s = ss.SearchStrategy(objective="obj", derived_from_adapted_icp="adapted_icp:1:deadbeef")
    s.geography.included = ["US"]
    s.geography.excluded = ["US"]
    s.person_criteria.job_titles = ["CTO"]
    s.person_criteria.excluded_job_titles = ["CTO"]
    issues = ss.validate_search_strategy(s)
    assert any("geography appear as both" in i for i in issues)
    assert any("job titles appear as both" in i for i in issues)


# 15. Unknowns remain unknown (declared, never converted to facts).
def test_unknowns_remain_unknown():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    assert s.unknowns                                            # ICP unknown/enrichment fields carried
    # unknown buying signals stay empty rather than invented
    assert s.buying_signals == []
    # nothing from unknowns leaked into known_facts
    assert not (set(u.lower() for u in s.unknowns) & set(k.lower() for k in s.known_facts))


# 16. Approval follows the forward-only lifecycle; approved is immutable.
def test_lifecycle_transitions():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    assert s.status == ss.STRATEGY_DRAFT
    # approve requires review first and a named approver
    for bad in ("",):
        raised = False
        try:
            ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by=bad)
        except ss.SearchStrategyError:
            raised = True
        assert raised
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="dana")
    assert s.status == ss.STRATEGY_APPROVED and s.approved_by == "dana" and s.approved_at
    # approved -> draft is illegal; approved -> archived is allowed
    raised = False
    try:
        ss.set_status(h, s.strategy_id, ss.STRATEGY_DRAFT)
    except ss.SearchStrategyError:
        raised = True
    assert raised
    ss.set_status(h, s.strategy_id, ss.STRATEGY_ARCHIVED)
    assert s.status == ss.STRATEGY_ARCHIVED


# 17. One hypothesis cannot access/mutate another hypothesis's strategy.
def test_strategy_isolated_between_hypotheses():
    ws = _workspace()
    a = _hypothesis_with_approved_icp(ws, "Healthcare", "Healthcare")
    b = _hypothesis_with_approved_icp(ws, "Logistics", "Logistics")
    sa = ss.generate_search_strategy(a, client=FakeDraftClient()).strategy
    # b does not contain a's strategy; transitioning it via b raises
    assert sa.strategy_id not in [s.strategy_id for s in b.list_search_strategies()]
    raised = False
    try:
        ss.set_status(b, sa.strategy_id, ss.STRATEGY_REVIEWED)
    except ss.SearchStrategyError:
        raised = True
    assert raised
    # deleting a never affects b
    b_sid = b.list_search_strategies()[0].strategy_id if b.list_search_strategies() else None
    ss.generate_search_strategy(b, client=FakeDraftClient())
    b_sid = b.list_search_strategies()[0].strategy_id
    ws.delete_hypothesis(a.project_id)
    assert ws.get_hypothesis(b.project_id).list_search_strategies()[0].strategy_id == b_sid


# 18. Existing fingerprints and ArtifactIdentity remain unchanged.
def test_fingerprints_and_identity_unchanged():
    icp = gi.GeneratedICP(metadata=gi.Metadata(name="FinTech", version="3", status=gi.STATUS_APPROVED))
    icp.business_context = gi.BusinessContext(description="desc", product_or_service="SDK",
                                              business_model="SaaS", capabilities=["x"])
    icp.target_companies = gi.TargetCompanies(target_industries=["FinTech"],
                                              target_company_types=["software"],
                                              target_geographies=["US"],
                                              preferred_employee_ranges=["50-500"])
    icp.target_buyers = gi.TargetBuyers(primary_buyer_roles=["CTO"])
    icp.dimensions = [gi.QualificationDimension(name="Segment fit", weight=40),
                      gi.QualificationDimension(name="Buyer persona", weight=60)]
    icp.hard_exclusions = [gi.HardExclusion(rule="Reject staffing", evidence_required="stated",
                                            evaluation_mode=gi.EVAL_SEMANTIC,
                                            scope=gi.SCOPE_CURRENT_COMPANY)]
    assert idy.fingerprint_generated_icp(icp) == \
        "08dba234fca68eae390445aaab4bfa244d71c4f2d70e05068d3f7cb2850ba9c3"
    assert idy.warning_id("abc", "No target geography declared.") == "bdef0a21cf2806af"


# 19. Existing qualification behavior remains unchanged (search strategy is not qualification).
def test_qualification_unchanged():
    import qualification_bridge as qb
    import scoring as sc
    up = qb.context_from_uploaded("Plain", "We sell to FinTech CTOs.")
    r = qb.score_with_context([sc.normalize_lead({"company": "X", "title": "CTO"}, 0)], up,
                              client=sc.MockClient())
    assert r and r[0].icp == "Plain"
    # search_strategy imports nothing from scoring / qualification
    src = (ROOT / "pipeline" / "search_strategy.py").read_text(encoding="utf-8")
    for forbidden in ("import scoring", "import qualification_bridge", "import icp_adapter"):
        assert forbidden not in src


# 20. All Streamlit pages compile/import.
def test_pages_compile():
    import py_compile
    for page in sorted((ROOT / "pages").glob("*.py")):
        py_compile.compile(str(page), doraise=True)
    py_compile.compile(str(ROOT / "app.py"), doraise=True)


# 21. No new import cycles.
def test_no_import_cycles():
    src = (ROOT / "pipeline" / "search_strategy.py").read_text(encoding="utf-8")
    assert "import icp_project" not in src        # icp_project imports search_strategy (lazily), not vice-versa
    import importlib
    importlib.import_module("search_strategy")
    importlib.import_module("icp_project")


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
