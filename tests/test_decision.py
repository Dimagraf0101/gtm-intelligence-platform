"""Unit tests for the Decision Layer (pipeline/decision.py) — Release 0.3.4 contract.

Offline, no LLM, no network, no pytest dependency:
    ./.venv/bin/python tests/test_decision.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import evidence as ev          # noqa: E402
import icp_profile as ip       # noqa: E402
import decision as dc          # noqa: E402


def _profile(dims=None, thresholds=None):
    return ip.build_profile_from_definition("T", {
        "dimensions": dims or [{"name": "D", "weight": 100}],
        "category_thresholds": thresholds or [{"label": "A+ / Hot", "min": 85, "max": 110},
                                              {"label": "Not Relevant", "min": 0, "max": 39}]})


def _conf(attr="company_industry", scope=ev.SCOPE_CURRENT, value="x"):
    return ev.EvidenceItem(attribute=attr, observed_value=value, source_field=attr,
                           status=ev.CONFIRMED, confidence=ev.HIGH, explanation="", provenance="",
                           employment_scope=scope)


def _decide_score(score, candidates=None, profile=None):
    return dc.decide(profile or _profile(), [_conf()], {"D": score}, dealbreaker_candidates=candidates)


# --- score -> priority thresholds -------------------------------------------

def test_score_to_priority_thresholds():
    expected = {95: "Priority 1", 89: "Priority 2", 75: "Priority 2", 74: "Priority 3",
                60: "Priority 3", 59: "Priority 4", 45: "Priority 4", 44: "Priority 5",
                30: "Priority 5", 29: "Disqualified"}
    for score, priority in expected.items():
        r = _decide_score(score)
        assert r.operational_priority == priority, (score, r.operational_priority)
        assert r.operational_lead_score == score


def test_helper_operational_priority_pure():
    assert dc.operational_priority(90) == "Priority 1" and dc.operational_priority(29) == "Disqualified"


# --- confirmed exclusion override -------------------------------------------

def test_confirmed_exclusion_zeroes_score_and_disqualifies():
    cand = [{"name": "excluded subsegment", "proposed_state": "confirmed",
             "evidence_attribute": "company_industry"}]
    r = _decide_score(95, candidates=cand)
    assert r.dealbreaker_state == "confirmed"
    assert r.operational_lead_score == 0 and r.operational_priority == "Disqualified"
    assert r.raw_icp_score == 95                                 # raw preserved for audit
    assert r.confirmed_dealbreakers == ["excluded subsegment"]


def test_suspected_dealbreaker_keeps_priority():
    # proposed confirmed but no confirmed evidence for the cited attribute -> stays suspected
    cand = [{"name": "maybe", "proposed_state": "confirmed", "evidence_attribute": "company_description"}]
    r = _decide_score(95, candidates=cand)
    assert r.dealbreaker_state == "suspected"
    assert r.operational_priority == "Priority 1" and r.operational_lead_score == 95


# --- missing / low coverage never change priority ---------------------------

def test_missing_enrichment_keeps_priority_from_score():
    prof = _profile(dims=[{"name": "A", "weight": 90}, {"name": "funding", "weight": 10}])
    r = dc.decide(prof, [_conf()], {"A": 90})                    # 'funding' unknown
    assert r.operational_priority == "Priority 1" and r.operational_lead_score == 90
    assert "funding" in r.unknown_fields and r.evidence_coverage == 90


def test_low_coverage_reduces_confidence_not_priority():
    prof = _profile(dims=[{"name": "A", "weight": 70}, {"name": "B", "weight": 30}])
    low = dc.decide(prof, [_conf()], {"A": 63})                  # B unknown -> coverage 70
    full = dc.decide(prof, [_conf()], {"A": 45, "B": 18})        # coverage 100, same raw 63
    assert low.operational_priority == "Priority 3" == full.operational_priority
    assert low.decision_confidence < full.decision_confidence


# --- conflicting / previous / robustness ------------------------------------

def test_conflicting_evidence_lowers_confidence_not_disqualify():
    conflict = [_conf(), ev.EvidenceItem(attribute="company_size", observed_value="4 vs 51-200",
                                         source_field="size", status=ev.CONFLICTING, confidence=ev.HIGH,
                                         explanation="", provenance="", employment_scope=ev.SCOPE_CURRENT)]
    clean = dc.decide(_profile(), [_conf()], {"D": 80})
    conf = dc.decide(_profile(), conflict, {"D": 80})
    assert conf.decision_confidence < clean.decision_confidence
    assert conf.operational_priority != "Disqualified"


def test_previous_employment_never_confirms_exclusion():
    prev = _conf(attr="previous_role", scope=ev.SCOPE_PREVIOUS, value="COO @ Bank")
    cand = [{"name": "wrong company", "proposed_state": "confirmed", "evidence_attribute": "previous_role"}]
    r = dc.decide(_profile(), [_conf(), prev], {"D": 95}, dealbreaker_candidates=cand)
    assert r.dealbreaker_state == "suspected" and r.operational_priority == "Priority 1"


def test_malformed_scores_clamped():
    prof = _profile(dims=[{"name": "A", "weight": 50}, {"name": "B", "weight": 50}])
    r = dc.decide(prof, [_conf()], {"A": "abc", "B": 999})       # A unknown, B clamped to 50
    assert r.raw_icp_score == 50
    assert any("non-numeric" in w for w in r.validation_warnings)


def test_no_usable_evidence_unknown_fit():
    r = dc.decide(_profile(), [], {})
    assert r.evidence_adjusted_fit == dc.FIT_UNKNOWN and r.evidence_coverage == 0


def test_confidence_is_deterministic():
    a, b = _decide_score(80), _decide_score(80)
    assert a.to_dict() == b.to_dict()


def test_no_builtin_commercial_exclusions():
    r = _decide_score(95)                                        # no candidates -> nothing excluded
    assert r.dealbreaker_state == "none" and r.operational_priority == "Priority 1"
    src = (ROOT / "pipeline" / "decision.py").read_text().lower()
    for term in ("crypto", "agency", "recruit", "consultanc"):
        assert term not in src


def test_internal_category_preserved_for_audit():
    r = _decide_score(95)                                        # raw 95 -> ICP band "A+ / Hot"
    assert r.internal_category == "A+ / Hot"


# --- runner -----------------------------------------------------------------

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
