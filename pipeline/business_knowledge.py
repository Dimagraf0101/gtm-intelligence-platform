"""Business Knowledge layer for the ICP Generator (Sprint 4.1C).

Deterministic structure/validation/merge/conflict layer that sits between a SourcePackage and the
future AI-extraction/interview steps:

    SourcePackage -> BusinessKnowledge -> (future) AI extraction / interview -> GeneratedICP

Python owns the *shape*: how facts are stored, attributed, merged, de-duplicated, and how conflicts
and status are handled. Future LLM steps may *propose* KnowledgeItems, but they are never promoted
automatically here. This module does NOT parse semantic facts from raw document text.

Principles enforced:
  - facts and interpretations stay separate (status/origin fields);
  - every item keeps source attribution;
  - missing information stays unknown (never invented, never negative);
  - conflicting information is preserved, not silently resolved;
  - deterministic, offline: no LLM, no network, no Anthropic API, no persistence.
"""
from __future__ import annotations

import re
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# --- controlled vocabularies -------------------------------------------------

# Status of a knowledge item.
CONFIRMED = "confirmed"
CONFLICTING = "conflicting"
UNKNOWN = "unknown"
PROPOSED = "proposed"
REJECTED = "rejected"
ITEM_STATUSES = (CONFIRMED, CONFLICTING, UNKNOWN, PROPOSED, REJECTED)

# Where an item came from.
ORIGIN_SOURCE = "source_extraction"
ORIGIN_USER = "user_input"
ORIGIN_AI = "ai_proposal"
ORIGIN_SYSTEM = "system_default"
ORIGINS = (ORIGIN_SOURCE, ORIGIN_USER, ORIGIN_AI, ORIGIN_SYSTEM)

# Conflict resolution status.
CONFLICT_UNRESOLVED = "unresolved"
CONFLICT_USER = "user_resolved"
CONFLICT_SYSTEM = "system_resolved"

# Temporal context of a fact (Sprint 5.2; hardened in 5.2.1). The default is "unknown": missing
# temporal information must never silently be treated as "current". A fact is only "current" when the
# extractor or a human caller explicitly provides that value.
TEMPORAL_CURRENT = "current"
TEMPORAL_HISTORICAL = "historical"
TEMPORAL_FORMER = "former"
TEMPORAL_FUTURE = "proposed_future"
TEMPORAL_UNKNOWN = "unknown"
TEMPORAL_CONTEXTS = (TEMPORAL_CURRENT, TEMPORAL_HISTORICAL, TEMPORAL_FORMER,
                     TEMPORAL_FUTURE, TEMPORAL_UNKNOWN)
# Contexts that describe a NON-current fact — never promoted to a current target.
NON_CURRENT_TEMPORAL = frozenset({TEMPORAL_HISTORICAL, TEMPORAL_FORMER})

# Category vocabulary (extensible; never industry-specific).
CATEGORIES = (
    "company", "product", "service", "capability", "technology", "industry", "subsegment",
    "business_model", "geography", "customer", "buyer", "excluded_buyer", "company_size",
    "commercial_constraint", "hard_exclusion_candidate", "evidence_rule", "unknown", "other",
)

# Default confidence per origin (0..1). Deterministic, transparent.
_DEFAULT_CONFIDENCE = {
    ORIGIN_USER: 0.9, ORIGIN_SOURCE: 0.7, ORIGIN_AI: 0.4, ORIGIN_SYSTEM: 0.1,
}
_PRESENT_STATUSES = (CONFIRMED, CONFLICTING, PROPOSED)   # "has a value" (not unknown/rejected)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# --- normalization helpers (conservative; no semantic reinterpretation) ------

_LEGAL_SUFFIXES = ("inc", "inc.", "llc", "ltd", "ltd.", "limited", "gmbh", "corp",
                   "corp.", "co", "co.", "plc", "ag", "sa", "bv", "oy", "ab")
# Explicit, safe geography aliases only — no fuzzy inference.
_GEO_ALIASES = {
    "us": "united states", "usa": "united states", "u.s.": "united states",
    "u.s.a.": "united states", "uk": "united kingdom", "u.k.": "united kingdom",
    "uae": "united arab emirates", "eu": "european union",
}


