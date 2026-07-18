"""Qualification run service (Sprint 11).

The application layer that qualifies one imported ``LeadBatch`` against a Market Hypothesis's Approved
Adapted ICP, by **reusing** the frozen qualification engine:

    LeadBatch → qualification_mapper → qualification_bridge.score_with_context → QualifiedLeadBatch

It validates lineage/ownership deterministically, maps domain leads into the engine, runs the engine
unchanged, and assembles an immutable ``QualifiedLeadBatch`` appended to the hypothesis. It never
edits the LeadBatch, Search Strategy, ICP, or Business Knowledge, and never duplicates scoring logic.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional

import qualification_bridge as qb
import qualification_mapper as qmap
import qualified_lead as ql
import search_strategy as ss
import icp_identity as idy


@dataclass
class QualificationRunResult:
    ok: bool = False
    error: str = ""
    batch: Optional[ql.QualifiedLeadBatch] = None

    def summary(self) -> dict:
        return {
            "ok": self.ok, "error": self.error,
            "stats": dict(self.batch.stats) if self.batch is not None else {},
            "batch_id": self.batch.batch_id if self.batch is not None else "",
            "derived_from_lead_batch": self.batch.derived_from_lead_batch if self.batch else "",
            "derived_from_adapted_icp": self.batch.derived_from_adapted_icp if self.batch else "",
        }


def _qualified_from_result(lead_id: str, result) -> ql.QualifiedLead:
    """Project one engine ScoringResult into a domain QualifiedLead (referencing the lead by id)."""
    warnings = []
    if getattr(result, "error", None):
        warnings.append(str(result.error))
    if getattr(result, "hard_dealbreaker", False) and getattr(result, "dealbreaker_reason", None):
        warnings.append(str(result.dealbreaker_reason))
    warnings += [f"unknown: {u}" for u in (getattr(result, "unknowns", []) or [])]
    return ql.QualifiedLead(
        lead_id=lead_id, score=getattr(result, "score", 0),
        decision=getattr(result, "operational_priority", None) or getattr(result, "category", ""),
        confidence=getattr(result, "confidence", ""), reason=getattr(result, "reason", ""),
        evidence=list(getattr(result, "signals", []) or []), warnings=warnings,
        result=dataclasses.asdict(result))


def _resolve_lineage_icp(hypothesis, batch):
    """Deterministically resolve the EXACT Adapted ICP for a LeadBatch by following its lineage:
    LeadBatch → (its Search Strategy) → (that strategy's Adapted ICP). Returns (icp, error). Never
    substitutes the hypothesis's *currently active* ICP — that would break historical lineage."""
    # 1-2. parse the LeadBatch's Search Strategy provenance
    try:
        ref_hyp, sid, _ver = ss.parse_search_strategy_reference(batch.derived_from_search_strategy)
    except ss.SearchStrategyError:
        return None, "Malformed Lead Batch provenance — its Search Strategy reference is invalid."
    if ref_hyp != hypothesis.project_id:
        return None, "Lineage disagreement — the Lead Batch's Search Strategy is for another hypothesis."

    # 3-6. resolve that exact Search Strategy in THIS hypothesis; it must exist, be owned, and have
    #      been Approved (historical policy: Approved, or Archived-after-Approved).
    strategy = next((s for s in getattr(hypothesis, "search_strategies", [])
                     if s.strategy_id == sid), None)
    if strategy is None:
        return None, "The Lead Batch's source Search Strategy no longer exists for this hypothesis."
    if strategy.hypothesis_id != hypothesis.project_id:
        return None, "The Lead Batch's source Search Strategy belongs to a different hypothesis."
    if not ss.was_ever_approved(strategy):
        return None, (f"The source Search Strategy is {strategy.status} and was never approved; "
                      "a Lead Batch may only be qualified via an approved Search Strategy.")

    # 7. read the strategy's Adapted ICP provenance and validate it
    if not (strategy.derived_from_adapted_icp or "").strip():
        return None, "The source Search Strategy has no Adapted ICP provenance."
    try:
        ident = idy.parse_artifact_identity(strategy.derived_from_adapted_icp)
    except idy.ArtifactIdentityError:
        return None, "Malformed Adapted ICP provenance on the source Search Strategy."
    if ident.artifact_type != idy.ARTIFACT_ADAPTED_ICP:
        return None, "The source Search Strategy does not reference an Adapted ICP."

    # 8-9. resolve the EXACT Adapted ICP version in THIS hypothesis's approved versions (an ICP from
    #      another hypothesis is simply not present here -> refused).
    target = next((icp for icp in getattr(hypothesis, "approved_versions", [])
                   if idy.artifact_identity_str(icp) == strategy.derived_from_adapted_icp), None)
    if target is None:
        return None, ("The exact Adapted ICP referenced by the Search Strategy does not exist in "
                      "this hypothesis (unknown, cross-hypothesis, or removed).")
    return target, ""


def qualify_lead_batch(hypothesis, lead_batch_id: str, *, qualified_by: str = "",
                       client=None) -> QualificationRunResult:
    """Qualify the LeadBatch ``lead_batch_id`` for ``hypothesis`` against the **exact** Adapted ICP its
    Search Strategy was derived from (Sprint 11.1) — never the hypothesis's currently active ICP.

    Full lineage resolved deterministically: Approved Adapted ICP → Approved Search Strategy →
    LeadBatch → QualifiedLeadBatch. Refuses on any missing/malformed/cross-hypothesis/disagreeing
    reference, an empty batch, or an empty qualifier. On success, appends a NEW immutable
    QualifiedLeadBatch; never mutates any source artifact."""
    if not (qualified_by or "").strip():
        return QualificationRunResult(ok=False, error="A qualifier name is required.")

    batch = next((b for b in getattr(hypothesis, "lead_batches", [])
                  if b.batch_id == lead_batch_id), None)
    if batch is None:
        return QualificationRunResult(ok=False, error=(
            "That Lead Batch does not exist for this hypothesis (unknown id or wrong hypothesis)."))
    if batch.hypothesis_id != hypothesis.project_id:                    # defensive ownership check
        return QualificationRunResult(ok=False, error="Lead Batch belongs to a different hypothesis.")
    if not batch.leads:
        return QualificationRunResult(ok=False, error="The Lead Batch is empty; nothing to qualify.")
    if not (batch.derived_from_search_strategy or "").strip():
        return QualificationRunResult(ok=False, error=(
            "Broken acquisition lineage — the Lead Batch has no Search Strategy provenance."))

    # resolve the EXACT Adapted ICP through the lineage (not the currently active ICP)
    target_icp, err = _resolve_lineage_icp(hypothesis, batch)
    if err:
        return QualificationRunResult(ok=False, error=err)
    try:
        ctx = qb.context_from_approved_icp(target_icp)                 # the exact referenced ICP
    except qb.BridgeError as exc:
        return QualificationRunResult(ok=False, error=f"Referenced Adapted ICP is not usable: {exc}")

    # map domain leads into the frozen engine, then run it unchanged
    scoring_leads = [qmap.to_scoring_lead(ld, i) for i, ld in enumerate(batch.leads)]
    results = qb.score_with_context(scoring_leads, ctx, client=client)

    id_by_index = {i: ld.lead_id for i, ld in enumerate(batch.leads)}
    qualified = [_qualified_from_result(id_by_index.get(r.lead_index, ""), r) for r in results]
    qlb = ql.QualifiedLeadBatch(
        hypothesis_id=hypothesis.project_id, derived_from_lead_batch=batch.batch_id,
        derived_from_search_strategy=batch.derived_from_search_strategy,
        derived_from_adapted_icp=idy.artifact_identity_str(target_icp),  # the EXACT ICP used
        qualified_by=qualified_by.strip(), qualified=qualified,
        stats=ql.qualified_statistics(qualified))

    hypothesis.qualified_batches.append(qlb)      # append-only; never mutates a previous batch
    hypothesis.touch()
    return QualificationRunResult(ok=True, batch=qlb)
