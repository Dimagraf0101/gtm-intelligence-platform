"""Tests for the Sprint 6 domain reframe + persistence foundation.

Proves: CompanyWorkspace / MarketHypothesis round-trip losslessly through JSON; fingerprints, approval
records, strategy, and the active-approved pointer survive a save/load boundary; the full pipeline
still runs across a persistence boundary; malformed/unsupported payloads refuse explicitly; and the
ICPPortfolio / ICPProject aliases keep every existing caller working. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_workspace_persistence.py
"""
import copy
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import generated_icp as gi              # noqa: E402
import icp_project as ip                # noqa: E402
import strategy_review as sr            # noqa: E402
import icp_approval as ap               # noqa: E402
import qualification_bridge as qb       # noqa: E402
import scoring as sc                    # noqa: E402
import export                           # noqa: E402
import workspace_store as store         # noqa: E402


class FakeDraftClient:
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


def _company(name="Acme"):
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "name", name), ("service", "name", "Custom software"),
                           ("industry", "target", "FinTech"), ("buyer", "role", "CTO"),
                           ("geography", "region", "US"), ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing agencies")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    c.add_unknown("funding stage")
    return c


def _approved_hypothesis(ws, name="FinTech"):
    h = ws.create_hypothesis(name, f"Sell to {name}")
    srw = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    srw.start_review()
    srw.set_weight("Segment fit", 40)
    srw.set_weight("Buyer persona", 60)
    for cand in srw.exclusion_candidates():
        srw.activate_exclusion(cand["rule"])
    reviewed, _ = srw.generate_reviewed_draft()
    ack = [w for w, _ in ap.warnings_with_ids(reviewed)]
    ap.approve_icp_version(h, reviewed, approved_by="dana", acknowledged_warning_ids=ack)
    return h


def _roundtrip(ws):
    return store.loads(store.dumps(ws))


# 1. Empty-state round-trip.
def test_empty_workspace_roundtrip():
    ws = ip.CompanyWorkspace(name="Hygge Software")
    ws2 = _roundtrip(ws)
    assert ws2.name == "Hygge Software"
    assert ws2.workspace_id == ws.workspace_id
    assert ws2.hypotheses == [] and ws2.general_icp_versions == []
    assert ws2.company.knowledge_items == []


# 2. With company knowledge.
def test_company_knowledge_roundtrip():
    ws = ip.CompanyWorkspace(company=_company())
    ws2 = _roundtrip(ws)
    assert ws2.company.to_json() == ws.company.to_json()
    assert "funding stage" in ws2.company.unknown_fields


# 3. Multiple hypotheses.
def test_multiple_hypotheses_roundtrip():
    ws = ip.CompanyWorkspace(company=_company())
    for nm in ("Healthcare", "Logistics", "Real Estate"):
        ws.create_hypothesis(nm, f"{nm} hypothesis")
    ws2 = _roundtrip(ws)
    assert [h.name for h in ws2.hypotheses] == ["Healthcare", "Logistics", "Real Estate"]
    assert {h.project_id for h in ws2.hypotheses} == {h.project_id for h in ws.hypotheses}


# 4. Hypothesis knowledge round-trip.
def test_hypothesis_knowledge_roundtrip():
    ws = ip.CompanyWorkspace(company=_company())
    h = ws.create_hypothesis("Healthcare", "h")
    h.project_knowledge.add_item("industry", "target", "Hospitals", status=bk.CONFIRMED,
                                 evidence_excerpt="Hospitals")
    ws2 = _roundtrip(ws)
    h2 = ws2.get_hypothesis(h.project_id)
    assert h2.hypothesis_knowledge.to_json() == h.project_knowledge.to_json()
    assert "Hospitals" in h2.project_knowledge.field("industries")


# 5/6/7/8/9/10. Draft + approved + records + strategy + fingerprint + active pointer.
def test_full_hypothesis_roundtrip_preserves_everything():
    ws = ip.CompanyWorkspace(company=_company())
    h = _approved_hypothesis(ws)
    fp_before = ap.fingerprint_generated_icp(ap.get_active_approved_icp(h))
    active_before = h.active_approved_version
    rec_before = h.approval_records[0].to_dict()

    ws2 = _roundtrip(ws)
    h2 = ws2.get_hypothesis(h.project_id)

    assert len(h2.draft_versions) == 1                                   # 5
    assert len(h2.approved_versions) == 1                                # 6
    assert h2.approved_versions[0].metadata.status == gi.STATUS_APPROVED
    assert h2.approval_records[0].to_dict() == rec_before                # 7
    assert h2.strategy is not None and h2.strategy.revision == h.strategy.revision   # 8
    assert h2.strategy.based_on_draft is not None                        # strategy fully preserved
    assert h2.strategy.reviewed_fingerprint == h.strategy.reviewed_fingerprint
    assert h2.active_approved_version == active_before                   # 9: pointer preserved
    active2 = ap.get_active_approved_icp(h2)
    assert active2 is not None                                           # 9: still resolvable
    assert ap.fingerprint_generated_icp(active2) == fp_before            # 10: fingerprint identical


