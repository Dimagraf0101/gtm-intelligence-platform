"""End-to-end pipeline + architectural invariant tests (Sprint 5.7B).

One headless run of the REAL system — no Streamlit, no network, no Anthropic — from a BusinessKnowledge
fixture through draft → strategy → IQS → approval → bridge → score_leads → workbook export. Plus the
load-bearing invariants the 5.7A review called out. No pytest:

    ./.venv/bin/python tests/test_end_to_end_pipeline.py
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
import strategy_review as sr            # noqa: E402
import icp_approval as ap               # noqa: E402
import icp_identity as idy              # noqa: E402
import icp_adapter as ad                # noqa: E402
import qualification_bridge as qb       # noqa: E402
import scoring as sc                    # noqa: E402
import export                           # noqa: E402
import workspace_revision as wr         # noqa: E402


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


def _company():
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "name", "Acme"), ("service", "name", "Custom software"),
                           ("industry", "target", "FinTech"), ("buyer", "role", "CTO"),
                           ("geography", "region", "US"), ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing agencies")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    return c


def _approve_full(port, project):
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Segment fit", 40)
    ws.set_weight("Buyer persona", 60)
    for cand in ws.exclusion_candidates():
        ws.activate_exclusion(cand["rule"])
    reviewed, report = ws.generate_reviewed_draft()
    ack = [w for w, _ in ap.warnings_with_ids(reviewed)]
    approved = ap.approve_icp_version(project, reviewed, approved_by="dana",
                                      acknowledged_warning_ids=ack)
    return ws, reviewed, approved


# --- the end-to-end architectural contract -----------------------------------
def test_end_to_end_knowledge_to_export():
    port = ip.ICPPortfolio(company=_company())
    project = port.create_project("FinTech", "Sell to FinTech scale-ups")
    ws, reviewed, approved = _approve_full(port, project)

    # exactly one Approved ICP reached the point of qualification
    assert approved.metadata.status == gi.STATUS_APPROVED
    assert len(project.approved_versions) == 1
    approved_snapshot = json.dumps(project.approved_versions[0].to_dict())

    # the bridge builds a context via the adapter (the only Generator->ICPProfile boundary)
    ctx = qb.context_from_approved_project(project)
    assert ctx.source_type == qb.SOURCE_APPROVED
    assert ctx.profile is not None                             # adapter produced the engine profile
    assert ctx.source_fingerprint == idy.fingerprint_generated_icp(
        ap.get_active_approved_icp(project))                   # context fp matches the approved version

    # qualification runs through the SAME engine; results are produced
    leads = [sc.normalize_lead({"company": "BigCo", "title": "CTO", "company_size_range": "100-500",
                                "location": "US",
                                "linkedin_url": "https://linkedin.com/in/bigco-cto"}, 0)]
    results = qb.score_with_context(leads, ctx, client=sc.MockClient())
    assert results and results[0].icp == "FinTech"

    # workbook + CSV export bytes are produced
    by_index = {ld.index: ld for ld in leads}
    pairs = [(by_index[r.lead_index], r) for r in results if r.lead_index in by_index]
    workbook = export.to_workbook_bytes(pairs, ctx.name)
    main_df = export.build_main_dataframe(pairs)
    main_csv = export.to_main_csv_bytes(main_df)
    assert isinstance(workbook, (bytes, bytearray)) and len(workbook) > 0
    assert isinstance(main_csv, (bytes, bytearray)) and len(main_csv) > 0

    # the Approved ICP was never mutated by bridge / scoring / export
    assert json.dumps(project.approved_versions[0].to_dict()) == approved_snapshot
    # the legacy PDF path was not involved (approved path carries a profile)
    assert ctx.source_type != qb.SOURCE_UPLOADED


# --- invariant: approved immutability across the whole downstream -------------
def test_approved_icp_immutable_downstream():
    port = ip.ICPPortfolio(company=_company())
    project = port.create_project("FinTech", "h")
    _, _, approved = _approve_full(port, project)
    stored = json.dumps(project.approved_versions[0].to_dict())
    # a returned active copy is safe to mutate; stored version is unaffected
    active = ap.get_active_approved_icp(project)
    active.metadata.name = "HACKED"
    active.dimensions[0].weight = 999
    ctx = qb.context_from_approved_project(project)
    qb.score_with_context([sc.normalize_lead({"company": "X", "title": "CTO"}, 0)], ctx,
                          client=sc.MockClient())
    assert json.dumps(project.approved_versions[0].to_dict()) == stored


# --- invariant: score_leads does not mutate the supplied profile / text -------
def test_score_leads_does_not_mutate_profile_or_text():
    port = ip.ICPPortfolio(company=_company())
    project = port.create_project("FinTech", "h")
    _approve_full(port, project)
    ctx = qb.context_from_approved_project(project)
    profile_before = ctx.profile.to_dict()
    text_before = ctx.semantic_text
    sc.score_leads([sc.normalize_lead({"company": "X", "title": "CTO"}, 0)],
                   ctx.semantic_text, ctx.name, profile=ctx.profile, client=sc.MockClient())
    assert ctx.profile.to_dict() == profile_before
    assert ctx.semantic_text == text_before


# --- invariant: composed knowledge isolation ---------------------------------
def test_composed_knowledge_never_mutates_sources():
    port = ip.ICPPortfolio(company=_company())
    project = port.create_project("FinTech", "h")
    project.project_knowledge.add_item("industry", "target", "InsurTech", status=bk.CONFIRMED,
                                       evidence_excerpt="InsurTech")
    company_before = port.company.to_json()
    project_before = project.project_knowledge.to_json()
    composed = ip.ComposedProjectKnowledge(port.company, project).composed()
    # mutate the transient composed copy every way a caller might
    for it in composed.knowledge_items:
        it.value = "MUTATED"
        it.status = bk.REJECTED
    composed.add_item("industry", "target", "Injected", status=bk.CONFIRMED)
    composed.knowledge_items.clear()
    assert port.company.to_json() == company_before
    assert project.project_knowledge.to_json() == project_before


# --- invariant: workspace invalidation is revision-driven --------------------
def test_workspace_revision_tokens():
    port = ip.ICPPortfolio(company=_company())
    project = port.create_project("FinTech", "h")

    # knowledge_revision changes ONLY when curated knowledge changes
    r0 = wr.knowledge_revision(port.company, project)
    assert wr.knowledge_revision(port.company, project) == r0          # deterministic / stable
    project.project_knowledge.add_item("subsegment", "focus", "embedded finance",
                                       status=bk.CONFIRMED, evidence_excerpt="x")
    r1 = wr.knowledge_revision(port.company, project)
    assert r1 != r0                                                    # project knowledge changed
    port.company.add_item("capability", "focus", "Cloud", status=bk.CONFIRMED, evidence_excerpt="x")
    assert wr.knowledge_revision(port.company, project) != r1          # company knowledge changed

    # approval_inputs_revision changes on strategy/reviewed-draft, NOT on approving
    ws, reviewed, _approved_ignore = None, None, None
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    a0 = wr.approval_inputs_revision(project)
    ws.set_weight("Segment fit", 40)
    ws.set_weight("Buyer persona", 60)
    a1 = wr.approval_inputs_revision(project)
    assert a1 != a0                                                    # strategy revision advanced
    for cand in ws.exclusion_candidates():
        ws.activate_exclusion(cand["rule"])
    reviewed, _ = ws.generate_reviewed_draft()
    a2 = wr.approval_inputs_revision(project)
    assert a2 != a1                                                    # a reviewed draft was produced
    ack = [w for w, _ in ap.warnings_with_ids(reviewed)]
    ap.approve_icp_version(project, reviewed, approved_by="dana", acknowledged_warning_ids=ack)
    assert wr.approval_inputs_revision(project) == a2                  # approving does NOT self-invalidate

    # knowledge_revision is stable under strategy edits (so the strategy page never spuriously rebuilds)
    rk = wr.knowledge_revision(port.company, project)
    ws.set_weight("Segment fit", 55)
    ws.set_weight("Buyer persona", 45)
    assert wr.knowledge_revision(port.company, project) == rk


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
