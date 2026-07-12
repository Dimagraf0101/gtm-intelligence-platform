"""Tests for the Business Knowledge layer (pipeline/business_knowledge.py).

Offline, no LLM, no network, no pytest:
    ./.venv/bin/python tests/test_business_knowledge.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk    # noqa: E402
import source_package as sp        # noqa: E402


def _ref(source_id, start=0, end=10, h="h1"):
    return bk.SourceReference(source_id=source_id, filename=f"{source_id}.pdf",
                              source_category="pitch_deck", character_start=start,
                              character_end=end, excerpt_hash=h, extraction_status="success")


def test_empty_business_knowledge():
    k = bk.BusinessKnowledge()
    assert k.knowledge_items == []
    assert k.field("products") == []
    s = k.summary()
    assert s["total_items"] == 0 and s["active_items"] == 0
    assert json.loads(k.to_json())["products"] == []


def test_add_and_retrieve():
    k = bk.BusinessKnowledge()
    item = k.add_item("product", "name", "Analytics SDK")
    assert item.status == bk.CONFIRMED
    assert k.get_items(category="product") == [item]
    assert k.field("products") == ["Analytics SDK"]


def test_exact_duplicate_merge():
    k = bk.BusinessKnowledge()
    a = k.add_item("industry", "target", "FinTech", origin=bk.ORIGIN_SOURCE,
                   confidence=0.6, source_references=[_ref("s1")], notes=["from deck"])
    b = k.add_item("industry", "target", "fintech", origin=bk.ORIGIN_USER,     # same normalized
                   confidence=0.9, source_references=[_ref("s2")], notes=["from interview"])
    assert a is b                                           # merged, not duplicated
    assert len(k.get_items(category="industry")) == 1
    assert len(a.source_references) == 2                    # references merged
    assert a.confidence == 0.9                              # highest kept
    assert "from deck" in a.notes and "from interview" in a.notes


def test_source_references_dedup_on_merge():
    k = bk.BusinessKnowledge()
    k.add_item("geography", "region", "US", source_references=[_ref("s1", 0, 5, "H")])
    item = k.add_item("geography", "region", "us", source_references=[_ref("s1", 0, 5, "H")])
    assert len(item.source_references) == 1                 # identical ref not duplicated


def test_conflicting_values():
    k = bk.BusinessKnowledge()
    a = k.add_item("industry", "target", "FinTech")
    b = k.add_item("industry", "target", "HealthTech")
    assert a.status == bk.CONFLICTING and b.status == bk.CONFLICTING
    assert len(k.conflicts) == 1
    rec = k.conflicts[0]
    assert rec.status == bk.CONFLICT_UNRESOLVED
    assert set(rec.item_ids) == {a.knowledge_id, b.knowledge_id}


def test_user_confirmed_preferred_keeps_contrary():
    k = bk.BusinessKnowledge()
    a = k.add_item("industry", "target", "FinTech")
    b = k.add_item("industry", "target", "HealthTech")
    k.confirm_item(a.knowledge_id, note="confirmed by founder")
    assert a.user_confirmed and a.status == bk.CONFIRMED
    # contrary evidence is NOT deleted
    assert b in k.knowledge_items and b.status == bk.CONFLICTING
    rec = k.conflicts[0]
    assert rec.status == bk.CONFLICT_USER and rec.preferred_item_id == a.knowledge_id


def test_ai_proposal_remains_proposed():
    k = bk.BusinessKnowledge()
    item = k.add_item("subsegment", "name", "embedded finance", origin=bk.ORIGIN_AI)
    assert item.status == bk.PROPOSED                       # never auto-promoted
    assert item.confidence == 0.4


def test_rejected_excluded_from_active_summary():
    k = bk.BusinessKnowledge()
    item = k.add_item("product", "name", "Legacy tool")
    k.reject_item(item.knowledge_id, note="discontinued")
    assert item in k.knowledge_items                        # kept for audit
    assert k.field("products") == []                        # excluded from active view
    assert k.summary()["fields"]["products"] == []


def test_mark_conflict_directly():
    k = bk.BusinessKnowledge()
    a = k.add_item("buyer", "role", "CTO", merge=False)
    b = k.add_item("buyer", "role", "CFO", merge=False)
    rec = k.mark_conflict([a.knowledge_id, b.knowledge_id], category="buyer", attribute="role")
    assert rec.status == bk.CONFLICT_UNRESOLVED
    assert a.status == bk.CONFLICTING and b.status == bk.CONFLICTING


def test_conflict_resolution_audit():
    k = bk.BusinessKnowledge()
    a = k.add_item("company_size", "preference", "50-500")
    b = k.add_item("company_size", "preference", "1000-5000")
    rec = k.conflicts[0]
    k.resolve_conflict(rec.conflict_id, a.knowledge_id, note="per CEO")
    assert rec.status == bk.CONFLICT_USER and rec.preferred_item_id == a.knowledge_id
    assert a.user_confirmed
    assert b in k.knowledge_items                           # contrary retained


def test_system_resolved_only_for_identical():
    k = bk.BusinessKnowledge()
    a = k.add_item("geography", "region", "US", merge=False)
    b = k.add_item("geography", "region", "usa", merge=False)   # same normalized 'united states'
    rec = k.mark_conflict([a.knowledge_id, b.knowledge_id])
    k.resolve_conflict(rec.conflict_id, a.knowledge_id, system=True)
    assert rec.status == bk.CONFLICT_SYSTEM
    # differing normalized values may NOT be system-resolved
    c = k.add_item("industry", "x", "FinTech", merge=False)
    d = k.add_item("industry", "x", "HealthTech", merge=False)
    rec2 = k.mark_conflict([c.knowledge_id, d.knowledge_id])
    try:
        k.resolve_conflict(rec2.conflict_id, c.knowledge_id, system=True)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_merge_items_explicit():
    k = bk.BusinessKnowledge()
    a = k.add_item("product", "name", "SDK", merge=False, source_references=[_ref("s1")])
    b = k.add_item("product", "name", "sdk", merge=False, source_references=[_ref("s2")])
    kept = k.merge_items(a.knowledge_id, b.knowledge_id)
    assert kept is a and b not in k.knowledge_items
    assert len(a.source_references) == 2


def test_normalization_deterministic():
    assert bk.normalize_text("  Hello   World ") == "hello world"
    assert bk.normalize_geography("USA") == "united states"
    assert bk.normalize_geography("uk") == "united kingdom"
    assert bk.normalize_company_name("Acme, Inc.") == "acme"
    assert bk.normalize_role("VP, Engineering.") == "vp, engineering"
    assert bk.normalize_employee_range("50 - 500") == "50-500"
    assert bk.normalize_employee_range("50+") == "50+"
    assert bk.normalize_url("https://WWW.Example.com/") == "example.com"
    assert bk.normalize_list(["A", "a", "B"]) == ["a", "b"]
    # deterministic: same input -> same output
    assert bk.normalize_company_name("Acme, Inc.") == bk.normalize_company_name("Acme, Inc.")


def test_missing_info_remains_unknown():
    k = bk.BusinessKnowledge()
    k.add_unknown("funding stage", note="not in materials")
    assert "funding stage" in k.unknown_fields
    # a field we never populated returns empty, not a fabricated negative
    assert k.field("technologies") == []
    unknown_items = k.get_items(category="unknown")
    assert unknown_items and unknown_items[0].status == bk.UNKNOWN


def test_source_package_compatibility_no_semantic_parsing():
    same = b"identical content"
    pkg = sp.build_package_from_files([("a.txt", same, "other"), ("b.txt", same, "other")])
    assert pkg.package_warnings                              # duplicate warning exists
    k = bk.from_source_package(pkg, entry_point="generate_new")
    assert k.source_package_id == pkg.package_id
    assert any(w for w in k.warnings)                        # package warnings copied
    assert k.knowledge_items == []                           # nothing invented / parsed
    assert k.entry_point == "generate_new"


def _populate(entry):
    k = bk.BusinessKnowledge(entry_point=entry)
    k.add_item("company", "overview", "We build analytics")
    k.add_item("product", "name", "Analytics SDK")
    k.add_item("buyer", "role", "CTO")
    k.add_item("geography", "region", "US")
    return k


def test_both_entry_points_behave_identically():
    a = _populate("generate_new")
    b = _populate("standardize_existing")
    assert a.summary()["fields"] == b.summary()["fields"]
    assert a.field("products") == b.field("products")
    assert a.entry_point != b.entry_point                    # audit only


def test_json_serialization():
    k = _populate("generate_new")
    k.add_item("industry", "target", "FinTech")
    k.add_item("industry", "target", "HealthTech")           # creates a conflict
    parsed = json.loads(k.to_json())
    assert parsed["source_package_id"] == ""
    assert parsed["conflicts"]
    assert isinstance(parsed["knowledge_items"], list)


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
