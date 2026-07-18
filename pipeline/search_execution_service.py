"""Search Execution application service (Sprint 12).

Thin orchestration between an Approved Search Strategy and a persisted LeadBatch via the Vayne route:

    Approved SearchStrategy → SearchExecution → VayneClient → CSV → vayne_adapter → lead_import → LeadBatch

It validates lineage/ownership/URL/requester, drives the injected Vayne client (submit / refresh /
download), and — on completion — imports the CSV through the **existing** ``lead_import`` gate (which
re-validates the Search Strategy approval + ownership and stamps authoritative provenance). It never
constructs or persists a LeadBatch itself, never duplicates CSV mapping, and never bypasses
``lead_import``. Idempotent: one completed execution yields at most one LeadBatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import search_execution as sx
import search_strategy as ss
import lead_import as li
import lead_batch as lb
from integrations import vayne_client as vc


@dataclass
class SearchExecutionResult:
    ok: bool = False
    error: str = ""
    transient: bool = False                     # True => a retry (Refresh) may succeed
    execution: Optional[sx.SearchExecution] = None
    import_result: Optional[li.LeadImportResult] = None

    def summary(self) -> dict:
        ex = self.execution
        return {
            "ok": self.ok, "error": self.error, "transient": self.transient,
            "execution_id": getattr(ex, "execution_id", ""),
            "status": getattr(ex, "status", ""),
            "external_job_id": getattr(ex, "external_job_id", ""),
            "derived_lead_batch_id": getattr(ex, "derived_lead_batch_id", ""),
        }


def _resolve_approved_strategy(hypothesis, strategy_id: str):
    """(strategy, error). A NEW execution requires the strategy to be **currently Approved**."""
    if not (strategy_id or "").strip():
        return None, "No Search Strategy was provided."
    strategy = next((s for s in getattr(hypothesis, "search_strategies", [])
                     if s.strategy_id == strategy_id), None)
    if strategy is None:
        return None, "That Search Strategy does not exist for this hypothesis."
    if strategy.hypothesis_id != hypothesis.project_id:
        return None, "That Search Strategy belongs to a different hypothesis."
    if strategy.status != ss.STRATEGY_APPROVED:
        return None, (f"The Search Strategy is {strategy.status}, not Approved. Approve it before "
                      "starting a new search execution.")
    return strategy, ""


def create_and_submit(hypothesis, strategy_id: str, sales_navigator_url: str, *,
                      requested_by: str = "", client=None, limit: int = 0) -> SearchExecutionResult:
    """Create a SearchExecution for an Approved Search Strategy and submit the Sales Navigator URL to
    the lead source. Refuses deterministically on lineage/ownership/URL/requester violations. On a
    transient submit error nothing is persisted and the caller may retry."""
    if not (requested_by or "").strip():
        return SearchExecutionResult(ok=False, error="A requester name is required.")
    strategy, err = _resolve_approved_strategy(hypothesis, strategy_id)
    if err:
        return SearchExecutionResult(ok=False, error=err)
    url_issues = sx.validate_sales_navigator_url(sales_navigator_url)
    if url_issues:
        return SearchExecutionResult(ok=False, error="Invalid Sales Navigator URL: "
                                     + "; ".join(url_issues))

    execution = sx.SearchExecution(
        hypothesis_id=hypothesis.project_id,
        derived_from_search_strategy=ss.search_strategy_reference(strategy),
        source=lb.SOURCE_VAYNE_SALESNAV, sales_navigator_url=sales_navigator_url.strip(),
        requested_by=requested_by.strip())

    client = client if client is not None else vc.VayneClient()
    try:
        job_id = client.submit(execution.sales_navigator_url,
                               name=f"{hypothesis.name or hypothesis.project_id} — {strategy.strategy_id}",
                               limit=limit)
    except vc.VayneClientError as exc:
        # nothing persisted on failure to submit; a transient error may be retried by re-submitting
        return SearchExecutionResult(ok=False, error=f"Vayne submission failed: {exc}",
                                     transient=getattr(exc, "transient", False))

    execution.external_job_id = str(job_id)
    execution.advance(sx.EXEC_SUBMITTED, note=f"submitted (job {job_id})")
    hypothesis.search_executions.append(execution)      # append-only
    hypothesis.touch()
    return SearchExecutionResult(ok=True, execution=execution)


def refresh_execution(hypothesis, execution_id: str, *, client=None) -> SearchExecutionResult:
    """Refresh an execution's status. On completion, download the CSV and import ONE LeadBatch through
    the existing lead_import gate, recording ``derived_lead_batch_id``. Idempotent: a terminal
    execution (or one that already produced a batch) is returned unchanged. Transient errors leave the
    execution unchanged so the user can refresh again."""
    execution = next((e for e in getattr(hypothesis, "search_executions", [])
                      if e.execution_id == execution_id), None)
    if execution is None:
        return SearchExecutionResult(ok=False, error="Search execution not found for this hypothesis.")
    if execution.is_terminal:
        return SearchExecutionResult(ok=True, execution=execution)     # idempotent: nothing to do
    if not execution.external_job_id:
        return SearchExecutionResult(ok=False, error="Execution has no external job id to refresh.")

    client = client if client is not None else vc.VayneClient()
    try:
        job = client.status(execution.external_job_id)
    except vc.VayneClientError as exc:
        return SearchExecutionResult(ok=False, execution=execution,
                                     error=f"Could not read Vayne status: {exc}",
                                     transient=getattr(exc, "transient", True))

    if job.state == vc.STATE_FAILED:
        execution.advance(sx.EXEC_FAILED, failure_reason="Vayne scraping job failed.")
        hypothesis.touch()
        return SearchExecutionResult(ok=False, execution=execution, error="Vayne scraping job failed.")

    if job.state != vc.STATE_FINISHED:
        if execution.status == sx.EXEC_SUBMITTED:
            execution.advance(sx.EXEC_RUNNING, note=f"scraped {job.scraped}")
            hypothesis.touch()
        return SearchExecutionResult(ok=True, execution=execution)     # still running; retry later

    # finished scraping -> download the CSV (transient if the export isn't ready yet)
    try:
        csv_bytes = client.download_csv(execution.external_job_id)
    except vc.VayneClientError as exc:
        if getattr(exc, "transient", False):
            return SearchExecutionResult(ok=True, execution=execution,
                                         error=f"Result not ready yet: {exc}", transient=True)
        execution.advance(sx.EXEC_FAILED, failure_reason=str(exc))
        hypothesis.touch()
        return SearchExecutionResult(ok=False, execution=execution, error=str(exc))
    if not csv_bytes:
        execution.advance(sx.EXEC_FAILED, failure_reason="Vayne reported completed but returned no CSV.")
        hypothesis.touch()
        return SearchExecutionResult(ok=False, execution=execution,
                                     error="Vayne returned no CSV for the completed job.")

    # import through the EXISTING lineage gate (re-validates strategy approval + ownership)
    _, sid, _ = ss.parse_search_strategy_reference(execution.derived_from_search_strategy)
    imp = li.import_leads_from_strategy(hypothesis, sid, csv_bytes,
                                        imported_by=execution.requested_by,
                                        search_execution_id=execution.execution_id)
    if not imp.ok:
        execution.advance(sx.EXEC_FAILED, failure_reason=imp.error)
        hypothesis.touch()
        return SearchExecutionResult(ok=False, execution=execution, error=imp.error, import_result=imp)

    execution.derived_lead_batch_id = imp.batch.batch_id      # set-once
    execution.advance(sx.EXEC_COMPLETED, note=f"lead batch {imp.batch.batch_id}")
    hypothesis.touch()
    return SearchExecutionResult(ok=True, execution=execution, import_result=imp)
