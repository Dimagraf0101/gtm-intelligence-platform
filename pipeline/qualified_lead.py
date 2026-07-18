"""Qualified Lead domain — immutable qualification results (Sprint 11).

A ``QualifiedLeadBatch`` is the immutable result of qualifying one ``LeadBatch`` against a Market
Hypothesis's Approved Adapted ICP. Each ``QualifiedLead`` **references** the source lead by id (it
never copies the lead's fields) and carries the engine's qualification outputs (score, decision,
evidence, warnings, and the full serialized result).

This module holds only the data model + statistics — no scoring, no engine calls. The engine is
invoked by the ``qualification_run`` service; the frozen scoring/bridge/identity modules are unchanged.
Qualifying again always creates a NEW batch; nothing here mutates a LeadBatch, ICP, strategy, or
knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import business_knowledge as bk


@dataclass
class QualifiedLead:
    """One qualification outcome. References the source lead by ``lead_id`` (fields are NOT copied)."""
    lead_id: str = ""
    score: int = 0
    decision: str = ""                          # operational priority / category
    confidence: str = ""
    reason: str = ""
    evidence: list = field(default_factory=list)     # engine signals
    warnings: list = field(default_factory=list)
    result: dict = field(default_factory=dict)       # full serialized engine ScoringResult

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "QualifiedLead":
        d = d or {}
        return cls(lead_id=d.get("lead_id", ""), score=d.get("score", 0),
                   decision=d.get("decision", ""), confidence=d.get("confidence", ""),
                   reason=d.get("reason", ""), evidence=list(d.get("evidence", [])),
                   warnings=list(d.get("warnings", [])), result=dict(d.get("result", {})))


@dataclass
class QualifiedLeadBatch:
    """An immutable batch of qualification results, owned by one Market Hypothesis."""
    batch_id: str = ""
    hypothesis_id: str = ""
    derived_from_lead_batch: str = ""           # the source LeadBatch.batch_id
    derived_from_search_strategy: str = ""      # the LeadBatch's Search Strategy reference (full chain)
    derived_from_adapted_icp: str = ""          # the EXACT Adapted ICP ArtifactIdentity used
    qualified_at: str = ""
    qualified_by: str = ""
    qualified: list = field(default_factory=list)    # list[QualifiedLead]
    stats: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = bk._new_id("qlb")
        if not self.qualified_at:
            self.qualified_at = bk._now()

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id, "hypothesis_id": self.hypothesis_id,
            "derived_from_lead_batch": self.derived_from_lead_batch,
            "derived_from_search_strategy": self.derived_from_search_strategy,
            "derived_from_adapted_icp": self.derived_from_adapted_icp,
            "qualified_at": self.qualified_at, "qualified_by": self.qualified_by,
            "qualified": [q.to_dict() for q in self.qualified], "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QualifiedLeadBatch":
        return cls(
            batch_id=d.get("batch_id", ""), hypothesis_id=d.get("hypothesis_id", ""),
            derived_from_lead_batch=d.get("derived_from_lead_batch", ""),
            derived_from_search_strategy=d.get("derived_from_search_strategy", ""),
            derived_from_adapted_icp=d.get("derived_from_adapted_icp", ""),
            qualified_at=d.get("qualified_at", ""), qualified_by=d.get("qualified_by", ""),
            qualified=[QualifiedLead.from_dict(x) for x in d.get("qualified", [])],
            stats=dict(d.get("stats", {})))


def qualified_statistics(qualified) -> dict:
    """Deterministic summary over a list of QualifiedLead."""
    total = len(qualified)
    by_decision: dict = {}
    for q in qualified:
        by_decision[q.decision] = by_decision.get(q.decision, 0) + 1
    scored = [q.score for q in qualified if not q.result.get("error")]
    return {
        "total": total,
        "by_decision": by_decision,
        "disqualified": sum(1 for q in qualified if q.decision == "Disqualified"),
        "errors": sum(1 for q in qualified if q.result.get("error")),
        "average_score": round(sum(scored) / len(scored)) if scored else 0,
    }
