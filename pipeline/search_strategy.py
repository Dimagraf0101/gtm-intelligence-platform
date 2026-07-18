"""Search Strategy — how to find leads for one Market Hypothesis (Sprint 9).

A SearchStrategy is a **hypothesis-owned, versioned, immutable** artifact **derived from that
hypothesis's latest Approved Adapted ICP**. It describes *who to search for* — company and person
criteria, geography, signals, exclusions, and structured LinkedIn Sales Navigator **filter
recommendations** the user will configure manually later. It does **not** scrape or qualify leads,
and it never generates a Sales Navigator URL or invents LinkedIn filter ids.

Design (frozen architecture; nothing in the ICP identity/approval/qualification stack changes):

* Filters are **derived deterministically in Python** from the approved Adapted ICP (which was itself
  AI-proposed, IQS-validated, and human-approved). The LLM never owns filters, versions, status, or
  identity; an optional wording client may only refine the free-text ``objective`` / ``rationale``.
* Provenance ``derived_from_adapted_icp`` is the source ICP's ``ArtifactIdentity`` via
  ``icp_identity`` (status-stable, persisted). No ``GeneratedICP`` / fingerprint / ArtifactIdentity
  change.
* Lifecycle ``Draft → Reviewed → Approved → Archived`` is a small, forward-only status transition on
  this dataclass — **not** a second approval framework, and it never touches ``icp_approval``.
* Confidence is computed by Python from evidence completeness; the model never assigns it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import business_knowledge as bk
import icp_identity as idy
import icp_approval as ap
from icp_profile import parse_employee_range

# --- lifecycle vocabulary ----------------------------------------------------

STRATEGY_DRAFT = "Draft"
STRATEGY_REVIEWED = "Reviewed"
STRATEGY_APPROVED = "Approved"
STRATEGY_ARCHIVED = "Archived"
STRATEGY_STATUSES = (STRATEGY_DRAFT, STRATEGY_REVIEWED, STRATEGY_APPROVED, STRATEGY_ARCHIVED)

# Forward-only transitions. Approved content is frozen (only archival is allowed after approval).
_ALLOWED_TRANSITIONS = {
    STRATEGY_DRAFT: {STRATEGY_REVIEWED, STRATEGY_ARCHIVED},
    STRATEGY_REVIEWED: {STRATEGY_APPROVED, STRATEGY_DRAFT, STRATEGY_ARCHIVED},
    STRATEGY_APPROVED: {STRATEGY_ARCHIVED},
    STRATEGY_ARCHIVED: set(),
}

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_LEVELS = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)


class SearchStrategyError(ValueError):
    """Raised for an invalid transition, a mutation of an approved strategy, or invalid structure."""


def _norm_list(values) -> list:
    """Normalize a list of strings: trim, drop empties, de-duplicate case-insensitively (order kept)."""
    out, seen = [], set()
    for v in (values or []):
        s = " ".join(str(v).split()).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


# --- structured sub-models ---------------------------------------------------

@dataclass
class Geography:
    included: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    notes: str = ""


@dataclass
class CompanyCriteria:
    industries: list = field(default_factory=list)
    company_sizes: list = field(default_factory=list)          # e.g. "50-500" (validated ranges)
    company_types: list = field(default_factory=list)
    growth_signals: list = field(default_factory=list)
    technologies: list = field(default_factory=list)
    revenue_funding_indicators: list = field(default_factory=list)
    included_keywords: list = field(default_factory=list)
    excluded_keywords: list = field(default_factory=list)


@dataclass
class PersonCriteria:
    job_titles: list = field(default_factory=list)
    seniority_levels: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    decision_maker_roles: list = field(default_factory=list)
    influencer_roles: list = field(default_factory=list)
    excluded_job_titles: list = field(default_factory=list)
    excluded_functions: list = field(default_factory=list)


@dataclass
class SalesNavFilters:
    """Structured Sales Navigator filter *recommendations* (values only — no URL, no filter ids)."""
    geography: list = field(default_factory=list)
    industry: list = field(default_factory=list)
    company_headcount: list = field(default_factory=list)
    company_type: list = field(default_factory=list)
    current_job_title: list = field(default_factory=list)
    seniority: list = field(default_factory=list)
    function: list = field(default_factory=list)
    years_in_current_position: list = field(default_factory=list)
    keywords: list = field(default_factory=list)


@dataclass
class SearchStrategy:
    strategy_id: str = ""
    version: str = "1"
    status: str = STRATEGY_DRAFT
    hypothesis_id: str = ""                                     # scope: the owning hypothesis
    derived_from_adapted_icp: str = ""                          # source Adapted ICP ArtifactIdentity
    objective: str = ""
    geography: Geography = field(default_factory=Geography)
    company_criteria: CompanyCriteria = field(default_factory=CompanyCriteria)
    person_criteria: PersonCriteria = field(default_factory=PersonCriteria)
    buying_signals: list = field(default_factory=list)
    sales_nav_filters: SalesNavFilters = field(default_factory=SalesNavFilters)
    exclusions: list = field(default_factory=list)
    rationale: str = ""
    known_facts: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)
    confidence: str = CONFIDENCE_LOW
    created_at: str = ""
    updated_at: str = ""
    approved_by: str = ""
    approved_at: str = ""

    def __post_init__(self):
        if not self.strategy_id:
            self.strategy_id = bk._new_id("srch")
        if not self.created_at:
            self.created_at = bk._now()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SearchStrategy":
        return cls(
            strategy_id=d.get("strategy_id", ""), version=d.get("version", "1"),
            status=d.get("status", STRATEGY_DRAFT), hypothesis_id=d.get("hypothesis_id", ""),
            derived_from_adapted_icp=d.get("derived_from_adapted_icp", ""),
            objective=d.get("objective", ""),
            geography=Geography(**d.get("geography", {}) or {}),
            company_criteria=CompanyCriteria(**d.get("company_criteria", {}) or {}),
            person_criteria=PersonCriteria(**d.get("person_criteria", {}) or {}),
            buying_signals=list(d.get("buying_signals", [])),
            sales_nav_filters=SalesNavFilters(**d.get("sales_nav_filters", {}) or {}),
            exclusions=list(d.get("exclusions", [])), rationale=d.get("rationale", ""),
            known_facts=list(d.get("known_facts", [])), assumptions=list(d.get("assumptions", [])),
            unknowns=list(d.get("unknowns", [])), confidence=d.get("confidence", CONFIDENCE_LOW),
            created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
            approved_by=d.get("approved_by", ""), approved_at=d.get("approved_at", ""))


# --- deterministic confidence (Python owns this, never the LLM) --------------

def _derive_confidence(company: CompanyCriteria, person: PersonCriteria, geo: Geography,
                       exclusions: list) -> str:
    core = [bool(company.industries), bool(person.job_titles), bool(geo.included), bool(exclusions)]
    filled = sum(core)
    if filled >= 4:
        return CONFIDENCE_HIGH
    if filled >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


# --- deterministic validation ------------------------------------------------

def _conflicts(included, excluded) -> list:
    inc = {v.lower() for v in included}
    return sorted(v for v in excluded if v.lower() in inc)


def validate_search_strategy(strategy: SearchStrategy) -> list:
    """Deterministic, testable validation. Returns a list of blocking issues ([] when valid)."""
    issues: list[str] = []
    if not (strategy.objective or "").strip():
        issues.append("Search objective is required.")
    if strategy.status not in STRATEGY_STATUSES:
        issues.append(f"Unknown status {strategy.status!r}.")
    if strategy.confidence not in CONFIDENCE_LEVELS:
        issues.append(f"Confidence {strategy.confidence!r} is not one of {list(CONFIDENCE_LEVELS)}.")

    cc, pc, geo = strategy.company_criteria, strategy.person_criteria, strategy.geography
    # every list must already be normalized (no empties, no case-insensitive duplicates)
    named = {
        "geography.included": geo.included, "geography.excluded": geo.excluded,
        "industries": cc.industries, "company_types": cc.company_types,
        "company.included_keywords": cc.included_keywords,
        "company.excluded_keywords": cc.excluded_keywords,
        "job_titles": pc.job_titles, "excluded_job_titles": pc.excluded_job_titles,
        "functions": pc.functions, "excluded_functions": pc.excluded_functions,
        "buying_signals": strategy.buying_signals, "exclusions": strategy.exclusions,
    }
    for label, values in named.items():
        if any(not str(v).strip() for v in values):
            issues.append(f"{label} contains an empty value.")
        if _norm_list(values) != list(values):
            issues.append(f"{label} is not normalized (duplicates or unnormalized entries).")

    # include / exclude conflicts
    for label, inc, exc in (("geography", geo.included, geo.excluded),
                            ("company keywords", cc.included_keywords, cc.excluded_keywords),
                            ("job titles", pc.job_titles, pc.excluded_job_titles),
                            ("functions", pc.functions, pc.excluded_functions)):
        clash = _conflicts(inc, exc)
        if clash:
            issues.append(f"{label} appear as both included and excluded: {', '.join(clash)}.")

    # company-size ranges structurally valid (reuse the engine's parser — single source)
    for raw in cc.company_sizes:
        rng = parse_employee_range(str(raw))
        if rng.low is None:
            issues.append(f"Malformed company-size range: {raw!r}.")
        elif rng.high is not None and rng.low > rng.high:
            issues.append(f"Company-size range low > high: {raw!r}.")

    # provenance must be a valid Adapted ICP artifact identity
    if not strategy.derived_from_adapted_icp:
        issues.append("Strategy has no source Adapted ICP reference.")
    else:
        try:
            ident = idy.parse_artifact_identity(strategy.derived_from_adapted_icp)
            if ident.artifact_type != idy.ARTIFACT_ADAPTED_ICP:
                issues.append("Source reference is not an Adapted ICP identity.")
        except idy.ArtifactIdentityError as e:
            issues.append(f"Invalid source Adapted ICP reference: {e}")
    return issues


# --- deterministic derivation from an approved Adapted ICP -------------------

def _derive_from_icp(icp) -> dict:
    tc, tb = icp.target_companies, icp.target_buyers
    industries = _norm_list(list(tc.target_industries) + list(tc.target_subsegments))
    sizes = _norm_list(list(tc.preferred_employee_ranges) + list(tc.acceptable_employee_ranges))
    geos = _norm_list(tc.target_geographies)
    company_types = _norm_list(tc.target_company_types)
    titles = _norm_list(list(tb.primary_buyer_roles) + list(tb.secondary_buyer_roles))
    excl_titles = _norm_list(tb.excluded_buyer_roles)
    hard_excl = _norm_list([e.rule for e in icp.hard_exclusions if getattr(e, "rule", "")])
    unknowns = _norm_list(list(icp.unknown_fields) + list(icp.enrichment_fields))

    geo = Geography(included=geos)
    company = CompanyCriteria(industries=industries, company_sizes=sizes,
                              company_types=company_types,
                              included_keywords=_norm_list(tc.target_subsegments))
    person = PersonCriteria(job_titles=titles, excluded_job_titles=excl_titles,
                            decision_maker_roles=_norm_list(tb.primary_buyer_roles))
    filters = SalesNavFilters(geography=geos, industry=industries, company_headcount=sizes,
                              company_type=company_types, current_job_title=titles,
                              keywords=_norm_list(tc.target_subsegments))
    known = _norm_list(industries + geos + titles)
    return {
        "objective": (f"Find {', '.join(titles[:3]) or 'target buyers'} at "
                      f"{', '.join(industries[:3]) or 'target companies'}"
                      f"{(' in ' + ', '.join(geos[:3])) if geos else ''}, matching "
                      f"'{icp.metadata.name}'."),
        "geography": geo, "company_criteria": company, "person_criteria": person,
        "sales_nav_filters": filters, "exclusions": _norm_list(hard_excl + excl_titles),
        "rationale": ("Derived from the approved Adapted ICP: industries, size, geography, buyer "
                      "titles and declared hard exclusions are projected into Sales Navigator filter "
                      "recommendations. Signals not evidenced by the ICP are left unknown."),
        "known_facts": known, "assumptions": [], "unknowns": unknowns,
    }


# --- generation (AI proposes upstream via the ICP; Python derives + validates) ---

@dataclass
class SearchStrategyResult:
    ok: bool = False
    refusal_reason: str = ""
    strategy: Optional[SearchStrategy] = None
    issues: list = field(default_factory=list)                 # deterministic validation issues
    derived_from_adapted_icp: str = ""

    def summary(self) -> dict:
        s = self.strategy
        return {
            "ok": self.ok, "refusal_reason": self.refusal_reason, "issues": list(self.issues),
            "derived_from_adapted_icp": self.derived_from_adapted_icp,
            "confidence": getattr(s, "confidence", ""),
            "unknowns": list(getattr(s, "unknowns", []) or []),
            "assumptions": list(getattr(s, "assumptions", []) or []),
        }


def generate_search_strategy(hypothesis, *, client=None) -> SearchStrategyResult:
    """Generate a Search Strategy for ``hypothesis`` from its latest **Approved** Adapted ICP.

    Refuses deterministically when no approved Adapted ICP exists (approval is mandatory). On success,
    appends a new immutable Draft version to the hypothesis. Never mutates the source ICP or knowledge.
    ``client`` is accepted for an optional free-text wording seam; the MVP is fully deterministic."""
    source = ap.get_active_approved_icp(hypothesis)             # the active approved Adapted ICP
    if source is None:
        return SearchStrategyResult(
            ok=False,
            refusal_reason=("No approved Adapted ICP for this hypothesis. Generate and approve an "
                            "Adapted ICP first — a Search Strategy is derived from an approved ICP."))

    derived_ref = idy.artifact_identity_str(source)            # via the identity authority
    parts = _derive_from_icp(source)
    strategy = SearchStrategy(
        version=str(len(getattr(hypothesis, "search_strategies", [])) + 1),
        status=STRATEGY_DRAFT, hypothesis_id=hypothesis.project_id,
        derived_from_adapted_icp=derived_ref, **parts)
    strategy.confidence = _derive_confidence(strategy.company_criteria, strategy.person_criteria,
                                             strategy.geography, strategy.exclusions)

    issues = validate_search_strategy(strategy)
    if issues:
        return SearchStrategyResult(ok=False, refusal_reason="Generated strategy failed validation.",
                                    strategy=strategy, issues=issues, derived_from_adapted_icp=derived_ref)

    hypothesis.search_strategies.append(strategy)
    hypothesis.touch()
    return SearchStrategyResult(ok=True, strategy=strategy, derived_from_adapted_icp=derived_ref)


# --- lifecycle transitions (forward-only; approved is immutable) -------------

def _find(hypothesis, strategy_id: str) -> SearchStrategy:
    for s in getattr(hypothesis, "search_strategies", []):
        if s.strategy_id == strategy_id:
            return s
    raise SearchStrategyError(f"No search strategy {strategy_id!r} on this hypothesis.")


def set_status(hypothesis, strategy_id: str, new_status: str, *, approved_by: str = "") -> SearchStrategy:
    """Deterministic forward-only status transition. Approving validates the structure and requires a
    named approver; an Approved strategy may only be Archived. Never mutates another hypothesis."""
    s = _find(hypothesis, strategy_id)
    if new_status not in STRATEGY_STATUSES:
        raise SearchStrategyError(f"Unknown status {new_status!r}.")
    if new_status not in _ALLOWED_TRANSITIONS[s.status]:
        raise SearchStrategyError(f"Illegal transition {s.status} -> {new_status}.")
    if new_status == STRATEGY_APPROVED:
        if not (approved_by or "").strip():
            raise SearchStrategyError("Approval requires an explicit approver name.")
        issues = validate_search_strategy(s)
        if issues:
            raise SearchStrategyError("Cannot approve an invalid strategy: " + "; ".join(issues))
        s.approved_by = approved_by.strip()
        s.approved_at = bk._now()
    s.status = new_status
    s.updated_at = bk._now()
    hypothesis.touch()
    return s
