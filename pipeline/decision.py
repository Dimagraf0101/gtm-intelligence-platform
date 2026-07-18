"""Decision Layer — Release 0.3, Sprint 3.3.

Combine a normalized ``ICPProfile`` (Knowledge Layer) with extracted ``EvidenceItem`` s (Evidence
Layer) and the per-dimension scores + dealbreaker candidates *proposed by the LLM*, and produce a
**deterministic, auditable** qualification decision.

Division of labour (docs/PRODUCT_CONSTITUTION.md, docs/DECISIONS.md ADR-002/003/004):

* **Python owns** numeric validation, dimension bounds, summation, threshold mapping, coverage,
  final priority, and confirmed deterministic exclusions.
* **The LLM only proposes** per-dimension points and dealbreaker candidates (with cited evidence);
  it never sets the final score, category, or a confirmed dealbreaker.

Hard rules enforced here:
  * Missing data stays unknown — it never reduces the raw score as negative evidence; it may only
    lower confidence and restrict A+ eligibility.
  * A dealbreaker is `confirmed` only with direct, confirmed, current/person-level evidence; a
    `suspected` dealbreaker never overrides the numeric priority; only `confirmed` yields Disqualified.
  * Previous-employment evidence never confirms a current-company exclusion.
  * Conflicting evidence lowers confidence but never auto-disqualifies.
  * A+ requires no confirmed dealbreaker AND Data Coverage >= 80%; otherwise
    ``A+ Candidate — Enrichment Required``.

Pure Python. No network, no LLM call, no I/O. Commercial exclusions are entirely data-driven (from
the passed candidates / the ICP profile) — this module hardcodes no commercial rules, so one ICP's
exclusions can never leak into another.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Union

from evidence import (  # noqa: E402
    EvidenceItem, CONFIRMED, CONFLICTING, SCOPE_CURRENT, SCOPE_PREVIOUS, SCOPE_NONE, HIGH,
)
from icp_profile import ICPProfile  # noqa: E402
import priority_policy as pp  # noqa: E402  canonical operational policy (leaf; no cycle)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISQUALIFIED = pp.DISQUALIFIED_LABEL
UNKNOWN_PRIORITY = "Unknown"

# Operational priority is derived ONLY from the operational lead score, via the fixed universal bands
# defined ONCE in `priority_policy` (the canonical operational policy). Highest-first (minimum, label);
# a score below the lowest minimum is Disqualified.
# 90-100 P1 · 75-89 P2 · 60-74 P3 · 45-59 P4 · 30-44 P5 · 0-29 Disqualified.
_OPERATIONAL_BANDS = pp.OPERATIONAL_PRIORITY_BANDS

STATE_CONFIRMED = "confirmed"
STATE_SUSPECTED = "suspected"
STATE_NONE = "none"

REVIEW_PRIORITY = "priority_review"
REVIEW_STANDARD = "standard_review"

FIT_UNKNOWN = "unknown"

# confidence deductions (documented, deterministic)
_CONFLICT_PENALTY = 15            # per conflicting current/person-level attribute
_SUSPECTED_PENALTY = 8           # per suspected dealbreaker
_LOW_QUALITY_MAX_PENALTY = 10    # scaled by fraction of confirmed evidence that is not high-quality
_BOUNDARY_MARGIN = 2             # within this many points of a band edge = "near boundary"


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class DecisionResult:
    raw_icp_score: int                          # summed dimension score, preserved for audit
    operational_lead_score: int                 # 0 on a confirmed exclusion, else == raw_icp_score
    operational_priority: str                   # Priority 1..5 / Disqualified, from operational score
    internal_category: str                      # the ICP's own category band (audit / AI Details)
    dealbreaker_state: str                      # confirmed | suspected | none
    confirmed_dealbreakers: list[str]
    suspected_dealbreakers: list[str]
    unknown_fields: list[str]                   # rubric dimensions with no usable evidence
    evidence_coverage: int                      # percent of scorable rubric weight with usable evidence
    evidence_adjusted_fit: Union[int, str]      # percent, or "unknown"
    decision_confidence: int                    # 0-100, deterministic
    confidence_reasons: list[str]
    review_recommendation: str                  # priority_review | standard_review
    validation_warnings: list[str]
    # audit extras
    max_score: int = 0
    usable_dimensions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_score(value) -> tuple[Optional[float], Optional[str]]:
    """Return (number, warning). Non-numeric -> (None, warning); numeric kept for later clamping."""
    if value is None:
        return None, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, f"malformed dimension score {value!r} (non-numeric); treated as unknown"


def operational_priority(score: int) -> str:
    """Operational priority derived ONLY from the numeric (operational) lead score."""
    for minimum, label in _OPERATIONAL_BANDS:
        if score >= minimum:
            return label
    return DISQUALIFIED


def _map_priority(score: int, profile: ICPProfile) -> Optional[str]:
    """Deterministic threshold mapping: the highest band whose min_score <= score."""
    candidates = [t for t in profile.category_thresholds
                  if t.min_score is not None and score >= t.min_score]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.min_score).label


def _top_band(profile: ICPProfile):
    bands = [t for t in profile.category_thresholds if t.min_score is not None]
    return max(bands, key=lambda t: t.min_score) if bands else None


def _top_label(profile: ICPProfile) -> Optional[str]:
    band = _top_band(profile)
    return band.label if band else None


def _near_boundary(score: int, profile: ICPProfile) -> bool:
    for t in profile.category_thresholds:
        for edge in (t.min_score, t.max_score):
            if edge is not None and abs(score - edge) <= _BOUNDARY_MARGIN:
                return True
    return False


def _resolve_dealbreaker(candidate: dict, ev_index: dict[str, list[EvidenceItem]]) -> tuple[str, str, Optional[str]]:
    """Return (label, state, downgrade_reason). ``confirmed`` requires the model to have proposed it
    AND direct confirmed current/person-level evidence with no conflict; otherwise ``suspected``."""
    label = str(candidate.get("label") or candidate.get("name") or "unnamed exclusion")
    attr = candidate.get("evidence_attribute") or candidate.get("attribute")
    proposed = str(candidate.get("proposed_state") or candidate.get("state") or STATE_SUSPECTED).lower()

    items = ev_index.get(attr, []) if attr else []
    has_confirmed_current = any(
        e.status == CONFIRMED and e.employment_scope in (SCOPE_CURRENT, SCOPE_NONE) for e in items)
    only_previous = bool(items) and all(e.employment_scope == SCOPE_PREVIOUS for e in items)
    has_conflict = any(e.status == CONFLICTING for e in items)

    if proposed == STATE_CONFIRMED and has_confirmed_current and not has_conflict:
        return label, STATE_CONFIRMED, None

    reason = None
    if proposed == STATE_CONFIRMED:                       # explain why we would NOT confirm
        if not attr:
            reason = "no cited evidence — cannot confirm"
        elif only_previous:
            reason = "only previous-employment evidence — cannot confirm current-company exclusion"
        elif has_conflict:
            reason = "conflicting evidence — cannot auto-confirm"
        elif not has_confirmed_current:
            reason = "no confirmed current evidence — cannot confirm"
    return label, STATE_SUSPECTED, reason


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decide(profile: ICPProfile,
           evidence_items: list[EvidenceItem],
           dimension_scores: dict[str, object],
           dealbreaker_candidates: Optional[list[dict]] = None) -> DecisionResult:
    """Produce a deterministic DecisionResult.

    ``dimension_scores`` maps ICP dimension name -> the LLM's proposed points (or None/absent when
    the model had no usable evidence for that dimension). ``dealbreaker_candidates`` are the LLM's
    proposals; Python decides their final state from ``evidence_items``.
    """
    warnings: list[str] = []
    ev_index: dict[str, list[EvidenceItem]] = {}
    for e in evidence_items:
        ev_index.setdefault(e.attribute, []).append(e)

    # ---- dimension scoring (Python owns bounds, summation) ------------------
    raw_score = 0
    usable_score = 0
    usable_max = 0
    usable_dims: list[str] = []
    unknown_dims: list[str] = []

    # warn about scores for dimensions not in the ICP rubric (never counted)
    dim_names = {d.name for d in profile.scoring_dimensions}
    for name in dimension_scores:
        if name not in dim_names:
            warnings.append(f"score for unknown dimension '{name}' ignored")

    for dim in profile.scoring_dimensions:
        if dim.weight is None:
            warnings.append(f"dimension '{dim.name}' has unknown weight; excluded from scoring")
            unknown_dims.append(dim.name)
            continue
        dmax = int(dim.weight)
        raw_value = dimension_scores.get(dim.name)
        number, warn = _coerce_score(raw_value)
        if warn:
            warnings.append(f"{dim.name}: {warn}")
        if number is None:
            unknown_dims.append(dim.name)      # unknown -> contributes 0 (floor), NEVER negative
            continue
        pts = int(round(number))
        if pts < 0:
            warnings.append(f"{dim.name}: score {pts} below 0; clamped to 0")
            pts = 0
        elif pts > dmax:
            warnings.append(f"{dim.name}: score {pts} above max {dmax}; clamped")
            pts = dmax
        raw_score += pts
        usable_score += pts
        usable_max += dmax
        usable_dims.append(dim.name)

    total_weight = sum(int(d.weight) for d in profile.scoring_dimensions if d.weight is not None)
    max_score = total_weight

    # ---- coverage & evidence-adjusted fit -----------------------------------
    # Coverage = share of scorable rubric *weight* backed by usable evidence (weight-based, so a
    # missing high-weight dimension lowers coverage more than a small one). Missing dimensions are
    # simply "not covered" — they are never counted as negative points.
    evidence_coverage = round(usable_max / total_weight * 100) if total_weight else 0

    # Evidence-Adjusted Fit = usable_score / usable_max, i.e. how well the lead scored on the
    # dimensions we could actually assess. Capped at 100. "unknown" when no usable evidence exists.
    if usable_max <= 0:
        evidence_adjusted_fit: Union[int, str] = FIT_UNKNOWN
    else:
        evidence_adjusted_fit = min(100, round(usable_score / usable_max * 100))

    # ---- dealbreakers (Python confirms; LLM only proposes) ------------------
    confirmed: list[str] = []
    suspected: list[str] = []
    for cand in (dealbreaker_candidates or []):
        label, state, reason = _resolve_dealbreaker(cand, ev_index)
        if state == STATE_CONFIRMED:
            confirmed.append(label)
        else:
            suspected.append(label)
            if reason:
                warnings.append(f"dealbreaker '{label}' kept suspected: {reason}")

    if confirmed:
        dealbreaker_state = STATE_CONFIRMED
    elif suspected:
        dealbreaker_state = STATE_SUSPECTED
    else:
        dealbreaker_state = STATE_NONE

    # ---- internal ICP category (audit / AI Details) -------------------------
    internal_category = _map_priority(raw_score, profile)
    if internal_category is None:
        internal_category = UNKNOWN_PRIORITY
        if not profile.category_thresholds:
            warnings.append("no category thresholds in ICP profile; internal category is Unknown")

    # ---- operational lead score + priority (Python is the sole authority) ---
    # Priority is derived ONLY from the operational lead score via the fixed bands. A confirmed hard
    # exclusion zeroes the operational score and forces Disqualified (raw score is preserved for
    # audit). Suspected dealbreakers, missing information and low coverage NEVER change priority.
    if confirmed:
        operational_lead_score = 0
        op_priority = DISQUALIFIED
    else:
        operational_lead_score = raw_score
        op_priority = operational_priority(raw_score)

    # ---- deterministic confidence ------------------------------------------
    conflicts = [e for e in evidence_items
                 if e.status == CONFLICTING and e.employment_scope in (SCOPE_CURRENT, SCOPE_NONE)]
    confirmed_items = [e for e in evidence_items
                       if e.status == CONFIRMED and e.employment_scope in (SCOPE_CURRENT, SCOPE_NONE)]

    confidence = evidence_coverage
    reasons: list[str] = [f"evidence coverage {evidence_coverage}%"]

    if conflicts:
        confidence -= _CONFLICT_PENALTY * len(conflicts)
        reasons.append(f"-{_CONFLICT_PENALTY * len(conflicts)}: {len(conflicts)} conflicting field(s)")
    if suspected:
        confidence -= _SUSPECTED_PENALTY * len(suspected)
        reasons.append(f"-{_SUSPECTED_PENALTY * len(suspected)}: {len(suspected)} suspected dealbreaker(s)")
    if confirmed_items:
        low_q = sum(1 for e in confirmed_items if e.confidence != HIGH)
        if low_q:
            pen = round(_LOW_QUALITY_MAX_PENALTY * low_q / len(confirmed_items))
            confidence -= pen
            reasons.append(f"-{pen}: {low_q}/{len(confirmed_items)} evidence items below high quality")
    if unknown_dims:
        reasons.append(f"{len(unknown_dims)} dimension(s) unknown (no usable evidence)")

    decision_confidence = max(0, min(100, confidence))

    # ---- review recommendation ---------------------------------------------
    near = _near_boundary(raw_score, profile)
    priority_review = (
        dealbreaker_state in (STATE_CONFIRMED, STATE_SUSPECTED)
        or bool(conflicts)
        or decision_confidence < 50
        or near
    )
    review_recommendation = REVIEW_PRIORITY if priority_review else REVIEW_STANDARD
    if near:
        reasons.append("near a category boundary")

    return DecisionResult(
        raw_icp_score=raw_score,
        operational_lead_score=operational_lead_score,
        operational_priority=op_priority,
        internal_category=internal_category,
        dealbreaker_state=dealbreaker_state,
        confirmed_dealbreakers=confirmed,
        suspected_dealbreakers=suspected,
        unknown_fields=sorted(unknown_dims),
        evidence_coverage=evidence_coverage,
        evidence_adjusted_fit=evidence_adjusted_fit,
        decision_confidence=decision_confidence,
        confidence_reasons=reasons,
        review_recommendation=review_recommendation,
        validation_warnings=warnings,
        max_score=max_score,
        usable_dimensions=usable_dims,
    )
