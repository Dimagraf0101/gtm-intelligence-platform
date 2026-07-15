"""ICP Project domain (Sprint 5.2).

Introduces the **ICP Project** as a first-class in-memory domain entity so an organization can hold
**one CompanyKnowledge base + many isolated ICP Projects**. Each project owns its own
ProjectKnowledge (hypothesis-only facts) and its own draft versions, so competing hypotheses
(FinTech, Healthcare, WordPress, Legacy Modernization, AI Automation) never contaminate one another.

Design constraints (kept deliberately thin):

* **Reuse, don't duplicate.** CompanyKnowledge and ProjectKnowledge are both plain
  ``business_knowledge.BusinessKnowledge`` instances. All the existing evidence/merge/conflict logic
  and the ``KnowledgeItem`` / ``ConflictRecord`` schema are reused verbatim — there is no second,
  competing knowledge schema.
* **ComposedProjectKnowledge = CompanyKnowledge + ProjectKnowledge** as a *read-time* view. It builds
  a **transient, read-only** ``BusinessKnowledge`` (sharing item references) so ``detect_gaps``,
  ``generate_draft_icp`` and the review workspace consume it through their existing read paths with
  zero changes. Composing **never mutates** CompanyKnowledge or ProjectKnowledge.
* **No promotion is ever automatic.** Moving knowledge between Company and Project scope is only ever
  done by the explicit service functions below, invoked by a human action; each is recorded as a
  ``user_input`` origin while the fact's own provenance (source references / evidence) is preserved.

No repositories, databases, events, or DDD ceremony — plain dataclasses and focused functions.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

import business_knowledge as bk

# Categories that describe reusable, hypothesis-independent COMPANY facts. Everything else (target
# industries/buyers/size/geo, trigger signals, hard-exclusion candidates, segment customer examples,
# project-specific unknowns) is hypothesis-scoped and belongs to a project's ProjectKnowledge.
COMPANY_CATEGORIES = ("company", "product", "service", "capability", "technology", "business_model")

# Explicit allowlist of SINGLE-VALUED fact categories: a project value *replaces* the company value
# for the same (category, attribute) in that project's composed view. Every other category is treated
# as MULTI-VALUED — company and project values are unioned and de-duplicated, so a project's values
# never hide the company's. Deliberately a tiny, explicit config, not a rules engine.
SINGLE_VALUE_CATEGORIES = frozenset({"company", "business_model", "company_size"})

# Explicit extraction destinations (Sprint 5.2.1) — no auto-classification, no silent fallback.
SCOPE_COMPANY = "company"
SCOPE_PROJECT = "project"

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


def _dedup(values) -> list[str]:
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --- entities ----------------------------------------------------------------

@dataclass
class ICPProject:
    """One ICP hypothesis, isolated from every other. Thin on purpose: it carries hypothesis-only
    knowledge and its own draft versions, and nothing else yet (no approval / interview / strategy
    history — those are later sprints)."""
    name: str = ""
    hypothesis: str = ""
    status: str = STATUS_ACTIVE
    project_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    project_knowledge: Optional[bk.BusinessKnowledge] = None
    draft_versions: list = field(default_factory=list)

    def __post_init__(self):
        if not self.project_id:
            self.project_id = bk._new_id("icpp")
        if not self.created_at:
            self.created_at = bk._now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.project_knowledge is None:
            self.project_knowledge = bk.BusinessKnowledge()

    def touch(self) -> None:
        self.updated_at = bk._now()


@dataclass
class ICPPortfolio:
    """One company, many isolated ICP projects. A convenience container for the review page/tests;
    it holds no logic beyond project lookup and composition."""
    company: Optional[bk.BusinessKnowledge] = None
    projects: list = field(default_factory=list)

    def __post_init__(self):
        if self.company is None:
            self.company = bk.BusinessKnowledge()

    def create_project(self, name: str, hypothesis: str = "", *,
                       status: str = STATUS_ACTIVE) -> ICPProject:
        project = ICPProject(name=name, hypothesis=hypothesis, status=status)
        self.projects.append(project)
        return project

    def get_project(self, project_id: str) -> ICPProject:
        for p in self.projects:
            if p.project_id == project_id:
                return p
        raise KeyError(project_id)

    def composed(self, project) -> bk.BusinessKnowledge:
        proj = project if isinstance(project, ICPProject) else self.get_project(project)
        return ComposedProjectKnowledge(self.company, proj).composed()


# --- composed read view ------------------------------------------------------

class ComposedProjectKnowledge:
    """Read-time composition of CompanyKnowledge + a single project's ProjectKnowledge.

    ``composed()`` returns a fresh, transient ``BusinessKnowledge`` built from **defensive deep
    copies** of the underlying items and conflicts, so it is genuinely read-only: no mutation through
    the composed view (``edit_item`` / ``confirm_item`` / ``reject_item`` / ``add_item`` /
    ``merge_items`` / ``resolve_conflict``, or direct item-reference mutation) can reach
    CompanyKnowledge or ProjectKnowledge. Because a ``BusinessKnowledge`` reads everything it exposes
    — ``field`` / ``get_items`` / ``summary`` / ``conflicts`` / ``unknown_fields`` — from those
    collections, the composed view reuses **all** existing read logic and can be passed directly to
    ``detect_gaps`` / ``generate_draft_icp``.

    Composition semantics (deterministic; only active, non-rejected items participate):

    * **single-value categories** (``SINGLE_VALUE_CATEGORIES``): an active project item overrides the
      company value(s) for the same ``(category, attribute)`` — the company item is dropped;
    * **multi-value categories** (everything else): company and project values are **unioned and
      de-duplicated** by ``(category, attribute, normalized_value)`` so project values never hide
      company values;
    * **project conflicts stay project-local** — they appear in this project's composed view and
      nowhere else; company conflicts are dropped only where a single-value override supersedes them.
    """

    def __init__(self, company: bk.BusinessKnowledge, project: ICPProject):
        self.company = company
        self.project = project

    def _override_keys(self) -> set:
        """(category, attribute) pairs where an active project item overrides the company value."""
        return {(it.category, it.attribute)
                for it in self.project.project_knowledge.knowledge_items
                if it.is_active and it.category in SINGLE_VALUE_CATEGORIES}

    def composed(self) -> bk.BusinessKnowledge:
        override = self._override_keys()
        project_active = [it for it in self.project.project_knowledge.knowledge_items if it.is_active]
        # Values the project already contributes, for multi-value de-duplication.
        project_value_keys = {(it.category, it.attribute, it.normalized_value)
                              for it in project_active}

        kept = []
        for it in self.company.knowledge_items:
            if not it.is_active:
                continue                                     # rejected items excluded
            if (it.category, it.attribute) in override:
                continue                                     # single-value: project overrides company
            if it.category not in SINGLE_VALUE_CATEGORIES and \
                    (it.category, it.attribute, it.normalized_value) in project_value_keys:
                continue                                     # multi-value: de-duplicate union
            kept.append(it)
        kept.extend(project_active)

        merged = bk.BusinessKnowledge(source_package_id=self.company.source_package_id)
        merged.knowledge_items = [copy.deepcopy(it) for it in kept]   # defensive copies: read-only
        merged.conflicts = copy.deepcopy(
            [c for c in self.company.conflicts if (c.category, c.attribute) not in override]
            + list(self.project.project_knowledge.conflicts))         # project conflicts stay local
        merged.unknown_fields = _dedup(list(self.company.unknown_fields)
                                       + list(self.project.project_knowledge.unknown_fields))
        merged.warnings = list(self.company.warnings) + list(self.project.project_knowledge.warnings)
        return merged

    def item_scope(self, knowledge_id: str) -> str:
        """Return 'project' if the item is owned by the project overlay, else 'company'."""
        for it in self.project.project_knowledge.knowledge_items:
            if it.knowledge_id == knowledge_id:
                return "project"
        return "company"


# --- explicit, human-invoked knowledge movement ------------------------------
#
# None of these run automatically or from AI: they are the *only* way knowledge crosses the
# Company/Project boundary, and each records a human (user_input) action while preserving provenance.

def _copy_item_into(dest: bk.BusinessKnowledge, item, *, origin: Optional[str] = None,
                    extra_notes=None):
    """Add a copy of ``item`` into ``dest`` reusing BusinessKnowledge's own merge/conflict rules.
    Provenance (source references, evidence excerpt, temporal context, normalized value) is carried
    over; ``origin`` overrides the recorded origin when a human action is being attributed."""
    return dest.add_item(
        item.category, item.attribute, item.value,
        status=item.status, confidence=item.confidence,
        origin=origin if origin is not None else item.origin,
        user_confirmed=item.user_confirmed,
        source_references=[copy.copy(r) for r in item.source_references],
        evidence_excerpt=item.evidence_excerpt,
        notes=list(item.notes) + list(extra_notes or []),
        normalized_value=item.normalized_value,
        temporal_context=item.temporal_context,
    )


def _remove_item(source: bk.BusinessKnowledge, knowledge_id: str):
    item = source._get(knowledge_id)                       # raises KeyError if absent
    source.knowledge_items = [it for it in source.knowledge_items
                              if it.knowledge_id != knowledge_id]
    source.updated_at = bk._now()
    return item


def promote_to_company(project: ICPProject, company: bk.BusinessKnowledge, knowledge_id: str, *,
                       note: str = ""):
    """Promote a project knowledge item UP into CompanyKnowledge (make it reusable company-wide).

    Explicit human action only — there is no automatic/AI-triggered promotion. Provenance is
    preserved on the promoted copy; the human decision is recorded as a ``user_input`` origin. The
    item is removed from the project so it lives in exactly one scope."""
    item = project.project_knowledge._get(knowledge_id)
    label = project.name or project.project_id
    promoted = _copy_item_into(
        company, item, origin=bk.ORIGIN_USER,
        extra_notes=[f"Promoted from ICP project '{label}' by human review."]
        + ([note] if note else []))
    _remove_item(project.project_knowledge, knowledge_id)
    project.touch()
    company.updated_at = bk._now()
    return promoted


def move_company_to_project(company: bk.BusinessKnowledge, project: ICPProject, knowledge_id: str, *,
                            note: str = ""):
    """Move a company item DOWN into a single project's ProjectKnowledge (it was hypothesis-specific,
    not a reusable company fact). Explicit human action; provenance preserved; removed from company."""
    item = company._get(knowledge_id)
    label = project.name or project.project_id
    moved = _copy_item_into(
        project.project_knowledge, item, origin=bk.ORIGIN_USER,
        extra_notes=[f"Moved from Company Knowledge into ICP project '{label}' by human review."]
        + ([note] if note else []))
    _remove_item(company, knowledge_id)
    project.touch()
    return moved


def copy_company_to_project(company: bk.BusinessKnowledge, project: ICPProject, knowledge_id: str, *,
                            value: Optional[str] = None, note: str = ""):
    """Copy a company item INTO a project as a project-specific override (the company item stays).

    With ``value`` given (and different), the project value overrides the company value **only for
    this project**; because it shares the same ``(category, attribute)`` it shadows the company item
    in this project's composed view and nowhere else. Provenance from the company source is dropped on
    a value override (the new value is a human assertion), otherwise preserved."""
    item = company._get(knowledge_id)
    is_override = value is not None and value != item.value
    label = project.name or project.project_id
    notes = list(item.notes) + [
        f"Copied from Company Knowledge into ICP project '{label}'"
        + (" as a project-specific override." if is_override else ".")]
    if note:
        notes.append(note)
    new = project.project_knowledge.add_item(
        item.category, item.attribute, value if value is not None else item.value,
        status=item.status, confidence=item.confidence, origin=bk.ORIGIN_USER,
        user_confirmed=item.user_confirmed,
        source_references=[] if is_override else [copy.copy(r) for r in item.source_references],
        evidence_excerpt="" if is_override else item.evidence_excerpt,
        notes=notes,
        normalized_value=None if is_override else item.normalized_value,
        temporal_context=item.temporal_context)
    project.touch()
    return new


def _ingest_into(dest: bk.BusinessKnowledge, source_bk: bk.BusinessKnowledge) -> bk.BusinessKnowledge:
    """Copy every extracted item into ``dest``, preserving provenance and temporal_context (reusing
    BusinessKnowledge's merge/conflict rules). Ingestion keeps the extractor's origin/confidence — it
    is not a human curation action and never promotes anything."""
    for it in list(source_bk.knowledge_items):
        _copy_item_into(dest, it)
    for f in source_bk.unknown_fields:
        if f not in dest.unknown_fields:
            dest.unknown_fields.append(f)
    dest.updated_at = bk._now()
    return dest


def route_extraction(company: bk.BusinessKnowledge, source, *, scope: str,
                     project: Optional[ICPProject] = None) -> bk.BusinessKnowledge:
    """Ingest a whole extraction into an **explicitly chosen** scope (Sprint 5.2.1).

    ``scope`` must be ``SCOPE_COMPANY`` or ``SCOPE_PROJECT``. Project scope **requires** a project —
    there is no auto-classification and no silent fallback to Company Knowledge when project scope was
    intended. ``source`` may be a ``BusinessKnowledge`` or an extraction result exposing
    ``.business_knowledge``. Returns the destination store. Provenance and temporal_context are
    preserved; nothing is promoted automatically."""
    source_bk = getattr(source, "business_knowledge", source)
    if scope == SCOPE_PROJECT:
        if project is None:
            raise ValueError("Project scope was selected but no ICP project was provided.")
        dest = _ingest_into(project.project_knowledge, source_bk)
        project.touch()
        return dest
    if scope == SCOPE_COMPANY:
        return _ingest_into(company, source_bk)
    raise ValueError(f"Unknown extraction scope '{scope}'. Choose {SCOPE_COMPANY!r} or {SCOPE_PROJECT!r}.")


def distribute_extraction(company: bk.BusinessKnowledge, project: ICPProject, source) -> tuple:
    """Route freshly extracted knowledge to the correct scope by category: COMPANY_CATEGORIES go to
    CompanyKnowledge, everything else (hypothesis facts) goes to the project. ``source`` may be a
    ``BusinessKnowledge`` or an extraction result exposing ``.business_knowledge``. Ingestion
    preserves the extractor's origin/confidence — it is not a human curation action.

    NOTE: this auto-classifies by category. The review page uses the explicit, user-chosen
    ``route_extraction`` instead; this helper is retained for programmatic callers/tests."""
    source_bk = getattr(source, "business_knowledge", source)
    for it in list(source_bk.knowledge_items):
        dest = company if it.category in COMPANY_CATEGORIES else project.project_knowledge
        _copy_item_into(dest, it)
    company.updated_at = bk._now()
    project.touch()
    return company, project
