"""Knowledge Review Workspace (Sprint 5.1, re-scoped in Sprint 5.2).

Business logic for the mandatory human-review stage. Knowledge is the platform's single Source of
Truth; this workspace lets a user browse and curate it before any ICP generation. Every operation
mutates **knowledge only** — nothing edits a GeneratedICP. Human edits are marked ``origin=user_input``;
AI-generated knowledge can never silently overwrite confirmed human knowledge (that invariant is
enforced by BusinessKnowledge's merge/conflict rules, which this layer reuses).

Sprint 5.2 makes the workspace **scope-aware**. It operates on:

* **Company Knowledge** — reusable, hypothesis-independent company facts (always present);
* an optional selected **ICP Project**, whose **ICP Knowledge** holds hypothesis-only facts; and
* the **Composed** view (Company Knowledge + ICP Knowledge) that gap detection and draft generation
  consume.

Constructed with a single ``BusinessKnowledge`` it behaves exactly as before (single-mode; that
knowledge *is* the company). The Lead Qualification app is untouched. This module owns the workflow
so the Streamlit page stays a thin view; it adds no new business logic beyond wiring the existing
BusinessKnowledge, knowledge_gaps, icp_draft_generator and icp_project primitives together.
"""
from __future__ import annotations

from typing import Optional

import business_knowledge as bk
import knowledge_gaps as kg
import icp_draft_generator as dg
import icp_project as ip

STATUSES = (bk.CONFIRMED, bk.PROPOSED, bk.CONFLICTING, bk.UNKNOWN)

# Scope terminology exposed to the UI: "Company Knowledge" and "ICP Knowledge" (never "overlay").
SCOPE_COMPANY = "company"
SCOPE_PROJECT = "project"
SCOPE_COMPOSED = "composed"


# --- read-models (plain dicts for the view) ---------------------------------

