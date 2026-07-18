"""Tests for typed ICP artifact identity (pipeline/icp_identity.py) — Sprint 7.1.

Proves that the content fingerprint stays byte-for-byte stable (approvals unaffected) while General
and Adapted ICPs gain distinct, deterministic, persistable semantic identities, and that unknown
artifact types are rejected. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_artifact_identity.py
"""
import copy
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi             # noqa: E402
import icp_identity as idy             # noqa: E402
import icp_project as ip               # noqa: E402
import business_knowledge as bk        # noqa: E402
import workspace_store as store        # noqa: E402


def _icp(scope, *, name="Acme ICP", status=gi.STATUS_DRAFT, version="1"):
    icp = gi.GeneratedICP(metadata=gi.Metadata(name=name, version=version, status=status,
                                               icp_scope=scope))
    icp.business_context = gi.BusinessContext(description="desc", product_or_service="SDK")
    icp.dimensions = [gi.QualificationDimension(name="Fit", weight=100)]
    return icp


# Pinned Sprint 5.5 fingerprint for the canonical fixture (must never change).
_PINNED_FP = "08dba234fca68eae390445aaab4bfa244d71c4f2d70e05068d3f7cb2850ba9c3"


def _pinned_icp():
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
    return icp


# 1. General + Adapted with identical content/version -> same content_fingerprint, different identity.
def test_identical_content_same_fingerprint_different_identity():
    g = _icp(gi.ICP_SCOPE_GENERAL)
    a = _icp(gi.ICP_SCOPE_ADAPTED)
    assert idy.content_fingerprint(g) == idy.content_fingerprint(a)       # same content
    assert idy.fingerprint_generated_icp(g) == idy.fingerprint_generated_icp(a)  # legacy unchanged
    assert idy.artifact_identity(g) != idy.artifact_identity(a)           # different identity
    assert str(idy.artifact_identity(g)).startswith("general_icp:")
    assert str(idy.artifact_identity(a)).startswith("adapted_icp:")
    # the identity embeds the CONTENT fingerprint (status-blind), not the legacy fingerprint
    assert idy.artifact_identity(g).content_fingerprint == idy.content_fingerprint(g)


# 2. Draft and Approved of the same typed version -> different legacy fp, SAME ArtifactIdentity.
def test_identity_stable_across_status_transition():
    draft = _icp(gi.ICP_SCOPE_GENERAL, status=gi.STATUS_DRAFT)
    approved = _icp(gi.ICP_SCOPE_GENERAL, status=gi.STATUS_APPROVED)      # same type/version/content
    assert idy.fingerprint_generated_icp(draft) != idy.fingerprint_generated_icp(approved)  # status differs
    assert idy.artifact_identity(draft) == idy.artifact_identity(approved)                   # identity stable
    assert idy.artifact_identity_str(draft) == idy.artifact_identity_str(approved)


# 3. Artifact identity is deterministic.
def test_artifact_identity_deterministic():
    g = _icp(gi.ICP_SCOPE_GENERAL)
    assert idy.artifact_identity(g) == idy.artifact_identity(copy.deepcopy(g))
    assert idy.artifact_identity_str(g) == idy.artifact_identity_str(g)
    # str <-> parse round-trips
    s = idy.artifact_identity_str(g)
    assert str(idy.parse_artifact_identity(s)) == s
    assert idy.parse_artifact_identity(s) == idy.artifact_identity(g)


# 3b. Two versions with identical content have different identities (no collision).
def test_versions_with_identical_content_differ():
    v1 = _icp(gi.ICP_SCOPE_GENERAL, version="1")
    v2 = _icp(gi.ICP_SCOPE_GENERAL, version="2")
    assert idy.content_fingerprint(v1) == idy.content_fingerprint(v2)    # identical content
    assert idy.artifact_identity(v1) != idy.artifact_identity(v2)        # but distinct artifacts


# 3c. Different content in the same type/version has different identity.
def test_different_content_same_type_version_differ():
    a = _icp(gi.ICP_SCOPE_GENERAL, version="1")
    b = _icp(gi.ICP_SCOPE_GENERAL, version="1")
    b.dimensions = [gi.QualificationDimension(name="Fit", weight=50)]    # content changed
    assert idy.artifact_identity(a) != idy.artifact_identity(b)


