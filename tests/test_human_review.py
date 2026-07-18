"""Human Review tests (Sprint 13b).

Proves the append-only human-decision layer: immutable decisions, deterministic statistics, a single
view-model projection joining Lead + QualifiedLead + decision, deterministic sort/filter/search, and a
canonical-schema export that reconstructs nothing — all without mutating any source artifact. Offline,
no LLM, no pytest:

    ./.venv/bin/python tests/test_human_review.py
"""
import sys
import json
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

_CSV = (
    b"First Name,Last Name,Job Title,Company,Location,LinkedIn URL,Company Website,"
    b"Company LinkedIn URL,Number of Connections,LinkedIn Founded Year,LinkedIn Specialities,"
    b"Job Started On,Employee Count,LinkedIn Industry,LinkedIn Employees\n"
    b"Jane,Doe,CTO,FinCo,US,https://linkedin.com/in/jane,https://finco.com,"
    b"https://linkedin.com/company/finco,873,2011,Payments,2021-03,240,FinTech,51-200\n"
    b"Bob,Fox,VP Eng,LogiCo,DE,https://linkedin.com/in/bob,https://logico.de,"
    b"https://linkedin.com/company/logico,412,2015,Logistics,2019-01,90,Logistics,11-50\n")


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


def _setup():
    """Full chain: company → General ICP → hypothesis → Adapted ICP → Approved Strategy →
    LeadBatch → QualifiedLeadBatch. Returns (ws, hypothesis, lead_batch, qualified_batch)."""
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
    srw = sr.StrategyReviewWorkspace(ws.company, h, draft_client=FakeDraftClient())
    srw.start_review()
    srw.set_weight("Fit", 60)
    srw.set_weight("Eng", 40)
    for cand in srw.exclusion_candidates():
        srw.activate_exclusion(cand["rule"])
    reviewed, _ = srw.generate_reviewed_draft()
    ap.approve_icp_version(h, reviewed, approved_by="d",
                           acknowledged_warning_ids=[w for w, _ in ap.warnings_with_ids(reviewed)])
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="d")
    batch = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="dana").batch
    q = qr.qualify_lead_batch(h, batch.batch_id, qualified_by="dana", client=sc.MockClient()).batch
    return ws, h, batch, q


# =============================================================================
# Review domain
# =============================================================================

def test_decision_is_immutable():
    d = lr.LeadReviewDecision(lead_id="ld1", review_status=lr.REVIEW_APPROVED)
    try:
        d.review_status = lr.REVIEW_REJECTED
        assert False, "decision was mutable"
    except Exception:
        pass


def test_only_allowed_statuses():
    for status in lr.REVIEW_STATUSES:
        assert lr.LeadReviewDecision(lead_id="x", review_status=status).review_status == status
    try:
        lr.LeadReviewDecision(lead_id="x", review_status="Maybe")
        assert False, "unknown status accepted"
    except lr.LeadReviewError:
        pass


def test_incompatible_fields_normalized():
    # a rejection reason is meaningless unless the lead is Rejected -> cleared deterministically
    approved = lr.LeadReviewDecision(lead_id="x", review_status=lr.REVIEW_APPROVED,
                                     rejection_reason="Wrong industry")
    assert approved.rejection_reason == ""
    skipped = lr.LeadReviewDecision(lead_id="x", review_status=lr.REVIEW_SKIPPED,
                                    rejection_reason="Wrong industry")
    assert skipped.rejection_reason == ""
    rejected = lr.LeadReviewDecision(lead_id="x", review_status=lr.REVIEW_REJECTED,
                                     rejection_reason=" Wrong industry ")
    assert rejected.rejection_reason == "Wrong industry"


def test_human_decision_is_derived_not_stored():
    assert lr.human_decision_for(lr.REVIEW_PENDING) == ""
    assert lr.human_decision_for(lr.REVIEW_APPROVED) == "Approved"
    assert lr.human_decision_for(lr.REVIEW_REJECTED) == "Rejected"
    assert lr.human_decision_for(lr.REVIEW_SKIPPED) == "Skipped"
    d = lr.LeadReviewDecision(lead_id="x", review_status=lr.REVIEW_APPROVED)
    assert "human_decision" not in d.to_dict()          # never persisted
    assert d.human_decision == "Approved"               # always derived