# StrategyDecisions detail round-trip (dimensions, exclusions, overrides).
def test_strategy_decisions_detail_roundtrip():
    ws = ip.CompanyWorkspace(company=_company())
    h = _approved_hypothesis(ws)
    ws2 = _roundtrip(ws)
    s2 = ws2.get_hypothesis(h.project_id).strategy
    assert set(s2.dimensions) == set(h.strategy.dimensions)
    for k, dec in h.strategy.dimensions.items():
        assert s2.dimensions[k].weight == dec.weight and s2.dimensions[k].included == dec.included
    assert set(s2.exclusions) == set(h.strategy.exclusions)


# 13. Full pipeline across a save/load boundary.
def test_pipeline_survives_save_load_boundary():
    ws = ip.CompanyWorkspace(company=_company())
    h = _approved_hypothesis(ws, "FinTech")

    with tempfile.TemporaryDirectory() as td:
        path = store.save_workspace(ws, Path(td) / "ws.json")
        assert path.exists()
        ws2 = store.load_workspace(path)

    h2 = ws2.get_hypothesis(h.project_id)
    ctx = qb.context_from_approved_project(h2)                           # bridge + adapter
    assert ctx.source_type == qb.SOURCE_APPROVED and ctx.profile is not None
    leads = [sc.normalize_lead({"company": "BigCo", "title": "CTO", "company_size_range": "100-500",
                                "location": "US",
                                "linkedin_url": "https://linkedin.com/in/bigco-cto"}, 0)]
    results = qb.score_with_context(leads, ctx, client=sc.MockClient())
    assert results and results[0].icp == "FinTech"
    by_index = {ld.index: ld for ld in leads}
    pairs = [(by_index[r.lead_index], r) for r in results if r.lead_index in by_index]
    assert len(export.to_workbook_bytes(pairs, ctx.name)) > 0            # export still works


# 14. Unsupported schema_version refuses explicitly.
def test_unsupported_schema_version_refuses():
    ws = ip.CompanyWorkspace(company=_company())
    payload = store.to_envelope(ws)
    payload["schema_version"] = 999
    raised = False
    try:
        store.from_envelope(payload)
    except store.WorkspacePersistenceError as e:
        raised = True
        assert "schema_version" in str(e)
    assert raised


# 15. Corrupt JSON / foreign payload refuses explicitly.
def test_corrupt_and_foreign_payloads_refuse():
    for bad in ("{not json", "", "[1,2,3]"):
        raised = False
        try:
            store.loads(bad)
        except store.WorkspacePersistenceError:
            raised = True
        assert raised, bad
    # a well-formed JSON that isn't a workspace
    raised = False
    try:
        store.from_envelope({"kind": "something_else", "schema_version": 1, "workspace": {}})
    except store.WorkspacePersistenceError as e:
        raised = True
        assert "workspace" in str(e).lower()
    assert raised
    # missing file
    raised = False
    try:
        store.load_workspace(Path(tempfile.gettempdir()) / "definitely_missing_ws_9f8a.json")
    except store.WorkspacePersistenceError:
        raised = True
    assert raised


# 11/12. Backward-compatible aliases and existing callers.
def test_backward_compatible_aliases():
    assert ip.ICPPortfolio is ip.CompanyWorkspace
    assert ip.ICPProject is ip.MarketHypothesis
    # existing construction + API still works
    port = ip.ICPPortfolio(company=_company())
    proj = port.create_project("FinTech", "h")
    assert isinstance(proj, ip.ICPProject)
    assert port.get_project(proj.project_id) is proj
    composed = port.composed(proj)
    assert isinstance(composed, bk.BusinessKnowledge)
    # a GeneratedICP round-trips losslessly on its own too (fingerprint stable)
    from generated_icp import new_icp
    icp = new_icp("X")
    assert gi.GeneratedICP.from_dict(icp.to_dict()).to_dict() == icp.to_dict()


def test_general_icp_lineage_is_structural():
    # scope is by ownership: general ICPs live on the workspace, adapted ICPs on the hypothesis
    ws = ip.CompanyWorkspace(company=_company())
    from generated_icp import new_icp
    ws.general_icp_versions.append(new_icp("General Acme ICP"))
    h = _approved_hypothesis(ws)
    ws2 = _roundtrip(ws)
    assert ws2.latest_general_icp() is not None
    assert ws2.latest_general_icp().metadata.name == "General Acme ICP"
    # adapted ICP stays on the hypothesis, not the workspace general lineage
    assert ws2.get_hypothesis(h.project_id).approved_versions[0].metadata.name != "General Acme ICP"


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
