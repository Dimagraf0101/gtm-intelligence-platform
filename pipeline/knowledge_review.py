"""Business Knowledge Review Workspace (Sprint 5.1).

Business logic for the mandatory human-review stage. BusinessKnowledge is the platform's single
Source of Truth; this workspace lets a user browse and curate it before any ICP generation. Every
operation mutates **BusinessKnowledge only** — nothing edits a GeneratedICP. Human edits are marked
``origin=user_input``; AI-generated knowledge can never silently overwrite confirmed human knowledge
(that invariant is enforced by BusinessKnowledge's merge/conflict rules, which this layer reuses).

This module is view-agnostic: it owns the workflow so the Streamlit page can stay a thin view. It
adds **no new business logic** — it wraps existing BusinessKnowledge, knowledge_gaps, and
icp_draft_generator primitives and exposes read-models + actions for the UI.
"""
from __future__ import annotations

from typing import Optional

import business_knowledge as bk
import knowledge_gaps as kg
import icp_draft_generator as dg

STATUSES = (bk.CONFIRMED, bk.PROPOSED, bk.CONFLICTING, bk.UNKNOWN)


# --- read-models (plain dicts for the view) ---------------------------------

def _item_view(it) -> dict:
    return {
        "knowledge_id": it.knowledge_id,
        "category": it.category,
        "attribute": it.attribute,
        "value": it.value,
        "status": it.status,
        "confidence": round(float(it.confidence), 2),
        "origin": it.origin,
        "user_confirmed": it.user_confirmed,
        "evidence_excerpt": it.evidence_excerpt,
        "sources": [r.filename + (f" §{r.section_index}" if r.section_index is not None else "")
                    for r in it.source_references],
        "notes": list(it.notes),
    }


def _conflict_view(c) -> dict:
    return {
        "conflict_id": c.conflict_id, "category": c.category, "attribute": c.attribute,
        "values": list(c.conflicting_values), "status": c.status,
        "item_ids": list(c.item_ids), "preferred_item_id": c.preferred_item_id,
    }


def _gap_view(g) -> dict:
    return {"section": g.section, "field": g.field, "severity": g.severity,
            "reason": g.reason, "question": g.suggested_question, "current_status": g.current_status}


# --- workspace ---------------------------------------------------------------

class KnowledgeReviewWorkspace:
    """Curation workspace over a single BusinessKnowledge (the Source of Truth)."""

    def __init__(self, knowledge: bk.BusinessKnowledge):
        self.knowledge = knowledge

    @classmethod
    def from_business_knowledge(cls, knowledge: bk.BusinessKnowledge) -> "KnowledgeReviewWorkspace":
        return cls(knowledge)

    @classmethod
    def from_extraction_result(cls, result) -> "KnowledgeReviewWorkspace":
        """Convenience: wrap the BusinessKnowledge produced by knowledge_extractor."""
        return cls(result.business_knowledge)

    # --- deterministic gap report (recomputed from current state) ------------

    def gap_report(self):
        return kg.detect_gaps(self.knowledge)

    # --- browse / filter / search / group -----------------------------------

    def categories(self) -> list[str]:
        return sorted({it.category for it in self.knowledge.knowledge_items
                       if it.status != bk.REJECTED})

    def items(self, *, category: Optional[str] = None, status: Optional[str] = None,
              search: Optional[str] = None, include_rejected: bool = False) -> list[dict]:
        out = []
        term = (search or "").strip().lower()
        for it in self.knowledge.knowledge_items:
            if not include_rejected and it.status == bk.REJECTED:
                continue
            if category and it.category != category:
                continue
            if status and it.status != status:
                continue
            if term:
                hay = f"{it.category} {it.attribute} {it.value} {it.evidence_excerpt}".lower()
                if term not in hay:
                    continue
            out.append(_item_view(it))
        return out

    def grouped(self, **filters) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for v in self.items(**filters):
            groups.setdefault(v["category"], []).append(v)
        return groups

    def conflicts(self, *, unresolved_only: bool = False) -> list[dict]:
        return [_conflict_view(c) for c in self.knowledge.conflicts
                if not (unresolved_only and c.status != bk.CONFLICT_UNRESOLVED)]

    # --- curation actions (mutate BusinessKnowledge ONLY) --------------------

    def confirm(self, knowledge_id: str, note: str = "") -> dict:
        return _item_view(self.knowledge.confirm_item(knowledge_id, note=note))

    def reject(self, knowledge_id: str, note: str = "") -> dict:
        return _item_view(self.knowledge.reject_item(knowledge_id, note=note))

    def edit(self, knowledge_id: str, *, value: Optional[str] = None,
             category: Optional[str] = None, attribute: Optional[str] = None,
             note: str = "") -> dict:
        return _item_view(self.knowledge.edit_item(
            knowledge_id, value=value, category=category, attribute=attribute, note=note))

    def add(self, category: str, attribute: str, value: str, *, evidence_excerpt: str = "",
            confidence: float = 0.9, note: str = "", confirmed: bool = True) -> dict:
        """Add a new human-provided fact (origin=user_input). Defaults to confirmed so it is
        protected from later AI overwrite; pass confirmed=False to add it as a proposal."""
        item = self.knowledge.add_item(
            category, attribute, value,
            status=(bk.CONFIRMED if confirmed else bk.PROPOSED),
            origin=bk.ORIGIN_USER, user_confirmed=confirmed,
            confidence=confidence, evidence_excerpt=evidence_excerpt,
            notes=[note] if note else None)
        return _item_view(item)

    def merge_duplicates(self, keep_id: str, other_id: str) -> dict:
        """Merge an exact-duplicate item into another (same category+attribute+normalized value)."""
        return _item_view(self.knowledge.merge_items(keep_id, other_id))

    def resolve_conflict(self, conflict_id: str, preferred_item_id: str, note: str = "") -> dict:
        return _conflict_view(self.knowledge.resolve_conflict(
            conflict_id, preferred_item_id, note=note))

    # --- summary (completeness / conflicts / unknowns / gaps) ----------------

    def summary(self) -> dict:
        gap = self.gap_report()
        s = self.knowledge.summary()
        return {
            "completeness": gap.completeness_score,
            "is_ready_for_icp_generation": gap.is_ready_for_icp_generation,
            "total_items": s["total_items"],
            "active_items": s["active_items"],
            "by_status": s["by_status"],
            "open_conflicts": self.conflicts(unresolved_only=True),
            "unknown_fields": list(self.knowledge.unknown_fields),
            "blocking_gaps": [_gap_view(g) for g in gap.blocking_gaps],
            "important_gaps": [_gap_view(g) for g in gap.important_gaps],
            "optional_gaps": [_gap_view(g) for g in gap.optional_gaps],
            "suggested_questions": list(gap.suggested_questions),
            "section_completeness": dict(gap.section_completeness),
        }

    # --- the single deterministic action for this sprint ---------------------

    def generate_draft(self, *, client=None, icp_name: Optional[str] = None,
                       user_notes: Optional[str] = None):
        """Generate a COMPLETELY NEW Draft ICP from the CURRENT Business Knowledge. The draft is a
        derived artifact returned to the caller; it is never stored on or edited by this workspace
        (each call regenerates from the current source of truth)."""
        return dg.generate_draft_icp(self.knowledge, self.gap_report(),
                                     icp_name=icp_name, user_notes=user_notes, client=client)
