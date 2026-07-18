"""Human Review view model (Sprint 13b).

The ONE deterministic projection that joins the three sources into a single reviewable row:

    Lead (business entity) + QualifiedLead (AI proposal) + LeadReviewDecision (human verdict)

Both the Streamlit page and the export adapter consume this projection, so the UI and the exported
workbook can never disagree, and neither assembles domain data on its own.

Strictly a projection: it joins by ``lead_id``, formats values, and derives ``human_decision`` from
``review_status``. It never mutates a source object, never recomputes qualification, never touches
priority, and never invents a value (unknown stays empty). Deterministic Python only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import lead_review as lr

# Operational priority ordering (mirrors the canonical export ordering; Python owns priority elsewhere).
PRIORITY_ORDER = ["Priority 1", "Priority 2", "Priority 3", "Priority 4", "Priority 5", "Disqualified"]
_PRIORITY_RANK = {label: i for i, label in enumerate(PRIORITY_ORDER)}


def _norm(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _join(values) -> str:
    return "; ".join(str(v) for v in (values or []) if str(v).strip())


def format_score_breakdown(dimensions) -> str:
    """Deterministic formatting of the engine's per-dimension points: ``Name:points``. Formatting only —
    no recomputation of any score.

    Dimension names are sorted so the value is **stable across a workspace save/reload**: the workspace
    is serialized with ``sort_keys=True``, so a dict's in-session insertion order (the ICP's declaration
    order) becomes alphabetical after a round-trip. Sorting here makes both paths agree, so the same
    batch always exports an identical Score Breakdown cell."""
    if not dimensions:
        return ""
    parts = []
    for name in sorted(dimensions):
        d = dimensions[name]
        points = d.get("points") if isinstance(d, dict) else getattr(d, "points", None)
        if points is not None:
            parts.append(f"{name}:{points}")
    return " ".join(parts)


@dataclass(frozen=True)
class ReviewRow:
    """One reviewable lead: business identity + AI proposal + human decision. Frozen — the view model is
    a read-only projection of immutable sources."""
    # --- identity / join key
    lead_id: str = ""
    # --- business data (Lead typed core + canonical attributes)
    company: str = ""
    contact: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    location: str = ""
    industry: str = ""
    company_size: str = ""
    linkedin_url: str = ""
    company_website: str = ""
    company_linkedin_url: str = ""
    connections: str = ""
    job_started: str = ""
    employee_count: str = ""
    founded_year: str = ""
    specialities: str = ""
    # --- AI proposal (QualifiedLead)
    score: int = 0
    priority: str = ""
    confidence: str = ""
    reason: str = ""
    signals: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    score_breakdown: str = ""
    # --- audit (QualifiedLead.result) — clearly labelled, never a decision
    internal_category: str = ""
    raw_icp_score: str = ""
    operational_lead_score: str = ""
    evidence_coverage: str = ""
    decision_confidence: str = ""
    evidence_adjusted_fit: str = ""
    confirmed_dealbreakers: list = field(default_factory=list)
    suspected_dealbreakers: list = field(default_factory=list)
    unknown_fields: list = field(default_factory=list)
    confidence_reasons: list = field(default_factory=list)
    validation_warnings: list = field(default_factory=list)
    review_recommendation: str = ""
    dealbreaker_state: str = ""
    model_confidence: str = ""
    is_mock: bool = False
    # --- human decision (LeadReviewDecision; Pending when undecided)
    review_status: str = lr.REVIEW_PENDING
    rejection_reason: str = ""
    reviewer_comment: str = ""
    decided_at: str = ""

    @property
    def human_decision(self) -> str:
        """Derived from ``review_status`` — never stored, so it cannot drift."""
        return lr.human_decision_for(self.review_status)

    @property
    def priority_rank(self) -> int:
        return _PRIORITY_RANK.get(self.priority, len(PRIORITY_ORDER))


def _row_from(lead, qualified, decision) -> ReviewRow:
    result = dict(getattr(qualified, "result", {}) or {})
    attrs = dict(getattr(lead, "attributes", {}) or {}) if lead is not None else {}
    g = (lambda name: _norm(getattr(lead, name, "")) if lead is not None else "")
    return ReviewRow(
        lead_id=getattr(qualified, "lead_id", ""),
        company=g("company_name"), contact=g("person_name"), title=g("current_title"),
        location=g("geography"), industry=g("industry"), company_size=g("company_size"),
        linkedin_url=g("linkedin_url"), company_website=g("company_url"),
        first_name=_norm(attrs.get("first_name")), last_name=_norm(attrs.get("last_name")),
        company_linkedin_url=_norm(attrs.get("company_linkedin_url")),
        connections=_norm(attrs.get("connections")), job_started=_norm(attrs.get("job_started")),
        employee_count=_norm(attrs.get("employee_count")),
        founded_year=_norm(attrs.get("founded_year")), specialities=_norm(attrs.get("specialities")),
        score=getattr(qualified, "score", 0), priority=getattr(qualified, "decision", ""),
        confidence=getattr(qualified, "confidence", ""), reason=getattr(qualified, "reason", ""),
        signals=list(getattr(qualified, "evidence", []) or []),
        warnings=list(getattr(qualified, "warnings", []) or []),
        score_breakdown=format_score_breakdown(result.get("dimensions")),
        internal_category=_norm(result.get("internal_category")),
        raw_icp_score=_norm(result.get("raw_icp_score")),
        operational_lead_score=_norm(result.get("operational_lead_score")),
        evidence_coverage=_norm(result.get("evidence_coverage")),
        decision_confidence=_norm(result.get("decision_confidence")),
        evidence_adjusted_fit=_norm(result.get("evidence_adjusted_fit")),
        confirmed_dealbreakers=list(result.get("confirmed_dealbreakers") or []),
        suspected_dealbreakers=list(result.get("suspected_dealbreakers") or []),
        unknown_fields=list(result.get("unknowns") or []),
        confidence_reasons=list(result.get("confidence_reasons") or []),
        validation_warnings=list(result.get("validation_warnings") or []),
        review_recommendation=_norm(result.get("review_recommendation")),
        dealbreaker_state=_norm(result.get("dealbreaker_state")),
        model_confidence=_norm(result.get("model_confidence")),
        is_mock=bool(result.get("is_mock", False)),
        review_status=getattr(decision, "review_status", lr.REVIEW_PENDING),
        rejection_reason=getattr(decision, "rejection_reason", ""),
        reviewer_comment=getattr(decision, "reviewer_comment", ""),
        decided_at=getattr(decision, "decided_at", ""))


def build_review_rows(hypothesis, qualified_batch) -> list:
    """Project one QualifiedLeadBatch into review rows, joined by ``lead_id`` to its source LeadBatch's
    leads and to the latest human decision. A lead with no decision is **Pending**. Sources are read
    only — never mutated."""
    if qualified_batch is None:
        return []
    source = next((b for b in getattr(hypothesis, "lead_batches", [])
                   if b.batch_id == qualified_batch.derived_from_lead_batch), None)
    lead_by_id = {l.lead_id: l for l in getattr(source, "leads", [])} if source is not None else {}
    review = hypothesis.review_for_qualified_batch(qualified_batch.batch_id) \
        if hasattr(hypothesis, "review_for_qualified_batch") else None
    decisions = review.current_decisions() if review is not None else {}
    return [_row_from(lead_by_id.get(q.lead_id), q, decisions.get(q.lead_id))
            for q in getattr(qualified_batch, "qualified", [])]


# --- deterministic sorting / filtering / search ------------------------------

def sort_rows(rows) -> list:
    """Canonical review order: operational priority ascending (P1 first, Disqualified last), then Lead
    Score descending — the same ordering the exported workbook uses."""
    return sorted(rows, key=lambda r: (r.priority_rank, -int(r.score or 0), r.company.lower()))


def filter_rows(rows, *, statuses=None, priorities=None, score_min=None, score_max=None,
                industries=None, company_sizes=None, geographies=None, search: str = "") -> list:
    """Deterministic filtering. Every filter is optional; an empty/None filter matches everything.
    ``search`` is free text over Company + Contact."""
    out = list(rows)
    if statuses:
        out = [r for r in out if r.review_status in set(statuses)]
    if priorities:
        out = [r for r in out if r.priority in set(priorities)]
    if score_min is not None:
        out = [r for r in out if int(r.score or 0) >= int(score_min)]
    if score_max is not None:
        out = [r for r in out if int(r.score or 0) <= int(score_max)]
    if industries:
        out = [r for r in out if r.industry in set(industries)]
    if company_sizes:
        out = [r for r in out if r.company_size in set(company_sizes)]
    if geographies:
        out = [r for r in out if r.location in set(geographies)]
    term = _norm(search).lower()
    if term:
        out = [r for r in out if term in r.company.lower() or term in r.contact.lower()]
    return out


def priority_distribution(rows) -> dict:
    """Counts per operational priority, in canonical order (labels with 0 are included)."""
    counts = {label: 0 for label in PRIORITY_ORDER}
    for r in rows:
        if r.priority in counts:
            counts[r.priority] += 1
        else:
            counts[r.priority] = counts.get(r.priority, 0) + 1
    return counts


def approved_rows(rows) -> list:
    return [r for r in rows if r.review_status == lr.REVIEW_APPROVED]
