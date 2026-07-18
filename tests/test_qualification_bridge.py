"""Tests for the Generator -> Qualification Bridge (pipeline/qualification_bridge.py) and the additive
``score_leads(..., profile=...)`` seam.

Proves both ICP sources converge on the one unchanged scoring engine, only Approved+valid ICPs
qualify, the Approved ICP is never mutated, and a captured run snapshot is stable. Offline, no LLM,
no pytest:

    ./.venv/bin/python tests/test_qualification_bridge.py
"""
import copy
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import generated_icp as gi              # noqa: E402
import icp_profile as ipf               # noqa: E402
import icp_project as ip                # noqa: E402
import strategy_review as sr            # noqa: E402
import icp_approval as ap               # noqa: E402
import scoring as sc                    # noqa: E402
import prequalification as pq           # noqa: E402
import qualification_bridge as qb       # noqa: E402

ICP_TEXT = "We sell software to FinTech CTOs. Prefer 50-500 employees. Reject staffing agencies."


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


def _approved_project():
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "name", "Acme"), ("service", "name", "Custom software"),
                           ("industry", "target", "FinTech"), ("buyer", "role", "CTO"),
                           ("geography", "region", "US"), ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing agencies")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    port = ip.ICPPortfolio(company=c)
    project = port.create_project("FinTech", "h")
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=FakeDraftClient())
    ws.start_review()
    ws.set_weight("Segment fit", 40)
    ws.set_weight("Buyer persona", 60)
    for cand in ws.exclusion_candidates():
        ws.activate_exclusion(cand["rule"])
    reviewed, _ = ws.generate_reviewed_draft()
    ack = [w for w, _ in ap.warnings_with_ids(reviewed)]
    ap.approve_icp_version(project, reviewed, approved_by="dana", acknowledged_warning_ids=ack)
    return port, project


def _leads(*rows):
    return [sc.normalize_lead(r, i) for i, r in enumerate(rows)]


# 1. Uploaded context builds correctly.
def test_uploaded_context():
    ctx = qb.context_from_uploaded("MyPDF", ICP_TEXT)
    assert ctx.source_type == qb.SOURCE_UPLOADED
    assert ctx.profile is None                          # score_leads will parse the text (legacy)
    assert ctx.name == "MyPDF" and ctx.semantic_text == ICP_TEXT
    assert ctx.source_version is None and ctx.source_fingerprint is None
    assert ctx.created_at is not None and ctx.source_label == "Uploaded Document"


# 2 & 3. Approved context builds correctly and carries profile/markdown/version/fingerprint.
def test_approved_context():
    port, project = _approved_project()
    ctx = qb.context_from_approved_project(project)
    assert ctx.source_type == qb.SOURCE_APPROVED
    assert isinstance(ctx.profile, ipf.ICPProfile)
    assert {(d.name, d.weight) for d in ctx.profile.scoring_dimensions} == \
        {("Segment fit", 40), ("Buyer persona", 60)}
    assert ctx.semantic_text.startswith("#") and len(ctx.semantic_text) > 50   # markdown
    assert ctx.source_version == "1"
    active = ap.get_active_approved_icp(project)
    assert ctx.source_fingerprint == ap.fingerprint_generated_icp(active)
    assert ctx.source_label == "Approved Generated ICP"


# 4. No Approved ICP -> BridgeError.
def test_no_approved_icp_raises():
    c = bk.BusinessKnowledge()
    c.add_item("company", "name", "Acme", status=bk.CONFIRMED, evidence_excerpt="Acme")
    port = ip.ICPPortfolio(company=c)
    project = port.create_project("FinTech", "h")           # never approved anything
    raised = False
    try:
        qb.context_from_approved_project(project)
    except qb.BridgeError as e:
        raised = True
        assert "no active approved" in str(e).lower()
    assert raised


# 5. A Draft ICP cannot qualify.
def test_draft_cannot_qualify():
    draft = gi.new_icp("Draft ICP")                          # status Draft
    assert draft.metadata.status == gi.STATUS_DRAFT
    raised = False
    try:
        qb.context_from_approved_icp(draft)
    except qb.BridgeError as e:
        raised = True
        assert "approved" in str(e).lower()
    assert raised


