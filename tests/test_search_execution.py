"""Search Execution & Vayne integration tests (Sprint 12).

Proves the automatic route — Approved SearchStrategy -> SearchExecution -> (fake) Vayne -> CSV ->
existing vayne_adapter -> existing lead_import gate -> immutable LeadBatch -> qualification — behaves
deterministically, is idempotent (one completed execution yields at most one LeadBatch), never
bypasses lead_import, keeps the authoritative Search Strategy provenance, and never serializes
credentials. Uses a deterministic in-memory FakeVayneClient — NO real Vayne API calls. No pytest:

    ./.venv/bin/python tests/test_search_execution.py
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
import lead_batch as lb                 # noqa: E402
import search_execution as sx           # noqa: E402
import search_execution_service as sxs  # noqa: E402
import qualification_run as qr          # noqa: E402
import scoring as sc                    # noqa: E402
import workspace_store as store         # noqa: E402
from integrations import vayne_client as vc   # noqa: E402

_URL = "https://www.linkedin.com/sales/search/people?query=fintech-ctos"
_CSV = (b"Full Name,Job Title,Company,Location,LinkedIn URL\n"
        b"Jane Doe,CTO,FinCo,US,https://linkedin.com/in/jane\n"
        b"Bob Fox,VP Eng,LogiCo,DE,https://linkedin.com/in/bob\n")
_MALFORMED_CSV = b"\xff\xfe not a csv at all, no header row resembling leads"


# --- deterministic fake Vayne client (no HTTP; mirrors VayneClient's public surface) -------------

class FakeVayneClient:
    """In-memory stand-in for VayneClient. Configurable to exercise every branch. Records what it was
    asked to submit. Has NO domain/persistence/UI knowledge — exactly like the real client boundary."""

    def __init__(self, *, state=vc.STATE_FINISHED, csv=_CSV, submit_error=None,
                 status_error=None, download_error=None, job_id="order-1"):
        self.state = state
        self.csv = csv
        self.submit_error = submit_error
        self.status_error = status_error
        self.download_error = download_error
        self.job_id = job_id
        self.submitted = []          # list of (url, name, limit)
        self.status_calls = 0
        self.download_calls = 0

    def submit(self, sales_navigator_url, name, *, limit=0):
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append((sales_navigator_url, name, limit))
        return self.job_id

    def status(self, external_job_id):
        self.status_calls += 1
        if self.status_error is not None:
            raise self.status_error
        finished = self.state == vc.STATE_FINISHED
        return vc.VayneJobStatus(state=self.state, scraped=2, target=2, export_ready=finished)

    def download_csv(self, external_job_id):
        self.download_calls += 1
        if self.download_error is not None:
            raise self.download_error
        return self.csv


# --- fixtures (same shape as test_lead_import / test_qualification_lineage) -----------------------

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


def _approved_strategy(h):
    s = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy
    ss.set_status(h, s.strategy_id, ss.STRATEGY_REVIEWED)
    ss.set_status(h, s.strategy_id, ss.STRATEGY_APPROVED, approved_by="d")
    return s


def _setup(name="Healthcare"):
    """Returns (ws, hypothesis, approved_strategy)."""
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    h = _hypothesis_with_approved_icp(ws, name)
    s = _approved_strategy(h)
    return ws, h, s


def _submit(h, s, client, url=_URL, requested_by="dana"):
    return sxs.create_and_submit(h, s.strategy_id, url, requested_by=requested_by, client=client)


# =============================================================================
# URL validation (deterministic, filter-agnostic)
# =============================================================================

def test_url_validation_accepts_valid_sales_navigator_url():
    assert sx.validate_sales_navigator_url(_URL) == []


def test_url_validation_rejects_non_https_wrong_host_and_credentials():
    assert sx.validate_sales_navigator_url("") == ["A Sales Navigator search URL is required."]
    assert sx.validate_sales_navigator_url("http://www.linkedin.com/sales/search")   # non-https
    assert sx.validate_sales_navigator_url("https://evil.com/sales/search")          # wrong host
    assert sx.validate_sales_navigator_url("https://www.linkedin.com/feed/")         # not a sales path
    assert sx.validate_sales_navigator_url("https://u:p@www.linkedin.com/sales/search")  # creds
    assert sx.validate_sales_navigator_url("https://www.linkedin.com/sales/x?q=" + "a" * 5000)  # length


# 4. Invalid Sales Navigator URL blocks execution (persists nothing).
def test_invalid_url_blocks_execution():
    ws, h, s = _setup()
    fv = FakeVayneClient()
    r = _submit(h, s, fv, url="http://evil.com/x")
    assert not r.ok and "Invalid Sales Navigator URL" in r.error
    assert h.list_search_executions() == [] and fv.submitted == []


# =============================================================================
# Lineage / ownership / requester guards (before any Vayne call)
# =============================================================================

# 1. Approved strategy allows execution.
def test_approved_strategy_allows_execution():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_RUNNING)
    r = _submit(h, s, fv)
    assert r.ok and r.execution.status == sx.EXEC_SUBMITTED
    assert fv.submitted and fv.submitted[0][0] == _URL


# 2/3. Draft / Reviewed / Archived strategy blocks a new execution.
def test_non_approved_strategy_blocks_execution():
    ws, h, _ = _setup()
    fv = FakeVayneClient()
    draft = ss.generate_search_strategy(h, client=FakeDraftClient()).strategy       # Draft
    assert not _submit(h, draft, fv).ok
    ss.set_status(h, draft.strategy_id, ss.STRATEGY_REVIEWED)                        # Reviewed
    r = _submit(h, draft, fv)
    assert not r.ok and "Reviewed" in r.error
    appr = _approved_strategy(h)
    ss.set_status(h, appr.strategy_id, ss.STRATEGY_ARCHIVED)                         # Archived
    assert not _submit(h, appr, fv).ok
    assert h.list_search_executions() == [] and fv.submitted == []


# (missing / unknown strategy id also blocks)
def test_missing_or_unknown_strategy_blocks():
    ws, h, _ = _setup()
    fv = FakeVayneClient()
    assert not sxs.create_and_submit(h, "", _URL, requested_by="d", client=fv).ok
    assert not sxs.create_and_submit(h, "nope", _URL, requested_by="d", client=fv).ok
    assert h.list_search_executions() == []


# 5. Missing requester blocks execution.
def test_missing_requester_blocks():
    ws, h, s = _setup()
    fv = FakeVayneClient()
    assert not _submit(h, s, fv, requested_by="   ").ok
    assert h.list_search_executions() == [] and fv.submitted == []


# 6. Cross-hypothesis strategy blocks execution.
def test_cross_hypothesis_strategy_blocks():
    ws = ip.CompanyWorkspace(company=_company())
    gicp.generate_and_append(ws, client=FakeDraftClient())
    a = _hypothesis_with_approved_icp(ws, "Healthcare")
    b = _hypothesis_with_approved_icp(ws, "Logistics")
    s_b = _approved_strategy(b)                          # strategy owned by b
    fv = FakeVayneClient()
    r = sxs.create_and_submit(a, s_b.strategy_id, _URL, requested_by="d", client=fv)   # submitted to a
    assert not r.ok and a.list_search_executions() == [] and fv.submitted == []


# =============================================================================
# Submission + async status
# =============================================================================

# 7. Successful submission records the external job id (and no LeadBatch yet).
def test_successful_submission_records_job_id():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_RUNNING, job_id="order-42")
    r = _submit(h, s, fv)
    ex = r.execution
    assert ex.external_job_id == "order-42" and ex.status == sx.EXEC_SUBMITTED
    assert ex.sales_navigator_url == _URL                # raw URL preserved for audit
    assert h.list_lead_batches() == []


# 8. A running job persists status without creating a LeadBatch.
def test_running_job_persists_without_lead_batch():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_RUNNING)
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert r.ok and ex.status == sx.EXEC_RUNNING
    assert h.list_lead_batches() == [] and not ex.derived_lead_batch_id


# 9. A completed job retrieves the CSV and creates exactly one LeadBatch through lead_import.
def test_completed_job_creates_one_lead_batch():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED)
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert r.ok and ex.status == sx.EXEC_COMPLETED
    assert len(h.list_lead_batches()) == 1
    assert fv.download_calls == 1
    assert r.import_result is not None and r.import_result.ok


# 10. Refreshing a completed execution is idempotent (single derived_lead_batch_id, one batch).
def test_completed_refresh_is_idempotent():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED)
    ex = _submit(h, s, fv).execution
    sxs.refresh_execution(h, ex.execution_id, client=fv)
    first_batch_id = ex.derived_lead_batch_id
    for _ in range(3):                                   # repeated refreshes must not re-import
        r = sxs.refresh_execution(h, ex.execution_id, client=fv)
        assert r.ok
    assert len(h.list_lead_batches()) == 1
    assert ex.derived_lead_batch_id == first_batch_id
    assert fv.download_calls == 1                        # no second download after terminal


# 11. A failed Vayne job creates no LeadBatch.
def test_failed_job_creates_no_lead_batch():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FAILED)
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert not r.ok and ex.status == sx.EXEC_FAILED
    assert ex.failed_at and ex.failure_reason
    assert h.list_lead_batches() == [] and not ex.derived_lead_batch_id


# 12. A completed job that returns no CSV is refused (no LeadBatch).
def test_completed_without_csv_refused():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED, csv=b"")
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert not r.ok and ex.status == sx.EXEC_FAILED
    assert h.list_lead_batches() == []


# 13. Malformed CSV is refused via the existing adapter/gate (no LeadBatch).
def test_malformed_csv_refused_via_adapter():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED, csv=_MALFORMED_CSV)
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert not r.ok and ex.status == sx.EXEC_FAILED
    assert h.list_lead_batches() == []


# (transient download-not-ready keeps the execution refreshable, persists no batch)
def test_transient_download_keeps_execution_open():
    ws, h, s = _setup()
    transient = vc.VayneClientError("export still building", transient=True)
    fv = FakeVayneClient(state=vc.STATE_FINISHED, download_error=transient)
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert r.ok and r.transient and not ex.is_terminal   # not failed; retryable
    assert h.list_lead_batches() == []
    # once the export is ready, a later refresh completes exactly one batch
    fv.download_error = None
    r2 = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert r2.ok and ex.status == sx.EXEC_COMPLETED and len(h.list_lead_batches()) == 1


# (transient status error leaves the execution unchanged and retryable)
def test_transient_status_error_is_retryable():
    ws, h, s = _setup()
    transient = vc.VayneClientError("network blip", transient=True)
    fv = FakeVayneClient(state=vc.STATE_RUNNING, status_error=transient)
    ex = _submit(h, s, fv).execution
    r = sxs.refresh_execution(h, ex.execution_id, client=fv)
    assert not r.ok and r.transient and ex.status == sx.EXEC_SUBMITTED   # unchanged
    fv.status_error = None
    assert sxs.refresh_execution(h, ex.execution_id, client=fv).ok


# (transient submit error persists nothing and is retryable)
def test_transient_submit_error_persists_nothing():
    ws, h, s = _setup()
    transient = vc.VayneClientError("timeout", transient=True)
    fv = FakeVayneClient(submit_error=transient)
    r = _submit(h, s, fv)
    assert not r.ok and r.transient
    assert h.list_search_executions() == []


# =============================================================================
# Provenance / lineage integrity
# =============================================================================

# 14. The LeadBatch retains the authoritative Search Strategy provenance.
# 15. The SearchExecution records the derived LeadBatch id (additive execution provenance).
def test_lead_batch_keeps_strategy_provenance_and_execution_records_batch():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED)
    ex = _submit(h, s, fv).execution
    sxs.refresh_execution(h, ex.execution_id, client=fv)
    b = h.latest_lead_batch()
    assert b.derived_from_search_strategy == ss.search_strategy_reference(s)   # authoritative, unchanged
    assert b.search_strategy_id == s.strategy_id
    assert b.derived_from_search_execution == ex.execution_id                  # additive only
    assert ex.derived_lead_batch_id == b.batch_id


# =============================================================================
# Manual fallback + qualification parity (same adapter / gate / domain)
# =============================================================================

# 16. The manual CSV fallback still works and leaves execution provenance empty.
def test_manual_csv_fallback_still_works():
    ws, h, s = _setup()
    res = li.import_leads_from_strategy(h, s.strategy_id, _CSV, imported_by="dana")  # no execution id
    assert res.ok and res.batch.derived_from_search_strategy == ss.search_strategy_reference(s)
    assert res.batch.derived_from_search_execution == ""                            # manual => empty
    assert h.list_search_executions() == []


# 17. Qualification of an automatically-produced LeadBatch works (same engine, pinned ICP).
def test_qualification_of_automatic_batch_works():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED)
    ex = _submit(h, s, fv).execution
    sxs.refresh_execution(h, ex.execution_id, client=fv)
    b = h.latest_lead_batch()
    res = qr.qualify_lead_batch(h, b.batch_id, qualified_by="dana", client=sc.MockClient())
    assert res.ok
    assert res.batch.derived_from_search_strategy == b.derived_from_search_strategy


# =============================================================================
# Persistence
# =============================================================================

# 18. Persistence round-trip preserves execution state + lineage.
def test_persistence_roundtrip_preserves_state_and_lineage():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED)
    ex = _submit(h, s, fv).execution
    sxs.refresh_execution(h, ex.execution_id, client=fv)
    ws2 = store.loads(store.dumps(ws))
    hb = ws2.get_hypothesis(h.project_id)
    e2 = hb.latest_search_execution()
    assert e2.status == sx.EXEC_COMPLETED
    assert e2.external_job_id == ex.external_job_id
    assert e2.derived_from_search_strategy == ss.search_strategy_reference(s)
    assert e2.derived_lead_batch_id == ex.derived_lead_batch_id
    assert e2.sales_navigator_url == _URL
    assert len(e2.events) == len(ex.events)


# 19. Old JSON without search executions loads with a safe default.
def test_old_json_without_executions_loads():
    ws, h, s = _setup()
    env = store.to_envelope(ws)
    body = env["workspace"]
    projects = body.get("projects", body.get("hypotheses", []))
    for proj in projects:
        proj.pop("search_executions", None)              # simulate a pre-Sprint-12 envelope
    ws2 = store.from_envelope(env)
    hb = ws2.get_hypothesis(h.project_id)
    assert hb.list_search_executions() == []


# 20. Credentials are never serialized into the workspace JSON.
def test_credentials_never_serialized():
    ws, h, s = _setup()
    fv = FakeVayneClient(state=vc.STATE_FINISHED)
    ex = _submit(h, s, fv).execution
    sxs.refresh_execution(h, ex.execution_id, client=fv)
    blob = store.dumps(ws)
    low = blob.lower()
    for secret_marker in ("token", "authorization", "bearer", "api_key", "apikey", "password", "secret"):
        assert secret_marker not in low, f"{secret_marker!r} leaked into persisted workspace"


# =============================================================================
# Boundary discipline
# =============================================================================

# 21. The Vayne client boundary has no domain / persistence / Streamlit dependency.
def test_vayne_client_has_no_domain_or_persistence_dependency():
    src = (ROOT / "pipeline" / "integrations" / "vayne_client.py").read_text(encoding="utf-8")
    import re
    imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.MULTILINE)
    forbidden = {"streamlit", "lead_batch", "lead_import", "vayne_adapter", "search_execution",
                 "search_execution_service", "icp_project", "workspace_store", "search_strategy",
                 "adapted_icp", "icp_approval", "scoring", "qualification_run"}
    leaked = forbidden.intersection(imports)
    assert not leaked, f"vayne_client imports forbidden module(s): {leaked}"


# 22. Frozen modules are unchanged by Sprint 12 (they must not import the new layer).
def test_frozen_modules_do_not_depend_on_new_layer():
    import re
    frozen = ["scoring.py", "qualification_bridge.py", "iqs_validator.py", "icp_identity.py",
              "icp_adapter.py", "icp_approval.py", "generated_icp.py", "workspace_store.py",
              "vayne_adapter.py"]
    new_layer = {"search_execution", "search_execution_service", "vayne_client", "integrations"}
    for name in frozen:
        src = (ROOT / "pipeline" / name).read_text(encoding="utf-8")
        imports = set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.MULTILINE))
        assert not (new_layer & imports), f"{name} unexpectedly imports the Sprint 12 layer"


# 23. SearchExecution status transitions are forward-only; terminals are immutable.
def test_status_transitions_forward_only_and_terminal_immutable():
    e = sx.SearchExecution(status=sx.EXEC_DRAFT)
    e.advance(sx.EXEC_SUBMITTED)
    e.advance(sx.EXEC_RUNNING)
    e.advance(sx.EXEC_COMPLETED)
    assert e.is_terminal
    for bad in (sx.EXEC_RUNNING, sx.EXEC_SUBMITTED, sx.EXEC_FAILED, sx.EXEC_DRAFT):
        try:
            e.advance(bad)
            assert False, "terminal execution accepted a transition"
        except sx.SearchExecutionError:
            pass
    # illegal skip from Draft straight to Completed is rejected
    e2 = sx.SearchExecution(status=sx.EXEC_DRAFT)
    try:
        e2.advance(sx.EXEC_COMPLETED)
        assert False, "illegal transition accepted"
    except sx.SearchExecutionError:
        pass


# 24. A second intentional execution of the same URL is allowed (no global URL de-dup) and each
#     completed execution yields at most one LeadBatch.
def test_second_intentional_execution_allowed():
    ws, h, s = _setup()
    fv1 = FakeVayneClient(state=vc.STATE_FINISHED, job_id="order-A")
    ex1 = _submit(h, s, fv1).execution
    sxs.refresh_execution(h, ex1.execution_id, client=fv1)
    fv2 = FakeVayneClient(state=vc.STATE_FINISHED, job_id="order-B")
    ex2 = _submit(h, s, fv2).execution                   # same URL, deliberate second run
    sxs.refresh_execution(h, ex2.execution_id, client=fv2)
    assert ex1.execution_id != ex2.execution_id
    assert len(h.list_search_executions()) == 2
    assert len(h.list_lead_batches()) == 2               # one batch per completed execution
    assert ex1.derived_lead_batch_id != ex2.derived_lead_batch_id


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
