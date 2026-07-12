"""IQS v1.0 deterministic validator for a GeneratedICP.

Implements the checkable rules of docs/iqs/IQS_v1.0.md §6 and docs/iqs/ICP_PROFILE_SCHEMA.md.
It is **deterministic and side-effect-free**: it inspects the profile and returns issues; it never
edits the ICP, never calls an LLM, never touches the network (IQS §6: "returns the issues; it never
edits the ICP").

Guardrails enforced here (Sprint 3.6.1 boundary carried into the Generator):
  - a PREFERRED employee range / geography / target company type is never a hard exclusion;
  - missing information is never converted into a rejection rule;
  - every hard exclusion must carry direct-evidence, an evaluation mode, and a scope.

Behavior is identical regardless of ``metadata.entry_point`` (generate_new vs standardize_existing).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from generated_icp import (
    GeneratedICP,
    STATUS_APPROVED,
    EVAL_MODES,
    EXCLUSION_SCOPES,
)

# Section weights for the completeness score. Transparent and fixed (sum = 100).
SECTION_WEIGHTS = {
    "Business Context": 15,
    "Target Companies": 20,
    "Target Buyers": 15,
    "Qualification Dimensions": 20,
    "Hard Exclusions": 15,
    "Evidence Requirements": 10,
    "Examples and Metadata": 5,
}
assert sum(SECTION_WEIGHTS.values()) == 100


@dataclass
class ValidationResult:
    is_valid: bool = False
    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    completeness_score: int = 0
    section_completeness: dict[str, int] = field(default_factory=dict)
    suggested_next_actions: list[str] = field(default_factory=list)


# --- helpers -----------------------------------------------------------------

def _parse_range(raw: str):
    """Reuse the engine's range parser so 'malformed range' means the same thing everywhere."""
    from icp_profile import parse_employee_range
    return parse_employee_range(raw)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


# --- validation --------------------------------------------------------------

