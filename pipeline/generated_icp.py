"""GeneratedICP — the typed, deterministic ICP model produced by the ICP Generator.

Both future entry points (``generate_new`` and ``standardize_existing``) produce **the same**
``GeneratedICP``; nothing downstream depends on the entry point. This module is pure Python
(dataclasses + json) — no LLM, no network, no API, no document extraction, no persistence.

Public surface: the dataclasses below plus ``GeneratedICP.to_dict()`` / ``.to_json()`` /
``.to_markdown()``. Markdown/JSON contain conclusions only — never chain-of-thought or raw prompts.

See docs/iqs/ICP_PROFILE_SCHEMA.md and docs/iqs/IQS_v1.0.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

# --- controlled vocabularies -------------------------------------------------

STATUS_DRAFT = "Draft"
STATUS_NEEDS_INFO = "Needs Information"
STATUS_READY = "Ready for Review"
STATUS_APPROVED = "Approved"
STATUSES = (STATUS_DRAFT, STATUS_NEEDS_INFO, STATUS_READY, STATUS_APPROVED)

ENTRY_GENERATE_NEW = "generate_new"
ENTRY_STANDARDIZE_EXISTING = "standardize_existing"
ENTRY_POINTS = (ENTRY_GENERATE_NEW, ENTRY_STANDARDIZE_EXISTING)

EVAL_DETERMINISTIC = "deterministic"
EVAL_SEMANTIC = "semantic"
EVAL_MODES = (EVAL_DETERMINISTIC, EVAL_SEMANTIC)

SCOPE_CURRENT_COMPANY = "current_company"
SCOPE_CURRENT_PERSON = "current_person"
SCOPE_RECORD_VALIDITY = "record_validity"
EXCLUSION_SCOPES = (SCOPE_CURRENT_COMPANY, SCOPE_CURRENT_PERSON, SCOPE_RECORD_VALIDITY)

# ICP scope (Sprint 7): a "general" ICP is generated from company-level knowledge only (the reusable
# capability baseline); an "adapted" ICP is generated from composed company + hypothesis knowledge.
# Default is "adapted" because every ICP the pipeline produced before this sprint is hypothesis-scoped.
# This field is NOT part of the fingerprint canonical form, so adding it never changes any fingerprint.
ICP_SCOPE_GENERAL = "general"
ICP_SCOPE_ADAPTED = "adapted"
ICP_SCOPES = (ICP_SCOPE_GENERAL, ICP_SCOPE_ADAPTED)


# --- sections ----------------------------------------------------------------

@dataclass
class Metadata:
    name: str = ""
    version: str = "1"
    status: str = STATUS_DRAFT
    created_date: str = field(default_factory=lambda: date.today().isoformat())
    updated_date: str = field(default_factory=lambda: date.today().isoformat())
    entry_point: str = ENTRY_GENERATE_NEW          # generate_new | standardize_existing
    source_files: list[str] = field(default_factory=list)
    icp_scope: str = ICP_SCOPE_ADAPTED             # general | adapted (Sprint 7; not in fingerprint)
    # Provenance (Sprint 8): for an Adapted ICP, the ArtifactIdentity string of the source General ICP
    # it was derived from ("" for General ICPs and pre-Sprint-8 artifacts). Not in the fingerprint.
    derived_from_general_icp: str = ""


@dataclass
class BusinessContext:
    description: str = ""
    product_or_service: str = ""
    value_proposition: str = ""
    capabilities: list[str] = field(default_factory=list)
    business_model: str = ""
    notes: str = ""


@dataclass
class TargetCompanies:
    target_industries: list[str] = field(default_factory=list)
    target_subsegments: list[str] = field(default_factory=list)
    target_company_types: list[str] = field(default_factory=list)
    preferred_employee_ranges: list[str] = field(default_factory=list)   # PREFERENCE, not exclusion
    acceptable_employee_ranges: list[str] = field(default_factory=list)  # PREFERENCE, not exclusion
    target_geographies: list[str] = field(default_factory=list)          # PREFERENCE, not exclusion
    target_business_models: list[str] = field(default_factory=list)
    preferred_attributes: list[str] = field(default_factory=list)


@dataclass
class TargetBuyers:
    primary_buyer_roles: list[str] = field(default_factory=list)
    secondary_buyer_roles: list[str] = field(default_factory=list)
    excluded_buyer_roles: list[str] = field(default_factory=list)
    title_tiers: list[str] = field(default_factory=list)
    buyer_notes: str = ""


@dataclass
class QualificationDimension:
    name: str = ""
    purpose: str = ""
    weight: int = 0
    scoring_guidance: str = ""
    required_evidence_attributes: list[str] = field(default_factory=list)
    external_enrichment_required: bool = False
    weight_is_default: bool = False               # True when weight was defaulted/suggested, not chosen


@dataclass
class PriorityBand:
    label: str
    min_score: int
    max_score: int


# The approved operational bands (Sprint 3.5.4). Priority is derived from the numeric lead score.
def standard_priority_bands() -> list[PriorityBand]:
    return [
        PriorityBand("Priority 1", 90, 100),
        PriorityBand("Priority 2", 75, 89),
        PriorityBand("Priority 3", 60, 74),
        PriorityBand("Priority 4", 45, 59),
        PriorityBand("Priority 5", 30, 44),
        PriorityBand("Disqualified", 0, 29),
    ]


@dataclass
class HardExclusion:
    rule: str = ""
    reason: str = ""
    evidence_required: str = ""
    evaluation_mode: str = EVAL_DETERMINISTIC     # deterministic | semantic
    scope: str = SCOPE_CURRENT_COMPANY            # current_company | current_person | record_validity


@dataclass
class EvidenceRequirements:
    accepted_sources: list[str] = field(default_factory=list)
    current_employment_rules: str = ""
    previous_employment_restrictions: str = ""
    conflict_handling: str = ""
    evidence_quality_rules: str = ""


@dataclass
class Examples:
    ideal_leads: list[str] = field(default_factory=list)
    acceptable_leads: list[str] = field(default_factory=list)
    non_ideal_leads: list[str] = field(default_factory=list)


@dataclass
class HistoryEntry:
    version: str = ""
    date: str = ""
    author: str = ""
    change_summary: str = ""


@dataclass
class GeneratedICP:
    metadata: Metadata = field(default_factory=Metadata)
    business_context: BusinessContext = field(default_factory=BusinessContext)
    target_companies: TargetCompanies = field(default_factory=TargetCompanies)
    target_buyers: TargetBuyers = field(default_factory=TargetBuyers)
    dimensions: list[QualificationDimension] = field(default_factory=list)
    priority_thresholds: list[PriorityBand] = field(default_factory=standard_priority_bands)
    hard_exclusions: list[HardExclusion] = field(default_factory=list)
    evidence_requirements: EvidenceRequirements = field(default_factory=EvidenceRequirements)
    unknown_fields: list[str] = field(default_factory=list)
    enrichment_fields: list[str] = field(default_factory=list)
    ambiguous_definitions: list[str] = field(default_factory=list)
    examples: Examples = field(default_factory=Examples)
    warnings: list[str] = field(default_factory=list)
    history: list[HistoryEntry] = field(default_factory=list)

    # --- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GeneratedICP":
        """Reconstruct a GeneratedICP from ``to_dict`` output (Sprint 6, deterministic, lossless).

        The rebuilt object is byte-for-byte identical in canonical content, so its fingerprint is
        unchanged across a round-trip."""
        return cls(
            metadata=Metadata(**d["metadata"]),
            business_context=BusinessContext(**d["business_context"]),
            target_companies=TargetCompanies(**d["target_companies"]),
            target_buyers=TargetBuyers(**d["target_buyers"]),
            dimensions=[QualificationDimension(**x) for x in d.get("dimensions", [])],
            priority_thresholds=[PriorityBand(**x) for x in d.get("priority_thresholds", [])],
            hard_exclusions=[HardExclusion(**x) for x in d.get("hard_exclusions", [])],
            evidence_requirements=EvidenceRequirements(**d["evidence_requirements"]),
            unknown_fields=list(d.get("unknown_fields", [])),
            enrichment_fields=list(d.get("enrichment_fields", [])),
            ambiguous_definitions=list(d.get("ambiguous_definitions", [])),
            examples=Examples(**d["examples"]),
            warnings=list(d.get("warnings", [])),
            history=[HistoryEntry(**x) for x in d.get("history", [])],
        )

    def to_json(self) -> str:
        # UTF-8 serializable, deterministic (dataclass field order preserved), no reordering.
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        m, bc, tc, tb = self.metadata, self.business_context, self.target_companies, self.target_buyers
        out: list[str] = []
        a = out.append

        def bullets(items):
            return "\n".join(f"- {x}" for x in items) if items else "- _(none)_"

        a(f"# ICP: {m.name or '(unnamed)'}")
        a(f"_Version {m.version} · Status: {m.status} · Entry point: {m.entry_point} · "
          f"Updated {m.updated_date}_")
        if m.source_files:
            a(f"_Sources: {', '.join(m.source_files)}_")

        a("\n## Business Context")
        if bc.product_or_service:
            a(f"- **Sells:** {bc.product_or_service}")
        if bc.description:
            a(f"- **About:** {bc.description}")
        if bc.value_proposition:
            a(f"- **Value:** {bc.value_proposition}")
        if bc.business_model:
            a(f"- **Business model:** {bc.business_model}")
        if bc.capabilities:
            a(f"- **Capabilities:** {', '.join(bc.capabilities)}")

        a("\n## Target Companies _(preferences — not rejections)_")
        a(f"- **Industries:** {', '.join(tc.target_industries) or '—'}")
        a(f"- **Subsegments:** {', '.join(tc.target_subsegments) or '—'}")
        a(f"- **Company types:** {', '.join(tc.target_company_types) or '—'}")
        a(f"- **Preferred size:** {', '.join(tc.preferred_employee_ranges) or '—'}"
          f"  ·  **Acceptable size:** {', '.join(tc.acceptable_employee_ranges) or '—'}")
        a(f"- **Geographies:** {', '.join(tc.target_geographies) or '—'}")

        a("\n## Target Buyers")
        a(f"- **Primary:** {', '.join(tb.primary_buyer_roles) or '—'}")
        a(f"- **Secondary/approver:** {', '.join(tb.secondary_buyer_roles) or '—'}")
        a(f"- **Excluded roles:** {', '.join(tb.excluded_buyer_roles) or '—'}")
        if tb.title_tiers:
            a(f"- **Title tiers:** {', '.join(tb.title_tiers)}")

        a("\n## Qualification Dimensions")
        a("| Dimension | Weight | Enrichment | Purpose |")
        a("|---|---:|:--:|---|")
        for d in self.dimensions:
            a(f"| {d.name} | {d.weight} | {'yes' if d.external_enrichment_required else 'no'} "
              f"| {d.purpose or ''} |")
        a(f"_Total weight: {sum(d.weight for d in self.dimensions)}_")

        a("\n## Priority Thresholds")
        for b in self.priority_thresholds:
            a(f"- **{b.label}:** {b.min_score}–{b.max_score}")

        a("\n## Hard Exclusions _(explicit rejections only)_")
        if not self.hard_exclusions:
            a("- _(none declared)_")
        for e in self.hard_exclusions:
            a(f"- **{e.rule}** — {e.reason or ''} "
              f"_(mode: {e.evaluation_mode}, scope: {e.scope}; evidence: {e.evidence_required or '—'})_")

        a("\n## Evidence Requirements")
        er = self.evidence_requirements
        a(f"- **Accepted sources:** {', '.join(er.accepted_sources) or '—'}")
        if er.current_employment_rules:
            a(f"- **Current employment:** {er.current_employment_rules}")
        if er.previous_employment_restrictions:
            a(f"- **Previous employment:** {er.previous_employment_restrictions}")
        if er.conflict_handling:
            a(f"- **Conflicts:** {er.conflict_handling}")
        if er.evidence_quality_rules:
            a(f"- **Quality:** {er.evidence_quality_rules}")

        a("\n## Unknown Fields")
        a(bullets(self.unknown_fields))
        a("\n## Enrichment Fields")
        a(bullets(self.enrichment_fields))
        if self.ambiguous_definitions:
            a("\n## Ambiguous Definitions")
            a(bullets(self.ambiguous_definitions))

        a("\n## Examples")
        a(f"- **Ideal:** {'; '.join(self.examples.ideal_leads) or '—'}")
        a(f"- **Acceptable:** {'; '.join(self.examples.acceptable_leads) or '—'}")
        a(f"- **Non-ideal:** {'; '.join(self.examples.non_ideal_leads) or '—'}")

        if self.warnings:
            a("\n## Warnings")
            a(bullets(self.warnings))
        if self.history:
            a("\n## History")
            for h in self.history:
                a(f"- v{h.version} · {h.date} · {h.author}: {h.change_summary}")

        return "\n".join(out) + "\n"


# --- convenience -------------------------------------------------------------

def new_icp(name: str, entry_point: str = ENTRY_GENERATE_NEW) -> GeneratedICP:
    """A blank Draft ICP with standard priority bands, for either entry point."""
    return GeneratedICP(metadata=Metadata(name=name, entry_point=entry_point, status=STATUS_DRAFT))
