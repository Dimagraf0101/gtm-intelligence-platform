"""Tests for AI Draft ICP generation (pipeline/icp_draft_generator.py).

Offline — no real Anthropic API. Fake/mock clients + crafted BusinessKnowledge. No pytest.
    ./.venv/bin/python tests/test_icp_draft_generator.py
"""
import sys
import json
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import generated_icp as gi          # noqa: E402
import business_knowledge as bk     # noqa: E402
import knowledge_gaps as kg         # noqa: E402
import icp_adapter as ad            # noqa: E402
import icp_profile as ip            # noqa: E402
import icp_draft_generator as dg    # noqa: E402


# --- fixtures ---------------------------------------------------------------

class FakeDraftClient:
    model = "fake"

    def __init__(self, responder, usage=None):
        self.responder = responder
        self.calls = []
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5,
                               "cache_write_tokens": 0, "cache_read_tokens": 0}

    def complete(self, system, user, *, structured=False):
        self.calls.append(user)
        return self.responder(user, len(self.calls)), dict(self.usage)


def _draft_json(dims=None):
    if dims is None:
        dims = [
            {"name": "Segment fit", "purpose": "match segment", "weight": 40,
             "scoring_guidance": "reward segment match", "required_evidence_attributes": ["a"],
             "external_enrichment_required": False},
            {"name": "Buyer persona", "purpose": "match buyer", "weight": 30,
             "scoring_guidance": "reward buyer match", "required_evidence_attributes": ["b"],
             "external_enrichment_required": False},
            {"name": "Company size", "purpose": "match size", "weight": 30,
             "scoring_guidance": "reward size fit", "required_evidence_attributes": ["c"],
             "external_enrichment_required": False},
        ]
    return json.dumps({
        "business_context": {"description": "We sell an SDK", "value_proposition": "V",
                             "business_model": "SaaS"},
        "dimensions": dims,
        "examples": {"ideal": ["good-fit co"], "acceptable": [], "non_ideal": ["poor-fit co"]},
        "generation_notes": "draft note",
    })


def _fixed(text):
    return FakeDraftClient(lambda user, n: text)


def _bk_full():
    k = bk.BusinessKnowledge()
    k.add_item("company", "name", "Acme", status=bk.CONFIRMED, evidence_excerpt="Acme")
    k.add_item("product", "name", "Analytics SDK", status=bk.CONFIRMED, evidence_excerpt="Analytics SDK")
    k.add_item("industry", "target", "FinTech", status=bk.CONFIRMED, evidence_excerpt="FinTech")
    k.add_item("geography", "region", "US", status=bk.CONFIRMED, evidence_excerpt="US")
    k.add_item("company_size", "preference", "50-500", status=bk.CONFIRMED, evidence_excerpt="50-500")
    k.add_item("buyer", "role", "CTO", status=bk.CONFIRMED, evidence_excerpt="CTO")
    k.add_item("hard_exclusion_candidate", "rule", "staffing agencies", status=bk.PROPOSED,
               evidence_excerpt="reject staffing agencies")
    k.add_unknown("funding stage")
    return k


def _gap(k):
    return kg.detect_gaps(k)


# --- tests ------------------------------------------------------------------

def test_valid_bk_generates_draft():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert res.successful is True and res.is_mock is False
    assert isinstance(res.generated_icp, gi.GeneratedICP)
    assert res.validation_result is not None


def test_status_always_draft():
    k = _bk_full()
    # even if the model tries to smuggle a status, Python forces Draft
    text = json.loads(_draft_json()); text["status"] = "Approved"
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(json.dumps(text)))
    assert res.generated_icp.metadata.status == gi.STATUS_DRAFT


def test_no_raw_package_in_signature():
    params = set(inspect.signature(dg.generate_draft_icp).parameters)
    assert params == {"business_knowledge", "gap_report", "icp_name", "user_notes", "client", "stats"}
    assert not any(bad in p for p in params for bad in ("package", "document", "source", "pdf", "text"))


def test_priority_thresholds_fixed():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    bands = [(b.label, b.min_score, b.max_score) for b in res.generated_icp.priority_thresholds]
    assert bands == [("Priority 1", 90, 100), ("Priority 2", 75, 89), ("Priority 3", 60, 74),
                     ("Priority 4", 45, 59), ("Priority 5", 30, 44), ("Disqualified", 0, 29)]


def test_weights_total_100_pass():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert not any("weights total" in e.lower() for e in res.validation_result.blocking_errors)


def test_invalid_weights_return_errors():
    dims = [{"name": "A", "purpose": "p", "weight": 40, "scoring_guidance": "g",
             "required_evidence_attributes": ["x"], "external_enrichment_required": False},
            {"name": "B", "purpose": "p", "weight": 40, "scoring_guidance": "g",
             "required_evidence_attributes": ["y"], "external_enrichment_required": False}]
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json(dims)))
    assert any("total 80" in e for e in res.validation_result.blocking_errors)
    assert res.validation_result.is_valid is False


def test_duplicate_dimensions_rejected():
    dims = [{"name": "Fit", "purpose": "p", "weight": 50, "scoring_guidance": "g",
             "required_evidence_attributes": ["x"], "external_enrichment_required": False},
            {"name": "fit", "purpose": "p", "weight": 50, "scoring_guidance": "g",
             "required_evidence_attributes": ["y"], "external_enrichment_required": False}]
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json(dims)))
    assert any("duplicate" in e.lower() for e in res.validation_result.blocking_errors)


def test_missing_buyers_remain_unknown():
    k = bk.BusinessKnowledge()
    k.add_item("product", "name", "SDK", status=bk.CONFIRMED, evidence_excerpt="SDK")
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert res.generated_icp.target_buyers.primary_buyer_roles == []
    assert "buyer roles" in res.generated_icp.unknown_fields