def test_append_only_latest_wins_with_history():
    ws, h, b, q = _setup()
    lead_id = q.qualified[0].lead_id
    lr.record_decision(h, q.batch_id, lead_id, lr.REVIEW_APPROVED, decided_by="dana")
    lr.record_decision(h, q.batch_id, lead_id, lr.REVIEW_REJECTED, rejection_reason="changed mind",
                       decided_by="dana")
    review = h.review_for_qualified_batch(q.batch_id)
    assert len(review.decisions) == 2                                   # nothing overwritten
    assert review.decision_for(lead_id).review_status == lr.REVIEW_REJECTED   # latest wins
    assert [d.review_status for d in review.history_for(lead_id)] == ["Approved", "Rejected"]


def test_review_statistics_deterministic():
    ws, h, b, q = _setup()
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, decided_by="d")
    review = h.review_for_qualified_batch(q.batch_id)
    s = lr.review_statistics(len(ids), review)
    assert s["total"] == 2 and s["approved"] == 1 and s["pending"] == 1
    assert s["reviewed"] == 1 and s["remaining"] == 1 and s["progress_pct"] == 50


def test_one_review_artifact_per_qualified_batch():
    ws, h, b, q = _setup()
    a = lr.get_or_create_review(h, q.batch_id, reviewed_by="d")
    again = lr.get_or_create_review(h, q.batch_id, reviewed_by="d")
    assert a is again and len(h.list_reviewed_batches()) == 1


def test_review_never_mutates_lead_or_qualified_lead():
    ws, h, b, q = _setup()
    lead_snapshot = json.dumps(b.to_dict(), sort_keys=True)
    qual_snapshot = json.dumps(q.to_dict(), sort_keys=True)
    for x in q.qualified:
        lr.record_decision(h, q.batch_id, x.lead_id, lr.REVIEW_REJECTED,
                           rejection_reason="no", decided_by="d")
    assert json.dumps(b.to_dict(), sort_keys=True) == lead_snapshot      # LeadBatch untouched
    assert json.dumps(q.to_dict(), sort_keys=True) == qual_snapshot      # QualifiedLeadBatch untouched


# =============================================================================
# Persistence
# =============================================================================

def test_review_round_trip_and_workspace_persistence():
    ws, h, b, q = _setup()
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, reviewer_comment="good",
                       decided_by="dana")
    lr.record_decision(h, q.batch_id, ids[1], lr.REVIEW_REJECTED, rejection_reason="Wrong industry",
                       decided_by="dana")
    review = h.review_for_qualified_batch(q.batch_id)
    assert lr.ReviewedLeadBatch.from_dict(review.to_dict()).to_dict() == review.to_dict()

    ws2 = store.loads(store.dumps(ws))
    h2 = ws2.get_hypothesis(h.project_id)
    r2 = h2.review_for_qualified_batch(q.batch_id)
    assert r2 is not None and len(r2.decisions) == 2
    assert r2.decision_for(ids[0]).review_status == lr.REVIEW_APPROVED
    assert r2.decision_for(ids[1]).rejection_reason == "Wrong industry"


def test_decisions_immutable_after_reload():
    ws, h, b, q = _setup()
    lr.record_decision(h, q.batch_id, q.qualified[0].lead_id, lr.REVIEW_APPROVED, decided_by="d")
    ws2 = store.loads(store.dumps(ws))
    r2 = ws2.get_hypothesis(h.project_id).review_for_qualified_batch(q.batch_id)
    try:
        r2.decisions[0].review_status = "Rejected"
        assert False, "decision mutable after reload"
    except Exception:
        pass


def test_old_workspace_without_reviews_loads():
    ws, h, b, q = _setup()
    env = store.to_envelope(ws)
    body = env["workspace"]
    for proj in body.get("projects", body.get("hypotheses", [])):
        proj.pop("reviewed_batches", None)                 # simulate a pre-13b envelope
    ws2 = store.from_envelope(env)
    h2 = ws2.get_hypothesis(h.project_id)
    assert h2.list_reviewed_batches() == []
    assert h2.review_for_qualified_batch(q.batch_id) is None


# =============================================================================
# View model
# =============================================================================

