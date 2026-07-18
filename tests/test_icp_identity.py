"""Tests for the low-level ICP identity module (pipeline/icp_identity.py) — Sprint 5.7B.

Proves the identity functions moved out of icp_approval are byte-for-byte backward compatible, the
strategy<->approval cycle is gone, and identity is deterministic. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_icp_identity.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi             # noqa: E402
import icp_identity as idy             # noqa: E402
import icp_approval as ap              # noqa: E402
import icp_adapter as ad               # noqa: E402


def _icp(status=gi.STATUS_APPROVED, version="3"):
    icp = gi.GeneratedICP(metadata=gi.Metadata(name="FinTech", version=version, status=status))
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


# Pinned expected values (the original Sprint 5.5 algorithm output for _icp()). If the identity
# algorithm ever changes, these fail — which is the intended backstop for the STOP condition.
_PINNED_FP = "08dba234fca68eae390445aaab4bfa244d71c4f2d70e05068d3f7cb2850ba9c3"
_PINNED_CONTENT = "496bff39b672ead4bccbc3214688d9d14e012d907a391a4b671615586db8423d"


# 1. Fingerprint compatibility — the algorithm is unchanged (pinned) and equals the re-export.
def test_fingerprints_are_backward_compatible():
    icp = _icp()
    assert idy.fingerprint_generated_icp(icp) == _PINNED_FP
    assert idy.content_fingerprint(icp) == _PINNED_CONTENT
    # icp_approval re-exports the identity API; both resolve to the same function/value
    assert ap.fingerprint_generated_icp(icp) == idy.fingerprint_generated_icp(icp)
    assert ap.content_fingerprint(icp) == idy.content_fingerprint(icp)
    assert ap.fingerprint_generated_icp is idy.fingerprint_generated_icp


def test_warning_id_backward_compatible():
    wid = idy.warning_id("abc", "No target geography declared.")
    assert wid == "bdef0a21cf2806af"
    assert ap.warning_id("abc", "No target geography declared.") == wid


# Determinism: same input -> same output; version/status distinguish; content fp ignores them.
def test_identity_determinism_and_scope():
    import copy
    icp = _icp()
    assert idy.fingerprint_generated_icp(icp) == idy.fingerprint_generated_icp(copy.deepcopy(icp))
    draft = _icp(status=gi.STATUS_DRAFT, version="1")
    approved = _icp(status=gi.STATUS_APPROVED, version="1")
    assert idy.fingerprint_generated_icp(draft) != idy.fingerprint_generated_icp(approved)  # status
    assert idy.content_fingerprint(draft) == idy.content_fingerprint(approved)               # ignored
    v1, v2 = _icp(version="1"), _icp(version="2")
    assert idy.fingerprint_generated_icp(v1) != idy.fingerprint_generated_icp(v2)             # version
    assert idy.content_fingerprint(v1) == idy.content_fingerprint(v2)                         # ignored


# The dependency cycle is gone: strategy_review must not import icp_approval.
def test_no_strategy_to_approval_cycle():
    src = (ROOT / "pipeline" / "strategy_review.py").read_text(encoding="utf-8")
    assert "import icp_approval" not in src
    assert "icp_identity" in src                     # depends downward on identity instead
    # both approval and strategy depend on the same identity module
    ap_src = (ROOT / "pipeline" / "icp_approval.py").read_text(encoding="utf-8")
    assert "icp_identity" in ap_src


# 2. Adapter determinism — same GeneratedICP -> same ICPProfile output.
def test_adapter_is_deterministic():
    import copy
    icp = _icp()
    a = ad.to_engine_profile(icp)
    b = ad.to_engine_profile(copy.deepcopy(icp))
    assert a.to_dict() == b.to_dict()
    # and it does not mutate the input ICP
    import json
    before = json.dumps(icp.to_dict())
    ad.to_engine_profile(icp)
    assert json.dumps(icp.to_dict()) == before


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
