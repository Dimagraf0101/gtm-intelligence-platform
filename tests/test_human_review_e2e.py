"""Human Review end-to-end stabilization tests (Sprint 13c).

Executes the complete production workflow against real repository code:

    Import → Qualification → Pending → Approve/Reject/Skip → Save → Reload → Continue → Export

and guarantees that no review work can be lost or become inconsistent: decisions, comments, timestamps,
reviewer identity, statistics and the append-only history all survive a workspace round-trip; statistics
never go stale; exports stay canonical and deterministic. Offline, no LLM, no pytest:

    ./.venv/bin/python tests/test_human_review_e2e.py
"""
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import business_knowledge as bk           # noqa: E402
import icp_project as ip                  # noqa: E402
import general_icp as gicp                # noqa: E402
import adapted_icp as aicp                # noqa: E402
import strategy_review as sr              # noqa: E402
import icp_approval as ap                 # noqa: E402
import search_strategy as ss              # noqa: E402
import lead_import as li                  # noqa: E402
import qualification_run as qr            # noqa: E402
import scoring as sc                      # noqa: E402
import lead_review as lr                  # noqa: E402
import review_view as rv                  # noqa: E402
import review_export as rx                # noqa: E402
import export                             # noqa: E402
import workspace_store as store           # noqa: E402
from review_view import ReviewRow         # noqa: E402

_HDR = (b"First Name,Last Name,Job Title,Company,Location,LinkedIn URL,Company Website,"
        b"Company LinkedIn URL,Number of Connections,LinkedIn Founded Year,LinkedIn Specialities,"
        b"Job Started On,Employee Count,LinkedIn Industry,LinkedIn Employees\n")


def _csv(n: int) -> bytes:
    rows = [_HDR]
    for i in range(n):
        rows.append(
            f"F{i},L{i},CTO,Co{i},{'US' if i % 2 else 'DE'},https://linkedin.com/in/p{i},"
            f"https://co{i}.com,https://linkedin.com/company/co{i},{100 + i},2011,Spec,"
            f"2021-03,{10 + i},{'FinTech' if i % 3 else 'Logistics'},51-200\n".encode())
    return b"".join(rows)


class FakeDraftClient:
    model = "fake"

    def complete(self, system, user, *, structured=False):
        draft = {
            "business_context": {"description": "A", "value_proposition": "v",
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


def _pipeline(n=6):
    """Real chain up to a QualifiedLeadBatch. Returns (ws, hypothesis, strategy, lead_batch, qbatch)."""
    c = bk.BusinessKnowledge()
    for cat, attr, val in (("company", "overview", "A"), ("service", "name", "X"),
                           ("industry", "target", "FinTech"), ("buyer", "role", "CTO"),
                           ("geography", "region", "US"), ("company_size", "preference", "50-500"),
                           ("hard_exclusion_candidate", "rule", "Reject staffing")):
        c.add_item(cat, attr, val, status=bk.CONFIRMED, evidence_excerpt=val)
    ws = ip.CompanyWorkspace(company=c)
    gicp.generate_and_append(ws, client=FakeDraftClient())
    h = ws.create_hypothesis("H", "x")
    h.project_knowledge.add_item("industry", "target", "FinTech", status=bk.CONFIRMED,
                                 evidence_excerpt="FinTech")
    aicp.generate_adapted_icp(ws, h, client=FakeDraftClient())
    w = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    w.start_review()
    w.set_weight("Fit", 60)
    w.set_weight("Eng", 40)
    for cand in w.exclusion_candidates():
        w.activate_exclusion(cand["rule"])
    reviewed, _ = w.generate_reviewed_draft()
    ap.approve_icp_version(h, reviewed, approved_by="d",
                           acknowledged_warning_ids=[x for x, _ in ap.warnings_with_ids(reviewed)])
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="d")
    b = li.import_leads_from_strategy(h, s.strategy_id, _csv(n), imported_by="dana").batch
    q = qr.qualify_lead_batch(h, b.batch_id, qualified_by="dana", client=sc.MockClient()).batch
    return ws, h, s, b, q


# =============================================================================
# Task 1 — full workflow
# =============================================================================

def test_full_import_review_save_reload_export_flow():
    ws, h, s, b, q = _pipeline(6)
    rows = rv.build_review_rows(h, q)
    ids = [r.lead_id for r in rows]
    assert len(rows) == 6 and all(r.review_status == lr.REVIEW_PENDING for r in rows)

    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, reviewer_comment="great",
                       decided_by="dana")
    lr.record_decision(h, q.batch_id, ids[1], lr.REVIEW_REJECTED, rejection_reason="Wrong industry",
                       decided_by="dana")
    lr.record_decision(h, q.batch_id, ids[2], lr.REVIEW_SKIPPED, decided_by="dana")
    before = lr.review_statistics(len(rows), h.review_for_qualified_batch(q.batch_id))
    assert (before["approved"], before["rejected"], before["skipped"], before["pending"]) == (1, 1, 1, 3)

    ws2 = store.loads(store.dumps(ws))                       # save → reload
    h2 = ws2.get_hypothesis(h.project_id)
    q2 = h2.latest_qualified_batch()
    after = lr.review_statistics(len(rv.build_review_rows(h2, q2)),
                                 h2.review_for_qualified_batch(q2.batch_id))
    assert after == before                                   # statistics survive reload exactly

    lr.record_decision(h2, q2.batch_id, ids[3], lr.REVIEW_APPROVED, decided_by="dana2")
    rows2 = rv.build_review_rows(h2, q2)
    assert lr.review_statistics(len(rows2), h2.review_for_qualified_batch(q2.batch_id))["approved"] == 2

    assert len(rx.select_rows(rows2, rx.SCOPE_APPROVED)) == 2
    assert len(rx.select_rows(rows2, rx.SCOPE_SELECTED, selected_ids=[ids[5]])) == 1
    assert len(rx.select_rows(rows2, rx.SCOPE_ALL)) == 6
    assert isinstance(rx.to_workbook_bytes(rows2, icp_name="ICP"), bytes)


