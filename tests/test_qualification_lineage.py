"""Qualification lineage tests (Sprint 11.1).

Proves default qualification uses the EXACT Adapted ICP the LeadBatch's Search Strategy was derived
from — never the hypothesis's currently active ICP — and that the whole chain (ICP → Strategy →
LeadBatch → QualifiedLeadBatch) resolves deterministically or refuses. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_qualification_lineage.py
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
import qualification_run as qr          # noqa: E402
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


def _approve_adapted(ws, h):
    """Adapt → review → approve one Adapted ICP version; return its ArtifactIdentity."""
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
    return idy.artifact_identity_str(ap.get_active_approved_icp(h))


def _approve_strategy(h):
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="d")
    return s


def _batch(h, s):
    return li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="dana").batch


def _v1_setup():
    """ICP v1 → Strategy v1 → LeadBatch1. Returns (ws, h, batch1, icp_v1_identity, strategy1)."""
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    h = ws.create_hypothesis("Healthcare", "x")
    h.project_knowledge.add_item("industry", "target", "Healthcare", status=bk.CONFIRMED,
                                 evidence_excerpt="Healthcare")
    icp_v1 = _approve_adapted(ws, h)
    s1 = _approve_strategy(h)
    b1 = _batch(h, s1)
    assert s1.derived_from_adapted_icp == icp_v1        # strategy pinned to v1
    return ws, h, b1, icp_v1, s1


# 1. ICP v1 → Strategy v1 → LeadBatch qualifies against ICP v1.
def test_qualifies_against_pinned_icp_v1():
    ws, h, b1, icp_v1, _ = _v1_setup()
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.ok
    assert res.batch.derived_from_adapted_icp == icp_v1
    assert res.batch.derived_from_search_strategy == b1.derived_from_search_strategy


# 2. Approving ICP v2 does not change qualification context for that batch.
def test_new_active_icp_does_not_affect_existing_batch():
    ws, h, b1, icp_v1, _ = _v1_setup()
    icp_v2 = _approve_adapted(ws, h)                    # a newer active Approved ICP
    assert icp_v2 != icp_v1 and idy.artifact_identity_str(ap.get_active_approved_icp(h)) == icp_v2
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.ok and res.batch.derived_from_adapted_icp == icp_v1   # NOT v2


# 3. A new Strategy v2 + LeadBatch v2 qualify against ICP v2.
def test_new_strategy_and_batch_use_icp_v2():
    ws, h, b1, icp_v1, _ = _v1_setup()
    icp_v2 = _approve_adapted(ws, h)
    s2 = _approve_strategy(h)                            # derived from active v2
    assert s2.derived_from_adapted_icp == icp_v2
    b2 = _batch(h, s2)
    res = qr.qualify_lead_batch(h, b2.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.ok and res.batch.derived_from_adapted_icp == icp_v2
    # ...and the old batch still resolves to v1
    r1 = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    assert r1.batch.derived_from_adapted_icp == icp_v1


# 4. Malformed strategy provenance blocks qualification.
def test_malformed_strategy_provenance_blocks():
    ws, h, b1, _, _ = _v1_setup()
    bad = lbmod.LeadBatch(hypothesis_id=h.project_id, source=lbmod.LeadSource(),
                          derived_from_search_strategy="not-a-valid-ref",
                          leads=[lbmod.Lead(lead_id="L", company_name="FinCo")])
    h.lead_batches.append(bad)
    res = qr.qualify_lead_batch(h, bad.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "malformed lead batch provenance" in res.error.lower()


# 5. Missing referenced SearchStrategy blocks qualification.
def test_missing_referenced_strategy_blocks():
    ws, h, b1, _, s1 = _v1_setup()
    h.search_strategies.clear()                          # strategy gone
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "no longer exists" in res.error.lower()


# 6. Cross-hypothesis SearchStrategy reference blocks qualification.
def test_cross_hypothesis_strategy_ref_blocks():
    ws, h, b1, _, _ = _v1_setup()
    other = ws.create_hypothesis("Other", "y")
    ref = f"search_strategy:{other.project_id}:some-strategy:1"       # points at another hypothesis
    bad = lbmod.LeadBatch(hypothesis_id=h.project_id, source=lbmod.LeadSource(),
                          derived_from_search_strategy=ref,
                          leads=[lbmod.Lead(lead_id="L", company_name="FinCo")])
    h.lead_batches.append(bad)
    res = qr.qualify_lead_batch(h, bad.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "another hypothesis" in res.error.lower()


# 7. Malformed ICP provenance on the strategy blocks qualification.
def test_malformed_icp_provenance_blocks():
    ws, h, b1, _, s1 = _v1_setup()
    s1.derived_from_adapted_icp = "not::a::valid::identity::x"        # corrupt the strategy's ICP ref
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "malformed adapted icp provenance" in res.error.lower()


# 8. Missing referenced Adapted ICP blocks qualification.
def test_missing_referenced_icp_blocks():
    ws, h, b1, _, _ = _v1_setup()
    h.approved_versions.clear()                          # the exact ICP is gone
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "does not exist in this hypothesis" in res.error.lower()


# 9. Cross-hypothesis ICP reference blocks qualification (ICP lives on another hypothesis).
def test_cross_hypothesis_icp_ref_blocks():
    ws, h, b1, icp_v1, s1 = _v1_setup()
    # point the strategy at an ICP identity approved on a DIFFERENT hypothesis
    other = ws.create_hypothesis("Other", "y")
    other.project_knowledge.add_item("industry", "target", "Other", status=bk.CONFIRMED,
                                     evidence_excerpt="Other")
    other_icp = _approve_adapted(ws, other)
    s1.derived_from_adapted_icp = other_icp              # not in h.approved_versions
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="d", client=sc.MockClient())
    assert not res.ok and "does not exist in this hypothesis" in res.error.lower()


# 10. Archived formerly-approved strategy remains a valid historical lineage source.
def test_archived_strategy_remains_valid_history():
    ws, h, b1, icp_v1, s1 = _v1_setup()
    ss.set_status(h, s1.strategy_id, ss.STRATEGY_ARCHIVED)   # archive the (previously approved) strategy
    assert s1.status == ss.STRATEGY_ARCHIVED and ss.was_ever_approved(s1)
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.ok and res.batch.derived_from_adapted_icp == icp_v1


# 11. QualifiedLeadBatch records the exact ICP identity used.
def test_records_exact_icp_identity():
    ws, h, b1, icp_v1, _ = _v1_setup()
    _approve_adapted(ws, h)                              # active now v2
    res = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.batch.derived_from_adapted_icp == icp_v1  # the exact pinned one, not active
    # and it matches a real approved version in this hypothesis
    assert any(idy.artifact_identity_str(v) == res.batch.derived_from_adapted_icp
               for v in h.approved_versions)


# 12. Provenance survives persistence round-trip (full chain).
def test_provenance_survives_roundtrip():
    ws, h, b1, icp_v1, _ = _v1_setup()
    r = qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    ref_chain = (r.batch.derived_from_lead_batch, r.batch.derived_from_search_strategy,
                 r.batch.derived_from_adapted_icp)
    ws2 = store.loads(store.dumps(ws))
    q = ws2.get_hypothesis(h.project_id).latest_qualified_batch()
    assert (q.derived_from_lead_batch, q.derived_from_search_strategy,
            q.derived_from_adapted_icp) == ref_chain
    assert q.derived_from_adapted_icp == icp_v1


# 13. Source artifacts remain immutable through qualification.
def test_source_artifacts_immutable():
    ws, h, b1, _, s1 = _v1_setup()
    lb_before = json.dumps(b1.to_dict())
    strat_before = json.dumps(s1.to_dict())
    icp_before = json.dumps(ap.get_active_approved_icp(h).to_dict())
    company_before = ws.company.to_json()
    qr.qualify_lead_batch(h, b1.batch_id, qualified_by="dana", client=sc.MockClient())
    assert json.dumps(h.list_lead_batches()[0].to_dict()) == lb_before
    assert json.dumps(next(s for s in h.search_strategies if s.strategy_id == s1.strategy_id).to_dict()) == strat_before
    assert json.dumps(ap.get_active_approved_icp(h).to_dict()) == icp_before
    assert ws.company.to_json() == company_before


# 14. Existing frozen qualification behavior remains unchanged.
def test_frozen_qualification_unchanged():
    import qualification_bridge as qb
    up = qb.context_from_uploaded("Plain", "We sell to FinTech CTOs.")
    r = qb.score_with_context([sc.normalize_lead({"company": "X", "title": "CTO"}, 0)], up,
                              client=sc.MockClient())
    assert r and r[0].icp == "Plain"
    assert idy.fingerprint_generated_icp  # identity module importable/unchanged shape
    src = (ROOT / "pipeline" / "qualification_run.py").read_text(encoding="utf-8")
    assert "context_from_approved_project" not in src   # now resolves the exact ICP, not the active one


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
