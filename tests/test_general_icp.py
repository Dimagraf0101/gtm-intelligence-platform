"""Tests for General ICP generation + lineage + persistence (Sprint 7).

Proves: a General ICP is generated from company knowledge ONLY, hypothesis knowledge is never used,
generation refuses deterministically on insufficient knowledge, unknowns stay unknown, versions are
immutable and append-only, fingerprints survive save/load, old Sprint 6 JSON still loads, and adapted
ICPs cannot enter the General lineage. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_general_icp.py
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
import workspace_store as store         # noqa: E402


class FakeDraftClient:
    """Deterministic offline draft: two dimensions totalling 100, no exclusion candidates."""
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


def _company_knowledge():
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "overview", "Acme builds custom software"),
                           ("service", "name", "Custom software delivery"),
                           ("capability", "focus", "Cloud migration"),
                           ("technology", "stack", "Python")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    c.add_unknown("funding stage")
    return c


def _workspace_with_hypothesis():
    ws = ip.CompanyWorkspace(company=_company_knowledge(), name="Hygge Software")
    h = ws.create_hypothesis("Healthcare", "Sell to hospitals")
    # distinctive hypothesis-only fact — must never appear in a General ICP
    h.project_knowledge.add_item("industry", "target", "Healthcare", status=bk.CONFIRMED,
                                 evidence_excerpt="Healthcare")
    h.project_knowledge.add_item("buyer", "role", "CMIO", status=bk.CONFIRMED)
    return ws, h


def _gen(ws):
    return gicp.generate_and_append(ws, client=FakeDraftClient())


# 1. General ICP generated from company knowledge.
def test_general_icp_generated_from_company_knowledge():
    ws, _ = _workspace_with_hypothesis()
    res = _gen(ws)
    assert res.ok and res.generated_icp is not None
    assert res.generated_icp.metadata.icp_scope == gi.ICP_SCOPE_GENERAL
    assert res.generated_icp.metadata.status == gi.STATUS_DRAFT      # never auto-approved
    assert res.validation_result is not None                        # IQS ran (sole authority)
    assert isinstance(res.is_mock, bool)                            # honestly reported


# 2. Hypothesis knowledge is not included.
def test_hypothesis_knowledge_is_excluded():
    ws, _ = _workspace_with_hypothesis()
    res = _gen(ws)
    blob = res.generated_icp.to_json()
    assert "Healthcare" not in blob and "CMIO" not in blob
    # ...and the company facts ARE reflected
    assert "Custom software delivery" in blob or "Cloud migration" in blob


# 3. Generation refused when company knowledge is insufficient.
def test_refuses_insufficient_company_knowledge():
    empty = ip.CompanyWorkspace(company=bk.BusinessKnowledge(), name="Empty Co")
    res = _gen(empty)
    assert not res.ok and res.generated_icp is None
    assert "Insufficient company knowledge" in res.refusal_reason
    assert empty.list_general_icps() == []                          # nothing appended on refusal
    # a knowledge base with only a target (no company/offer) is also insufficient
    only_target = bk.BusinessKnowledge()
    only_target.add_item("industry", "target", "SaaS", status=bk.CONFIRMED, evidence_excerpt="SaaS")
    assert gicp.sufficiency_reason(only_target) != ""


# 4. Unknown values remain unknown.
def test_unknown_values_remain_unknown():
    ws, _ = _workspace_with_hypothesis()
    res = _gen(ws)
    assert "funding stage" in res.generated_icp.unknown_fields      # declared, not invented
    assert res.summary()["unknown_fields"]                          # surfaced to the UI


# 5. New version is appended instead of overwriting history.
def test_new_version_is_appended():
    ws, _ = _workspace_with_hypothesis()
    _gen(ws)
    _gen(ws)
    assert len(ws.list_general_icps()) == 2
    assert [g.metadata.version for g in ws.list_general_icps()] == ["1", "2"]


# 6. Existing versions remain immutable.
def test_existing_versions_immutable():
    ws, _ = _workspace_with_hypothesis()
    _gen(ws)
    v1_snapshot = json.dumps(ws.list_general_icps()[0].to_dict())
    _gen(ws)
    assert json.dumps(ws.list_general_icps()[0].to_dict()) == v1_snapshot
    assert ws.list_general_icps()[0] is not ws.list_general_icps()[1]


# 7. Latest General ICP resolves correctly.
def test_latest_general_icp_resolves():
    ws, _ = _workspace_with_hypothesis()
    _gen(ws)
    _gen(ws)
    assert ws.latest_general_icp() is ws.list_general_icps()[-1]
    assert ws.latest_general_icp().metadata.version == "2"


# 8/9. Fingerprint stable after save/load; General ICP survives JSON round-trip.
def test_general_icp_roundtrip_and_stable_fingerprint():
    ws, _ = _workspace_with_hypothesis()
    _gen(ws)
    fp = idy.fingerprint_generated_icp(ws.latest_general_icp())
    ws2 = store.loads(store.dumps(ws))
    assert len(ws2.list_general_icps()) == 1
    assert ws2.latest_general_icp().metadata.icp_scope == gi.ICP_SCOPE_GENERAL
    assert idy.fingerprint_generated_icp(ws2.latest_general_icp()) == fp     # 8
    assert ws2.get_general_icp(fp) is not None                              # resolvable by fp
    assert ws2.latest_general_icp().to_dict() == ws.latest_general_icp().to_dict()  # 9 lossless


# 10. Older Sprint 6 workspace JSON loads with safe defaults.
def test_old_sprint6_json_loads_with_defaults():
    # a Sprint-6-style envelope: no general_icp_versions, and a nested ICP metadata with no icp_scope
    old_icp = gi.new_icp("Legacy Adapted")
    md = old_icp.to_dict()
    md["metadata"].pop("icp_scope", None)                          # simulate pre-Sprint-7 payload
    envelope = {
        "schema_version": store.SCHEMA_VERSION, "kind": "gtm_company_workspace",
        "workspace": {
            "workspace_id": "ws-legacy", "name": "Legacy Co", "created_at": "t", "updated_at": "t",
            "metadata": {}, "company": bk.BusinessKnowledge().to_dict(),
            # general_icp_versions intentionally ABSENT
            "hypotheses": [{
                "project_id": "p1", "name": "H", "hypothesis": "", "status": "active",
                "created_at": "t", "updated_at": "t",
                "project_knowledge": bk.BusinessKnowledge().to_dict(),
                "draft_versions": [md], "not_applicable": {}, "strategy": None,
                "approved_versions": [], "approval_records": [], "active_approved_version": None,
            }],
        },
    }
    ws = store.from_envelope(envelope)
    assert ws.list_general_icps() == []                            # missing field -> safe default
    h = ws.get_hypothesis("p1")
    assert h.draft_versions[0].metadata.icp_scope == gi.ICP_SCOPE_ADAPTED   # default applied


# 11. Adapted/hypothesis ICP cannot be added to the General ICP lineage.
def test_adapted_icp_cannot_enter_general_lineage():
    ws = ip.CompanyWorkspace(company=_company_knowledge())
    adapted = gi.new_icp("Adapted ICP")                            # default scope = adapted
    assert adapted.metadata.icp_scope == gi.ICP_SCOPE_ADAPTED
    raised = False
    try:
        ws.append_general_icp(adapted)
    except ValueError as e:
        raised = True
        assert "general-scoped" in str(e)
    assert raised and ws.list_general_icps() == []


# 12. UI-facing service returns deterministic error states.
def test_service_returns_deterministic_states():
    empty = ip.CompanyWorkspace(company=bk.BusinessKnowledge())
    r = gicp.generate_and_append(empty, client=FakeDraftClient())
    s = r.summary()
    assert s["ok"] is False and s["refusal_reason"] and s["is_valid"] is False
    ws, _ = _workspace_with_hypothesis()
    r2 = _gen(ws)
    s2 = r2.summary()
    assert s2["ok"] is True and isinstance(s2["is_valid"], bool)
    assert "blocking_gaps" in s2 and "unknown_fields" in s2


# 13. Offline / mock LLM mode works (no network).
def test_offline_mock_mode_works():
    import icp_draft_generator as dg
    client, is_live = dg.get_draft_client()                        # no API key -> mock
    assert is_live is False
    ws, _ = _workspace_with_hypothesis()
    res = gicp.generate_and_append(ws, client=client)
    assert res.ok and res.is_mock is True


# 15. No new top-level import cycles.
def test_no_import_cycles():
    # general_icp depends downward; nothing it imports may import general_icp back.
    for mod in ("icp_draft_generator", "business_knowledge", "knowledge_gaps", "generated_icp",
                "icp_project", "iqs_validator", "icp_identity"):
        src = (ROOT / "pipeline" / f"{mod}.py").read_text(encoding="utf-8")
        assert "import general_icp" not in src, f"{mod} imports general_icp (cycle risk)"
    # and it imports cleanly
    import importlib
    importlib.import_module("general_icp")


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