# 4. Artifact identity survives JSON save/load (it is derived, recomputed identically).
def test_artifact_identity_survives_roundtrip():
    ws = ip.CompanyWorkspace(company=bk.BusinessKnowledge(), name="Co")
    g = _icp(gi.ICP_SCOPE_GENERAL)
    ws.append_general_icp(g)
    before = idy.artifact_identity_str(ws.latest_general_icp())
    ws2 = store.loads(store.dumps(ws))
    after = idy.artifact_identity_str(ws2.latest_general_icp())
    assert before == after
    assert ws2.latest_general_icp().metadata.icp_scope == gi.ICP_SCOPE_GENERAL


# 5. Sprint 6 / Sprint 7 JSON loads safely (missing scope -> adapted; identity still computable).
def test_old_json_identity_safe():
    # a GeneratedICP dict with NO icp_scope key (pre-Sprint-7)
    old = gi.new_icp("Legacy")
    md = old.to_dict()
    md["metadata"].pop("icp_scope", None)
    restored = gi.GeneratedICP.from_dict(md)
    assert restored.metadata.icp_scope == gi.ICP_SCOPE_ADAPTED           # safe default
    assert idy.artifact_type_of(restored) == idy.ARTIFACT_ADAPTED_ICP
    assert idy.artifact_identity_str(restored).startswith("adapted_icp:")


# 6. Existing approval fingerprints remain valid (pinned, unchanged by this sprint).
def test_approval_fingerprints_unchanged():
    assert idy.fingerprint_generated_icp(_pinned_icp()) == _PINNED_FP
    import icp_approval as ap
    assert ap.fingerprint_generated_icp is idy.fingerprint_generated_icp
    # warning ids also unchanged
    assert idy.warning_id("abc", "No target geography declared.") == "bdef0a21cf2806af"


# 7. Invalid or unknown artifact type is rejected explicitly.
def test_unknown_artifact_type_rejected():
    bad = _icp("nonsense_scope")
    raised = False
    try:
        idy.artifact_identity(bad)
    except idy.ArtifactIdentityError as e:
        raised = True
        assert "Unknown ICP scope" in str(e)
    assert raised
    # malformed / unknown-type / malformed-version identity strings (3-part format now)
    for s in ("no_colon", "general_icp:abc", "weird_icp:1:abc", "general_icp:1:",
              "general_icp::abc", "general_icp:1:2:3", "general_icp:v 1:abc", 123):
        r = False
        try:
            idy.parse_artifact_identity(s)
        except idy.ArtifactIdentityError:
            r = True
        assert r, s
    # a malformed version on a live ICP is rejected too
    bad_ver = _icp(gi.ICP_SCOPE_GENERAL, version="")
    r = False
    try:
        idy.artifact_identity(bad_ver)
    except idy.ArtifactIdentityError:
        r = True
    assert r
    # require_artifact_type rejects an unknown expected type
    r = False
    try:
        idy.require_artifact_type(_icp(gi.ICP_SCOPE_GENERAL), "made_up")
    except idy.ArtifactIdentityError:
        r = True
    assert r


# 8. General ICP lineage rejects an adapted artifact identity.
def test_general_lineage_rejects_adapted():
    ws = ip.CompanyWorkspace(company=bk.BusinessKnowledge())
    assert idy.require_artifact_type(_icp(gi.ICP_SCOPE_GENERAL), idy.ARTIFACT_GENERAL_ICP)
    raised = False
    try:
        ws.append_general_icp(_icp(gi.ICP_SCOPE_ADAPTED))
    except ValueError as e:
        raised = True
        assert "general-scoped" in str(e)
    assert raised and ws.list_general_icps() == []
    # an unknown-scope ICP is also refused by the lineage (via the identity authority)
    raised = False
    try:
        ws.append_general_icp(_icp("weird"))
    except ValueError:
        raised = True
    assert raised


# 10. No new import cycles: icp_identity depends only on generated_icp (downward).
def test_no_import_cycles():
    src = (ROOT / "pipeline" / "icp_identity.py").read_text(encoding="utf-8")
    for forbidden in ("import icp_project", "import icp_approval", "import strategy_review",
                      "import icp_adapter", "import qualification_bridge"):
        assert forbidden not in src, forbidden
    import importlib
    importlib.import_module("icp_identity")


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
