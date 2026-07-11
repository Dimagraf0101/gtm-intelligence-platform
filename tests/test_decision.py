"""Unit tests for the Decision Layer (pipeline/decision.py).

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


# --- shared fixtures (a FinTech-like profile, built deterministically) -------

def _profile():
    return ip.build_profile_from_definition("FinTechTest", {
        "dimensions": [
            {"name": "Subsegment fit", "weight": 25},
            {"name": "Stage & funding fit", "weight": 20},
            {"name": "Buyer persona", "weight": 15},
            {"name": "Company size", "weight": 10},
            {"name": "Eng hiring signal", "weight": 10},
            {"name": "Geography", "weight": 10},
            {"name": "Reachability", "weight": 10},
        ],
        "category_thresholds": [
            {"label": "A+ / Hot", "min": 85, "max": 110},
            {"label": "A / High", "min": 70, "max": 84},
            {"label": "B / Normal", "min": 55, "max": 69},
            {"label": "C / Low", "min": 40, "max": 54},
            {"label": "Not Relevant", "min": 0, "max": 39},
        ],
    })


def _confirmed(attr, scope=ev.SCOPE_CURRENT, conf=ev.HIGH, value="x"):
    return ev.EvidenceItem(attribute=attr, observed_value=value, source_field=attr,
                           status=ev.CONFIRMED, confidence=conf, explanation="", provenance="",
                           employment_scope=scope)

def _all_dims_high():
    return {"Subsegment fit": 24, "Stage & funding fit": 18, "Buyer persona": 15,
            "Company size": 9, "Eng hiring signal": 8, "Geography": 10, "Reachability": 8}  # sum 92

def _covered_dims_only_high():
    # only the 4 data-earnable dimensions are usable; the 3 enrichment dims are unknown
    return {"Subsegment fit": 24, "Buyer persona": 14, "Company size": 9, "Geography": 10}  # 57/60


# --- tests ------------------------------------------------------------------

def test_clean_high_fit_lead_is_top_tier():
    r = dc.decide(_profile(), [_confirmed("company_industry")], _all_dims_high())
    assert r.raw_icp_score == 92
    assert r.evidence_coverage == 100
    assert r.provisional_priority == "A+ / Hot"
    assert r.dealbreaker_state == dc.STATE_NONE
    assert r.evidence_adjusted_fit == 92


def test_a_plus_blocked_by_low_coverage():
    r = dc.decide(_profile(), [_confirmed("company_industry")], _covered_dims_only_high())
    assert r.evidence_coverage == 60                 # 60/100 weight usable
    assert isinstance(r.evidence_adjusted_fit, int) and r.evidence_adjusted_fit >= 85
    assert r.provisional_priority == dc.A_PLUS_ENRICHMENT
    assert set(r.unknown_fields) == {"Stage & funding fit", "Eng hiring signal", "Reachability"}


def test_confirmed_dealbreaker_disqualifies():
    ev_items = [_confirmed("company_industry", value="crypto exchange")]
    cand = [{"label": "excluded subsegment: crypto", "evidence_attribute": "company_industry",
             "proposed_state": "confirmed"}]
    r = dc.decide(_profile(), ev_items, _all_dims_high(), dealbreaker_candidates=cand)
    assert r.dealbreaker_state == dc.STATE_CONFIRMED
    assert r.confirmed_dealbreakers == ["excluded subsegment: crypto"]
    assert r.provisional_priority == dc.DISQUALIFIED


def test_suspected_dealbreaker_does_not_override_priority():
    # model proposes 'confirmed' but there is no confirmed evidence -> stays suspected
    cand = [{"label": "maybe agency", "evidence_attribute": "company_description",
             "proposed_state": "confirmed"}]
    r = dc.decide(_profile(), [_confirmed("company_industry")], _all_dims_high(),
                  dealbreaker_candidates=cand)
    assert r.dealbreaker_state == dc.STATE_SUSPECTED
    assert r.provisional_priority == "A+ / Hot"        # numeric priority NOT overridden
    assert r.confirmed_dealbreakers == []


def test_suspected_dealbreaker_remains_eligible_for_human_review():
    cand = [{"label": "maybe out of scope", "proposed_state": "suspected"}]
    r = dc.decide(_profile(), [_confirmed("company_industry")], _all_dims_high(),
                  dealbreaker_candidates=cand)
    assert r.dealbreaker_state == dc.STATE_SUSPECTED
    assert r.provisional_priority != dc.DISQUALIFIED
    assert r.review_recommendation == dc.REVIEW_PRIORITY


def test_missing_data_is_unknown_never_negative():
    scores = {"Subsegment fit": 24, "Buyer persona": 14, "Company size": 9, "Geography": 10}
    r = dc.decide(_profile(), [_confirmed("company_industry")], scores)
    # unknown dims contribute 0 (floor), never reduce the score below the sum of known points
    assert r.raw_icp_score == 57
    assert "Stage & funding fit" in r.unknown_fields
    # coverage reduced (not the raw score turned negative)
    assert r.evidence_coverage == 60


def test_conflicting_company_size_lowers_confidence_not_disqualify():
    clean = dc.decide(_profile(), [_confirmed("company_industry")], _all_dims_high())
    conflict_ev = [_confirmed("company_industry"),
                   ev.EvidenceItem(attribute="company_size", observed_value="4 vs 51-200",
                                   source_field="size", status=ev.CONFLICTING, confidence=ev.HIGH,
                                   explanation="", provenance="", employment_scope=ev.SCOPE_CURRENT)]
    conf = dc.decide(_profile(), conflict_ev, _all_dims_high())
    assert conf.decision_confidence < clean.decision_confidence   # confidence drops
    assert conf.provisional_priority != dc.DISQUALIFIED           # not auto-disqualified


def test_previous_employment_never_confirms_current_exclusion():
    # a previous fintech role cited for a current-company exclusion must NOT confirm it
    ev_items = [_confirmed("previous_role", scope=ev.SCOPE_PREVIOUS, value="COO @ PayCargo"),
                _confirmed("company_industry", value="Transportation")]
    cand = [{"label": "wrong company type", "evidence_attribute": "previous_role",
             "proposed_state": "confirmed"}]
    r = dc.decide(_profile(), ev_items, _all_dims_high(), dealbreaker_candidates=cand)
    assert r.dealbreaker_state == dc.STATE_SUSPECTED
    assert r.provisional_priority != dc.DISQUALIFIED


def test_icp_specific_exclusion_isolation():
    # with NO dealbreaker candidates passed, nothing is excluded regardless of company —
    # the Decision Layer holds no built-in commercial rules.
    ev_items = [_confirmed("company_industry", value="crypto exchange")]
    r = dc.decide(_profile(), ev_items, _all_dims_high(), dealbreaker_candidates=None)
    assert r.dealbreaker_state == dc.STATE_NONE
    assert r.provisional_priority == "A+ / Hot"
    # and the module hardcodes no commercial exclusion terms
    src = (ROOT / "pipeline" / "decision.py").read_text().lower()
    for term in ("crypto", "agency", "recruit", "bank", "consultanc"):
        assert term not in src, f"commercial term '{term}' must not be hardcoded"


def test_malformed_dimension_scores_are_clamped_with_warnings():
    scores = {"Subsegment fit": "abc", "Buyer persona": -5, "Company size": 999,
              "Geography": 10, "NotADimension": 50}
    r = dc.decide(_profile(), [_confirmed("company_industry")], scores)
    # 'abc' -> unknown; -5 -> 0; 999 -> clamped to 10 (max); Geography 10; extra ignored
    assert r.raw_icp_score == 0 + 10 + 10               # Buyer(0) + Company size(10) + Geography(10)
    assert any("non-numeric" in w for w in r.validation_warnings)
    assert any("below 0" in w for w in r.validation_warnings)
    assert any("above max" in w for w in r.validation_warnings)
    assert any("unknown dimension 'NotADimension'" in w for w in r.validation_warnings)


def test_no_usable_evidence_returns_unknown_fit():
    r = dc.decide(_profile(), [], {})                    # no dimension scores at all
    assert r.evidence_adjusted_fit == dc.FIT_UNKNOWN
    assert r.evidence_coverage == 0
    assert len(r.unknown_fields) == 7


def test_confidence_is_deterministic():
    p = _profile()
    a = dc.decide(p, [_confirmed("company_industry")], _all_dims_high())
    b = dc.decide(p, [_confirmed("company_industry")], _all_dims_high())
    assert a.decision_confidence == b.decision_confidence
    assert a.to_dict() == b.to_dict()


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
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