def test_historical_industry_not_promoted():
    k = _bk_full()
    k.add_item("industry", "historical", "Healthcare", status=bk.PROPOSED,
               notes=["historical project experience"], evidence_excerpt="")
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    tc = res.generated_icp.target_companies
    assert "FinTech" in tc.target_industries
    assert "Healthcare" not in tc.target_industries
    assert any("historical" in w.lower() for w in res.generated_icp.warnings)


def test_hard_exclusion_only_from_candidate():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    rules = [h.rule for h in res.generated_icp.hard_exclusions]
    assert rules == ["staffing agencies"]
    for h in res.generated_icp.hard_exclusions:
        assert h.evidence_required and h.evaluation_mode in gi.EVAL_MODES and h.scope in gi.EXCLUSION_SCOPES

    # a knowledge base with no explicit candidate -> no hard exclusions
    k2 = bk.BusinessKnowledge()
    k2.add_item("product", "name", "SDK", status=bk.CONFIRMED, evidence_excerpt="SDK")
    res2 = dg.generate_draft_icp(k2, _gap(k2), client=_fixed(_draft_json()))
    assert res2.generated_icp.hard_exclusions == []


def test_preferred_criteria_not_converted_to_exclusion():
    k = bk.BusinessKnowledge()
    k.add_item("product", "name", "SDK", status=bk.CONFIRMED, evidence_excerpt="SDK")
    k.add_item("company_size", "preference", "50-500", status=bk.CONFIRMED, evidence_excerpt="50-500")
    k.add_item("geography", "region", "US", status=bk.CONFIRMED, evidence_excerpt="US")
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert res.generated_icp.hard_exclusions == []
    assert "50-500" in res.generated_icp.target_companies.preferred_employee_ranges


def test_unresolved_gaps_become_unknown_and_warnings():
    k = bk.BusinessKnowledge()          # sparse -> many gaps
    k.add_item("product", "name", "SDK", status=bk.CONFIRMED, evidence_excerpt="SDK")
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert res.generation_notes                                # suggested next actions present
    assert any("suggested next actions" in n.lower() for n in res.generation_notes)
    assert res.generated_icp.unknown_fields                    # gaps surfaced as unknowns


def test_proposed_buyer_remains_uncertain():
    k = bk.BusinessKnowledge()
    k.add_item("product", "name", "SDK", status=bk.CONFIRMED, evidence_excerpt="SDK")
    k.add_item("buyer", "role", "VP Eng", status=bk.PROPOSED, evidence_excerpt="")
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert res.generated_icp.target_buyers.primary_buyer_roles == []
    assert any("proposed buyer" in w.lower() for w in res.generated_icp.warnings)


def test_confirmed_buyer_populates_role():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert "CTO" in res.generated_icp.target_buyers.primary_buyer_roles


def test_parser_fallback():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed("```json\n" + _draft_json() + "\n```"))
    assert res.successful and res.generated_icp.dimensions


def test_retry_only_failed_call():
    k = _bk_full()

    def responder(user, n):
        if n == 1:
            raise RuntimeError("transient")
        return _draft_json()

    res = dg.generate_draft_icp(k, _gap(k), client=FakeDraftClient(responder))
    assert res.retries == 1 and res.model_calls == 2 and res.successful


def test_parse_failure_not_silent_valid_icp():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed("not json at all"))
    assert res.successful is False
    assert res.parser_failures >= 1
    assert res.generated_icp.metadata.status == gi.STATUS_DRAFT
    assert res.generated_icp.dimensions == []                  # no fabricated dimensions
    assert res.validation_result.is_valid is False


def test_mock_mode_clearly_marked():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=dg.MockICPDraftClient())
    assert res.is_mock is True and res.generated_icp.metadata.status == gi.STATUS_DRAFT
    assert res.validation_result is not None
    assert any("mock" in w.lower() for w in res.generated_icp.warnings)


def test_token_metrics_captured():
    k = _bk_full()
    client = _fixed(_draft_json())
    client.usage = {"input_tokens": 12, "output_tokens": 7,
                    "cache_read_tokens": 3, "cache_write_tokens": 9}
    res = dg.generate_draft_icp(k, _gap(k), client=client)
    assert (res.input_tokens, res.output_tokens, res.cache_read_tokens, res.cache_write_tokens) \
        == (12, 7, 3, 9)
    assert res.model_calls == 1


def test_serializers_work():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    md = res.generated_icp.to_markdown()
    assert "# ICP:" in md and "## Qualification Dimensions" in md
    assert json.loads(res.generated_icp.to_json())["metadata"]["status"] == gi.STATUS_DRAFT
    assert json.dumps(res.to_dict())                           # result serializable


def test_adapter_can_consume_approved_version():
    k = _bk_full()
    res = dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()))
    assert res.validation_result.is_valid, res.validation_result.blocking_errors
    icp = res.generated_icp
    icp.metadata.status = gi.STATUS_APPROVED                   # simulate later human approval
    prof = ad.to_engine_profile(icp)
    assert isinstance(prof, ip.ICPProfile)
    assert "staffing agencies" in prof.hard_exclusions


def test_stats_populated():
    k = _bk_full()
    stats = {}
    dg.generate_draft_icp(k, _gap(k), client=_fixed(_draft_json()), stats=stats)
    assert stats.get("successful") is True and "input_tokens" in stats and "model_calls" in stats


def _run():
    tests = [v for kk, v in sorted(globals().items()) if kk.startswith("test_") and callable(v)]
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
