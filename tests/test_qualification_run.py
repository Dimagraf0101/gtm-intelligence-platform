"""Qualification Engine integration tests (Sprint 11).

Proves the deterministic pipeline LeadBatch → mapper → qualification_bridge → immutable
QualifiedLeadBatch: the frozen engine is reused (not duplicated), lineage/ownership is validated,
source artifacts are never mutated, and results persist + round-trip. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_qualification_run.py
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
import lead_batch as lbmod              # noqa: E402
import scoring as sc                    # noqa: E402
import icp_identity as idy              # noqa: E402
import qualification_mapper as qmap     # noqa: E402
import qualification_run as qr          # noqa: E402
import qualified_lead as ql             # noqa: E402
import workspace_store as store         # noqa: E402

_CSV = (b"Full Name,Job Title,Company,Location,LinkedIn URL\n"
        b"Jane Doe,CTO,FinCo,US,https://linkedin.com/in/jane\n"
        b"Bob Fox,VP Eng,LogiCo,DE,https://linkedin.com/in/bob\n")


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


def _approved(ws, h):
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    srw = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    srw.start_review()
    srw.set_weight("Fit", 60)
    srw.set_weight("Eng", 40)
    for c in srw.exclusion_candidates():
        srw.activate_exclusion(c["rule"])
    rv, _ = srw.generate_reviewed_draft()
    ap.approve_icp_version(h, rv, approved_by="d",
                           acknowledged_warning_ids=[w for w, _ in ap.warnings_with_ids(rv)])
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="d")
    return s


def _ready_hypothesis(name="Healthcare"):
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    h = ws.create_hypothesis(name, "x")
    h.project_knowledge.add_item("industry", "target", name, status=bk.CONFIRMED, evidence_excerpt=name)
    s = _approved(ws, h)
    batch = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="dana").batch
    return ws, h, batch


# Mapper correctness — domain Lead -> engine Lead via the engine's own normalizer.
def test_mapper_correctness():
    lead = lbmod.Lead(lead_id="L1", company_name="FinCo", person_name="Jane Doe",
                      current_title="CTO", company_size="50-200", geography="US",
                      linkedin_url="https://linkedin.com/in/jane", industry="FinTech")
    scoring_lead = qmap.to_scoring_lead(lead, 0)
    assert isinstance(scoring_lead, sc.Lead) and scoring_lead.index == 0
    assert scoring_lead.fields.get("company") == "FinCo"
    assert scoring_lead.fields.get("job_title") == "CTO"
    assert scoring_lead.fields.get("location") == "US"


# Successful qualification produces an immutable QualifiedLeadBatch with provenance.
def test_successful_qualification():
    ws, h, batch = _ready_hypothesis()
    adapted_id = idy.artifact_identity_str(ap.get_active_approved_icp(h))
    res = qr.qualify_lead_batch(h, batch.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.ok and res.batch is not None
    qlb = res.batch
    assert len(qlb.qualified) == len(batch.leads)
    assert qlb.derived_from_lead_batch == batch.batch_id
    assert qlb.derived_from_adapted_icp == adapted_id and qlb.qualified_by == "dana"
    assert h.list_qualified_batches() == [qlb]
    # each qualified lead references the source lead by id and carries engine outputs
    q = qlb.qualified[0]
    assert q.lead_id in {l.lead_id for l in batch.leads}
    assert isinstance(q.score, int) and q.decision and q.result
    assert not hasattr(q, "company_name")                 # references, does not copy Lead fields


# Empty LeadBatch is refused.
def test_empty_lead_batch_refused():
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    h = ws.create_hypothesis("H", "x")
    _approved(ws, h)
    empty = lbmod.LeadBatch(hypothesis_id=h.project_id, source=lbmod.LeadSource(),
                            derived_from_search_strategy="search_strategy:x:y:1", leads=[])
    h.lead_batches.append(empty)
    res = qr.qualify_lead_batch(h, empty.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "empty" in res.error.lower()
    assert h.list_qualified_batches() == []


# Referenced Adapted ICP absent from this hypothesis is refused (Sprint 11.1 lineage semantics).
def test_referenced_icp_absent_refused():
    ws, h, batch = _ready_hypothesis()
    # remove the exact Adapted ICP the batch's strategy references -> lineage cannot resolve it
    h.approved_versions.clear()
    res = qr.qualify_lead_batch(h, batch.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "adapted icp" in res.error.lower()


# Hypothesis mismatch / unknown batch is refused.
def test_hypothesis_mismatch_refused():
    ws, a, batch = _ready_hypothesis("Healthcare")
    b = ws.create_hypothesis("Logistics", "y")
    _approved(ws, b)
    res = qr.qualify_lead_batch(b, batch.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok                                     # a's batch id is unknown to b
    assert b.list_qualified_batches() == []


# Broken acquisition lineage (no strategy provenance) is refused.
def test_broken_lineage_refused():
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    h = ws.create_hypothesis("H", "x")
    _approved(ws, h)
    batch = lbmod.LeadBatch(hypothesis_id=h.project_id, source=lbmod.LeadSource(),
                            derived_from_search_strategy="",   # broken lineage
                            leads=[lbmod.Lead(lead_id="L1", company_name="FinCo")])
    h.lead_batches.append(batch)
    res = qr.qualify_lead_batch(h, batch.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "lineage" in res.error.lower()


# Missing qualifier name is refused.
def test_missing_qualifier_refused():
    ws, h, batch = _ready_hypothesis()
    assert not qr.qualify_lead_batch(h, batch.batch_id, qualified_by="  ", client=sc.MockClient()).ok


# Immutability: qualification never edits the LeadBatch / ICP / knowledge; re-run appends a new batch.
def test_immutability_and_no_source_mutation():
    ws, h, batch = _ready_hypothesis()
    lb_before = json.dumps(batch.to_dict())
    icp_before = json.dumps(ap.get_active_approved_icp(h).to_dict())
    company_before = ws.company.to_json()
    r1 = qr.qualify_lead_batch(h, batch.batch_id, qualified_by="d", client=sc.MockClient())
    snap = json.dumps(r1.batch.to_dict())
    r2 = qr.qualify_lead_batch(h, batch.batch_id, qualified_by="d", client=sc.MockClient())
    assert r2.batch.batch_id != r1.batch.batch_id and len(h.list_qualified_batches()) == 2
    assert json.dumps(h.list_qualified_batches()[0].to_dict()) == snap        # first result immutable
    # source artifacts untouched
    assert json.dumps(h.list_lead_batches()[0].to_dict()) == lb_before
    assert json.dumps(ap.get_active_approved_icp(h).to_dict()) == icp_before
    assert ws.company.to_json() == company_before


# Persistence + round-trip preserves the qualified batch and its provenance.
def test_persistence_roundtrip():
    ws, h, batch = _ready_hypothesis()
    adapted_id = idy.artifact_identity_str(ap.get_active_approved_icp(h))
    qr.qualify_lead_batch(h, batch.batch_id, qualified_by="dana", client=sc.MockClient())
    ws2 = store.loads(store.dumps(ws))
    h2 = ws2.get_hypothesis(h.project_id)
    assert len(h2.list_qualified_batches()) == 1
    q = h2.latest_qualified_batch()
    assert q.derived_from_lead_batch == batch.batch_id and q.derived_from_adapted_icp == adapted_id
    assert len(q.qualified) == len(batch.leads)
    assert q.to_dict() == h.latest_qualified_batch().to_dict()                # lossless


# Old JSON without qualified_batches loads safely.
def test_old_json_without_qualified_batches_loads():
    envelope = {
        "schema_version": store.SCHEMA_VERSION, "kind": "gtm_company_workspace",
        "workspace": {
            "workspace_id": "ws1", "name": "Legacy", "created_at": "t", "updated_at": "t",
            "metadata": {}, "company": bk.BusinessKnowledge().to_dict(), "general_icp_versions": [],
            "hypotheses": [{
                "project_id": "p1", "name": "H", "hypothesis": "", "status": "active",
                "created_at": "t", "updated_at": "t",
                "project_knowledge": bk.BusinessKnowledge().to_dict(),
                "draft_versions": [], "not_applicable": {}, "strategy": None,
                "approved_versions": [], "approval_records": [], "active_approved_version": None,
                "search_strategies": [], "lead_batches": [],
                # qualified_batches ABSENT (pre-Sprint-11)
            }],
        },
    }
    ws = store.from_envelope(envelope)
    assert ws.get_hypothesis("p1").list_qualified_batches() == []


# The engine is reused unchanged (frozen modules untouched; qualification behavior preserved).
def test_engine_reused_unchanged():
    # the run service must not modify scoring/bridge; it only calls them
    src = (ROOT / "pipeline" / "qualification_run.py").read_text(encoding="utf-8")
    assert "import scoring" not in src            # goes through the bridge, not scoring directly
    mapper = (ROOT / "pipeline" / "qualification_mapper.py").read_text(encoding="utf-8")
    assert "score_leads(" not in mapper and "def score" not in mapper   # mapper does not score
    # existing qualification behavior still works via the bridge
    import qualification_bridge as qb
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