# =============================================================================
# Task 2 — persistence fidelity
# =============================================================================

def test_all_decision_facets_survive_reload():
    ws, h, s, b, q = _pipeline(3)
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, reviewer_comment="strong fit",
                       decided_by="alice")
    lr.record_decision(h, q.batch_id, ids[1], lr.REVIEW_REJECTED, rejection_reason="Wrong industry",
                       reviewer_comment="revisit later", decided_by="bob")
    original = h.review_for_qualified_batch(q.batch_id)
    stamp = original.decision_for(ids[0]).decided_at

    r2 = store.loads(store.dumps(ws)).get_hypothesis(h.project_id) \
        .review_for_qualified_batch(q.batch_id)
    d0, d1 = r2.decision_for(ids[0]), r2.decision_for(ids[1])
    assert d0.review_status == lr.REVIEW_APPROVED and d0.reviewer_comment == "strong fit"
    assert d0.decided_by == "alice" and d0.decided_at == stamp          # identity + timestamp intact
    assert d1.rejection_reason == "Wrong industry" and d1.reviewer_comment == "revisit later"
    assert d1.decided_by == "bob"
    assert r2.reviewed_by == original.reviewed_by and r2.created_at == original.created_at


def test_append_only_history_survives_reload():
    ws, h, s, b, q = _pipeline(2)
    lead = q.qualified[0].lead_id
    for status in (lr.REVIEW_APPROVED, lr.REVIEW_SKIPPED, lr.REVIEW_REJECTED):
        lr.record_decision(h, q.batch_id, lead, status, rejection_reason="final", decided_by="d")
    r2 = store.loads(store.dumps(ws)).get_hypothesis(h.project_id) \
        .review_for_qualified_batch(q.batch_id)
    assert [d.review_status for d in r2.history_for(lead)] == ["Approved", "Skipped", "Rejected"]
    assert r2.decision_for(lead).review_status == lr.REVIEW_REJECTED     # latest wins
    assert len(r2.decisions) == 3                                        # history never disappears


# =============================================================================
# Task 4 — impossible / edge states
# =============================================================================

def test_impossible_states_normalized_or_refused():
    # a rejection reason only survives on a Rejected decision
    for status in (lr.REVIEW_APPROVED, lr.REVIEW_SKIPPED, lr.REVIEW_PENDING):
        d = lr.LeadReviewDecision(lead_id="x", review_status=status, rejection_reason="Wrong industry")
        assert d.rejection_reason == "", status
    # Rejected without a reason is legitimate (reason is optional), not an error
    assert lr.LeadReviewDecision(lead_id="x", review_status=lr.REVIEW_REJECTED).rejection_reason == ""
    # a comment is valid on any status
    assert lr.LeadReviewDecision(lead_id="x", review_status=lr.REVIEW_SKIPPED,
                                 reviewer_comment="later").reviewer_comment == "later"
    # an unknown status is refused outright
    try:
        lr.LeadReviewDecision(lead_id="x", review_status="Maybe")
        assert False, "unknown status accepted"
    except lr.LeadReviewError:
        pass


