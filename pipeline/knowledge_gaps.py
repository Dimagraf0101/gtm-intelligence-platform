"""Deterministic gap analysis over BusinessKnowledge (Sprint 4.1C).

Identifies what is still missing before an ICP can be generated, ordered by severity, and produces
concise clarification questions. It never treats missing information as negative evidence — a gap is
"we still need to ask", not "this is false". Offline: no LLM, no network, no API.

Readiness measured here is *readiness for ICP generation only* — it says nothing about whether the
business facts are commercially correct.
"""
from __future__ import annotations

import uuid
import dataclasses
from dataclasses import dataclass, asdict

import business_knowledge as bkmod

# severities
BLOCKING = "blocking"
IMPORTANT = "important"
OPTIONAL = "optional"

# core categories whose unresolved conflicts are considered "important"
_CORE_CONFLICT_CATEGORIES = {
    "product", "service", "industry", "subsegment", "buyer", "company_size",
    "geography", "hard_exclusion_candidate",
}


@dataclass
class GapItem:
    gap_id: str = ""
    section: str = ""
    field: str = ""
    severity: str = OPTIONAL
    reason: str = ""
    current_status: str = "missing"
    suggested_question: str = ""
    related_knowledge_ids: list[str] = dataclasses.field(default_factory=list)
    resolved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KnowledgeGapReport:
    is_ready_for_icp_generation: bool = False
    blocking_gaps: list[GapItem] = dataclasses.field(default_factory=list)
    important_gaps: list[GapItem] = dataclasses.field(default_factory=list)
    optional_gaps: list[GapItem] = dataclasses.field(default_factory=list)
    unresolved_conflicts: list[dict] = dataclasses.field(default_factory=list)
    completeness_score: int = 0
    section_completeness: dict = dataclasses.field(default_factory=dict)
    suggested_questions: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_ready_for_icp_generation": self.is_ready_for_icp_generation,
            "blocking_gaps": [g.to_dict() for g in self.blocking_gaps],
            "important_gaps": [g.to_dict() for g in self.important_gaps],
            "optional_gaps": [g.to_dict() for g in self.optional_gaps],
            "unresolved_conflicts": list(self.unresolved_conflicts),
            "completeness_score": self.completeness_score,
            "section_completeness": self.section_completeness,
            "suggested_questions": self.suggested_questions,
            "warnings": self.warnings,
        }


def _gap(section, fieldname, severity, reason, question) -> GapItem:
    return GapItem(gap_id=f"gap-{uuid.uuid4().hex[:8]}", section=section, field=fieldname,
                   severity=severity, reason=reason, current_status="missing",
                   suggested_question=question)


