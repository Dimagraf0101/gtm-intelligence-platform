"""Tests for the Business Knowledge Review Workspace (pipeline/knowledge_review.py).

Offline, no LLM, no pytest:
    ./.venv/bin/python tests/test_knowledge_review.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk     # noqa: E402
import generated_icp as gi          # noqa: E402
import knowledge_review as kr       # noqa: E402


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "d", "value_proposition": "v", "business_model": "m"},
            "dimensions": [
                {"name": "Segment fit", "purpose": "p", "weight": 50, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Buyer persona", "purpose": "p", "weight": 50, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": ["x"], "acceptable": [], "non_ideal": ["y"]},
            "generation_notes": "note",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _bk():
    k = bk.BusinessKnowledge()
    k.add_item("product", "name", "Analytics SDK", status=bk.CONFIRMED, evidence_excerpt="Analytics SDK")
    k.add_item("industry", "target", "FinTech", status=bk.PROPOSED, evidence_excerpt="FinTech")
    k.add_item("buyer", "role", "CTO", status=bk.PROPOSED)
    k.add_item("geography", "region", "US", status=bk.CONFIRMED, evidence_excerpt="US")
    k.add_unknown("funding stage")
    return k


def _ws():
    return kr.KnowledgeReviewWorkspace.from_business_knowledge(_bk())


# --- browse / filter / search / group ---------------------------------------

def test_browse_and_group_by_category():
    ws = _ws()
    groups = ws.grouped()
    assert set(groups) >= {"product", "industry", "buyer", "geography"}
    # each view carries confidence, status, sources, evidence
    prod = groups["product"][0]
    assert prod["status"] == bk.CONFIRMED and "confidence" in prod and "evidence_excerpt" in prod


def test_filter_by_category_and_status():
    ws = _ws()
    assert all(v["category"] == "industry" for v in ws.items(category="industry"))
    assert all(v["status"] == bk.CONFIRMED for v in ws.items(status=bk.CONFIRMED))


def test_search():
    ws = _ws()
    hits = ws.items(search="fintech")
    assert len(hits) == 1 and hits[0]["value"] == "FinTech"
    assert ws.items(search="zzz-none") == []


def test_rejected_hidden_by_default():
    ws = _ws()
    vid = ws.items(category="buyer")[0]["knowledge_id"]
    ws.reject(vid)
    assert ws.items(category="buyer") == []
    assert len(ws.items(category="buyer", include_rejected=True)) == 1


# --- curation actions --------------------------------------------------------

def test_confirm_item():
    ws = _ws()
    vid = ws.items(category="industry")[0]["knowledge_id"]
    view = ws.confirm(vid)
    assert view["status"] == bk.CONFIRMED and view["user_confirmed"] is True


def test_reject_item_kept_for_audit():
    ws = _ws()
    vid = ws.items(category="buyer")[0]["knowledge_id"]
    ws.reject(vid, note="not a buyer")
    item = ws.knowledge._get(vid)
    assert item.status == bk.REJECTED and item in ws.knowledge.knowledge_items


def test_edit_value_marks_user_input_and_preserves_provenance():
    ws = _ws()
    it0 = ws.knowledge.get_items(category="product")[0]
    refs_before = list(it0.source_references)
    view = ws.edit(it0.knowledge_id, value="Embedded Analytics SDK")
    assert view["value"] == "Embedded Analytics SDK"
    assert view["origin"] == bk.ORIGIN_USER
    assert ws.knowledge._get(it0.knowledge_id).source_references == refs_before  # provenance kept
    assert ws.knowledge._get(it0.knowledge_id).normalized_value == "embedded analytics sdk"


def test_add_new_knowledge_is_user_input():
    ws = _ws()
    view = ws.add("subsegment", "focus", "embedded finance", evidence_excerpt="", note="added by user")
    assert view["origin"] == bk.ORIGIN_USER and view["user_confirmed"] is True
    assert any(v["value"] == "embedded finance" for v in ws.items(category="subsegment"))


def test_merge_duplicates():
    k = bk.BusinessKnowledge()
    a = k.add_item("product", "name", "SDK", status=bk.CONFIRMED, merge=False,
                   source_references=[bk.SourceReference(source_id="s1", filename="a.pdf")])
    b = k.add_item("product", "name", "sdk", status=bk.CONFIRMED, merge=False,
                   source_references=[bk.SourceReference(source_id="s2", filename="b.pdf")])
    ws = kr.KnowledgeReviewWorkspace(k)
    ws.merge_duplicates(a.knowledge_id, b.knowledge_id)
    prod = ws.items(category="product")
    assert len(prod) == 1 and len(prod[0]["sources"]) == 2


def test_resolve_conflict_keeps_contrary_evidence():
    k = bk.BusinessKnowledge()
    a = k.add_item("company_size", "preference", "50-500")
    b = k.add_item("company_size", "preference", "1000-5000")   # conflicting
    ws = kr.KnowledgeReviewWorkspace(k)
    assert k.conflicts
    cid = k.conflicts[0].conflict_id
    ws.resolve_conflict(cid, a.knowledge_id, note="per founder")
    assert k.conflicts[0].status == bk.CONFLICT_USER
    assert b in k.knowledge_items                                # contrary evidence retained


# --- the key invariant -------------------------------------------------------

def test_ai_cannot_overwrite_confirmed_human_knowledge():
    ws = _ws()
    # human confirms an industry value
    vid = ws.items(category="industry")[0]["knowledge_id"]
    ws.confirm(vid)
    human = ws.knowledge._get(vid)
    assert human.user_confirmed and human.status == bk.CONFIRMED
    # a later AI proposal with a DIFFERENT value must not overwrite it
    ws.knowledge.add_item("industry", "target", "HealthTech", status=bk.PROPOSED, origin=bk.ORIGIN_AI)
    assert human.user_confirmed and human.status == bk.CONFIRMED     # untouched
    assert ws.knowledge.conflicts                                    # surfaced as a conflict instead


# --- summary -----------------------------------------------------------------

def test_summary_reports_completeness_conflicts_unknowns_gaps():
    ws = _ws()
    s = ws.summary()
    assert 0 <= s["completeness"] <= 100
    assert isinstance(s["is_ready_for_icp_generation"], bool)
    assert "funding stage" in s["unknown_fields"]
    for key in ("blocking_gaps", "important_gaps", "optional_gaps", "open_conflicts",
                "by_status", "suggested_questions"):
        assert key in s
    assert isinstance(s["blocking_gaps"], list)


def test_summary_reflects_edits_live():
    ws = _ws()
    before = ws.summary()["by_status"].get(bk.CONFIRMED, 0)
    ws.confirm(ws.items(category="industry")[0]["knowledge_id"])
    after = ws.summary()["by_status"].get(bk.CONFIRMED, 0)
    assert after == before + 1


# --- deterministic Generate Draft action ------------------------------------

def test_generate_draft_returns_fresh_draft_not_stored():
    ws = _ws()
    res = ws.generate_draft(client=FakeDraftClient())
    assert res.generated_icp.metadata.status == gi.STATUS_DRAFT
    assert res.successful
    # workspace does not retain or edit the draft
    assert not hasattr(ws, "draft") and not hasattr(ws, "generated_icp")
    # generating again yields an independent object built from the same source of truth
    res2 = ws.generate_draft(client=FakeDraftClient())
    assert res2.generated_icp is not res.generated_icp


def test_generate_draft_uses_current_knowledge():
    ws = _ws()
    ws.add("industry", "extra", "InsurTech")           # curate first
    res = ws.generate_draft(client=FakeDraftClient())
    assert "InsurTech" in res.generated_icp.target_companies.target_industries


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
