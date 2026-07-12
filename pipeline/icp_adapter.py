"""Adapter: a valid, Approved GeneratedICP -> the existing engine's pipeline.icp_profile.ICPProfile.

This is the only bridge between the ICP Generator and the Qualification Engine. It is deterministic,
does not infer missing data, and behaves identically for both entry points (generate_new /
standardize_existing) — the engine never learns which path produced the ICP.

Guardrails (mirrors Sprint 3.6.1):
  - only an **Approved** and **IQS-valid** profile may be adapted;
  - target criteria (size/geography/company types) never become hard exclusions;
  - excluded company types come only from **explicitly declared** deterministic company-scoped hard
    exclusions — never inferred, never a global commercial exclusion;
  - universal exclusions stay validity-only (ICPProfile's default), untouched here.
"""
from __future__ import annotations

from generated_icp import (
    GeneratedICP,
    STATUS_APPROVED,
    EVAL_DETERMINISTIC,
    SCOPE_CURRENT_COMPANY,
)
from icp_profile import ICPProfile, ScoringDimension, CategoryThreshold, parse_employee_range
import iqs_validator


class AdapterError(ValueError):
    """Raised when a GeneratedICP cannot be adapted (not Approved, or invalid)."""


def can_use(icp: GeneratedICP) -> bool:
    """True iff this ICP may be handed to the engine: Approved *and* IQS-valid."""
    return icp.metadata.status == STATUS_APPROVED and iqs_validator.validate(icp).is_valid


def to_engine_profile(icp: GeneratedICP, *, validate: bool = True) -> ICPProfile:
    """Convert an Approved, valid GeneratedICP into an engine ICPProfile.

    Rejects Draft / Needs Information / Ready for Review, and (when ``validate``) any Approved
    profile that fails IQS. Warnings are preserved; target-vs-hard-exclusion separation is preserved.
    """
    if icp.metadata.status != STATUS_APPROVED:
        raise AdapterError(
            f"Only Approved ICPs can be used for qualification; status is '{icp.metadata.status}'.")

    warnings = list(icp.warnings)
    if validate:
        report = iqs_validator.validate(icp)
        if not report.is_valid:
            raise AdapterError(
                "Approved ICP failed IQS validation: " + "; ".join(report.blocking_errors))
        # Preserve validator warnings alongside the profile's own, without duplicates.
        for w in report.warnings:
            if w not in warnings:
                warnings.append(w)

    tc, tb = icp.target_companies, icp.target_buyers

    # Employee ranges are TARGET preferences (engine treats them as targets, never as rejections).
    employee_ranges = [parse_employee_range(r)
                       for r in list(tc.preferred_employee_ranges) + list(tc.acceptable_employee_ranges)
                       if str(r).strip()]

    # Excluded company types: ONLY explicit, deterministic, company-scoped hard exclusions.
    excluded_company_types = [
        e.rule for e in icp.hard_exclusions
        if e.evaluation_mode == EVAL_DETERMINISTIC and e.scope == SCOPE_CURRENT_COMPANY and e.rule
    ]

    dimensions = [ScoringDimension(name=d.name, weight=d.weight) for d in icp.dimensions]
    scoring_weights = {d.name: d.weight for d in icp.dimensions}

    category_thresholds = [
        CategoryThreshold(label=b.label, min_score=b.min_score, max_score=b.max_score)
        for b in icp.priority_thresholds
    ]

    # Enrichment-required fields = dimensions the ICP explicitly marked as enrichment-dependent.
    enrichment_required = [d.name for d in icp.dimensions if d.external_enrichment_required]

    profile = ICPProfile(
        name=icp.metadata.name,
        target_industries=list(tc.target_industries),
        target_subsegments=list(tc.target_subsegments),
        target_company_types=list(tc.target_company_types),
        excluded_company_types=excluded_company_types,
        target_geographies=list(tc.target_geographies),
        employee_ranges=employee_ranges,
        target_buyer_personas=list(tb.primary_buyer_roles) + list(tb.secondary_buyer_roles),
        buyer_title_tiers=list(tb.title_tiers),
        scoring_dimensions=dimensions,
        scoring_weights=scoring_weights,
        category_thresholds=category_thresholds,
        hard_exclusions=[e.rule for e in icp.hard_exclusions if e.rule],
        ambiguous_definitions=list(icp.ambiguous_definitions),
        enrichment_required_fields=enrichment_required,
        # universal_exclusions intentionally left to ICPProfile's validity-only default.
        notes=[],
        warnings=warnings,
        unknown_fields=list(icp.unknown_fields),
    )
    return profile