def validate(icp: GeneratedICP) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    m = icp.metadata
    bc = icp.business_context
    tc = icp.target_companies
    tb = icp.target_buyers
    dims = icp.dimensions

    # --- blocking: metadata / context ---
    if not _norm(m.name):
        errors.append("Missing ICP name.")
    if not (_norm(bc.product_or_service) or _norm(bc.description)):
        errors.append("Missing product/service context (business_context).")

    # --- blocking: qualification dimensions ---
    if not dims:
        errors.append("No qualification dimensions defined.")
    else:
        seen = set()
        for d in dims:
            key = _norm(d.name)
            if key and key in seen:
                errors.append(f"Duplicate qualification dimension: '{d.name}'.")
            seen.add(key)
        for d in dims:
            w = d.weight
            if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0 or w > 100:
                errors.append(f"Invalid weight for dimension '{d.name}': {w!r}.")
        total = sum(d.weight for d in dims
                    if isinstance(d.weight, (int, float)) and not isinstance(d.weight, bool))
        if total != 100:
            errors.append(f"Dimension weights total {total}, must total 100.")

    # --- blocking: priority thresholds ---
    errors.extend(_validate_thresholds(icp.priority_thresholds))

    # --- blocking: hard exclusions well-formed + target/exclusion contradiction ---
    target_types = {_norm(t) for t in tc.target_company_types if _norm(t)}
    for e in icp.hard_exclusions:
        label = e.rule or "(unnamed rule)"
        if not _norm(e.evidence_required):
            errors.append(f"Hard exclusion '{label}' is missing evidence_required.")
        if e.evaluation_mode not in EVAL_MODES:
            errors.append(f"Hard exclusion '{label}' has invalid/missing evaluation_mode.")
        if e.scope not in EXCLUSION_SCOPES:
            errors.append(f"Hard exclusion '{label}' has invalid/missing scope.")
        # target-vs-exclusion contradiction: a company type cannot be both targeted and excluded.
        if _norm(e.rule) in target_types:
            errors.append(
                f"Company type '{e.rule}' is both a target and a hard exclusion (contradiction).")

    # --- blocking: malformed employee ranges (PREFERENCES, but must be well-formed) ---
    # parse_employee_range signals failure with low=None (never returns None), so a non-empty
    # range string that fails to parse — or whose low > high — is malformed.
    for raw in list(tc.preferred_employee_ranges) + list(tc.acceptable_employee_ranges):
        if not _norm(raw):
            continue
        rng = _parse_range(raw)
        if rng.low is None:
            errors.append(f"Malformed employee range: '{raw}'.")
        elif rng.high is not None and rng.low > rng.high:
            errors.append(f"Malformed employee range (low > high): '{raw}'.")

    # --- blocking: approved-with-errors ---
    if m.status == STATUS_APPROVED and errors:
        errors.append("Status is 'Approved' while blocking errors remain; approval is not allowed.")

    # --- warnings (non-blocking) ---
    if not tc.target_geographies:
        warnings.append("No target geography declared.")
    if not tb.primary_buyer_roles or not tb.secondary_buyer_roles:
        warnings.append("No buyer hierarchy (missing primary or secondary buyer roles).")
    if not icp.examples.non_ideal_leads:
        warnings.append("No non-ideal (poor-fit) examples provided.")
    if not icp.enrichment_fields and not any(d.external_enrichment_required for d in dims):
        warnings.append("No enrichment fields declared.")
    if icp.ambiguous_definitions:
        warnings.append(
            "Ambiguous definitions present: " + "; ".join(icp.ambiguous_definitions))
    if any(getattr(d, "weight_is_default", False) for d in dims):
        warnings.append("One or more dimension weights are defaulted/suggested, not chosen.")
    er = icp.evidence_requirements
    if not er.accepted_sources or not _norm(er.current_employment_rules):
        warnings.append("Incomplete evidence requirements (accepted sources / current-employment "
                        "rules not fully specified).")
    if not m.source_files:
        warnings.append("Missing source attribution (no source files recorded).")
    if icp.unknown_fields:
        warnings.append("Unresolved unknown fields: " + ", ".join(icp.unknown_fields))

    completeness, section_scores = _completeness(icp)

    result = ValidationResult(
        is_valid=(len(errors) == 0),
        blocking_errors=errors,
        warnings=warnings,
        completeness_score=completeness,
        section_completeness=section_scores,
        suggested_next_actions=_next_actions(errors, warnings, section_scores),
    )
    return result


def _validate_thresholds(bands) -> list[str]:
    """Blocking checks: malformed (min>max), overlapping, and gaps in 0..100 coverage."""
    errors: list[str] = []
    if not bands:
        errors.append("No priority thresholds defined.")
        return errors
    for b in bands:
        if b.min_score > b.max_score:
            errors.append(f"Priority band '{b.label}' is malformed (min {b.min_score} > "
                          f"max {b.max_score}).")
    ordered = sorted(bands, key=lambda b: b.min_score)
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.min_score <= prev.max_score:
            errors.append(f"Priority bands overlap: '{prev.label}' ({prev.min_score}-{prev.max_score}) "
                          f"and '{cur.label}' ({cur.min_score}-{cur.max_score}).")
        elif cur.min_score > prev.max_score + 1:
            errors.append(f"Gap in priority thresholds between '{prev.label}' (ends {prev.max_score}) "
                          f"and '{cur.label}' (starts {cur.min_score}).")
    lo = min(b.min_score for b in bands)
    hi = max(b.max_score for b in bands)
    if lo > 0:
        errors.append(f"Priority thresholds do not cover the low end (start at {lo}, expected 0).")
    if hi < 100:
        errors.append(f"Priority thresholds do not cover the high end (end at {hi}, expected 100).")
    return errors