def test_revert_to_pending_is_consistent():
    ws, h, s, b, q = _pipeline(3)
    lead = q.qualified[0].lead_id
    lr.record_decision(h, q.batch_id, lead, lr.REVIEW_APPROVED, decided_by="d")
    lr.record_decision(h, q.batch_id, lead, lr.REVIEW_PENDING, decided_by="d")   # revert
    st = lr.review_statistics(3, h.review_for_qualified_batch(q.batch_id))
    assert st["approved"] == 0 and st["pending"] == 3 and st["reviewed"] == 0
    row = next(r for r in rv.build_review_rows(h, q) if r.lead_id == lead)
    assert row.review_status == lr.REVIEW_PENDING and row.human_decision == ""


# =============================================================================
# Task 5 — statistics never stale
# =============================================================================

def test_statistics_after_single_bulk_change_and_reload():
    ws, h, s, b, q = _pipeline(6)
    rows = rv.build_review_rows(h, q)
    ids = [r.lead_id for r in rows]

    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, decided_by="d")
    review = h.review_for_qualified_batch(q.batch_id)
    assert lr.review_statistics(6, review)["progress_pct"] == 17          # 1/6

    lr.record_bulk(h, q.batch_id, ids[1:4], lr.REVIEW_REJECTED, rejection_reason="bulk",
                   decided_by="d")
    st = lr.review_statistics(6, review)
    assert (st["approved"], st["rejected"], st["reviewed"], st["remaining"]) == (1, 3, 4, 2)

    lr.record_decision(h, q.batch_id, ids[1], lr.REVIEW_APPROVED, decided_by="d")   # change decision
    st = lr.review_statistics(6, review)
    assert (st["approved"], st["rejected"], st["reviewed"]) == (2, 2, 4)   # no stale counters

    r2 = store.loads(store.dumps(ws)).get_hypothesis(h.project_id) \
        .review_for_qualified_batch(q.batch_id)
    assert lr.review_statistics(6, r2) == st                               # identical after reload


def test_multiple_review_batches_are_isolated():
    ws, h, s, b, q = _pipeline(4)
    b2 = li.import_leads_from_strategy(h, s.strategy_id, _csv(3), imported_by="dana").batch
    q2 = qr.qualify_lead_batch(h, b2.batch_id, qualified_by="dana", client=sc.MockClient()).batch

    lr.record_decision(h, q.batch_id, q.qualified[0].lead_id, lr.REVIEW_APPROVED, decided_by="d")
    lr.record_bulk(h, q2.batch_id, [x.lead_id for x in q2.qualified], lr.REVIEW_REJECTED,
                   decided_by="d")

    assert len(h.list_reviewed_batches()) == 2
    s1 = lr.review_statistics(4, h.review_for_qualified_batch(q.batch_id))
    s2 = lr.review_statistics(3, h.review_for_qualified_batch(q2.batch_id))
    assert (s1["approved"], s1["rejected"]) == (1, 0)
    assert (s2["approved"], s2["rejected"]) == (0, 3)
    # a decision in one batch never leaks into the other
    assert all(r.review_status == lr.REVIEW_REJECTED for r in rv.build_review_rows(h, q2))


def test_priority_distribution_matches_rows():
    ws, h, s, b, q = _pipeline(6)
    rows = rv.build_review_rows(h, q)
    dist = rv.priority_distribution(rows)
    assert sum(dist.values()) == len(rows)
    assert list(dist.keys())[:6] == rv.PRIORITY_ORDER          # canonical order preserved


# =============================================================================
# Task 6 — export determinism
# =============================================================================

def test_export_deterministic_no_dupes_no_missing():
    ws, h, s, b, q = _pipeline(6)
    rows = rv.build_review_rows(h, q)
    main = rx.build_main_rows(rows)
    assert list(main[0].keys()) == export.MAIN_COLUMNS
    assert list(rx.build_ai_rows(rows)[0].keys()) == export.AI_COLUMNS
    assert len(main) == len(rows)                                        # no missing rows
    assert len({r["LinkedIn URL"] for r in main}) == len(main)           # no duplicated rows
    assert [r["#"] for r in main] == list(range(1, len(main) + 1))       # deterministic ordinals
    assert rx.build_main_rows(rows) == main                              # stable across runs


def test_export_human_decision_matches_review_status():
    ws, h, s, b, q = _pipeline(4)
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, decided_by="d")
    lr.record_decision(h, q.batch_id, ids[1], lr.REVIEW_REJECTED, rejection_reason="no", decided_by="d")
    lr.record_decision(h, q.batch_id, ids[2], lr.REVIEW_SKIPPED, decided_by="d")
    expected = {"Approved": "Approved", "Rejected": "Rejected", "Skipped": "Skipped", "Pending": ""}
    for row in rx.build_main_rows(rv.build_review_rows(h, q)):
        assert row["Human Decision"] == expected[row["Review Status"]]