def test_view_model_joins_and_defaults_to_pending():
    ws, h, b, q = _setup()
    rows = rv.build_review_rows(h, q)
    assert len(rows) == 2
    assert all(r.review_status == lr.REVIEW_PENDING for r in rows)      # no decision yet
    fin = next(r for r in rows if r.company == "FinCo")
    # canonical BUSINESS fields (typed core + attributes)
    assert fin.contact == "Jane Doe" and fin.first_name == "Jane" and fin.last_name == "Doe"
    assert fin.title == "CTO" and fin.location == "US" and fin.industry == "FinTech"
    assert fin.company_size == "51-200" and fin.company_website == "https://finco.com"
    assert fin.company_linkedin_url == "https://linkedin.com/company/finco"
    assert fin.connections == "873" and fin.founded_year == "2011"
    assert fin.job_started == "2021-03" and fin.employee_count == "240"
    assert fin.specialities == "Payments"
    # canonical AI fields
    assert fin.priority and isinstance(fin.score, int) and fin.score_breakdown
    assert fin.internal_category != "" and fin.confidence != ""


def test_view_model_reflects_decision_and_derives_human_decision():
    ws, h, b, q = _setup()
    lead_id = q.qualified[0].lead_id
    lr.record_decision(h, q.batch_id, lead_id, lr.REVIEW_APPROVED, reviewer_comment="ok",
                       decided_by="d")
    row = next(r for r in rv.build_review_rows(h, q) if r.lead_id == lead_id)
    assert row.review_status == lr.REVIEW_APPROVED
    assert row.human_decision == "Approved"            # derived, cannot drift
    assert row.reviewer_comment == "ok"


def test_view_model_does_not_mutate_sources():
    ws, h, b, q = _setup()
    before_lead = json.dumps(b.to_dict(), sort_keys=True)
    before_qual = json.dumps(q.to_dict(), sort_keys=True)
    rv.build_review_rows(h, q)
    assert json.dumps(b.to_dict(), sort_keys=True) == before_lead
    assert json.dumps(q.to_dict(), sort_keys=True) == before_qual


# =============================================================================
# Sorting / filtering / search
# =============================================================================

def test_sort_by_priority_then_score_desc():
    from review_view import ReviewRow
    rows = [ReviewRow(lead_id="a", company="A", priority="Priority 3", score=90),
            ReviewRow(lead_id="b", company="B", priority="Priority 1", score=50),
            ReviewRow(lead_id="c", company="C", priority="Priority 1", score=95),
            ReviewRow(lead_id="d", company="D", priority="Disqualified", score=99)]
    assert [r.lead_id for r in rv.sort_rows(rows)] == ["c", "b", "a", "d"]


def test_filters_and_search():
    ws, h, b, q = _setup()
    rows = rv.build_review_rows(h, q)
    assert len(rv.filter_rows(rows, search="finco")) == 1                 # company search
    assert len(rv.filter_rows(rows, search="bob")) == 1                   # contact search
    assert len(rv.filter_rows(rows, industries=["FinTech"])) == 1
    assert len(rv.filter_rows(rows, company_sizes=["11-50"])) == 1
    assert len(rv.filter_rows(rows, geographies=["DE"])) == 1
    assert len(rv.filter_rows(rows, statuses=[lr.REVIEW_PENDING])) == 2   # all pending
    assert len(rv.filter_rows(rows, statuses=[lr.REVIEW_APPROVED])) == 0
    assert len(rv.filter_rows(rows, score_min=0, score_max=100)) == 2
    assert len(rv.filter_rows(rows, score_min=101)) == 0


def test_bulk_applies_only_to_given_ids():
    ws, h, b, q = _setup()
    rows = rv.build_review_rows(h, q)
    target = [rows[0].lead_id]
    n = lr.record_bulk(h, q.batch_id, target, lr.REVIEW_APPROVED, decided_by="d")
    assert n == 1
    after = {r.lead_id: r.review_status for r in rv.build_review_rows(h, q)}
    assert after[rows[0].lead_id] == lr.REVIEW_APPROVED
    assert after[rows[1].lead_id] == lr.REVIEW_PENDING                    # untouched


# =============================================================================
# Export
# =============================================================================

def test_export_canonical_columns_and_order_unchanged():
    ws, h, b, q = _setup()
    rows = rv.build_review_rows(h, q)
    main = rx.build_main_rows(rows)
    ai = rx.build_ai_rows(rows)
    assert list(main[0].keys()) == export.MAIN_COLUMNS
    assert list(ai[0].keys()) == export.AI_COLUMNS
    assert list(rx.to_main_dataframe(rows).columns) == export.MAIN_COLUMNS


