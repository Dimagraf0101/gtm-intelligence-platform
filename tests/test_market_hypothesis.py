"""Tests for Market Hypothesis adaptation (Sprint 8).

Proves: an Adapted ICP is generated per hypothesis from Company + Hypothesis knowledge, records the
source General ICP's ArtifactIdentity, never modifies the General ICP or Company knowledge, keeps
hypotheses independent (delete one, others intact), versions immutably, and round-trips. Offline, no
LLM, no pytest:

    ./.venv/bin/python tests/test_market_hypothesis.py
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
import general_icp as gicp             # noqa: E402
import adapted_icp as aicp             # noqa: E402
import workspace_store as store         # noqa: E402


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "Acme builds custom software",
                                 "value_proposition": "v", "business_model": "services"},
            "dimensions": [
                {"name": "Capability fit", "purpose": "p", "weight": 60, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Engagement fit", "purpose": "p", "weight": 40, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": [], "acceptable": [], "non_ideal": []},
            "generation_notes": "note",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _company():
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "overview", "Acme builds custom software"),
                           ("service", "name", "Custom software delivery"),
                           ("capability", "focus", "Cloud migration")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    return c


def _workspace_with_general():
    ws = ip.CompanyWorkspace(company=_company(), name="Hygge Software")
    gicp.generate_and_append(ws, client=FakeDraftClient())
    return ws


def _hypothesis(ws, name="Healthcare", target="Healthcare"):
    h = ws.create_hypothesis(name, f"Sell to {name}")
    h.project_knowledge.add_item("industry", "target", target, status=bk.CONFIRMED,
                                 evidence_excerpt=target)
    return h


# Market Hypothesis is an aggregate owned by CompanyWorkspace; many are independent.
def test_company_has_many_independent_hypotheses():
    ws = _workspace_with_general()
    for nm in ("Healthcare", "Logistics", "Real Estate"):
        _hypothesis(ws, nm, nm)
    assert [h.name for h in ws.hypotheses] == ["Healthcare", "Logistics", "Real Estate"]
    assert len({h.project_id for h in ws.hypotheses}) == 3
    # each owns its own knowledge overlay
    heal = ws.hypotheses[0]
    assert "Healthcare" in heal.hypothesis_knowledge.field("industries")
    assert "Logistics" not in heal.hypothesis_knowledge.field("industries")


# Adapted ICP is derived from Company + Hypothesis knowledge and records the source General ICP.
def test_adapted_icp_derives_from_general_and_hypothesis():
    ws = _workspace_with_general()
    general_id = idy.artifact_identity_str(ws.latest_general_icp())
    h = _hypothesis(ws)
    res = aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    assert res.ok and res.generated_icp is not None
    icp = res.generated_icp
    assert icp.metadata.icp_scope == gi.ICP_SCOPE_ADAPTED
    assert icp.metadata.derived_from_general_icp == general_id            # source General ICP recorded
    assert res.derived_from_general_icp == general_id
    # hypothesis knowledge is reflected; it is the hypothesis's own draft lineage
    assert "Healthcare" in icp.to_json()
    assert h.draft_versions and h.draft_versions[-1] is icp
    assert res.validation_result is not None                             # IQS ran


# Refused deterministically when no General ICP exists (workflow order enforced).
def test_refuses_without_general_icp():
    ws = ip.CompanyWorkspace(company=_company(), name="NoGeneral")
    h = _hypothesis(ws)
    res = aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    assert not res.ok and "General ICP" in res.refusal_reason
    assert h.draft_versions == []                                        # nothing appended


# The General ICP is never modified by adaptation.
def test_general_icp_never_modified():
    ws = _workspace_with_general()
    before = json.dumps(ws.latest_general_icp().to_dict())
    before_id = idy.artifact_identity_str(ws.latest_general_icp())
    h = _hypothesis(ws)
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    assert json.dumps(ws.latest_general_icp().to_dict()) == before
    assert idy.artifact_identity_str(ws.latest_general_icp()) == before_id
    assert ws.latest_general_icp().metadata.icp_scope == gi.ICP_SCOPE_GENERAL


# Company Business Knowledge is never modified by adaptation.
def test_company_knowledge_never_modified():
    ws = _workspace_with_general()
    before = ws.company.to_json()
    h = _hypothesis(ws)
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    assert ws.company.to_json() == before


# Version history: each generation appends a new immutable adapted version.
def test_adapted_versions_append_immutably():
    ws = _workspace_with_general()
    h = _hypothesis(ws)
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    v1_snapshot = json.dumps(h.draft_versions[0].to_dict())
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    assert [d.metadata.version for d in h.draft_versions] == ["1", "2"]
    assert json.dumps(h.draft_versions[0].to_dict()) == v1_snapshot      # earlier version unchanged
    assert h.draft_versions[0] is not h.draft_versions[1]


# Deleting one hypothesis never affects another.
def test_delete_hypothesis_is_independent():
    ws = _workspace_with_general()
    a = _hypothesis(ws, "Healthcare", "Healthcare")
    b = _hypothesis(ws, "Logistics", "Logistics")
    aicp.generate_adapted_icp(ws, a, client=FakeDraftClient())
    aicp.generate_adapted_icp(ws, b, client=FakeDraftClient())
    b_id = idy.artifact_identity_str(b.draft_versions[-1])
    removed = ws.delete_hypothesis(a.project_id)
    assert removed.project_id == a.project_id
    assert [h.name for h in ws.hypotheses] == ["Logistics"]
    # b is untouched
    assert idy.artifact_identity_str(ws.get_hypothesis(b.project_id).draft_versions[-1]) == b_id
    # deleting a missing hypothesis raises
    raised = False
    try:
        ws.delete_hypothesis("nope")
    except KeyError:
        raised = True
    assert raised


# The whole aggregate (hypotheses + adapted ICPs + derivation) survives save/load.
def test_market_hypothesis_roundtrip():
    ws = _workspace_with_general()
    general_id = idy.artifact_identity_str(ws.latest_general_icp())
    h = _hypothesis(ws)
    res = aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    adapted_id = idy.artifact_identity_str(res.generated_icp)

    ws2 = store.loads(store.dumps(ws))
    h2 = ws2.get_hypothesis(h.project_id)
    assert h2.hypothesis_knowledge.field("industries") == h.project_knowledge.field("industries")
    assert len(h2.draft_versions) == 1
    assert h2.draft_versions[-1].metadata.derived_from_general_icp == general_id   # provenance survives
    assert idy.artifact_identity_str(h2.draft_versions[-1]) == adapted_id          # identity stable
    assert idy.artifact_identity_str(ws2.latest_general_icp()) == general_id


# Old JSON (no derived_from field) loads with a safe default.
def test_old_json_without_derived_from_loads():
    old = gi.new_icp("Legacy Adapted")
    md = old.to_dict()
    md["metadata"].pop("derived_from_general_icp", None)                 # pre-Sprint-8 payload
    restored = gi.GeneratedICP.from_dict(md)
    assert restored.metadata.derived_from_general_icp == ""              # safe default
    # ...and still round-trips + fingerprints stable (derived_from is not in the fingerprint)
    assert idy.fingerprint_generated_icp(gi.GeneratedICP.from_dict(md)) == idy.fingerprint_generated_icp(old)


# derived_from is NOT part of the fingerprint / identity (provenance only).
def test_derived_from_not_in_fingerprint():
    a = gi.new_icp("X")
    b = copy.deepcopy(a)
    b.metadata.derived_from_general_icp = "general_icp:1:deadbeef"
    assert idy.fingerprint_generated_icp(a) == idy.fingerprint_generated_icp(b)
    assert idy.content_fingerprint(a) == idy.content_fingerprint(b)
    assert idy.artifact_identity(a) == idy.artifact_identity(b)


# Reuses existing approval unchanged: an adapted draft can be reviewed + approved as before.
def test_adapted_icp_flows_into_existing_approval():
    import strategy_review as sr
    import icp_approval as ap
    ws = _workspace_with_general()
    h = _hypothesis(ws)
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    srw = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    srw.start_review()
    srw.set_weight("Capability fit", 60)
    srw.set_weight("Engagement fit", 40)
    for cand in srw.exclusion_candidates():
        srw.activate_exclusion(cand["rule"])
    reviewed, _ = srw.generate_reviewed_draft()
    ack = [w for w, _ in ap.warnings_with_ids(reviewed)]
    approved = ap.approve_icp_version(h, reviewed, approved_by="dana", acknowledged_warning_ids=ack)
    assert approved.metadata.status == gi.STATUS_APPROVED
    assert ap.get_active_approved_icp(h) is not None


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
