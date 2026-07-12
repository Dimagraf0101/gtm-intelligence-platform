"""Offline integration tests for the wired qualification flow (pipeline/scoring.py).

No network, no Anthropic API, no pytest dependency:
    ./.venv/bin/python tests/test_scoring_integration.py

Real model calls are replaced by MockClient and small scripted fake clients.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import scoring as sc          # noqa: E402
import evidence as ev         # noqa: E402
import icp_profile as ip      # noqa: E402
import export                 # noqa: E402  (compatibility check)


# --- ICP texts --------------------------------------------------------------

ICP_PLAIN = "ICP: target B2B SaaS companies. Primary buyer: CTO. (No explicit rubric here.)"

# A minimal ICP whose deterministic parse yields a rubric with an enrichment dimension.
ICP_ENRICH = """Weighted model (out of 100)
Dimension
Wt
Subsegment fit
60
Stage & funding fit
40
Score → category
A+ / Hot
85-110
A / High
70-84
B / Normal
55-69
C / Low
40-54
Not Relevant
0-39
"""

# default-framework dimension names (used when no rubric is parsed)
DEFAULT_DIMS = ["title", "industry", "company_size", "location", "signals"]
# scores that sum to 100 (raw) -> operational Priority 1
MAX_SCORES = {"title": 40, "industry": 25, "company_size": 15, "location": 10, "signals": 10}


# --- helpers ----------------------------------------------------------------

def _raw(**kw):
    return dict(kw)

def _lead(idx=0, **cols):
    base = {"first name": "Test", "last name": f"Lead{idx}",
            "linkedin url": f"https://www.linkedin.com/in/test{idx}"}
    base.update(cols)
    return sc.normalize_lead(base, idx)


class FakeClient:
    """Scripted client. `handler(leads, calls) -> response_str`."""
    model = "fake"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def complete(self, system, user):
        leads = sc._json_blocks(user)[-1]
        self.calls.append([l["lead_index"] for l in leads])
        return self.handler(leads, self.calls)


def _scripted(proposal_for):
    def handler(leads, calls):
        return json.dumps([proposal_for(l) for l in leads])
    return FakeClient(handler)


# --- tests ------------------------------------------------------------------

def test_complete_high_fit_lead_mock():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software",
                       "linkedin employees": "51-200", "location": "San Francisco, CA"})
    out = sc.score_leads([lead], ICP_PLAIN, "Plain", client=sc.MockClient())
    r = out[0]
    assert r.is_mock is True and r.confidence == "low"          # mock never looks like production
    assert r.score == 60 and r.category == "Priority 3"          # operational priority from score 60
    assert r.raw_icp_score == 60 and r.operational_lead_score == 60
    assert r.category == r.provisional_priority


def test_missing_funding_hiring_remain_unknown():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    # model scores only Subsegment fit; Stage & funding fit is unearnable -> omitted
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": {"Subsegment fit": 55},
                                  "unknown_fields": ["Stage & funding fit"],
                                  "model_confidence": "medium"})
    r = sc.score_leads([lead], ICP_ENRICH, "Enrich", client=client)[0]
    assert "Stage & funding fit" in r.unknowns
    assert r.evidence_coverage == 60                             # 60/100 weight usable
    assert r.raw_icp_score == 55                                 # missing dim not penalized


def test_suspected_dealbreaker_does_not_disqualify():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    # proposed 'confirmed' but cites an attribute with NO evidence -> downgraded to suspected
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": MAX_SCORES,   # raw 100 -> Priority 1
                                  "dealbreaker_candidates": [
                                      {"name": "maybe agency", "proposed_state": "confirmed",
                                       "evidence_attribute": "company_description"}]})
    r = sc.score_leads([lead], ICP_PLAIN, "Plain", client=client)[0]
    assert r.dealbreaker_state == "suspected"
    assert r.hard_dealbreaker is False
    assert r.category == "Priority 1"                            # suspected never lowers priority


def test_confirmed_dealbreaker_with_current_evidence_disqualifies():
    lead = _lead(0, **{"job title": "CTO", "company": "CryptoCo",
                       "linkedin industry": "Crypto Exchange"})
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": {d: 5 for d in DEFAULT_DIMS},
                                  "dealbreaker_candidates": [
                                      {"name": "excluded subsegment: crypto",
                                       "proposed_state": "confirmed",
                                       "evidence_attribute": "company_industry"}]})
    r = sc.score_leads([lead], ICP_PLAIN, "Plain", client=client)[0]
    assert r.dealbreaker_state == "confirmed"
    assert r.hard_dealbreaker is True
    assert r.category == "Disqualified" and r.operational_lead_score == 0
    assert r.raw_icp_score is not None                          # raw score preserved for audit


def test_previous_employment_cannot_confirm_current_exclusion():
    lead = _lead(0, **{"job title": "Advisor", "company": "FreightAssoc",
                       "linkedin industry": "Transportation",
                       "job title (2)": "COO", "company (2)": "PayCargo",
                       "linkedin industry (2)": "Financial Services"})
    assert lead.previous_roles and lead.previous_roles[0]["company"] == "PayCargo"
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": MAX_SCORES,   # raw 100 -> Priority 1
                                  "dealbreaker_candidates": [
                                      {"name": "wrong company type", "proposed_state": "confirmed",
                                       "evidence_attribute": "previous_role"}]})
    r = sc.score_leads([lead], ICP_PLAIN, "Plain", client=client)[0]
    assert r.dealbreaker_state == "suspected"
    assert r.category == "Priority 1"                            # previous-role dealbreaker cannot confirm


def test_conflicting_employee_data_lowers_confidence_not_reject():
    common = {"job title": "CTO", "company": "Acme", "linkedin industry": "Software",
              "location": "San Francisco, CA"}
    clean_lead = _lead(0, **{**common, "linkedin employees": "51-200",
                             "linkedin company employee count": "108"})
    conflict_lead = _lead(0, **{**common, "linkedin employees": "51-200",
                                "linkedin company employee count": "4"})
    prop = lambda l: {"lead_index": l["lead_index"], "dimension_scores": {d: 8 for d in DEFAULT_DIMS}}
    clean = sc.score_leads([clean_lead], ICP_PLAIN, "P", client=_scripted(prop))[0]
    conflict = sc.score_leads([conflict_lead], ICP_PLAIN, "P", client=_scripted(prop))[0]
    assert conflict.decision_confidence < clean.decision_confidence
    assert conflict.category != "Disqualified"


def test_final_score_is_python_calculated():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    # model returns exact per-dimension points; Python sums them (40+25+15+10+10 = 100)
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": {"title": 40, "industry": 25,
                                                       "company_size": 15, "location": 10,
                                                       "signals": 10}})
    r = sc.score_leads([lead], ICP_PLAIN, "P", client=client)[0]
    assert r.raw_icp_score == 100 and r.score == 100


def test_final_priority_comes_from_decision_not_model():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    # model tries to inject a bogus category; it must be ignored
    client = _scripted(lambda l: {"lead_index": l["lead_index"], "category": "ZZZ-FAKE",
                                  "dimension_scores": {d: 8 for d in DEFAULT_DIMS}})
    r = sc.score_leads([lead], ICP_PLAIN, "P", client=client)[0]
    assert r.category != "ZZZ-FAKE"
    assert r.category == r.provisional_priority


def test_model_confidence_is_not_final_confidence():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    # model claims high confidence, but only a tiny dimension scored -> low coverage -> low confidence
    client = _scripted(lambda l: {"lead_index": l["lead_index"], "model_confidence": "high",
                                  "dimension_scores": {"signals": 5}})   # weight 10 -> 10% coverage
    r = sc.score_leads([lead], ICP_PLAIN, "P", client=client)[0]
    assert r.model_confidence == "high"
    assert r.confidence == "low"                                # Python-derived, independent


def test_mockclient_conforms_to_new_schema():
    dims = [{"name": "Subsegment fit", "max": 60}, {"name": "Stage & funding fit", "max": 40}]
    leads = [{"lead_index": 0, "current": {"job_title": "CTO"}, "previous_roles": []}]
    # dimensions now live in the (cacheable) system context; leads in the user message
    system = sc.build_system_blocks("system prompt", f"## dims\n```json\n{json.dumps(dims)}\n```")
    user = f"## leads\n```json\n{json.dumps(leads)}\n```"
    out = json.loads(sc.MockClient().complete(system, user))
    obj = out[0]
    for key in ("lead_index", "dimension_scores", "evidence_by_dimension",
                "dealbreaker_candidates", "qualification_reason", "unknown_fields", "model_confidence"):
        assert key in obj, f"missing {key}"
    assert "Stage & funding fit" not in obj["dimension_scores"]   # enrichment dim left unknown
    assert "Stage & funding fit" in obj["unknown_fields"]
    assert obj["model_confidence"] == "low"


def test_existing_score_leads_caller_and_export_compat():
    leads = [_lead(i, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software",
                         "linkedin employees": "51-200", "location": "San Francisco, CA"})
             for i in range(3)]
    results = sc.score_leads(leads, ICP_PLAIN, "Plain", client=sc.MockClient())
    assert len(results) == 3
    by_idx = {l.index: l for l in leads}
    pairs = [(by_idx[r.lead_index], r) for r in results]
    df = export.build_dataframe(pairs)                            # export.py must still work
    assert list(df.columns) == export.COLUMNS
    assert len(df) == 3


def test_malformed_output_retries_only_failed_batch():
    leads = [_lead(0, **{"job title": "CTO", "company": "A", "linkedin industry": "Software"}),
             _lead(1, **{"job title": "CTO", "company": "B", "linkedin industry": "Software"})]
    attempts = {0: 0, 1: 0}

    def handler(batch_leads, calls):
        idx = batch_leads[0]["lead_index"]
        attempts[idx] += 1
        if idx == 0 and attempts[0] == 1:
            return "not json at all — malformed"          # fail batch 0 once
        return json.dumps([{"lead_index": idx, "dimension_scores": {d: 5 for d in DEFAULT_DIMS}}])

    client = FakeClient(handler)
    results = sc.score_leads(leads, ICP_PLAIN, "P", client=client, batch_size=1)
    assert attempts[0] == 2                                  # batch 0 retried once
    assert attempts[1] == 1                                  # batch 1 never repeated
    assert all(r.error is None for r in results)             # both recovered


# --- Sprint 3.4.2: enrichment guard + caching -------------------------------

def _icp_with(second_dim):
    return (f"Weighted model (out of 100)\nDimension\nWt\nSubsegment fit\n60\n{second_dim}\n40\n"
            f"Score → category\nA+ / Hot\n85-110\nA / High\n70-84\nB / Normal\n55-69\n"
            f"C / Low\n40-54\nNot Relevant\n0-39\n")

ICP_FUNDING = _icp_with("Stage & funding fit")
ICP_HIRING = _icp_with("Eng hiring signal")
ICP_REACH = _icp_with("Reachability")


def _score_two_dims(second_dim_name, icp_text):
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": {"Subsegment fit": 55, second_dim_name: 38}})
    return sc.score_leads([lead], icp_text, "E", client=client)[0]


def test_funding_score_removed_without_evidence():
    r = _score_two_dims("Stage & funding fit", ICP_FUNDING)
    assert "Stage & funding fit" in r.unknowns
    assert r.raw_icp_score == 55                          # funding points not counted
    assert any("enrichment guard" in w for w in r.validation_warnings)


def test_hiring_score_removed_without_evidence():
    r = _score_two_dims("Eng hiring signal", ICP_HIRING)
    assert "Eng hiring signal" in r.unknowns
    assert r.raw_icp_score == 55


def test_reachability_score_removed_without_evidence():
    r = _score_two_dims("Reachability", ICP_REACH)
    assert "Reachability" in r.unknowns
    assert r.raw_icp_score == 55


def test_direct_confirmed_evidence_allows_enrichment_score():
    prof = ip.build_profile_from_definition("E", {
        "dimensions": [{"name": "Subsegment fit", "weight": 60},
                       {"name": "Stage & funding fit", "weight": 40}],
        "category_thresholds": [{"label": "A+ / Hot", "min": 85, "max": 110}]})
    ev_items = [ev.EvidenceItem(attribute="funding_stage", observed_value="Series B",
                                source_field="enrichment", status=ev.CONFIRMED, confidence=ev.HIGH,
                                explanation="", provenance="", employment_scope=ev.SCOPE_NONE)]
    guarded, removed, warns = sc.apply_enrichment_guard(
        prof, ev_items, {"Subsegment fit": 55, "Stage & funding fit": 38})
    assert "Stage & funding fit" in guarded and removed == []   # kept: direct evidence present


def test_removed_dims_reduce_coverage_and_adjust_fit():
    r = _score_two_dims("Stage & funding fit", ICP_FUNDING)
    assert r.evidence_coverage == 60                     # only Subsegment fit (60/100) usable
    assert r.evidence_adjusted_fit == round(55 / 60 * 100)   # fit uses only usable dims (=92)


def test_no_enrichment_priority_category():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": {"Subsegment fit": 58, "Stage & funding fit": 39}})
    r = sc.score_leads([lead], ICP_FUNDING, "E", client=client)[0]
    # funding guarded -> raw 58 -> operational Priority 4; the enrichment category is gone
    assert r.evidence_coverage == 60 and r.raw_icp_score == 58
    assert r.category == "Priority 4" and "Enrichment" not in r.category


def test_absence_based_dealbreaker_does_not_disqualify():
    lead = _lead(0, **{"job title": "CTO", "company": "Acme", "linkedin industry": "Software"})
    client = _scripted(lambda l: {"lead_index": l["lead_index"],
                                  "dimension_scores": {"Subsegment fit": 40},
                                  "dealbreaker_candidates": [
                                      {"name": "No hiring signal", "proposed_state": "confirmed",
                                       "evidence_attribute": "eng_hiring_signal"}]})
    r = sc.score_leads([lead], ICP_FUNDING, "E", client=client)[0]
    assert r.dealbreaker_state == "suspected"
    assert r.category != "Disqualified"


def test_caching_unavailable_fallback():
    # simulate a model/SDK that rejects cache_control -> AnthropicClient must retry uncached
    class _Messages:
        def __init__(self): self.calls = []
        def create(self, model, max_tokens, system, messages):
            self.calls.append(system)
            if isinstance(system, list) and any("cache_control" in b for b in system):
                raise Exception("cache_control is not supported for this model")
            Block = type("B", (), {"type": "text", "text": "[]"})
            return type("M", (), {"content": [Block()]})()

    class _Client:
        def __init__(self): self.messages = _Messages()

    c = sc.AnthropicClient.__new__(sc.AnthropicClient)      # bypass real __init__ / API
    c.model, c._client = "fake", _Client()
    system = sc.build_system_blocks("sys", "icp context")   # includes cache_control
    out = c.complete(system, "user")
    assert out == "[]"                                       # succeeded via fallback
    assert len(c._client.messages.calls) == 2               # cached attempt + uncached retry
    assert not any("cache_control" in b for b in c._client.messages.calls[1])  # 2nd call stripped


# --- Sprint 3.6: deterministic pre-qualification integration ----------------

ICP_SIZE = ("Weighted model (out of 100)\nDimension\nWt\nSubsegment fit\n100\n"
            "Score → category\nA+ / Hot\n85-110\nA / High\n70-84\nB / Normal\n55-69\n"
            "C / Low\n40-54\nNot Relevant\n0-39\nTarget company size: 50-500 employees.\n")
# same, but with an EXPLICIT hard size exclusion in the dealbreakers section
ICP_SIZE_HARD = ("Weighted model (out of 100)\nDimension\nWt\nSubsegment fit\n100\n"
                 "Score → category\nA+ / Hot\n85-110\nA / High\n70-84\nB / Normal\n55-69\n"
                 "C / Low\n40-54\nNot Relevant\n0-39\n"
                 "Dealbreakers\n1\nCompanies with fewer than 50 employees must be rejected\n"
                 "2\nCompanies with more than 500 employees must be rejected\n")


class CountingClient:
    """Fake client that records exactly which lead indices reach the model."""
    model = "fake-count"

    def __init__(self, fail_first_idx=None):
        self.seen = []
        self.calls = 0
        self.fail_first_idx = fail_first_idx
        self._failed = set()

    def complete(self, system, user):
        self.calls += 1
        leads = sc._json_blocks(user)[-1]
        idxs = [l["lead_index"] for l in leads]
        self.seen.extend(idxs)
        key = tuple(idxs)
        if self.fail_first_idx is not None and self.fail_first_idx in idxs and key not in self._failed:
            self._failed.add(key)
            return "not valid json"
        return json.dumps([{"lead_index": i, "dimension_scores": {"Subsegment fit": 80}} for i in idxs])


def _sized_lead(idx, size, **extra):
    base = {"first name": "F", "last name": f"L{idx}", "job title": "CTO", "company": f"C{idx}",
            "linkedin url": f"https://www.linkedin.com/in/lead{idx}",
            "linkedin industry": "Software", "linkedin employees": size}
    base.update(extra)
    return sc.normalize_lead(base, idx)


def test_prequalified_lead_bypasses_model_and_merges():
    leads = [_sized_lead(0, "2-10"), _sized_lead(1, "51-200")]   # 0 disqualified deterministically
    client = CountingClient()
    stats = {}
    results = sc.score_leads(leads, ICP_SIZE_HARD, "FinTech", client=client, stats=stats)
    assert stats["prequalified"] == 1 and stats["sent_to_model"] == 1
    assert 0 not in client.seen and 1 in client.seen           # lead 0 never reached the model
    by = {r.lead_index: r for r in results}
    assert set(by) == {0, 1}                                    # original indices preserved
    assert by[0].model == "python-prequalification" and by[0].is_mock is False
    assert by[0].provisional_priority == "Disqualified" and by[0].dealbreaker_state == "confirmed"
    # export-compatible
    pairs = [({l.index: l for l in leads}[r.lead_index], r) for r in results]
    mdf = export.build_main_dataframe(pairs)
    assert "Disqualified" in set(mdf["Priority"])


def test_model_call_count_decreases_with_deterministic_exclusions():
    with_excl = [_sized_lead(0, "2-10"), _sized_lead(1, "1001-5000"),
                 _sized_lead(2, "51-200"), _sized_lead(3, "80-250")]
    c1, s1 = CountingClient(), {}
    sc.score_leads(with_excl, ICP_SIZE_HARD, "F", client=c1, stats=s1, batch_size=5)
    assert s1["prequalified"] == 2 and s1["sent_to_model"] == 2 and len(set(c1.seen)) == 2

    all_in = [_sized_lead(i, "51-200") for i in range(4)]
    c2, s2 = CountingClient(), {}
    sc.score_leads(all_in, ICP_SIZE_HARD, "F", client=c2, stats=s2, batch_size=5)
    assert s2["sent_to_model"] == 4 and len(set(c2.seen)) == 4
    assert s1["sent_to_model"] < s2["sent_to_model"]           # fewer leads reach the model


def test_retry_never_resends_prequalified_or_successful():
    leads = [_sized_lead(0, "2-10"), _sized_lead(1, "51-200"), _sized_lead(2, "80-250")]
    client = CountingClient(fail_first_idx=1)                   # batch with lead 1 fails once
    results = sc.score_leads(leads, ICP_SIZE_HARD, "F", client=client, batch_size=1)
    assert 0 not in client.seen                                # prequalified lead never sent, even on retry
    assert client.seen.count(1) >= 2                           # lead 1 was retried
    assert client.seen.count(2) == 1                           # successful lead 2 never repeated
    assert all(r.error is None for r in results)


def test_mockclient_receives_only_non_prefiltered():
    leads = [_sized_lead(0, "2-10"), _sized_lead(1, "51-200")]
    results = sc.score_leads(leads, ICP_SIZE_HARD, "FinTech", client=sc.MockClient())
    by = {r.lead_index: r for r in results}
    assert by[0].model == "python-prequalification" and by[0].is_mock is False
    assert by[1].is_mock is True                               # only the non-prefiltered lead hit the mock


def test_preferred_size_range_does_not_prequalify():
    # ICP_SIZE has a TARGET range (50-500) but NO hard exclusion -> the tiny company must reach Claude
    leads = [_sized_lead(0, "2-10"), _sized_lead(1, "51-200")]
    client = CountingClient()
    stats = {}
    sc.score_leads(leads, ICP_SIZE, "FinTech", client=client, stats=stats)
    assert stats["prequalified"] == 0 and stats["sent_to_model"] == 2
    assert set(client.seen) == {0, 1}                          # both leads reached the model


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