def _item_view(it, scope: Optional[str] = None) -> dict:
    v = {
        "knowledge_id": it.knowledge_id,
        "category": it.category,
        "attribute": it.attribute,
        "value": it.value,
        "status": it.status,
        "confidence": round(float(it.confidence), 2),
        "origin": it.origin,
        "user_confirmed": it.user_confirmed,
        "temporal_context": getattr(it, "temporal_context", bk.TEMPORAL_CURRENT),
        "evidence_excerpt": it.evidence_excerpt,
        "sources": [r.filename + (f" §{r.section_index}" if r.section_index is not None else "")
                    for r in it.source_references],
        "notes": list(it.notes),
    }
    if scope is not None:
        v["scope"] = scope
    return v


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
    """Curation workspace over Company Knowledge and (optionally) one selected ICP Project."""

    def __init__(self, knowledge: Optional[bk.BusinessKnowledge] = None, *,
                 company: Optional[bk.BusinessKnowledge] = None,
                 project: Optional["ip.ICPProject"] = None):
        # Backward compat: a positional `knowledge` is the Company Knowledge (single-mode).
        self.company = company if company is not None else knowledge
        if self.company is None:
            raise ValueError("KnowledgeReviewWorkspace requires company knowledge.")
        self.project = project

    # `knowledge` kept as an alias for the company store (existing callers/tests use it directly).
    @property
    def knowledge(self) -> bk.BusinessKnowledge:
        return self.company

    @knowledge.setter
    def knowledge(self, value: bk.BusinessKnowledge) -> None:
        self.company = value

    @classmethod
    def from_business_knowledge(cls, knowledge: bk.BusinessKnowledge) -> "KnowledgeReviewWorkspace":
        return cls(knowledge)

    @classmethod
    def from_extraction_result(cls, result) -> "KnowledgeReviewWorkspace":
        """Convenience: wrap the BusinessKnowledge produced by knowledge_extractor."""
        return cls(result.business_knowledge)

    @classmethod
    def for_project(cls, company: bk.BusinessKnowledge,
                    project: "ip.ICPProject") -> "KnowledgeReviewWorkspace":
        return cls(company=company, project=project)

    def select_project(self, project: Optional["ip.ICPProject"]) -> None:
        self.project = project

    # --- scope helpers -------------------------------------------------------

    @property
    def project_knowledge(self) -> Optional[bk.BusinessKnowledge]:
        return self.project.project_knowledge if self.project is not None else None

    def composed(self) -> bk.BusinessKnowledge:
        """The Company Knowledge + ICP Knowledge read view (or just Company Knowledge in single-mode)."""
        if self.project is None:
            return self.company
        return ip.ComposedProjectKnowledge(self.company, self.project).composed()

    def _active_knowledge(self) -> bk.BusinessKnowledge:
        """Knowledge used for gaps / draft generation: composed when a project is selected."""
        return self.composed()

    def _scope_knowledge(self, scope: str) -> bk.BusinessKnowledge:
        if scope == SCOPE_PROJECT:
            if self.project is None:
                raise ValueError("No ICP project selected.")
            return self.project.project_knowledge
        if scope == SCOPE_COMPOSED:
            return self.composed()
        return self.company

    def _owner(self, knowledge_id: str) -> bk.BusinessKnowledge:
        """Return the knowledge store that actually owns an item (company or the project overlay)."""
        try:
            self.company._get(knowledge_id)
            return self.company
        except KeyError:
            pass
        if self.project is not None:
            self.project.project_knowledge._get(knowledge_id)   # raises KeyError if truly absent
            return self.project.project_knowledge
        raise KeyError(knowledge_id)

    # --- deterministic gap report (recomputed from current state) ------------

    def gap_report(self):
        return kg.detect_gaps(self._active_knowledge())

    # --- browse / filter / search / group -----------------------------------

    def categories(self, *, scope: str = SCOPE_COMPANY) -> list[str]:
        kn = self._scope_knowledge(scope)
        return sorted({it.category for it in kn.knowledge_items if it.status != bk.REJECTED})

    def items(self, *, scope: str = SCOPE_COMPANY, category: Optional[str] = None,
              status: Optional[str] = None, search: Optional[str] = None,
              include_rejected: bool = False) -> list[dict]:
        kn = self._scope_knowledge(scope)
        # In the composed view, tag each item with the scope that actually owns it.
        composed = ip.ComposedProjectKnowledge(self.company, self.project) \
            if (scope == SCOPE_COMPOSED and self.project is not None) else None
        out = []
        term = (search or "").strip().lower()
        for it in kn.knowledge_items:
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
            item_scope = composed.item_scope(it.knowledge_id) if composed else \
                (scope if scope in (SCOPE_COMPANY, SCOPE_PROJECT) else None)
            out.append(_item_view(it, scope=item_scope))
        return out

    def company_items(self, **filters) -> list[dict]:
        return self.items(scope=SCOPE_COMPANY, **filters)

    def project_items(self, **filters) -> list[dict]:
        return self.items(scope=SCOPE_PROJECT, **filters)

    def composed_items(self, **filters) -> list[dict]:
        return self.items(scope=SCOPE_COMPOSED, **filters)

    def grouped(self, **filters) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for v in self.items(**filters):
            groups.setdefault(v["category"], []).append(v)
        return groups

    def conflicts(self, *, scope: str = SCOPE_COMPANY, unresolved_only: bool = False) -> list[dict]:
        kn = self._scope_knowledge(scope)
        return [_conflict_view(c) for c in kn.conflicts
                if not (unresolved_only and c.status != bk.CONFLICT_UNRESOLVED)]

    # --- curation actions (mutate the owning knowledge store) ----------------

    def confirm(self, knowledge_id: str, note: str = "") -> dict:
        return _item_view(self._owner(knowledge_id).confirm_item(knowledge_id, note=note))

    def reject(self, knowledge_id: str, note: str = "") -> dict:
        return _item_view(self._owner(knowledge_id).reject_item(knowledge_id, note=note))

    def edit(self, knowledge_id: str, *, value: Optional[str] = None,
             category: Optional[str] = None, attribute: Optional[str] = None,
             temporal_context: Optional[str] = None, note: str = "") -> dict:
        return _item_view(self._owner(knowledge_id).edit_item(
            knowledge_id, value=value, category=category, attribute=attribute,
            temporal_context=temporal_context, note=note))

    def add(self, category: str, attribute: str, value: str, *, scope: str = SCOPE_COMPANY,
            evidence_excerpt: str = "", confidence: float = 0.9, note: str = "",
            temporal_context: str = bk.TEMPORAL_CURRENT, confirmed: bool = True) -> dict:
        """Add a new human-provided fact (origin=user_input) to the chosen scope. Defaults to
        confirmed so it is protected from later AI overwrite; pass confirmed=False for a proposal."""
        kn = self._scope_knowledge(scope)
        item = kn.add_item(
            category, attribute, value,
            status=(bk.CONFIRMED if confirmed else bk.PROPOSED),
            origin=bk.ORIGIN_USER, user_confirmed=confirmed,
            confidence=confidence, evidence_excerpt=evidence_excerpt,
            temporal_context=temporal_context,
            notes=[note] if note else None)
        return _item_view(item, scope=scope if scope != SCOPE_COMPOSED else None)

    def merge_duplicates(self, keep_id: str, other_id: str) -> dict:
        """Merge an exact-duplicate item into another (same category+attribute+normalized value)."""
        owner = self._owner(keep_id)
        return _item_view(owner.merge_items(keep_id, other_id))

    def resolve_conflict(self, conflict_id: str, preferred_item_id: str, note: str = "") -> dict:
        owner = self._owner(preferred_item_id)
        return _conflict_view(owner.resolve_conflict(conflict_id, preferred_item_id, note=note))

    # --- explicit Company <-> ICP knowledge movement (human action) ----------

    def promote_to_company(self, knowledge_id: str, *, note: str = "") -> dict:
        """Promote an ICP Knowledge item into Company Knowledge (reusable). Human action only."""
        if self.project is None:
            raise ValueError("Select an ICP project before promoting ICP knowledge.")
        return _item_view(ip.promote_to_company(self.project, self.company, knowledge_id, note=note),
                          scope=SCOPE_COMPANY)

    def move_to_project(self, knowledge_id: str, *, note: str = "") -> dict:
        """Move a Company Knowledge item into the selected ICP Project. Human action only."""
        if self.project is None:
            raise ValueError("Select an ICP project before moving knowledge into it.")
        return _item_view(
            ip.move_company_to_project(self.company, self.project, knowledge_id, note=note),
            scope=SCOPE_PROJECT)

    def copy_to_project(self, knowledge_id: str, *, value: Optional[str] = None,
                        note: str = "") -> dict:
        """Copy a Company Knowledge item into the selected ICP Project as an override. Human action."""
        if self.project is None:
            raise ValueError("Select an ICP project before copying knowledge into it.")
        return _item_view(
            ip.copy_company_to_project(self.company, self.project, knowledge_id,
                                       value=value, note=note),
            scope=SCOPE_PROJECT)

    # --- summary (completeness / conflicts / unknowns / gaps) ----------------

    def summary(self) -> dict:
        knowledge = self._active_knowledge()
        gap = kg.detect_gaps(knowledge)
        s = knowledge.summary()
        out = {
            "completeness": gap.completeness_score,
            "is_ready_for_icp_generation": gap.is_ready_for_icp_generation,
            "total_items": s["total_items"],
            "active_items": s["active_items"],
            "by_status": s["by_status"],
            "open_conflicts": [_conflict_view(c) for c in knowledge.conflicts
                               if c.status == bk.CONFLICT_UNRESOLVED],
            "unknown_fields": list(knowledge.unknown_fields),
            "blocking_gaps": [_gap_view(g) for g in gap.blocking_gaps],
            "important_gaps": [_gap_view(g) for g in gap.important_gaps],
            "optional_gaps": [_gap_view(g) for g in gap.optional_gaps],
            "suggested_questions": list(gap.suggested_questions),
            "section_completeness": dict(gap.section_completeness),
        }
        if self.project is not None:
            out["project"] = {
                "project_id": self.project.project_id, "name": self.project.name,
                "hypothesis": self.project.hypothesis, "status": self.project.status,
                "draft_versions": len(self.project.draft_versions),
                "company_items": len([it for it in self.company.knowledge_items if it.is_active]),
                "project_items": len([it for it in self.project.project_knowledge.knowledge_items
                                      if it.is_active]),
            }
        return out

    # --- Generate Draft ICP (from the composed knowledge) --------------------

    def generate_draft(self, *, client=None, icp_name: Optional[str] = None,
                       user_notes: Optional[str] = None):
        """Generate a COMPLETELY NEW Draft ICP from the CURRENT composed knowledge (Company + ICP).

        The result is a brand-new GeneratedICP; when a project is selected it is appended to
        ``project.draft_versions`` and **no previous draft is ever mutated**. Status stays Draft (no
        approval logic here). In single-mode the draft is simply returned to the caller."""
        knowledge = self._active_knowledge()
        result = dg.generate_draft_icp(knowledge, kg.detect_gaps(knowledge),
                                       icp_name=icp_name, user_notes=user_notes, client=client)
        if self.project is not None and getattr(result, "generated_icp", None) is not None:
            self.project.draft_versions.append(result.generated_icp)
            self.project.touch()
        return result
