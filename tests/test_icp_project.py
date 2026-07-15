"""Tests for the ICP Project domain (pipeline/icp_project.py) and the scope-aware review workspace.

Proves the Sprint 5.2 isolation guarantees: one CompanyKnowledge base + many isolated ICP Projects,
composed read views, explicit human-only knowledge movement, temporal context persistence, and draft
versioning. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_icp_project.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import icp_project as ip                # noqa: E402
import knowledge_review as kr           # noqa: E402
import knowledge_extractor as ke        # noqa: E402


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "d", "value_proposition": "v", "business_model": "m"},
            "dimensions": [
                {"name": "Segment fit", "purpose": "p", "weight": 50, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Buyer persona", "purpose": "p", "weight": 50, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": ["x"], "acceptable": [], "non_ideal": ["y"]},
            "generation_notes": "note",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _company():
    """Reusable, hypothesis-independent company facts."""
    c = bk.BusinessKnowledge()
    c.add_item("company", "name", "Acme Software", status=bk.CONFIRMED, evidence_excerpt="Acme")
    c.add_item("service", "name", "Custom software delivery", status=bk.CONFIRMED,
               evidence_excerpt="Custom software delivery")
    c.add_item("technology", "stack", "Python", status=bk.CONFIRMED, evidence_excerpt="Python")
    c.add_item("business_model", "model", "Time & materials", status=bk.CONFIRMED,
               evidence_excerpt="Time & materials")           # single-valued fact
    return c


def _portfolio():
    port = ip.ICPPortfolio(company=_company())
    fintech = port.create_project("FinTech", hypothesis="Sell to FinTech scale-ups")
    healthcare = port.create_project("Healthcare", hypothesis="Sell to hospital networks")
    fintech.project_knowledge.add_item("industry", "target", "FinTech", status=bk.CONFIRMED,
                                       evidence_excerpt="FinTech")
    fintech.project_knowledge.add_item("buyer", "role", "VP Engineering", status=bk.CONFIRMED)
    healthcare.project_knowledge.add_item("industry", "target", "Healthcare", status=bk.CONFIRMED,
                                          evidence_excerpt="Healthcare")
    healthcare.project_knowledge.add_item("buyer", "role", "CMIO", status=bk.CONFIRMED)
    return port, fintech, healthcare


# 1. Two ICP Projects share one CompanyKnowledge.
def test_projects_share_one_company_knowledge():
    port, fintech, healthcare = _portfolio()
    assert fintech.project_knowledge is not healthcare.project_knowledge
    fin_composed = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    heal_composed = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    # Same company object underlies both; a company edit is visible in both composed views.
    port.company.add_item("capability", "focus", "Cloud migration", status=bk.CONFIRMED,
                          evidence_excerpt="Cloud migration")
    assert "Cloud migration" in ip.ComposedProjectKnowledge(port.company, fintech).composed().capabilities
    assert "Cloud migration" in ip.ComposedProjectKnowledge(port.company, healthcare).composed().capabilities


# 2. A FinTech project item never appears in Healthcare.
def test_project_item_does_not_leak_across_projects():
    port, fintech, healthcare = _portfolio()
    heal = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert "FinTech" not in heal.field("industries")
    assert "VP Engineering" not in heal.buyer_roles
    fin = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    assert "Healthcare" not in fin.field("industries")
    assert "CMIO" not in fin.buyer_roles


# 3. Company Knowledge appears in both composed views.
def test_company_knowledge_in_every_composed_view():
    port, fintech, healthcare = _portfolio()
    for proj in (fintech, healthcare):
        composed = ip.ComposedProjectKnowledge(port.company, proj).composed()
        assert "Custom software delivery" in composed.services
        assert "Python" in composed.technologies


# 4. A project override affects only that project (single-valued category).
def test_override_is_isolated_to_one_project():
    port, fintech, healthcare = _portfolio()
    # Company business model = Time & materials; FinTech overrides the SAME single-value fact.
    company_item = port.company.get_items(category="business_model")[0]
    ws = kr.KnowledgeReviewWorkspace.for_project(port.company, fintech)
    ws.copy_to_project(company_item.knowledge_id, value="Fixed-bid")
    fin = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    heal = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert "Fixed-bid" in fin.business_models and "Time & materials" not in fin.business_models
    assert "Time & materials" in heal.business_models and "Fixed-bid" not in heal.business_models
    assert "Time & materials" in port.company.business_models                            # company intact


# 5. Promoting a project item to CompanyKnowledge requires explicit human action.
def test_promotion_is_explicit_only():
    port, fintech, _ = _portfolio()
    fintech.project_knowledge.add_item("capability", "edge", "Real-time fraud scoring",
                                       status=bk.CONFIRMED, evidence_excerpt="fraud scoring")
    # No automatic promotion: composing / gap detection / draft generation never move it up.
    ip.ComposedProjectKnowledge(port.company, fintech).composed()
    assert "Real-time fraud scoring" not in port.company.capabilities
    # Only the explicit human action promotes it.
    pid = fintech.project_knowledge.get_items(category="capability")[0].knowledge_id
    ip.promote_to_company(fintech, port.company, pid)
    assert "Real-time fraud scoring" in port.company.capabilities
    assert "Real-time fraud scoring" not in [i.value for i in
                                             fintech.project_knowledge.get_items(category="capability")
                                             if i.is_active]


# 6. Promotion preserves provenance.
def test_promotion_preserves_provenance():
    port, fintech, _ = _portfolio()
    ref = bk.SourceReference(source_id="s1", filename="fintech_deck.pdf", source_category="deck",
                             section_index=2)
    fintech.project_knowledge.add_item("capability", "edge", "Real-time fraud scoring",
                                       status=bk.CONFIRMED, origin=bk.ORIGIN_SOURCE,
                                       source_references=[ref], evidence_excerpt="fraud scoring")
    pid = fintech.project_knowledge.get_items(category="capability")[0].knowledge_id
    promoted = ip.promote_to_company(fintech, port.company, pid)
    assert [r.filename for r in promoted.source_references] == ["fintech_deck.pdf"]
    assert promoted.evidence_excerpt == "fraud scoring"
    assert promoted.origin == bk.ORIGIN_USER                       # human action recorded
    assert any("Promoted from ICP project" in n for n in promoted.notes)


# 7. temporal_context persists through extraction and review.
def test_temporal_context_persists_through_extraction():
    knowledge = bk.BusinessKnowledge()
    src_index = {}
    # Drive the ingest path directly (offline, no LLM): a proposal marked historical must persist.
    proposal = {"category": "industry", "attribute": "target", "value": "Legacy Banking",
                "status": "proposed", "confidence": 0.9, "temporal_context": "historical_market",
                "source_filename": "", "evidence_excerpt": ""}
    ke._ingest_proposal(proposal, knowledge, src_index, ke.KnowledgeExtractionResult())
    item = knowledge.get_items(category="industry")[0]
    assert item.temporal_context == bk.TEMPORAL_HISTORICAL
    # And a manual review edit can set it too.
    ws = kr.KnowledgeReviewWorkspace(knowledge)
    ws.edit(item.knowledge_id, temporal_context=bk.TEMPORAL_CURRENT)
    assert knowledge._get(item.knowledge_id).temporal_context == bk.TEMPORAL_CURRENT


# 8. Historical knowledge is not promoted into current target criteria.
def test_historical_knowledge_not_a_current_target():
    import icp_draft_generator as dg
    knowledge = _company()
    knowledge.add_item("industry", "target", "Newspapers", status=bk.CONFIRMED,
                       evidence_excerpt="Newspapers", temporal_context=bk.TEMPORAL_HISTORICAL)
    knowledge.add_item("industry", "target", "FinTech", status=bk.CONFIRMED,
                       evidence_excerpt="FinTech", temporal_context=bk.TEMPORAL_CURRENT)
    included, _, historical = dg._targets(knowledge, "industry")
    assert "Newspapers" in historical and "Newspapers" not in included
    assert "FinTech" in included


# 9. Draft generation uses CompanyKnowledge + only the selected project's knowledge.
def test_draft_uses_company_plus_selected_project_only():
    port, fintech, healthcare = _portfolio()
    ws = kr.KnowledgeReviewWorkspace.for_project(port.company, fintech)
    res = ws.generate_draft(client=FakeDraftClient(), icp_name="FinTech ICP")
    assert res.generated_icp is not None
    blob = res.generated_icp.to_json() if hasattr(res.generated_icp, "to_json") \
        else json.dumps(res.generated_icp.to_dict())
    assert "FinTech" in blob                    # selected project's hypothesis facts are present
    assert "Healthcare" not in blob             # the other project's facts never leak in
    assert "CMIO" not in blob


# 10. Every draft generation creates a new version.
def test_every_generation_creates_new_version():
    port, fintech, _ = _portfolio()
    ws = kr.KnowledgeReviewWorkspace.for_project(port.company, fintech)
    assert len(fintech.draft_versions) == 0
    ws.generate_draft(client=FakeDraftClient())
    ws.generate_draft(client=FakeDraftClient())
    assert len(fintech.draft_versions) == 2
    assert fintech.draft_versions[0] is not fintech.draft_versions[1]


# 11. Previous drafts remain unchanged.
def test_previous_drafts_are_immutable():
    port, fintech, _ = _portfolio()
    ws = kr.KnowledgeReviewWorkspace.for_project(port.company, fintech)
    ws.generate_draft(client=FakeDraftClient(), icp_name="First")
    first = fintech.draft_versions[0]
    first_snapshot = json.dumps(first.to_dict())
    # A new curation + a second generation must not mutate the first draft.
    ws.add("industry", "target", "InsurTech", scope=kr.SCOPE_PROJECT)
    ws.generate_draft(client=FakeDraftClient(), icp_name="Second")
    assert json.dumps(fintech.draft_versions[0].to_dict()) == first_snapshot
    assert fintech.draft_versions[0] is first


# 12. Knowledge Review operates on a selected ICP Project.
def test_review_operates_on_selected_project():
    port, fintech, healthcare = _portfolio()
    ws = kr.KnowledgeReviewWorkspace.for_project(port.company, fintech)
    company_vals = {v["value"] for v in ws.company_items()}
    project_vals = {v["value"] for v in ws.project_items()}
    composed_vals = {v["value"] for v in ws.composed_items()}
    assert "Custom software delivery" in company_vals
    assert "FinTech" in project_vals and "FinTech" not in company_vals
    assert {"Custom software delivery", "FinTech"} <= composed_vals
    assert "Healthcare" not in composed_vals            # only the selected project
    # Composed items are labelled by owning scope for the UI.
    scopes = {v["value"]: v["scope"] for v in ws.composed_items()}
    assert scopes["Custom software delivery"] == "company" and scopes["FinTech"] == "project"


# 13. Full existing test suite remains green (checked by run_all; here we assert backward-compat API).
def test_backward_compatible_single_mode_api():
    ws = kr.KnowledgeReviewWorkspace(_company())            # positional == company (single mode)
    assert ws.knowledge is ws.company
    assert ws.project is None
    vid = ws.items(category="service")[0]["knowledge_id"]
    ws.confirm(vid)
    assert ws.knowledge._get(vid).user_confirmed
    res = ws.generate_draft(client=FakeDraftClient())       # returns a result, no project storage
    assert res.generated_icp is not None


# ---------------------------------------------------------------------------
# Sprint 5.2.1 — scope hardening regressions
# ---------------------------------------------------------------------------

# 5.2.1(1) Unknown temporal context is not treated as current.
def test_missing_temporal_is_unknown_not_current():
    # A plain KnowledgeItem / add_item with no temporal info defaults to unknown, never current.
    assert bk.KnowledgeItem().temporal_context == bk.TEMPORAL_UNKNOWN
    k = bk.BusinessKnowledge()
    it = k.add_item("industry", "target", "FinTech", status=bk.CONFIRMED)
    assert it.temporal_context == bk.TEMPORAL_UNKNOWN and it.temporal_context != bk.TEMPORAL_CURRENT
    # Extraction with no temporal_context provided also persists as unknown (not current).
    proposal = {"category": "service", "attribute": "name", "value": "Delivery",
                "status": "proposed", "confidence": 0.9, "source_filename": "", "evidence_excerpt": ""}
    ke._ingest_proposal(proposal, k, {}, ke.KnowledgeExtractionResult())
    assert k.get_items(category="service")[0].temporal_context == bk.TEMPORAL_UNKNOWN
    # Only an explicit "current" persists as current.
    assert ke._persist_temporal("current") == bk.TEMPORAL_CURRENT
    assert ke._persist_temporal("") == bk.TEMPORAL_UNKNOWN


# 5.2.1(2) The composed view cannot mutate either underlying store.
def test_composed_view_cannot_mutate_underlying_stores():
    port, fintech, _ = _portfolio()
    before_company = port.company.to_json()
    before_project = fintech.project_knowledge.to_json()
    composed = ip.ComposedProjectKnowledge(port.company, fintech).composed()

    # Every mutating path on the composed BusinessKnowledge must leave the real stores untouched.
    cid = composed.get_items(category="service")[0].knowledge_id
    composed.edit_item(cid, value="HACKED")
    composed.confirm_item(cid)
    composed.reject_item(composed.get_items(category="industry")[0].knowledge_id)
    composed.add_item("industry", "target", "Injected", status=bk.CONFIRMED)
    # direct reference mutation
    composed.knowledge_items[0].value = "MUTATED"
    composed.knowledge_items[0].status = bk.REJECTED

    assert port.company.to_json() == before_company
    assert fintech.project_knowledge.to_json() == before_project
    assert "HACKED" not in port.company.to_json()
    assert "Injected" not in fintech.project_knowledge.to_json()


# 5.2.1(3a) Multi-value project knowledge does not hide company multi-values.
def test_multivalue_union_does_not_hide_company_values():
    port, fintech, _ = _portfolio()
    port.company.add_item("industry", "target", "SaaS", status=bk.CONFIRMED, evidence_excerpt="SaaS")
    # FinTech project adds its own industry; the company's must still be present (union).
    fin = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    assert "SaaS" in fin.field("industries")           # company multi-value not hidden
    assert "FinTech" in fin.field("industries")        # project multi-value present too


# 5.2.1(3b) Multi-value union is de-duplicated.
def test_multivalue_union_is_deduplicated():
    port, fintech, _ = _portfolio()
    port.company.add_item("industry", "target", "FinTech", status=bk.CONFIRMED,
                          evidence_excerpt="FinTech")   # same value the project already has
    fin = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    assert fin.field("industries").count("FinTech") == 1


# 5.2.1(3c) Single-value override affects only the selected project.
def test_single_value_override_isolated():
    port, fintech, healthcare = _portfolio()
    fintech.project_knowledge.add_item("business_model", "model", "Fixed-bid", status=bk.CONFIRMED,
                                       origin=bk.ORIGIN_USER, user_confirmed=True)
    fin = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    heal = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert fin.business_models == ["Fixed-bid"]
    assert heal.business_models == ["Time & materials"]


# 5.2.1(3d) Project conflicts stay project-local.
def test_project_conflicts_stay_local():
    port, fintech, healthcare = _portfolio()
    # Two differing single-value assertions in the FinTech project create a project-local conflict.
    fintech.project_knowledge.add_item("company_size", "preference", "50-200", status=bk.CONFIRMED)
    fintech.project_knowledge.add_item("company_size", "preference", "500-1000", status=bk.CONFIRMED)
    assert fintech.project_knowledge.conflicts
    heal = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert not heal.conflicts                          # never leaks into another project
    assert not port.company.conflicts                  # nor into company


# 5.2.1(4a) Extraction to Project A never appears in Project B or Company Knowledge.
def test_extraction_to_project_is_isolated():
    port, fintech, healthcare = _portfolio()
    src = bk.BusinessKnowledge()
    src.add_item("customer", "best_customer", "FinCo", status=bk.CONFIRMED, evidence_excerpt="FinCo")
    ip.route_extraction(port.company, src, scope=ip.SCOPE_PROJECT, project=fintech)
    fin = ip.ComposedProjectKnowledge(port.company, fintech).composed()
    heal = ip.ComposedProjectKnowledge(port.company, healthcare).composed()
    assert "FinCo" in [i.value for i in fin.get_items(category="customer")]
    assert "FinCo" not in [i.value for i in heal.get_items(category="customer")]
    assert "FinCo" not in [i.value for i in port.company.get_items(category="customer")]


# 5.2.1(4b) Extraction to Company Knowledge appears in both project composed views.
def test_extraction_to_company_visible_everywhere():
    port, fintech, healthcare = _portfolio()
    src = bk.BusinessKnowledge()
    src.add_item("capability", "focus", "24/7 support", status=bk.CONFIRMED,
                 evidence_excerpt="24/7 support")
    ip.route_extraction(port.company, src, scope=ip.SCOPE_COMPANY)
    for proj in (fintech, healthcare):
        composed = ip.ComposedProjectKnowledge(port.company, proj).composed()
        assert "24/7 support" in composed.capabilities


# 5.2.1(4c) Project scope with no project raises — no silent fallback to Company Knowledge.
def test_route_extraction_project_scope_requires_project():
    company = _company()
    src = bk.BusinessKnowledge()
    src.add_item("customer", "best_customer", "X", status=bk.CONFIRMED, evidence_excerpt="X")
    raised = False
    try:
        ip.route_extraction(company, src, scope=ip.SCOPE_PROJECT, project=None)
    except ValueError:
        raised = True
    assert raised
    assert "X" not in [i.value for i in company.get_items(category="customer")]   # no fallback


# 5.2.1(4d) Extraction preserves provenance and temporal_context.
def test_route_extraction_preserves_provenance_and_temporal():
    port, fintech, _ = _portfolio()
    ref = bk.SourceReference(source_id="s1", filename="playbook.pdf", source_category="playbook")
    src = bk.BusinessKnowledge()
    src.add_item("customer", "best_customer", "OldCo", status=bk.CONFIRMED, source_references=[ref],
                 evidence_excerpt="OldCo", temporal_context=bk.TEMPORAL_FORMER)
    ip.route_extraction(port.company, src, scope=ip.SCOPE_PROJECT, project=fintech)
    it = fintech.project_knowledge.get_items(category="customer")[0]
    assert [r.filename for r in it.source_references] == ["playbook.pdf"]
    assert it.temporal_context == bk.TEMPORAL_FORMER


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