# 6. An adapter-invalid ICP -> BridgeError.
def test_adapter_invalid_icp_raises():
    bad = gi.GeneratedICP(metadata=gi.Metadata(name="Bad", status=gi.STATUS_APPROVED))  # no dimensions
    raised = False
    try:
        qb.context_from_approved_icp(bad)
    except qb.BridgeError as e:
        raised = True
        assert "iqs" in str(e).lower() or "valid" in str(e).lower()
    assert raised


# 7. score_leads(profile=None) behaves identically to before (still parses text).
def test_score_leads_profile_none_unchanged():
    called = {"n": 0}
    orig = sc.build_scoring_profile

    def spy(name, text):
        called["n"] += 1
        return orig(name, text)
    sc.build_scoring_profile = spy
    try:
        results = sc.score_leads(_leads({"company": "X", "title": "CTO"}), ICP_TEXT, "Plain",
                                 client=sc.MockClient())
    finally:
        sc.build_scoring_profile = orig
    assert called["n"] == 1 and len(results) == 1            # legacy path parsed the text exactly once


# 8. score_leads(profile=P) does not call build_scoring_profile().
def test_score_leads_profile_supplied_skips_parsing():
    port, project = _approved_project()
    profile = ap and qb.context_from_approved_project(project).profile
    called = {"n": 0}
    orig = sc.build_scoring_profile

    def spy(name, text):
        called["n"] += 1
        return orig(name, text)
    sc.build_scoring_profile = spy
    try:
        sc.score_leads(_leads({"company": "X", "title": "CTO"}), "irrelevant text", "FinTech",
                       profile=profile, client=sc.MockClient())
    finally:
        sc.build_scoring_profile = orig
    assert called["n"] == 0                                  # the supplied profile bypassed parsing


# 9. Prequalification uses the supplied profile.
def test_prequalification_uses_supplied_profile():
    # a profile whose EXPLICIT hard size exclusion must deterministically disqualify a tiny company
    profile = ipf.ICPProfile(
        name="SizeGated",
        scoring_dimensions=[ipf.ScoringDimension(name="Fit", weight=100)],
        scoring_weights={"Fit": 100},
        category_thresholds=[ipf.CategoryThreshold(label=b.label, min_score=b.min_score,
                                                   max_score=b.max_score)
                             for b in gi.standard_priority_bands()],
        hard_exclusions=["Reject if fewer than 50 employees"])
    seen = {}
    orig = pq.prequalify

    def spy(prof, lead, evs, **kw):
        seen["profile"] = prof
        return orig(prof, lead, evs, **kw)
    pq.prequalify = spy
    try:
        stats = {}
        results = sc.score_leads(_leads({"company": "Tiny Co", "title": "CTO",
                                         "company_size_range": "10-20"}),
                                 ICP_TEXT, "SizeGated", profile=profile, client=sc.MockClient(),
                                 stats=stats)
    finally:
        pq.prequalify = orig
    assert seen["profile"] is profile                        # exact supplied profile flowed in
    assert results[0].category == "Disqualified"             # ...and drove the deterministic reject
    assert stats["sent_to_model"] == 0                       # never reached the model


# 10. Decision uses the supplied profile.
def test_decision_uses_supplied_profile():
    port, project = _approved_project()
    profile = qb.context_from_approved_project(project).profile
    seen = {}
    orig = sc.decide

    def spy(prof, *a, **k):
        seen["profile"] = prof
        return orig(prof, *a, **k)
    sc.decide = spy
    try:
        # a valid, in-geography lead clears prequalification so it reaches the model + decision layer
        sc.score_leads(_leads({"company": "BigCo", "title": "CTO", "company_size_range": "100-500",
                               "location": "US",
                               "linkedin_url": "https://linkedin.com/in/bigco-cto"}),
                       ICP_TEXT, "FinTech", profile=profile, client=sc.MockClient())
    finally:
        sc.decide = orig
    assert seen.get("profile") is profile                    # decision layer scored the supplied profile