def normalize_text(value: str) -> str:
    """Casefold + trim + collapse internal whitespace. No synonym inference, no rewriting."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def normalize_company_name(value: str) -> str:
    """normalize_text + strip trailing legal suffix and surrounding punctuation (explicit list)."""
    t = normalize_text(value).replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip()
    parts = t.split(" ")
    while parts and parts[-1].strip(".") in {s.strip(".") for s in _LEGAL_SUFFIXES}:
        parts.pop()
    return " ".join(parts).strip()


def normalize_role(value: str) -> str:
    """normalize_text + drop trailing punctuation. No abbreviation expansion (that would be a guess)."""
    return normalize_text(value).strip(" .,-/")


def normalize_geography(value: str) -> str:
    """normalize_text + map only explicit, safe aliases (US->united states, UK->united kingdom, ...)."""
    t = normalize_text(value).strip(" .,")
    return _GEO_ALIASES.get(t, _GEO_ALIASES.get(t.replace(".", ""), t))


def normalize_employee_range(value: str) -> str:
    """Reuse the engine's range parser to canonicalize '50 - 500'/'50–500'/'50+' -> 'low-high'/'low+'."""
    from icp_profile import parse_employee_range
    rng = parse_employee_range(value)
    if rng.low is None:
        return normalize_text(value)
    if rng.high is None:
        return f"{rng.low}+"
    if rng.low == rng.high:
        return str(rng.low)
    return f"{rng.low}-{rng.high}"


def normalize_url(value: str) -> str:
    """Lowercase, strip scheme/www and trailing slash. No redirect following, no canonicalization."""
    t = normalize_text(value)
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^www\.", "", t)
    return t.rstrip("/")