def detect_gaps(bk: "bkmod.BusinessKnowledge") -> KnowledgeGapReport:
    """Analyze a BusinessKnowledge aggregate and return a KnowledgeGapReport.

    Entry-point neutral: ``bk.entry_point`` is ignored here.
    """
    def has(name):
        return bool(bk.field(name))

    products = has("products")
    services = has("services")
    industries = has("industries")
    subsegments = has("subsegments")
    business_models = has("business_models")
    target_markets = has("target_markets")
    geographies = has("target_geographies")
    company_size = has("company_size_preferences")
    buyers = has("buyer_roles")
    excluded_buyers = has("excluded_buyer_roles")
    hard_exclusions = has("hard_exclusion_candidates")
    evidence_rules = has("evidence_rules")
    best_customers = has("best_customer_examples")
    lost_customers = has("lost_customer_examples")
    technologies = has("technologies")
    constraints = has("commercial_constraints")
    company = has("company_overview")

    any_target = any([industries, subsegments, business_models, target_markets,
                      geographies, company_size])
    any_dimension_input = any([industries, subsegments, buyers, company_size, geographies])

    blocking: list[GapItem] = []
    important: list[GapItem] = []
    optional: list[GapItem] = []

    # --- blocking ---
    if not (products or services):
        blocking.append(_gap("Company and Offer", "product_or_service", BLOCKING,
                             "No product or service is recorded to sell against.",
                             "What product or service is this ICP intended to sell?"))
    if not company:
        blocking.append(_gap("Company and Offer", "company_context", BLOCKING,
                             "No company context/overview is recorded.",
                             "How would you briefly describe your company and what it offers?"))
    if not any_target:
        blocking.append(_gap("Target Company", "target_company", BLOCKING,
                             "No target-company attributes are defined.",
                             "What kind of companies is this ICP meant to target?"))
    if not buyers:
        blocking.append(_gap("Buyers", "buyer_roles", BLOCKING,
                             "No buyer role is recorded.",
                             "Who is the primary buyer for this offer?"))
    if not any_dimension_input:
        blocking.append(_gap("Qualification Inputs", "qualification_dimensions", BLOCKING,
                             "No attributes exist that could seed qualification dimensions.",
                             "Which attributes matter most when deciding if a company is a good fit?"))
    if not (hard_exclusions or excluded_buyers):
        blocking.append(_gap("Exclusions and Constraints", "target_vs_exclusion", BLOCKING,
                             "No hard exclusions are declared, so target vs. exclusion cannot be "
                             "separated.",
                             "Which company types or buyers should be explicitly excluded "
                             "(as hard rejections, not just preferences)?"))

    # --- important ---
    if not geographies:
        important.append(_gap("Target Company", "target_geographies", IMPORTANT,
                             "No target geography preference is recorded.",
                             "Which geographies do you prefer to target?"))
    if not company_size:
        important.append(_gap("Target Company", "company_size_preferences", IMPORTANT,
                             "No company-size preference is recorded.",
                             "What company size do you prefer, and is it a preference or a hard "
                             "exclusion?"))
    if not best_customers:
        important.append(_gap("Examples", "best_customer_examples", IMPORTANT,
                             "No best-customer examples are recorded.",
                             "Can you name a few of your best current customers?"))
    if not excluded_buyers:
        important.append(_gap("Buyers", "excluded_buyer_roles", IMPORTANT,
                             "No excluded buyer roles are recorded.",
                             "Are there buyer roles you never want to target?"))
    if not evidence_rules:
        important.append(_gap("Evidence and Unknowns", "evidence_rules", IMPORTANT,
                             "No evidence rules are recorded.",
                             "What evidence must be present before a company is treated as a match?"))
    if not bk.unknown_fields:
        important.append(_gap("Evidence and Unknowns", "unknown_fields", IMPORTANT,
                             "No unknown/enrichment fields are declared.",
                             "Which fields may remain unknown without penalizing the lead?"))

    core_conflicts = [c for c in bk.conflicts
                      if c.status == bkmod.CONFLICT_UNRESOLVED
                      and c.category in _CORE_CONFLICT_CATEGORIES]
    for c in core_conflicts:
        g = _gap("Conflicts", c.attribute or c.category, IMPORTANT,
                 f"Unresolved conflict on {c.category}/{c.attribute}: "
                 f"{', '.join(str(v) for v in c.conflicting_values)}.",
                 f"Which value is correct for {c.attribute or c.category}?")
        g.current_status = "conflicting"
        g.related_knowledge_ids = list(c.item_ids)
        important.append(g)

    # --- optional ---
    if not lost_customers:
        optional.append(_gap("Examples", "lost_customer_examples", OPTIONAL,
                             "No lost-customer examples are recorded.",
                             "Are there customers you lost or that turned out to be a poor fit?"))
    if not _has_competitors(bk):
        optional.append(_gap("Company and Offer", "competitors", OPTIONAL,
                             "No competitor information is recorded.",
                             "Who are your main competitors?"))
    if not technologies:
        optional.append(_gap("Qualification Inputs", "technologies", OPTIONAL,
                             "No technology preferences are recorded.",
                             "Are there technologies a target company should (or shouldn't) use?"))
    if not subsegments:
        optional.append(_gap("Target Company", "subsegments", OPTIONAL,
                             "No market subsegments are recorded.",
                             "Are there specific market subsegments you focus on?"))
    if not constraints:
        optional.append(_gap("Exclusions and Constraints", "commercial_constraints", OPTIONAL,
                             "No commercial constraints are recorded.",
                             "Are there commercial constraints (budget, contract, region) to honor?"))

    completeness, section_scores = _completeness(
        products, services, company, any_target, industries or subsegments or target_markets,
        company_size, geographies, buyers, excluded_buyers, hard_exclusions, constraints,
        evidence_rules, bool(bk.unknown_fields), best_customers, lost_customers)

    report = KnowledgeGapReport(
        is_ready_for_icp_generation=(len(blocking) == 0),
        blocking_gaps=blocking, important_gaps=important, optional_gaps=optional,
        unresolved_conflicts=[c.to_dict() for c in bk.conflicts
                              if c.status == bkmod.CONFLICT_UNRESOLVED],
        completeness_score=completeness, section_completeness=section_scores,
        warnings=list(bk.warnings),
    )
    # Questions ordered by severity (blocking -> important -> optional), de-duplicated.
    seen, ordered = set(), []
    for g in blocking + important + optional:
        if g.suggested_question not in seen:
            seen.add(g.suggested_question)
            ordered.append(g.suggested_question)
    report.suggested_questions = ordered
    return report


def _has_competitors(bk) -> bool:
    return any(it.has_value and (it.category == "competitor" or it.attribute == "competitor")
               for it in bk.knowledge_items)


# Section weights for the readiness completeness score (transparent; sum = 100).
_SECTION_WEIGHTS = {
    "Company and Offer": 20,
    "Target Company": 20,
    "Buyers": 15,
    "Qualification Inputs": 20,
    "Exclusions and Constraints": 10,
    "Evidence and Unknowns": 10,
    "Examples": 5,
}
assert sum(_SECTION_WEIGHTS.values()) == 100


def _completeness(products, services, company, any_target, target_attrs, company_size,
                  geographies, buyers, excluded_buyers, hard_exclusions, constraints,
                  evidence_rules, has_unknowns, best_customers, lost_customers):
    """Transparent readiness score.

    Each section's fraction = (filled key sub-fields) / (total key sub-fields); its contribution =
    round(fraction * weight). completeness_score = sum of contributions (0..100). Readiness only —
    it does not measure business correctness.
    """
    def frac(filled, total):
        return (sum(1 for x in filled if x) / total) if total else 0.0

    sections = {
        # Company and Offer: company context + something to sell.
        "Company and Offer": frac([company, (products or services)], 2),
        # Target Company: target attributes + size + geography.
        "Target Company": frac([target_attrs, company_size, geographies], 3),
        # Buyers: primary buyers + excluded buyers.
        "Buyers": frac([buyers, excluded_buyers], 2),
        # Qualification Inputs: at least one dimension-seeding attribute per axis.
        "Qualification Inputs": frac([target_attrs, buyers, company_size, geographies], 4),
        # Exclusions and Constraints.
        "Exclusions and Constraints": frac([hard_exclusions, constraints], 2),
        # Evidence and Unknowns.
        "Evidence and Unknowns": frac([evidence_rules, has_unknowns], 2),
        # Examples.
        "Examples": frac([best_customers, lost_customers], 2),
    }
    section_scores = {name: round(_SECTION_WEIGHTS[name] * f) for name, f in sections.items()}
    return sum(section_scores.values()), section_scores