# 11. Uploaded and Approved paths both execute the identical scoring engine.
def test_both_paths_use_same_engine():
    port, project = _approved_project()
    ctx = qb.context_from_approved_project(project)
    leads = _leads({"company": "BigCo", "title": "CTO", "company_size_range": "100-500"})
    via_bridge = qb.score_with_context(copy.deepcopy(leads), ctx, client=sc.MockClient())
    direct = sc.score_leads(copy.deepcopy(leads), ctx.semantic_text, ctx.name,
                            profile=ctx.profile, client=sc.MockClient())
    assert [(r.lead_index, r.score, r.category) for r in via_bridge] == \
           [(r.lead_index, r.score, r.category) for r in direct]
    # and the uploaded path runs the very same function (profile=None)
    up = qb.context_from_uploaded("MyPDF", ICP_TEXT)
    up_results = qb.score_with_context(copy.deepcopy(leads), up, client=sc.MockClient())
    assert len(up_results) == 1 and up_results[0].icp == "MyPDF"


# 12. The Approved ICP is never mutated by the bridge or a scoring run.
def test_approved_icp_never_mutated():
    port, project = _approved_project()
    before = json.dumps(project.approved_versions[0].to_dict())
    ctx = qb.context_from_approved_project(project)
    qb.score_with_context(_leads({"company": "X", "title": "CTO"}), ctx, client=sc.MockClient())
    assert json.dumps(project.approved_versions[0].to_dict()) == before


# 13 & 14. The run snapshot is stable; changing the active Approved ICP afterward doesn't affect it.
def test_run_snapshot_is_stable():
    port, project = _approved_project()
    ctx = qb.context_from_approved_project(project)
    v_before, fp_before = ctx.source_version, ctx.source_fingerprint
    profile_before = ctx.profile
    # break the active pointer / clear approvals after capturing the context
    project.active_approved_version = None
    project.approved_versions.clear()
    assert ap.get_active_approved_icp(project) is None
    # the captured context is unchanged and still scores against the snapshotted profile
    assert ctx.source_version == v_before and ctx.source_fingerprint == fp_before
    assert ctx.profile is profile_before
    results = qb.score_with_context(_leads({"company": "X", "title": "CTO"}), ctx,
                                    client=sc.MockClient())
    assert len(results) == 1


# 15 & 16. A context is always exactly one source — the two modes can never be merged.
def test_context_is_single_source():
    port, project = _approved_project()
    up = qb.context_from_uploaded("PDF", ICP_TEXT)
    appr = qb.context_from_approved_project(project)
    # uploaded carries text but no profile; approved carries a profile; neither can hold "both"
    assert (up.source_type == qb.SOURCE_UPLOADED and up.profile is None
            and up.source_fingerprint is None)
    assert (appr.source_type == qb.SOURCE_APPROVED and appr.profile is not None
            and appr.source_fingerprint is not None)
    assert up.source_type != appr.source_type
    # frozen: a captured context cannot be mutated into a blended source
    raised = False
    try:
        up.profile = appr.profile        # type: ignore[misc]
    except Exception:
        raised = True
    assert raised


# 17/18/19. Legacy PDF/text path and results are unchanged (validate_context + delegation only).
def test_uploaded_path_matches_direct_score_leads():
    leads = _leads({"company": "X", "title": "CTO"}, {"company": "Y", "title": "VP Eng"})
    up = qb.context_from_uploaded("Plain", ICP_TEXT)
    via_bridge = qb.score_with_context(copy.deepcopy(leads), up, client=sc.MockClient())
    direct = sc.score_leads(copy.deepcopy(leads), ICP_TEXT, "Plain", client=sc.MockClient())
    assert [(r.lead_index, r.score, r.category, r.icp) for r in via_bridge] == \
           [(r.lead_index, r.score, r.category, r.icp) for r in direct]


def test_validate_context_blocks_empty_and_bad_source():
    assert qb.validate_context(qb.context_from_uploaded("N", ICP_TEXT)) == []
    empty = qb.QualificationICPContext(name="N", semantic_text="  ", source_type=qb.SOURCE_UPLOADED)
    assert qb.validate_context(empty)
    raised = False
    try:
        qb.score_with_context(_leads({"company": "X"}), empty, client=sc.MockClient())
    except qb.BridgeError:
        raised = True
    assert raised


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