def test_export_maps_business_ai_and_review_fields():
    ws, h, b, q = _setup()
    lead_id = next(x.lead_id for x in q.qualified)
    lr.record_decision(h, q.batch_id, lead_id, lr.REVIEW_APPROVED, reviewer_comment="great",
                       decided_by="d")
    rows = rv.build_review_rows(h, q)
    row = next(m for m in rx.build_main_rows(rows) if m["Company"] in ("FinCo", "LogiCo"))
    fin = next(m for m in rx.build_main_rows(rows) if m["Company"] == "FinCo")
    # business
    assert fin["First Name"] == "Jane" and fin["Last Name"] == "Doe"
    assert fin["Company Website"] == "https://finco.com"
    assert fin["Company LinkedIn URL"] == "https://linkedin.com/company/finco"
    assert fin["Number of Connections"] == "873" and fin["LinkedIn Founded Year"] == "2011"
    assert fin["LinkedIn Employees"] == "51-200" and fin["LinkedIn Specialities"] == "Payments"
    assert fin["Job Started"] == "2021-03"
    # AI
    assert isinstance(fin["Lead Score"], int) and fin["Priority"] and fin["Score Breakdown"]
    # review (Human Decision derived from Review Status)
    approved = next(m for m in rx.build_main_rows(rows) if m["Review Status"] == "Approved")
    assert approved["Human Decision"] == "Approved" and approved["Reviewer Comment"] == "great"


def test_export_scopes_approved_selected_all():
    ws, h, b, q = _setup()
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, decided_by="d")
    rows = rv.build_review_rows(h, q)
    assert len(rx.select_rows(rows, rx.SCOPE_APPROVED)) == 1              # default scope
    assert len(rx.select_rows(rows, rx.SCOPE_ALL)) == 2
    assert len(rx.select_rows(rows, rx.SCOPE_SELECTED, selected_ids=[ids[1]])) == 1


def test_export_invents_nothing_for_missing_values():
    ws, h, b, q = _setup()
    # a minimal CSV with no business attributes -> those export cells must be empty, not invented
    minimal = b"Company,Job Title,LinkedIn URL\nAcme,CTO,https://linkedin.com/in/x\n"
    s = h.latest_approved_search_strategy()
    batch2 = li.import_leads_from_strategy(h, s.strategy_id, minimal, imported_by="d").batch
    q2 = qr.qualify_lead_batch(h, batch2.batch_id, qualified_by="d", client=sc.MockClient()).batch
    row = rx.build_main_rows(rv.build_review_rows(h, q2))[0]
    for empty_col in ("First Name", "Last Name", "Job Started", "Number of Connections",
                      "Company LinkedIn URL", "LinkedIn Founded Year", "LinkedIn Specialities"):
        assert row[empty_col] == "", f"{empty_col} was invented"
    assert row["Company"] == "Acme"                                       # real data preserved


def test_export_workbook_and_csv_bytes():
    ws, h, b, q = _setup()
    lr.record_decision(h, q.batch_id, q.qualified[0].lead_id, lr.REVIEW_APPROVED, decided_by="d")
    rows = rx.select_rows(rv.build_review_rows(h, q), rx.SCOPE_APPROVED)
    xlsx = rx.to_workbook_bytes(rows, icp_name="ICP")
    assert isinstance(xlsx, bytes) and len(xlsx) > 2000
    csv_bytes = rx.to_main_csv_bytes(rows)
    header = csv_bytes.decode("utf-8-sig").splitlines()[0].split(",")
    assert header[:3] == ["#", "Lead Score", "Priority"]                  # canonical order preserved


def test_summary_rows_carry_real_review_counts():
    ws, h, b, q = _setup()
    ids = [x.lead_id for x in q.qualified]
    lr.record_decision(h, q.batch_id, ids[0], lr.REVIEW_APPROVED, decided_by="d")
    rows = rv.build_review_rows(h, q)
    summary = dict(rx.build_summary_rows(rows, "c", "icp", "now"))
    assert summary["Review — Approved"] == 1 and summary["Review — Pending"] == 1


# =============================================================================
# Application / regression
# =============================================================================

def test_page_exists_and_compiles():
    import py_compile
    page = ROOT / "pages" / "11_Human_Review.py"
    assert page.exists()
    py_compile.compile(str(page), doraise=True)


def test_empty_and_missing_states():
    ws, h, b, q = _setup()
    assert rv.build_review_rows(h, None) == []                            # no batch selected
    empty = type(q)(hypothesis_id=h.project_id, derived_from_lead_batch=b.batch_id, qualified=[])
    assert rv.build_review_rows(h, empty) == []                           # batch with zero leads
    assert lr.review_statistics(0, None)["progress_pct"] == 0             # no reviews yet


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