def _completeness(icp: GeneratedICP):
    """Transparent, deterministic completeness score.

    Each section earns a fraction in [0, 1] = (number of its key sub-fields that are filled) /
    (number of key sub-fields). The section's contribution = round(fraction * section_weight).
    completeness_score = sum of contributions (0..100). No hidden weighting, no LLM.
    """
    bc, tc, tb = icp.business_context, icp.target_companies, icp.target_buyers
    dims = icp.dimensions

    def frac(filled, total):
        return (filled / total) if total else 0.0

    # Business Context: description, product_or_service, value_proposition, business_model (4 keys).
    bc_filled = sum(bool(_norm(x)) for x in
                    (bc.description, bc.product_or_service, bc.value_proposition, bc.business_model))
    bc_frac = frac(bc_filled, 4)

    # Target Companies: industries, subsegments, company_types, size preference, geography (5 keys).
    tc_filled = sum(bool(x) for x in (
        tc.target_industries, tc.target_subsegments, tc.target_company_types,
        (tc.preferred_employee_ranges or tc.acceptable_employee_ranges), tc.target_geographies))
    tc_frac = frac(tc_filled, 5)

    # Target Buyers: primary, secondary, title tiers (3 keys).
    tb_filled = sum(bool(x) for x in
                    (tb.primary_buyer_roles, tb.secondary_buyer_roles, tb.title_tiers))
    tb_frac = frac(tb_filled, 3)

    # Qualification Dimensions: present + weights total 100, then mean per-dimension detail.
    if not dims:
        dim_frac = 0.0
    else:
        weights_ok = 1.0 if sum(d.weight for d in dims) == 100 else 0.0
        per_dim = [
            sum(bool(x) for x in (_norm(d.purpose), _norm(d.scoring_guidance),
                                  bool(d.required_evidence_attributes))) / 3
            for d in dims
        ]
        detail = sum(per_dim) / len(per_dim)
        dim_frac = 0.5 * weights_ok + 0.5 * detail

    # Hard Exclusions: empty -> 0 (nothing declared); else fraction well-formed.
    if not icp.hard_exclusions:
        he_frac = 0.0
    else:
        well_formed = sum(
            1 for e in icp.hard_exclusions
            if _norm(e.rule) and _norm(e.evidence_required)
            and e.evaluation_mode in EVAL_MODES and e.scope in EXCLUSION_SCOPES)
        he_frac = well_formed / len(icp.hard_exclusions)

    # Evidence Requirements: 5 keys.
    er = icp.evidence_requirements
    er_filled = (bool(er.accepted_sources)
                 + bool(_norm(er.current_employment_rules))
                 + bool(_norm(er.previous_employment_restrictions))
                 + bool(_norm(er.conflict_handling))
                 + bool(_norm(er.evidence_quality_rules)))
    er_frac = frac(er_filled, 5)

    # Examples and Metadata: name present + at least one example (2 keys).
    has_example = bool(icp.examples.ideal_leads or icp.examples.acceptable_leads
                       or icp.examples.non_ideal_leads)
    em_filled = bool(_norm(icp.metadata.name)) + has_example
    em_frac = frac(em_filled, 2)

    fracs = {
        "Business Context": bc_frac,
        "Target Companies": tc_frac,
        "Target Buyers": tb_frac,
        "Qualification Dimensions": dim_frac,
        "Hard Exclusions": he_frac,
        "Evidence Requirements": er_frac,
        "Examples and Metadata": em_frac,
    }
    section_scores = {name: round(SECTION_WEIGHTS[name] * f) for name, f in fracs.items()}
    total = sum(section_scores.values())
    return total, section_scores


def _next_actions(errors, warnings, section_scores) -> list[str]:
    """Deterministic guidance: fix blocking errors first, then the weakest sections."""
    actions: list[str] = []
    if errors:
        actions.append("Resolve blocking errors before this ICP can be approved.")
        actions.extend(errors[:5])
    # Point at the lowest-scoring sections (relative to their weight).
    weak = sorted(
        section_scores.items(),
        key=lambda kv: (kv[1] / SECTION_WEIGHTS[kv[0]]) if SECTION_WEIGHTS[kv[0]] else 1.0,
    )
    for name, _score in weak[:2]:
        ratio = (_score / SECTION_WEIGHTS[name]) if SECTION_WEIGHTS[name] else 1.0
        if ratio < 1.0:
            actions.append(f"Improve completeness of section: {name}.")
    if warnings and not errors:
        actions.append("Acknowledge the warnings before approval.")
    return actions