def test_export_after_reload_is_identical():
    ws, h, s, b, q = _pipeline(5)
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, reviewer_comment="c", decided_by="d")
    before = rx.build_main_rows(rv.build_review_rows(h, q))
    h2 = store.loads(store.dumps(ws)).get_hypothesis(h.project_id)
    after = rx.build_main_rows(rv.build_review_rows(h2, h2.latest_qualified_batch()))
    assert after == before                                               # byte-identical projection


# =============================================================================
# Task 8 — scale sanity (deterministic code only)
# =============================================================================

def _synthetic(n):
    pri = rv.PRIORITY_ORDER
    return [ReviewRow(lead_id=f"ld{i}", company=f"Co{i % 97}", contact=f"P{i}", title="CTO",
                      location="US" if i % 2 else "DE",
                      industry="FinTech" if i % 3 else "Logistics", company_size="51-200",
                      linkedin_url=f"https://l/{i}", company_website=f"https://c{i}.com",
                      score=i % 101, priority=pri[i % 6],
                      review_status=(lr.REVIEW_APPROVED if i % 4 == 0 else lr.REVIEW_PENDING))
            for i in range(n)]


def test_large_batches_sort_filter_export():
    for n in (100, 500, 1000, 5000):
        rows = _synthetic(n)
        t0 = time.perf_counter()
        ordered = rv.sort_rows(rows)
        filtered = rv.filter_rows(rows, priorities=["Priority 1"], score_min=10)
        searched = rv.filter_rows(rows, search="Co42")
        main = rx.build_main_rows(rows)
        elapsed = time.perf_counter() - t0
        assert len(ordered) == n and len(main) == n
        assert all(r.priority == "Priority 1" for r in filtered)
        assert all("co42" in r.company.lower() for r in searched)
        # generous bound — deterministic projection must stay far from interactive-latency limits
        assert elapsed < 5.0, f"{n} rows took {elapsed:.2f}s"


def test_large_review_artifact_round_trip():
    review = lr.ReviewedLeadBatch(hypothesis_id="p1", derived_from_qualified_batch="qb")
    for i in range(5000):
        review.record(f"ld{i}", lr.REVIEW_APPROVED, decided_by="d")
    restored = lr.ReviewedLeadBatch.from_dict(review.to_dict())
    assert len(restored.decisions) == 5000
    assert len(restored.current_decisions()) == 5000
    assert lr.review_statistics(5000, restored)["approved"] == 5000


# =============================================================================
# Task 3 / regression — UI addressability + single projection
# =============================================================================

def test_every_visible_lead_is_uniquely_addressable():
    """Regression (Sprint 13c): leads sharing company/contact/priority/status must remain individually
    selectable. A label built without the row ordinal collapses them and makes a lead unreviewable."""
    rows = [ReviewRow(lead_id="ld1", company="Acme", contact="", priority="Priority 1"),
            ReviewRow(lead_id="ld2", company="Acme", contact="", priority="Priority 1")]
    labels = {f"{i + 1}. {r.company or '—'} · {r.contact or '—'} · {r.priority} ({r.review_status})": i
              for i, r in enumerate(rows)}
    assert len(labels) == len(rows)                                       # every lead addressable
    src = (ROOT / "pages" / "11_Human_Review.py").read_text(encoding="utf-8")
    assert "{i + 1}." in src, "details selector must include the row ordinal to stay unique"


def test_page_does_not_join_domain_objects_itself():
    """ReviewRow must remain the ONLY projection: the page may not rebuild the Lead/QualifiedLead/Review
    join. (A read-only lineage caption referencing an id is fine.)"""
    src = (ROOT / "pages" / "11_Human_Review.py").read_text(encoding="utf-8")
    for forbidden in ("lead_by_id", ".current_decisions(", ".decision_for(", "for l in ", ".leads}"):
        assert forbidden not in src, f"page performs its own join: {forbidden}"
    assert "rv.build_review_rows(" in src


def test_review_never_mutates_sources_across_full_flow():
    ws, h, s, b, q = _pipeline(4)
    lead_snap = json.dumps(b.to_dict(), sort_keys=True)
    qual_snap = json.dumps(q.to_dict(), sort_keys=True)
    ids = [x.lead_id for x in q.qualified]
    lr.record_bulk(h, q.batch_id, ids, lr.REVIEW_APPROVED, decided_by="d")
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_REJECTED, rejection_reason="x", decided_by="d")
    rv.build_review_rows(h, q)
    rx.build_main_rows(rv.build_review_rows(h, q))
    assert json.dumps(b.to_dict(), sort_keys=True) == lead_snap
    assert json.dumps(q.to_dict(), sort_keys=True) == qual_snap


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
