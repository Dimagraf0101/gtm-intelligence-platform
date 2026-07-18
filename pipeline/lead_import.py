"""Lead import application service (Sprint 10.1).

The application layer that enforces lead-acquisition **lineage**: a persisted ``LeadBatch`` must be
derived from exactly one **Approved** ``SearchStrategy`` owned by the **same** MarketHypothesis. It
resolves and validates the strategy (existence, ownership, Approved status), a non-empty importer, and
valid CSV — then calls the pure ``vayne_adapter`` to map rows to domain leads, builds the ``LeadBatch``
with immutable provenance, and appends it.

Boundary: the CSV adapter parses/maps only and never resolves hypothesis ownership or strategy
approval; this service owns that. Deterministic Python only — no LLM, no scoring, no qualification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import lead_batch as lb
import vayne_adapter as va
import search_strategy as ss


@dataclass
class LeadImportResult:
    """Deterministic, UI-facing outcome of a lineage-checked lead import."""
    ok: bool = False
    error: str = ""
    batch: Optional[lb.LeadBatch] = None
    warnings: list = field(default_factory=list)
    derived_from_search_strategy: str = ""

    def summary(self) -> dict:
        return {
            "ok": self.ok, "error": self.error, "warnings": list(self.warnings),
            "derived_from_search_strategy": self.derived_from_search_strategy,
            "stats": dict(self.batch.stats) if self.batch is not None else {},
            "batch_id": self.batch.batch_id if self.batch is not None else "",
        }


def _resolve_approved_strategy(hypothesis, strategy_id: str):
    """Return (strategy, error). Refuses deterministically for missing / unknown / cross-hypothesis /
    not-Approved strategies. Ownership is proven by lookup within THIS hypothesis's own list."""
    if not (strategy_id or "").strip():
        return None, "No Search Strategy was provided; a persisted Lead Batch must derive from one."
    strategy = next((s for s in getattr(hypothesis, "search_strategies", [])
                     if s.strategy_id == strategy_id), None)
    if strategy is None:
        return None, ("That Search Strategy does not exist for this hypothesis (unknown id or it "
                      "belongs to another hypothesis).")
    if strategy.hypothesis_id != hypothesis.project_id:                 # defensive ownership check
        return None, "That Search Strategy belongs to a different hypothesis."
    if strategy.status != ss.STRATEGY_APPROVED:
        return None, (f"The Search Strategy is {strategy.status}, not Approved. Approve it before "
                      "importing leads against it.")
    return strategy, ""


def import_leads_from_strategy(hypothesis, strategy_id: str, csv_bytes, *,
                               imported_by: str = "", source_label: str = "",
                               search_execution_id: str = "") -> LeadImportResult:
    """Persist a NEW immutable Lead Batch for ``hypothesis``, derived from the Approved Search Strategy
    ``strategy_id``. Refuses (persists nothing) on any lineage / importer / CSV violation. Re-importing
    from the same strategy always appends a new batch; a previous batch is never mutated.

    ``search_execution_id`` (Sprint 12) is optional operational provenance recorded on the batch when
    the CSV came from an automated Search Execution; it never weakens the authoritative Search Strategy
    provenance. The manual-CSV fallback leaves it empty."""
    if not (imported_by or "").strip():
        return LeadImportResult(ok=False, error="An importer name is required to persist a batch.")

    strategy, err = _resolve_approved_strategy(hypothesis, strategy_id)
    if err:
        return LeadImportResult(ok=False, error=err)

    try:
        leads = va.leads_from_csv(csv_bytes)                            # pure ACL: parse + map only
    except va.VayneImportError as exc:
        return LeadImportResult(ok=False, error=str(exc))

    reference = ss.search_strategy_reference(strategy)                  # via the strategy's authority
    source = lb.LeadSource(kind=lb.SOURCE_VAYNE_SALESNAV, label=source_label)
    batch = lb.build_lead_batch(
        hypothesis.project_id, leads, source=source, imported_by=imported_by.strip(),
        search_strategy_id=strategy.strategy_id, derived_from_search_strategy=reference,
        derived_from_search_execution=(search_execution_id or ""))
    if batch.stats.get("imported", 0) == 0:
        return LeadImportResult(ok=False,
                                error="No importable leads — every row was missing a company name.",
                                warnings=lb.batch_warnings(batch))

    hypothesis.lead_batches.append(batch)          # append-only; never mutates a previous batch
    hypothesis.touch()
    return LeadImportResult(ok=True, batch=batch, warnings=lb.batch_warnings(batch),
                            derived_from_search_strategy=reference)
