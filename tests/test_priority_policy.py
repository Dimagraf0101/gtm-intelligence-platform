"""Decision-engine single-source-of-truth tests (Sprint 12.0.2).

Proves the cleanup is behavior-preserving: the canonical operational priority policy
(`priority_policy`) is the one source the decision engine and the ICP-generation defaults derive from;
`SCORE_THRESHOLD` is gone and cannot influence qualification; boundaries and dealbreaker rules are
unchanged; and the audit `internal_category` (ICP `category_thresholds`) stays independent of the
operational decision. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_priority_policy.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import evidence as ev          # noqa: E402
import icp_profile as ip       # noqa: E402
import decision as dc          # noqa: E402
import generated_icp as gi     # noqa: E402
import priority_policy as pp    # noqa: E402
import config                   # noqa: E402


def _profile(dims=None, thresholds=None):
    return ip.build_profile_from_definition("T", {
        "dimensions": dims or [{"name": "D", "weight": 100}],
        "category_thresholds": thresholds or [{"label": "A+ / Hot", "min": 85, "max": 110},
                                              {"label": "Not Relevant", "min": 0, "max": 39}]})


def _conf(attr="company_industry", scope=ev.SCOPE_CURRENT, value="x"):
    return ev.EvidenceItem(attribute=attr, observed_value=value, source_field=attr,
                           status=ev.CONFIRMED, confidence=ev.HIGH, explanation="", provenance="",
                           employment_scope=scope)


def _decide(score, candidates=None, profile=None):
    return dc.decide(profile or _profile(), [_conf()], {"D": score}, dealbreaker_candidates=candidates)


_BOUNDARIES = {29: "Disqualified", 30: "Priority 5", 44: "Priority 5", 45: "Priority 4",
               59: "Priority 4", 60: "Priority 3", 74: "Priority 3", 75: "Priority 2",
               89: "Priority 2", 90: "Priority 1"}


# 1. SCORE_THRESHOLD is not imported or consumed by runtime code.
def test_score_threshold_not_consumed_by_runtime():
    # config no longer exposes it as an attribute (it is not read from the environment anymore)
    assert not hasattr(config, "SCORE_THRESHOLD")
    # no pipeline source performs an env lookup for it (a quoted literal == an env key / attr access);
    # the only allowed mention is the explanatory prose comment in config.py (unquoted).
    for path in (ROOT / "pipeline").glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert '"SCORE_THRESHOLD"' not in src and "'SCORE_THRESHOLD'" not in src, \
            f"{path.name} still references the SCORE_THRESHOLD env key"


# 2. An old environment variable named SCORE_THRESHOLD does not change qualification behavior.
def test_stray_env_var_does_not_change_qualification():
    saved = os.environ.get("SCORE_THRESHOLD")
    os.environ["SCORE_THRESHOLD"] = "5"          # a value that WOULD matter if it were read
    try:
        for score, expected in _BOUNDARIES.items():
            assert dc.operational_priority(score) == expected
            assert _decide(score).operational_priority == expected
    finally:
        if saved is None:
            os.environ.pop("SCORE_THRESHOLD", None)
        else:
            os.environ["SCORE_THRESHOLD"] = saved


# 3. Boundary behavior is unchanged (exact edges).
def test_boundaries_unchanged():
    for score, expected in _BOUNDARIES.items():
        assert dc.operational_priority(score) == expected, (score, expected)
        r = _decide(score)
        assert r.operational_priority == expected and r.operational_lead_score == score


# 4. A confirmed dealbreaker overrides a high score.
def test_confirmed_dealbreaker_overrides_high_score():
    cand = [{"name": "excluded", "proposed_state": "confirmed", "evidence_attribute": "company_industry"}]
    r = _decide(95, candidates=cand)
    assert r.dealbreaker_state == "confirmed"
    assert r.operational_priority == "Disqualified" and r.operational_lead_score == 0
    assert r.raw_icp_score == 95                 # raw preserved for audit


# 5. A suspected dealbreaker does not automatically disqualify.
def test_suspected_dealbreaker_does_not_disqualify():
    # proposed confirmed but cited attribute has no confirmed current evidence -> stays suspected
    cand = [{"name": "maybe", "proposed_state": "confirmed", "evidence_attribute": "company_description"}]
    r = _decide(95, candidates=cand)
    assert r.dealbreaker_state == "suspected" and r.operational_priority == "Priority 1"


# 6. standard_priority_bands() is derived from the canonical policy.
def test_standard_bands_derived_from_canonical_policy():
    rows = [(b.label, b.min_score, b.max_score) for b in gi.standard_priority_bands()]
    assert rows == list(pp.default_priority_band_rows())
    # and the operational (min, label) view the decision engine uses is the same canonical tuple
    assert dc._OPERATIONAL_BANDS is pp.OPERATIONAL_PRIORITY_BANDS
    # every non-terminal canonical band appears with matching floor in the derived table
    for minimum, label in pp.OPERATIONAL_PRIORITY_BANDS:
        assert (label, minimum) in [(r[0], r[1]) for r in rows]


# 7. Returned ICP band structures cannot accidentally mutate the canonical policy.
def test_returned_bands_cannot_mutate_policy():
    bands = gi.standard_priority_bands()
    bands[0].min_score = -999
    bands.append("junk")
    # a fresh call is unaffected, and the canonical policy is intact
    assert gi.standard_priority_bands()[0].min_score == 90
    assert pp.OPERATIONAL_PRIORITY_BANDS == ((90, "Priority 1"), (75, "Priority 2"), (60, "Priority 3"),
                                             (45, "Priority 4"), (30, "Priority 5"))
    # the canonical tuple is genuinely immutable
    try:
        pp.OPERATIONAL_PRIORITY_BANDS[0] = (1, "x")
        assert False, "policy tuple was mutable"
    except TypeError:
        pass


# 8. Custom ICP category_thresholds still drive internal_category (audit).
def test_custom_category_thresholds_still_produce_internal_category():
    prof = _profile(thresholds=[{"label": "Tier A", "min": 50, "max": 100},
                                {"label": "Tier B", "min": 0, "max": 49}])
    assert _decide(80, profile=prof).internal_category == "Tier A"
    assert _decide(20, profile=prof).internal_category == "Tier B"


# 9. Custom internal_category thresholds do NOT affect operational_priority.
def test_custom_thresholds_do_not_affect_operational_priority():
    # thresholds that disagree wildly with the operational bands (e.g. would call 20 "Tier A")
    prof = _profile(thresholds=[{"label": "Tier A", "min": 10, "max": 100},
                                {"label": "Tier Z", "min": 0, "max": 9}])
    r20 = _decide(20, profile=prof)
    assert r20.internal_category == "Tier A"            # audit label follows the custom rubric
    assert r20.operational_priority == "Disqualified"   # but the operational verdict is policy-driven
    r80 = _decide(80, profile=prof)
    assert r80.internal_category == "Tier A" and r80.operational_priority == "Priority 2"


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
