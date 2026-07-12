"""Tests for the gap-analysis layer (pipeline/knowledge_gaps.py).

Offline, no LLM, no network, no pytest:
    ./.venv/bin/python tests/test_knowledge_gaps.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk    # noqa: E402
import knowledge_gaps as kg        # noqa: E402


def _ready_bk(entry="generate_new", skip=()):
    """A BusinessKnowledge that has every field the gap detector checks (unless skipped)."""
    k = bk.BusinessKnowledge(entry_point=entry)
    if "company" not in skip:
        k.add_item("company", "overview", "We build analytics")
    if "offer" not in skip:
        k.add_item("product", "name", "Analytics SDK")
    if "all_targets" not in skip:
        k.add_item("industry", "target", "FinTech")
        if "subsegment" not in skip:
            k.add_item("subsegment", "name", "embedded finance")
        if "company_size" not in skip:
            k.add_item("company_size", "preference", "50-500")
        if "geography" not in skip:
            k.add_item("geography", "region", "US")
    if "buyer" not in skip:
        k.add_item("buyer", "role", "CTO")
    if "excluded_buyer" not in skip:
        k.add_item("excluded_buyer", "role", "recruiter")
    if "hard_exclusion" not in skip:
        k.add_item("hard_exclusion_candidate", "rule", "staffing agencies")
    if "evidence_rule" not in skip:
        k.add_item("evidence_rule", "rule", "current employment only")
    if "unknown" not in skip:
        k.add_unknown("funding stage")
    if "best_customer" not in skip:
        k.add_item("customer", "best_customer", "BigCo")
    if "lost_customer" not in skip:
        k.add_item("customer", "lost_customer", "SmallCo")
    if "technology" not in skip:
        k.add_item("technology", "name", "AWS")
    if "constraint" not in skip:
        k.add_item("commercial_constraint", "rule", "US-only contracts")
    if "competitor" not in skip:
        k.add_item("other", "competitor", "RivalCo")
    return k


def _blocking_fields(report):
    return {g.field for g in report.blocking_gaps}


def test_empty_bk_has_blocking_gaps():
    report = kg.detect_gaps(bk.BusinessKnowledge())
    assert report.is_ready_for_icp_generation is False
    for f in ("product_or_service", "company_context", "target_company", "buyer_roles",
              "qualification_dimensions", "target_vs_exclusion"):
        assert f in _blocking_fields(report), f"missing blocking gap: {f}"


def test_full_bk_is_ready_with_no_gaps():
    report = kg.detect_gaps(_ready_bk())
    assert report.is_ready_for_icp_generation is True
    assert report.blocking_gaps == []
    assert report.important_gaps == []
    assert report.optional_gaps == []
    assert report.completeness_score == 100


def test_blocking_gap_product_service():
    report = kg.detect_gaps(_ready_bk(skip={"offer"}))
    assert "product_or_service" in _blocking_fields(report)
    assert report.is_ready_for_icp_generation is False


def test_important_gap_geography():
    report = kg.detect_gaps(_ready_bk(skip={"geography"}))
    assert any(g.field == "target_geographies" for g in report.important_gaps)
    assert report.is_ready_for_icp_generation is True       # important does not block


def test_important_gap_unknown_fields():
    report = kg.detect_gaps(_ready_bk(skip={"unknown"}))
    assert any(g.field == "unknown_fields" for g in report.important_gaps)


def test_optional_gap_lost_customers():
    report = kg.detect_gaps(_ready_bk(skip={"lost_customer"}))
    assert any(g.field == "lost_customer_examples" for g in report.optional_gaps)
    assert report.is_ready_for_icp_generation is True


def test_optional_gap_technology_and_competitor():
    report = kg.detect_gaps(_ready_bk(skip={"technology", "competitor"}))
    fields = {g.field for g in report.optional_gaps}
    assert "technologies" in fields and "competitors" in fields


def test_unresolved_conflict_is_important():
    k = _ready_bk()
    k.add_item("buyer", "role", "CFO")                      # conflicts with existing CTO
    report = kg.detect_gaps(k)
    assert report.unresolved_conflicts
    assert any(g.current_status == "conflicting" for g in report.important_gaps)


def test_missing_info_not_negative():
    report = kg.detect_gaps(_ready_bk(skip={"geography"}))
    gap = next(g for g in report.important_gaps if g.field == "target_geographies")
    assert gap.current_status == "missing"                  # a gap, not a false/negative fact
    assert gap.resolved is False


def test_completeness_transparent_and_deterministic():
    r1 = kg.detect_gaps(_ready_bk())
    r2 = kg.detect_gaps(_ready_bk())
    assert r1.completeness_score == r2.completeness_score == 100
    assert 0 <= kg.detect_gaps(bk.BusinessKnowledge()).completeness_score <= 100
    assert sum(r1.section_completeness.values()) == r1.completeness_score


def test_completeness_rewards_more_complete():
    full = kg.detect_gaps(_ready_bk()).completeness_score
    empty = kg.detect_gaps(bk.BusinessKnowledge()).completeness_score
    assert full > empty


def test_questions_ordered_by_severity():
    report = kg.detect_gaps(bk.BusinessKnowledge())         # has blocking + important + optional
    qs = report.suggested_questions
    # a known blocking question must precede a known optional question
    blocking_q = "What product or service is this ICP intended to sell?"
    optional_q = "Who are your main competitors?"
    assert blocking_q in qs and optional_q in qs
    assert qs.index(blocking_q) < qs.index(optional_q)


def test_entry_point_neutral():
    a = kg.detect_gaps(_ready_bk("generate_new", skip={"geography"}))
    b = kg.detect_gaps(_ready_bk("standardize_existing", skip={"geography"}))
    assert a.completeness_score == b.completeness_score
    assert _blocking_fields(a) == _blocking_fields(b)
    assert {g.field for g in a.important_gaps} == {g.field for g in b.important_gaps}


def test_report_json_serializable():
    report = kg.detect_gaps(_ready_bk(skip={"geography", "lost_customer"}))
    j = json.dumps(report.to_dict())
    d = json.loads(j)
    assert "blocking_gaps" in d and "completeness_score" in d


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
