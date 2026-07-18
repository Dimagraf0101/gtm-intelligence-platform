"""Lead-acquisition lineage tests (Sprint 10.1).

Proves a persisted LeadBatch is derived from exactly one **Approved** SearchStrategy owned by the same
MarketHypothesis, with immutable provenance, deterministic refusals, and round-trip stability.
Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_lead_import.py
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk         # noqa: E402
import icp_project as ip                # noqa: E402
import general_icp as gicp             # noqa: E402
import adapted_icp as aicp             # noqa: E402
import strategy_review as sr            # noqa: E402
import icp_approval as ap               # noqa: E402
import search_strategy as ss            # noqa: E402
import lead_import as li                # noqa: E402
import workspace_store as store         # noqa: E402

_CSV = (b"Full Name,Job Title,Company,Location,LinkedIn URL\n"
        b"Jane Doe,CTO,FinCo,US,https://linkedin.com/in/jane\n"
        b"Bob Fox,VP,LogiCo,DE,https://linkedin.com/in/bob\n")


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "Acme", "value_proposition": "v",
                                 "business_model": "services"},
            "dimensions": [
                {"name": "Fit", "purpose": "p", "weight": 60, "scoring_guidance": "g",
                 "required_evidence_attributes": ["a"], "external_enrichment_required": False},
                {"name": "Eng", "purpose": "p", "weight": 40, "scoring_guidance": "g",
                 "required_evidence_attributes": ["b"], "external_enrichment_required": False},
            ],
            "examples": {"ideal": [], "acceptable": [], "non_ideal": []}, "generation_notes": "n",
        }
        return json.dumps(draft), {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}


def _company():
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "overview", "Acme"), ("service", "name", "X"),
                           ("industry", "target", "FinTech"), ("buyer", "role", "CTO"),
                           ("geography", "region", "US"), ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    return c


def _workspace():
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    return ws


def _hypothesis_with_approved_icp(ws, name="Healthcare"):
    h = ws.create_hypothesis(name, "x")
    h.project_knowledge.add_item("industry", "target", name, status=bk.CONFIRMED, evidence_excerpt=name)
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    srw = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    srw.start_review()
    srw.set_weight("Fit", 60)
    srw.set_weight("Eng", 40)
    for c in srw.exclusion_candidates():
        srw.activate_exclusion(c["rule"])
    reviewed, _ = srw.generate_reviewed_draft()
    ap.approve_icp_version(h, reviewed, approved_by="d",
                           acknowledged_warning_ids=[w for w, _ in ap.warnings_with_ids(reviewed)])
    return h


def _new_strategy(h):
    return ss.generate_search_strategy(h, client=FakeDraftClient()).strategy


def _approved_strategy(h):
    s = _new_strategy(h)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="d")
    return s


# 1. Approved strategy allows import (and records provenance).
def test_approved_allows_import():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s = _approved_strategy(h)
    res = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="dana")
    assert res.ok and res.batch is not None
    assert res.batch.derived_from_search_strategy == ss.search_strategy_reference(s)
    assert res.batch.search_strategy_id == s.strategy_id
    assert h.list_lead_batches() == [res.batch]


# 2/3/4. Draft / Reviewed / Archived strategy blocks persistence.
def test_non_approved_status_blocks():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    draft = _new_strategy(h)                                    # Draft
    assert not li.import_leads_from_strategy(h, draft.strategy_id, _CSV, imported_by="d").ok
    ss.set_status(h, draft.strategy_id, ss.STRATEGY_REVIEWED)   # Reviewed
    r = li.import_leads_from_strategy(h, draft.strategy_id, _CSV, imported_by="d")
    assert not r.ok and "Reviewed" in r.error
    # Approved then Archived
    appr = _approved_strategy(h)
    ss.set_status(h, appr.strategy_id, ss.STRATEGY_ARCHIVED)
    r2 = li.import_leads_from_strategy(h, appr.strategy_id, _CSV, imported_by="d")
    assert not r2.ok and "Archived" in r2.error
    assert h.list_lead_batches() == []                          # nothing persisted


# 5 & 6. Missing / unknown strategy id blocks.
def test_missing_and_unknown_strategy_block():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    assert not li.import_leads_from_strategy(h, "", _CSV, imported_by="d").ok            # missing
    assert not li.import_leads_from_strategy(h, "does-not-exist", _CSV, imported_by="d").ok  # unknown
    assert h.list_lead_batches() == []


# 7. Cross-hypothesis strategy blocks.
def test_cross_hypothesis_strategy_blocks():
    ws = _workspace()
    a = _hypothesis_with_approved_icp(ws, "Healthcare")
    b = _hypothesis_with_approved_icp(ws, "Logistics")
    s_b = _approved_strategy(b)                                 # approved strategy owned by b
    res = li.import_leads_from_strategy(a, s_b.strategy_id, _CSV, imported_by="d")
    assert not res.ok                                           # a does not own s_b
    assert a.list_lead_batches() == []


# 8. Provenance survives JSON round-trip.
def test_provenance_survives_roundtrip():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s = _approved_strategy(h)
    ref = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="dana").derived_from_search_strategy
    ws2 = store.loads(store.dumps(ws))
    hb = ws2.get_hypothesis(h.project_id)
    assert hb.latest_lead_batch().derived_from_search_strategy == ref == ss.search_strategy_reference(s)


# 9. Re-import from the same strategy creates a new immutable batch.
def test_reimport_creates_new_immutable_batch():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s = _approved_strategy(h)
    b1 = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="d").batch
    snap = json.dumps(b1.to_dict())
    b2 = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="d").batch
    assert b2.batch_id != b1.batch_id and len(h.list_lead_batches()) == 2
    assert json.dumps(h.list_lead_batches()[0].to_dict()) == snap
    assert b1.derived_from_search_strategy == b2.derived_from_search_strategy


# 10. Two approved strategies in one hypothesis remain distinguishable in provenance.
def test_two_strategies_distinguishable():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s1 = _approved_strategy(h)
    s2 = _approved_strategy(h)                                  # a second (v2) approved strategy
    assert s1.strategy_id != s2.strategy_id
    r1 = li.import_leads_from_strategy(h, s1.strategy_id, _CSV, imported_by="d")
    r2 = li.import_leads_from_strategy(h, s2.strategy_id, _CSV, imported_by="d")
    assert r1.batch.derived_from_search_strategy != r2.batch.derived_from_search_strategy
    assert ss.search_strategy_reference(s1) != ss.search_strategy_reference(s2)


# 11. Missing importer blocks persistence.
def test_missing_importer_blocks():
    ws = _workspace()
    h = _hypothesis_with_approved_icp(ws)
    s = _approved_strategy(h)
    assert not li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="  ").ok
    assert h.list_lead_batches() == []


# 12. Old JSON compatibility: a pre-10.1 batch (no derived_from) loads with a safe default.
def test_old_batch_json_loads():
    import lead_batch as lb
    old = {
        "batch_id": "lb-old", "hypothesis_id": "p1", "source": {"kind": lb.SOURCE_VAYNE_SALESNAV},
        "imported_at": "t", "imported_by": "x", "search_strategy_id": "s1",
        # derived_from_search_strategy ABSENT (pre-Sprint-10.1)
        "leads": [], "stats": {},
    }
    b = lb.LeadBatch.from_dict(old)
    assert b.derived_from_search_strategy == "" and b.search_strategy_id == "s1"


# 13. Existing qualification behavior remains unchanged.
def test_qualification_unchanged():
    import qualification_bridge as qb
    import scoring as sc
    up = qb.context_from_uploaded("Plain", "We sell to FinTech CTOs.")
    r = qb.score_with_context([sc.normalize_lead({"company": "X", "title": "CTO"}, 0)], up,
                              client=sc.MockClient())
    assert r and r[0].icp == "Plain"


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