def normalize_list(values) -> list[str]:
    """normalize_text each element, drop empties, de-duplicate preserving order."""
    out, seen = [], set()
    for v in values or []:
        n = normalize_text(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


_NORMALIZERS = {
    "company": normalize_company_name,
    "buyer": normalize_role,
    "excluded_buyer": normalize_role,
    "geography": normalize_geography,
    "company_size": normalize_employee_range,
}


def _normalize_for(category: str, value: str) -> str:
    return _NORMALIZERS.get(category, normalize_text)(value)


# --- typed models ------------------------------------------------------------

@dataclass
class SourceReference:
    source_id: str = ""
    filename: str = ""
    source_category: str = ""
    section_index: int | None = None
    page_or_slide: int | None = None
    character_start: int | None = None
    character_end: int | None = None
    excerpt_hash: str = ""
    extraction_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def _key(self):
        return (self.source_id, self.section_index, self.character_start,
                self.character_end, self.excerpt_hash)


@dataclass
class KnowledgeItem:
    knowledge_id: str = ""
    category: str = "other"
    attribute: str = ""
    value: str = ""
    normalized_value: str = ""
    status: str = PROPOSED
    confidence: float = 0.0
    source_references: list[SourceReference] = field(default_factory=list)
    evidence_excerpt: str = ""
    notes: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    user_confirmed: bool = False
    origin: str = ORIGIN_SOURCE
    temporal_context: str = TEMPORAL_UNKNOWN     # current | historical | former | proposed_future | unknown

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_active(self) -> bool:
        return self.status != REJECTED

    @property
    def has_value(self) -> bool:
        return self.status in _PRESENT_STATUSES and bool(self.normalized_value)


@dataclass
class ConflictRecord:
    conflict_id: str = ""
    category: str = ""
    attribute: str = ""
    item_ids: list[str] = field(default_factory=list)
    conflicting_values: list[str] = field(default_factory=list)
    status: str = CONFLICT_UNRESOLVED
    preferred_item_id: str | None = None
    resolution_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- derived-field selectors (category [+ attribute] -> aggregate list) -------

# Each aggregate field is a *view* over knowledge_items, not a separate store, so the two can never
# drift. (category, include_attributes, exclude_attributes).
_FIELD_SELECTORS = {
    "company_overview": ("company", None, None),
    "products": ("product", None, None),
    "services": ("service", None, None),
    "capabilities": ("capability", None, None),
    "technologies": ("technology", None, None),
    "industries": ("industry", None, {"target_market"}),
    "subsegments": ("subsegment", None, None),
    "business_models": ("business_model", None, None),
    "target_markets": ("industry", {"target_market"}, None),
    "target_geographies": ("geography", None, None),
    "current_customers": ("customer", None, {"best_customer", "lost_customer"}),
    "best_customer_examples": ("customer", {"best_customer"}, None),
    "lost_customer_examples": ("customer", {"lost_customer"}, None),
    "buyer_roles": ("buyer", None, None),
    "excluded_buyer_roles": ("excluded_buyer", None, None),
    "company_size_preferences": ("company_size", None, None),
    "commercial_constraints": ("commercial_constraint", None, None),
    "hard_exclusion_candidates": ("hard_exclusion_candidate", None, None),
    "evidence_rules": ("evidence_rule", None, None),
}


class BusinessKnowledge:
    """Aggregate store of business facts, with deterministic merge/conflict handling.

    ``entry_point`` is audit metadata only — behavior is identical for generate_new /
    standardize_existing.
    """

    def __init__(self, *, source_package_id: str = "", entry_point: str | None = None):
        self.knowledge_id = _new_id("bk")
        self.created_at = _now()
        self.updated_at = self.created_at
        self.source_package_id = source_package_id
        self.entry_point = entry_point            # audit only; never branches behavior
        self.knowledge_items: list[KnowledgeItem] = []
        self.conflicts: list[ConflictRecord] = []
        self.warnings: list[str] = []
        self.unknown_fields: list[str] = []

    # --- item management -----------------------------------------------------

    def add_item(self, category: str, attribute: str, value: str, *,
                 status: str | None = None, confidence: float | None = None,
                 origin: str = ORIGIN_SOURCE, source_references=None, evidence_excerpt: str = "",
                 notes=None, user_confirmed: bool = False, normalized_value: str | None = None,
                 temporal_context: str = TEMPORAL_UNKNOWN, merge: bool = True) -> KnowledgeItem:
        """Add a knowledge item, applying deterministic merge/conflict rules (unless merge=False)."""
        if category not in CATEGORIES:
            self.warnings.append(f"Unknown category '{category}' recorded as-is.")
        norm = normalize_text(normalized_value) if normalized_value is not None \
            else _normalize_for(category, value)

        if status is None:
            status = (PROPOSED if origin == ORIGIN_AI else
                      UNKNOWN if origin == ORIGIN_SYSTEM else CONFIRMED)
        if user_confirmed:
            status = CONFIRMED
        if confidence is None:
            confidence = _DEFAULT_CONFIDENCE.get(origin, 0.5)

        item = KnowledgeItem(
            knowledge_id=_new_id("kn"), category=category, attribute=attribute, value=value,
            normalized_value=norm, status=status, confidence=float(confidence),
            source_references=list(source_references or []), evidence_excerpt=evidence_excerpt,
            notes=list(notes or []), created_at=_now(), updated_at=_now(),
            user_confirmed=user_confirmed, origin=origin, temporal_context=temporal_context,
        )
        if category == "unknown" and attribute and attribute not in self.unknown_fields:
            self.unknown_fields.append(attribute)

        self.updated_at = _now()
        if not merge:
            self.knowledge_items.append(item)
            return item

        same = [it for it in self.knowledge_items
                if it.is_active and it.category == category and it.attribute == attribute]
        exact = [it for it in same if it.normalized_value == norm]
        if exact:
            self._merge_into(exact[0], item)         # rule 1: exact duplicate merges
            return exact[0]

        self.knowledge_items.append(item)
        differing = [it for it in same if it.normalized_value != norm and it.has_value]
        if differing and item.has_value:             # rule 2: same attr, different value -> conflict
            self.mark_conflict([item.knowledge_id] + [d.knowledge_id for d in differing],
                               category=category, attribute=attribute)
        return item

    def _merge_into(self, target: KnowledgeItem, incoming: KnowledgeItem) -> None:
        existing_keys = {r._key() for r in target.source_references}
        for ref in incoming.source_references:
            if ref._key() not in existing_keys:
                target.source_references.append(ref)
                existing_keys.add(ref._key())
        target.confidence = max(target.confidence, incoming.confidence)   # keep highest
        for n in incoming.notes:
            if n not in target.notes:
                target.notes.append(n)
        if not target.evidence_excerpt and incoming.evidence_excerpt:
            target.evidence_excerpt = incoming.evidence_excerpt
        # A confirmed observation upgrades a proposed one; never downgrade a user-confirmed item.
        if not target.user_confirmed and incoming.status == CONFIRMED and target.status == PROPOSED:
            target.status = CONFIRMED
        target.updated_at = _now()

    def get_items(self, category: str | None = None, status: str | None = None) -> list[KnowledgeItem]:
        return [it for it in self.knowledge_items
                if (category is None or it.category == category)
                and (status is None or it.status == status)]

    def confirm_item(self, knowledge_id: str, note: str = "") -> KnowledgeItem:
        """User confirms a value: it becomes preferred; contrary evidence is kept (rule 3)."""
        item = self._get(knowledge_id)
        item.user_confirmed = True
        item.status = CONFIRMED
        item.updated_at = _now()
        for rec in self.conflicts:
            if knowledge_id in rec.item_ids and rec.status == CONFLICT_UNRESOLVED:
                rec.status = CONFLICT_USER
                rec.preferred_item_id = knowledge_id
                rec.resolution_note = note
        self.updated_at = _now()
        return item

    def reject_item(self, knowledge_id: str, note: str = "") -> KnowledgeItem:
        """Reject a value: kept for audit, excluded from active summaries (rule 5)."""
        item = self._get(knowledge_id)
        item.status = REJECTED
        if note:
            item.notes.append(note)
        item.updated_at = _now()
        self.updated_at = _now()
        return item

    def edit_item(self, knowledge_id: str, *, value: str | None = None,
                  category: str | None = None, attribute: str | None = None,
                  temporal_context: str | None = None, note: str = "") -> KnowledgeItem:
        """Human edit of an item's value/category/attribute/temporal_context. Marks the item
        ``origin=user_input`` and recomputes the normalized value. Status/user_confirmed are left
        unchanged (a bare edit is not a confirmation) — call ``confirm_item`` to protect it from AI
        overwrite. Source references and prior notes are preserved."""
        item = self._get(knowledge_id)
        if category is not None:
            item.category = category
        if attribute is not None:
            item.attribute = attribute
        if value is not None:
            item.value = value
        if temporal_context is not None:
            item.temporal_context = temporal_context
        if value is not None or category is not None:
            item.normalized_value = _normalize_for(item.category, item.value)
        item.origin = ORIGIN_USER
        if note:
            item.notes.append(note)
        item.updated_at = _now()
        self.updated_at = _now()
        return item

    def mark_conflict(self, item_ids: list[str], *, category: str | None = None,
                      attribute: str | None = None, note: str = "") -> ConflictRecord:
        items = [self._get(i) for i in item_ids]
        preferred = next((it for it in items if it.user_confirmed), None)
        for it in items:
            it.status = CONFIRMED if it is preferred else CONFLICTING
            it.updated_at = _now()
        rec = ConflictRecord(
            conflict_id=_new_id("cf"),
            category=category or items[0].category,
            attribute=attribute or items[0].attribute,
            item_ids=list(item_ids),
            conflicting_values=[it.value for it in items],
            status=CONFLICT_USER if preferred else CONFLICT_UNRESOLVED,
            preferred_item_id=preferred.knowledge_id if preferred else None,
            resolution_note=note,
        )
        self.conflicts.append(rec)
        self.updated_at = _now()
        return rec

    def resolve_conflict(self, conflict_id: str, preferred_item_id: str, *,
                         note: str = "", system: bool = False) -> ConflictRecord:
        """Resolve a conflict. system_resolved is only for mechanical cases (identical normalized)."""
        rec = next((c for c in self.conflicts if c.conflict_id == conflict_id), None)
        if rec is None:
            raise KeyError(conflict_id)
        if system:
            vals = {self._get(i).normalized_value for i in rec.item_ids}
            if len(vals) > 1:
                raise ValueError("system_resolved only allowed for identical normalized values")
            rec.status = CONFLICT_SYSTEM
        else:
            rec.status = CONFLICT_USER
            self.confirm_item(preferred_item_id, note=note)
        rec.preferred_item_id = preferred_item_id
        rec.resolution_note = note
        self.updated_at = _now()
        return rec

    def merge_items(self, keep_id: str, other_id: str) -> KnowledgeItem:
        """Explicitly merge two same-attribute items with identical normalized value into one."""
        keep, other = self._get(keep_id), self._get(other_id)
        if (keep.category, keep.attribute) != (other.category, other.attribute):
            raise ValueError("can only merge items of the same category+attribute")
        if keep.normalized_value != other.normalized_value:
            raise ValueError("values differ; use mark_conflict instead of merge")
        self._merge_into(keep, other)
        self.knowledge_items.remove(other)
        return keep

    def add_unknown(self, field_name: str, note: str = "") -> None:
        if field_name not in self.unknown_fields:
            self.unknown_fields.append(field_name)
        self.add_item("unknown", field_name, "", status=UNKNOWN, origin=ORIGIN_SYSTEM,
                      notes=[note] if note else None, merge=False)

    # --- derived views -------------------------------------------------------

    def _values(self, category, include_attrs=None, exclude_attrs=None) -> list[str]:
        picked = [it for it in self.knowledge_items
                  if it.category == category and it.has_value
                  and (include_attrs is None or it.attribute in include_attrs)
                  and (exclude_attrs is None or it.attribute not in exclude_attrs)]
        picked.sort(key=lambda it: (it.created_at, it.knowledge_id))
        return [it.value for it in picked]

    def field(self, name: str) -> list[str]:
        cat, inc, exc = _FIELD_SELECTORS[name]
        return self._values(cat, inc, exc)

    # Convenience properties for the aggregate fields (all derived, never stored twice).
    def __getattr__(self, name):
        # Called only when normal attribute lookup fails; maps aggregate field names to views.
        if name in _FIELD_SELECTORS:
            return self.field(name)
        raise AttributeError(name)

    # --- serialization / summary --------------------------------------------

    def to_dict(self) -> dict:
        d = {
            "knowledge_id": self.knowledge_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_package_id": self.source_package_id,
            "entry_point": self.entry_point,
        }
        for name in _FIELD_SELECTORS:
            d[name] = self.field(name)
        d["unknown_fields"] = list(self.unknown_fields)
        d["conflicts"] = [c.to_dict() for c in self.conflicts]
        d["warnings"] = list(self.warnings)
        d["knowledge_items"] = [it.to_dict() for it in self.knowledge_items]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "BusinessKnowledge":
        """Reconstruct a BusinessKnowledge from ``to_dict`` output (Sprint 6, deterministic).

        The derived aggregate field lists in ``to_dict`` are ignored — every value is rebuilt from the
        stored ``knowledge_items`` / ``conflicts`` so the two can never drift after a round-trip."""
        obj = cls(source_package_id=d.get("source_package_id", ""),
                  entry_point=d.get("entry_point"))
        obj.knowledge_id = d.get("knowledge_id", obj.knowledge_id)
        obj.created_at = d.get("created_at", obj.created_at)
        obj.updated_at = d.get("updated_at", obj.updated_at)
        obj.warnings = list(d.get("warnings", []))
        obj.unknown_fields = list(d.get("unknown_fields", []))
        items = []
        for it in d.get("knowledge_items", []):
            payload = {k: v for k, v in it.items() if k != "source_references"}
            item = KnowledgeItem(**payload)
            item.source_references = [SourceReference(**r) for r in it.get("source_references", [])]
            items.append(item)
        obj.knowledge_items = items
        obj.conflicts = [ConflictRecord(**c) for c in d.get("conflicts", [])]
        return obj

    def summary(self) -> dict:
        """Active summary (rejected excluded): grouped values, counts, unresolved conflicts."""
        by_status: dict[str, int] = {}
        for it in self.knowledge_items:
            by_status[it.status] = by_status.get(it.status, 0) + 1
        return {
            "total_items": len(self.knowledge_items),
            "active_items": sum(1 for it in self.knowledge_items if it.is_active),
            "by_status": by_status,
            "unresolved_conflicts": sum(1 for c in self.conflicts
                                        if c.status == CONFLICT_UNRESOLVED),
            "unknown_fields": list(self.unknown_fields),
            "fields": {name: self.field(name) for name in _FIELD_SELECTORS},
        }

    # --- internal ------------------------------------------------------------

    def _get(self, knowledge_id: str) -> KnowledgeItem:
        for it in self.knowledge_items:
            if it.knowledge_id == knowledge_id:
                return it
        raise KeyError(knowledge_id)


# --- SourcePackage compatibility --------------------------------------------

def from_source_package(package, *, entry_point: str | None = None) -> BusinessKnowledge:
    """Initialize an EMPTY BusinessKnowledge from a SourcePackage.

    Preserves the package id and copies package warnings. It does NOT parse semantic facts from
    ``merged_text`` and creates NO invented knowledge items — semantic extraction is a later step.
    """
    bk = BusinessKnowledge(source_package_id=getattr(package, "package_id", ""),
                           entry_point=entry_point)
    bk.warnings.extend(list(getattr(package, "package_warnings", [])))
    return bk
