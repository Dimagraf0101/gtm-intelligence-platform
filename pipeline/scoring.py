"""Qualification Engine — score leads against an ICP (Release 0.3 integrated flow).

Pipeline per run:
  1. Build a normalized ``ICPProfile`` from the extracted ICP text (Knowledge Layer). If
     deterministic parsing is incomplete, warnings are preserved and a generic default scoring
     framework is used while the *original ICP text is still sent to the model* for semantic
     interpretation — missing structured fields are never invented.
  2. For every lead, extract ``EvidenceItem`` records (Evidence Layer), keeping current vs previous
     employment separate and preserving conflicts / unknowns.
  3. The model returns **proposals only** (dimension scores, evidence, dealbreaker candidates,
     reason, unknown fields, model_confidence). It never sets the final score/category/dealbreaker.
  4. ``pipeline.decision.decide`` (Decision Layer) produces the authoritative ``DecisionResult``:
     Python owns the final score, priority, dealbreaker verdict and confidence.

Backward compatibility: ``Lead``, ``Dimension``, ``ScoringResult``, ``normalize_lead``,
``get_client`` and ``score_leads`` keep their existing public shape; ``ScoringResult`` gains
**additive** fields. ``app.py`` and ``pipeline/export.py`` continue to work unchanged.

Offline ``MockClient`` conforms to the new proposal schema and is clearly marked non-production.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional, Union

from config import BASE_DIR  # noqa: E402  (config has side effect: load_dotenv)
import evidence              # noqa: E402  Evidence Layer
import icp_profile           # noqa: E402  Knowledge Layer
import prequalification      # noqa: E402  deterministic Python pre-qualification
from decision import decide  # noqa: E402  Decision Layer

logger = logging.getLogger("qualification")

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 5
MAX_RETRIES = 2                      # max retries per FAILED batch (3 attempts total)
PROMPT_PATH = BASE_DIR / "prompts" / "scoring_system.md"

# Generic default scoring framework — used ONLY when deterministic ICP parsing does not yield a
# rubric. Transparent fallback (not invented ICP data); the incompleteness is surfaced as a warning.
_DEFAULT_DEFINITION = {
    "dimensions": [
        {"name": "title", "weight": 40}, {"name": "industry", "weight": 25},
        {"name": "company_size", "weight": 15}, {"name": "location", "weight": 10},
        {"name": "signals", "weight": 10},
    ],
    "category_thresholds": [
        {"label": "A+ / Hot", "min": 90, "max": 100}, {"label": "A / High", "min": 75, "max": 89},
        {"label": "B / Normal", "min": 50, "max": 74}, {"label": "C / Low", "min": 30, "max": 49},
        {"label": "Not Relevant", "min": 0, "max": 29},
    ],
}

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_FIELD_MAP: dict[str, list[str]] = {
    "first_name": ["first name", "first_name"],
    "last_name": ["last name", "last_name"],
    "job_title": ["job title", "job_title", "title"],
    "job_started": ["job started on", "job started", "job_started"],
    "headline": ["headline"],
    "summary": ["summary", "about"],
    "job_description": ["job description", "job_description"],
    "skills": ["skills"],
    "company": ["company"],
    "company_size_range": ["linkedin employees", "linkedin company size"],
    "employee_count": ["linkedin company employee count", "employee count"],
    "industry": ["linkedin industry", "industry"],
    "location": ["location"],
    "specialities": ["linkedin specialities", "specialities", "specialties"],
    "company_description": ["linkedin description", "company description"],
    "founded_year": ["linkedin founded year", "founded year"],
    "connections": ["number of connections", "connections"],
    "premium": ["premium member", "premium"],
    "linkedin_url": ["linkedin url", "linkedin_url", "profile url"],
    "company_linkedin_url": ["corporate linkedin url", "company linkedin url"],
    "company_website": ["corporate website", "company website", "website"],
    "recent_posts": ["recent_posts", "recent posts"],
}

_MODEL_FIELDS = [
    "first_name", "last_name", "job_title", "job_started", "headline", "summary",
    "job_description", "skills", "company", "company_size_range", "employee_count",
    "industry", "location", "specialities", "company_description", "founded_year",
    "connections", "premium", "recent_posts",
]


@dataclass
class Lead:
    """A normalized input lead. ``raw`` keeps every original CSV column for export."""
    index: int
    fields: dict[str, str]
    raw: dict[str, str] = field(default_factory=dict)
    previous_roles: list[dict] = field(default_factory=list)  # additive: past employment (isolated)

    def model_view(self) -> dict[str, str]:
        """Compact dict of non-empty CURRENT fields to embed in the prompt."""
        return {k: self.fields[k] for k in _MODEL_FIELDS if self.fields.get(k)}


def normalize_lead(raw_row: dict[str, str], index: int) -> Lead:
    """Map a raw CSV row to a normalized :class:`Lead` (case-insensitive columns).

    Current employment goes into ``fields``; previous employment (Vayne '(2)'/'(3)'/'(4)' columns)
    goes into ``previous_roles`` and is kept strictly separate.
    """
    lower = {(k or "").strip().lower(): (v or "") for k, v in raw_row.items()}
    fields: dict[str, str] = {}
    for norm, candidates in _FIELD_MAP.items():
        for cand in candidates:
            if cand in lower and str(lower[cand]).strip():
                fields[norm] = str(lower[cand]).strip()
                break

    previous_roles: list[dict] = []
    for n in (2, 3, 4):
        title = str(lower.get(f"job title ({n})", "")).strip()
        company = str(lower.get(f"company ({n})", "")).strip()
        if not title and not company:
            continue
        previous_roles.append({
            "title": title, "company": company,
            "industry": str(lower.get(f"linkedin industry ({n})", "")).strip(),
            "started": str(lower.get(f"job started on ({n})", "")).strip(),
            "ended": str(lower.get(f"job ended on ({n})", "")).strip(),
        })

    return Lead(index=index, fields=fields, raw=dict(raw_row), previous_roles=previous_roles)


# ---------------------------------------------------------------------------
# Output model (backward-compatible + additive fields)
# ---------------------------------------------------------------------------

@dataclass
class Dimension:
    points: int
    max: int
    evidence: str = ""


@dataclass
class ScoringResult:
    """Engine output for one lead. Existing fields preserved; new fields are additive."""
    # --- existing (used by export.py / app.py) ---
    lead_index: int
    icp: str
    score: int                      # 0-100, Python-computed (== raw_icp_score)
    category: str                   # Python-computed (== provisional_priority)
    dimensions: dict[str, Dimension]
    hard_dealbreaker: bool
    dealbreaker_reason: Optional[str]
    reason: str
    signals: list[str]
    confidence: str                 # high | medium | low (Python-derived; NOT model self-report)
    unknowns: list[str]
    model: str
    error: Optional[str] = None
    # --- additive (Release 0.3 Decision Layer outputs) ---
    raw_icp_score: Optional[int] = None
    operational_lead_score: Optional[int] = None
    operational_priority: Optional[str] = None
    internal_category: Optional[str] = None
    provisional_priority: Optional[str] = None
    evidence_coverage: Optional[int] = None
    evidence_adjusted_fit: Optional[Union[int, str]] = None
    decision_confidence: Optional[int] = None
    dealbreaker_state: Optional[str] = None
    confirmed_dealbreakers: list[str] = field(default_factory=list)
    suspected_dealbreakers: list[str] = field(default_factory=list)
    review_recommendation: Optional[str] = None
    confidence_reasons: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    model_confidence: Optional[str] = None   # raw model self-report, kept for transparency only
    is_mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dimensions"] = {k: asdict(v) for k, v in self.dimensions.items()}
        return d


# ---------------------------------------------------------------------------
# ICP profile (Knowledge Layer) with transparent fallback
# ---------------------------------------------------------------------------

def build_scoring_profile(icp_name: str, icp_text: str) -> icp_profile.ICPProfile:
    """Return an ICPProfile suitable for scoring. If deterministic parsing did not yield a rubric,
    fall back to a generic default framework while preserving the original warnings (no invention)."""
    prof = icp_profile.build_profile_from_text(icp_name, icp_text)
    if prof.scoring_dimensions and prof.category_thresholds:
        return prof
    fb = icp_profile.build_profile_from_definition(icp_name, _DEFAULT_DEFINITION)
    fb.warnings = (list(prof.warnings)
                   + ["ICP rubric not fully detected by deterministic parsing; using a generic "
                      "default scoring framework and relying on the LLM for semantic interpretation."]
                   + fb.warnings)
    fb.unknown_fields = list(dict.fromkeys(list(prof.unknown_fields) + list(fb.unknown_fields)))
    # preserve any commercial signals we did extract (never invented)
    fb.hard_exclusions = prof.hard_exclusions or fb.hard_exclusions
    fb.excluded_company_types = prof.excluded_company_types or fb.excluded_company_types
    fb.target_industries = prof.target_industries or fb.target_industries
    return fb


# ---------------------------------------------------------------------------
# Prompt building (schema/rules live in prompts/scoring_system.md)
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def build_icp_context(profile: icp_profile.ICPProfile, icp_text: str) -> str:
    """Stable per-run context (system side): ICP definition + dimensions + known exclusions.
    Contains NO lead-specific data, so it is safe to cache."""
    dims = [{"name": d.name, "max": d.weight} for d in profile.scoring_dimensions]
    exclusions = profile.hard_exclusions or []
    excl_block = ("\n".join(f"- {e}" for e in exclusions)
                  if exclusions else "- none detected — infer candidate exclusions from the ICP text")
    return (
        f"# ICP: {profile.name}\n\n"
        f"## ICP definition (for semantic interpretation)\n{icp_text}\n\n"
        f"## Scoring dimensions (score each 0..max; return null or omit the score when the required "
        f"evidence is unavailable)\n```json\n{json.dumps(dims, ensure_ascii=False)}\n```\n\n"
        f"## Known ICP hard exclusions (ICP-specific)\n{excl_block}"
    )


def build_system_blocks(system_prompt: str, icp_context: str) -> list[dict]:
    """System content blocks. The stable ICP context is marked cacheable (ephemeral); the system
    prompt is small and left uncached. No lead-specific/private data is ever placed here."""
    return [
        {"type": "text", "text": system_prompt},
        {"type": "text", "text": icp_context, "cache_control": {"type": "ephemeral"}},
    ]


def build_user_prompt(batch: list[Lead]) -> str:
    """User message — LEAD-SPECIFIC data only (never cached)."""
    leads_payload = [
        {"lead_index": lead.index, "current": lead.model_view(), "previous_roles": lead.previous_roles}
        for lead in batch
    ]
    return (
        f"## Leads to score ({len(batch)})\n"
        f"```json\n{json.dumps(leads_payload, ensure_ascii=False)}\n```\n\n"
        f"Return a JSON array, one object per lead, following the system-prompt schema exactly."
    )


# ---------------------------------------------------------------------------
# Model clients (pluggable)
# ---------------------------------------------------------------------------

def _is_cache_error(exc: Exception) -> bool:
    return "cache" in str(exc).lower()


def _strip_cache_control(system: list[dict]) -> list[dict]:
    return [{k: v for k, v in b.items() if k != "cache_control"} for b in system]


class AnthropicClient:
    """Real Claude client. ``system`` may be a plain string or a list of content blocks (with
    ``cache_control`` for prompt caching). If caching is unavailable it transparently falls back to
    an uncached call, so behaviour is preserved on SDKs/models without caching support."""

    def __init__(self, model: str = MODEL, api_key: Optional[str] = None):
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def complete(self, system, user: str) -> str:
        try:
            msg = self._client.messages.create(
                model=self.model, max_tokens=6000, system=system,
                messages=[{"role": "user", "content": user}])
        except Exception as exc:  # noqa: BLE001
            if isinstance(system, list) and _is_cache_error(exc):
                logger.warning("Prompt caching unavailable (%s); retrying without cache_control.",
                               type(exc).__name__)
                msg = self._client.messages.create(
                    model=self.model, max_tokens=6000, system=_strip_cache_control(system),
                    messages=[{"role": "user", "content": user}])
            else:
                raise
        return "".join(block.text for block in msg.content if block.type == "text")


_ENRICHMENT_HINT = ("funding", "stage", "hiring", "headcount", "revenue", "traffic",
                    "tech stack", "reachability", "engagement")


class MockClient:
    """Deterministic OFFLINE stand-in conforming to the new proposal schema.

    It is a placeholder, not a judgement: it scores only non-enrichment dimensions (modestly),
    leaves enrichment dimensions unknown (so coverage is realistically < 100%), proposes no
    dealbreakers, and marks every result as MOCK. It must never be mistaken for production output.
    """

    model = "mock"

    def complete(self, system, user: str) -> str:
        # dimensions now live in the (cacheable) system context; leads in the user message
        sys_text = system if isinstance(system, str) else "\n".join(b.get("text", "") for b in system)
        dim_blocks = _json_blocks(sys_text)
        dims = dim_blocks[0] if dim_blocks else []
        lead_blocks = _json_blocks(user)
        leads = lead_blocks[-1] if lead_blocks else []
        return json.dumps([self._score_one(lead, dims) for lead in leads], ensure_ascii=False)

    @staticmethod
    def _score_one(lead: dict, dims: list[dict]) -> dict:
        dimension_scores: dict[str, int] = {}
        evidence_by_dimension: dict[str, list[str]] = {}
        unknown: list[str] = []
        for d in dims:
            name = str(d.get("name", ""))
            mx = int(d.get("max") or 0)
            if any(k in name.lower() for k in _ENRICHMENT_HINT):
                unknown.append(name)                       # cannot assess offline -> unknown
                continue
            dimension_scores[name] = round(mx * 0.6)        # modest placeholder
            evidence_by_dimension[name] = ["[MOCK] placeholder based on available fields"]
        return {
            "lead_index": lead.get("lead_index"),
            "dimension_scores": dimension_scores,
            "evidence_by_dimension": evidence_by_dimension,
            "dealbreaker_candidates": [],
            "qualification_reason": "[MOCK/OFFLINE] placeholder scoring — not a production judgement.",
            "unknown_fields": unknown,
            "model_confidence": "low",
        }


def get_client() -> tuple[Any, bool]:
    """Return ``(client, is_live)``. Live when ANTHROPIC_API_KEY is present."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicClient(), True
    logger.warning("ANTHROPIC_API_KEY not set — using offline MockClient (results are placeholders).")
    return MockClient(), False


# ---------------------------------------------------------------------------
# Parsing (tolerant)
# ---------------------------------------------------------------------------

def _json_blocks(text: str) -> list:
    """Return the parsed content of every ```json ...``` fenced block (in order)."""
    out = []
    for m in re.finditer(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def _extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array out of a model response, tolerating fences/prose and minor malformations."""
    fenced = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON array found in model response")
        candidate = text[start:end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)   # trailing commas
        repaired = re.sub(r"}\s*{", "},{", repaired)          # missing commas between objects
        data = json.loads(repaired)
    if not isinstance(data, list):
        raise ValueError("model response was not a JSON array")
    return data


# ---------------------------------------------------------------------------
# Proposal -> DecisionResult -> ScoringResult
# ---------------------------------------------------------------------------

def _confidence_level(score: int) -> str:
    return "high" if score >= 70 else "medium" if score >= 40 else "low"


def _clamp(value: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


# Enrichment-signal keyword -> evidence attributes that would DIRECTLY confirm it. The base
# Evidence Layer (Vayne export) produces none of these today, so an enrichment dimension is only
# kept if a future enrichment step supplies confirmed direct evidence under one of these attributes.
# ICP-aware (driven by ICPProfile.enrichment_required_fields); no commercial rules hardcoded.
_ENRICHMENT_EVIDENCE_ATTRS: dict[str, tuple[str, ...]] = {
    "funding": ("funding_stage", "funding_round", "recent_funding"),
    "stage": ("funding_stage", "company_stage"),
    "hiring": ("eng_hiring_signal", "open_roles", "hiring_signal"),
    "headcount": ("engineering_headcount", "eng_headcount"),
    "reachability": ("reachability", "recent_activity", "outreach_history"),
    "outreach": ("outreach_history", "recent_activity"),
    "revenue": ("revenue", "arr"),
    "traffic": ("web_traffic", "traffic"),
    "layoffs": ("layoffs", "distress"),
    "tech stack": ("tech_stack", "technology_stack"),
    "technology stack": ("tech_stack", "technology_stack"),
    "recent activity": ("recent_activity", "recent_posts"),
}


def _acceptable_evidence_attrs(dim_name: str) -> set[str]:
    low = dim_name.lower()
    attrs: set[str] = set()
    for kw, ats in _ENRICHMENT_EVIDENCE_ATTRS.items():
        if kw in low:
            attrs.update(ats)
    return attrs


def apply_enrichment_guard(profile: icp_profile.ICPProfile, evidence_items: list,
                           dimension_scores: dict) -> tuple[dict, list[str], list[str]]:
    """Remove model scores for enrichment-required dimensions that have no confirmed DIRECT
    evidence. Returns (guarded_scores, removed_dimensions, warnings). Missing enrichment data stays
    unknown — it is never turned into a zero/negative, and model claims cannot manufacture evidence
    (only Python-extracted EvidenceItems count)."""
    confirmed_attrs = {
        e.attribute for e in evidence_items
        if e.status == evidence.CONFIRMED and e.employment_scope in (evidence.SCOPE_CURRENT, evidence.SCOPE_NONE)
    }
    guarded = dict(dimension_scores)
    removed: list[str] = []
    warnings: list[str] = []
    for name in profile.enrichment_required_fields:
        if name not in guarded:
            continue                                  # already unknown / not scored
        acceptable = _acceptable_evidence_attrs(name)
        if acceptable & confirmed_attrs:
            continue                                  # direct evidence exists -> keep the score
        guarded.pop(name, None)
        removed.append(name)
        warnings.append(f"enrichment guard: '{name}' requires data not present in the source; score "
                        f"removed and marked unknown (no confirmed direct evidence).")
    return guarded, removed, warnings


def _result_from_proposal(lead: Lead, icp_name: str, profile: icp_profile.ICPProfile,
                          proposal: dict, model_name: str, is_mock: bool) -> ScoringResult:
    evidence_items = evidence.extract_evidence(lead.fields, lead.previous_roles)
    dimension_scores = proposal.get("dimension_scores") or {}
    dimension_scores, guard_removed, guard_warnings = apply_enrichment_guard(
        profile, evidence_items, dimension_scores)
    candidates = proposal.get("dealbreaker_candidates") or []
    # normalize candidate keys the Decision Layer understands
    norm_candidates = [{
        "label": c.get("name") or c.get("rule") or c.get("label"),
        "proposed_state": c.get("proposed_state") or c.get("state"),
        "evidence_attribute": c.get("evidence_attribute") or c.get("attribute"),
    } for c in candidates if isinstance(c, dict)]

    result = decide(profile, evidence_items, dimension_scores, norm_candidates)

    ev_by_dim = proposal.get("evidence_by_dimension") or {}
    dims: dict[str, Dimension] = {}
    for d in profile.scoring_dimensions:
        cap = int(d.weight) if d.weight is not None else 0
        pts = _clamp(dimension_scores.get(d.name), 0, cap)
        ev = "; ".join(str(x) for x in (ev_by_dim.get(d.name) or []))[:200]
        dims[d.name] = Dimension(points=pts, max=cap, evidence=ev)

    confidence = "low" if is_mock else _confidence_level(result.decision_confidence)
    model_conf = str(proposal.get("model_confidence", "")).lower() or None
    reason = str(proposal.get("qualification_reason", ""))[:400]
    signals = result.confirmed_dealbreakers + result.suspected_dealbreakers

    return ScoringResult(
        lead_index=lead.index, icp=icp_name,
        score=result.operational_lead_score, category=result.operational_priority,
        dimensions=dims,
        hard_dealbreaker=(result.dealbreaker_state == "confirmed"),
        dealbreaker_reason="; ".join(result.confirmed_dealbreakers) or None,
        reason=reason, signals=signals, confidence=confidence,
        unknowns=sorted(set(result.unknown_fields) | set(guard_removed)), model=model_name,
        raw_icp_score=result.raw_icp_score, operational_lead_score=result.operational_lead_score,
        operational_priority=result.operational_priority, internal_category=result.internal_category,
        provisional_priority=result.operational_priority,
        evidence_coverage=result.evidence_coverage, evidence_adjusted_fit=result.evidence_adjusted_fit,
        decision_confidence=result.decision_confidence, dealbreaker_state=result.dealbreaker_state,
        confirmed_dealbreakers=result.confirmed_dealbreakers,
        suspected_dealbreakers=result.suspected_dealbreakers,
        review_recommendation=result.review_recommendation,
        confidence_reasons=result.confidence_reasons,
        validation_warnings=guard_warnings + result.validation_warnings,
        model_confidence=model_conf, is_mock=is_mock,
    )


def _result_from_prequal(lead: Lead, icp_name: str, pr) -> ScoringResult:
    """Build a Disqualified ScoringResult from a deterministic pre-qualification (no model call)."""
    reason = pr.reason or "Deterministically disqualified by Python pre-qualification."
    return ScoringResult(
        lead_index=lead.index, icp=icp_name, score=0, category="Disqualified",
        dimensions={}, hard_dealbreaker=True, dealbreaker_reason=reason,
        reason=reason, signals=[], confidence="high", unknowns=[],
        model="python-prequalification", error=None,
        raw_icp_score=0, operational_lead_score=0, operational_priority="Disqualified",
        internal_category="Disqualified", provisional_priority="Disqualified", evidence_coverage=None,
        evidence_adjusted_fit=None, decision_confidence=100, dealbreaker_state="confirmed",
        confirmed_dealbreakers=[reason], suspected_dealbreakers=[],
        review_recommendation="priority_review",
        confidence_reasons=[f"deterministic pre-qualification ({pr.matched_rule})"],
        validation_warnings=["Determined by deterministic Python pre-qualification (no model call)."]
        + list(pr.warnings),
        model_confidence=None, is_mock=False)


def _error_result(lead: Lead, icp_name: str, model_name: str, message: str, is_mock: bool) -> ScoringResult:
    return ScoringResult(
        lead_index=lead.index, icp=icp_name, score=0, category="Error",
        dimensions={}, hard_dealbreaker=False, dealbreaker_reason=None,
        reason="Scoring failed for this lead.", signals=[], confidence="low",
        unknowns=[], model=model_name, error=message, provisional_priority="Error",
        review_recommendation="priority_review", is_mock=is_mock,
    )


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------

def _score_batch(client, system_blocks: list, profile: icp_profile.ICPProfile,
                 batch: list[Lead], is_mock: bool, call_counter: Optional[dict] = None) -> list[ScoringResult]:
    """Score one batch. Retries only THIS batch on failure (max MAX_RETRIES retries)."""
    user = build_user_prompt(batch)
    model_name = getattr(client, "model", "unknown")
    idxs = [lead.index for lead in batch]
    last_err = "unknown error"

    for attempt in range(1, MAX_RETRIES + 2):        # 1 initial + MAX_RETRIES retries
        try:
            if call_counter is not None:
                call_counter["calls"] = call_counter.get("calls", 0) + 1
            parsed = _extract_json_array(client.complete(system_blocks, user))
            by_index = {int(o.get("lead_index", -1)): o for o in parsed if isinstance(o, dict)}
            results = []
            for lead in batch:
                obj = by_index.get(lead.index)
                if obj is None:
                    results.append(_error_result(lead, profile.name, model_name,
                                                 "lead missing from model response", is_mock))
                else:
                    results.append(_result_from_proposal(lead, profile.name, profile, obj,
                                                          model_name, is_mock))
            return results
        except Exception as exc:  # noqa: BLE001 — retry any parse/transport failure for THIS batch
            last_err = f"{type(exc).__name__}: {exc}"
            logger.warning("Batch %s attempt %d/%d failed: %s", idxs, attempt, MAX_RETRIES + 1, last_err)
            if attempt <= MAX_RETRIES:
                time.sleep(1.0 * attempt)

    logger.error("Batch %s permanently failed after %d attempts", idxs, MAX_RETRIES + 1)
    return [_error_result(lead, profile.name, model_name, last_err, is_mock) for lead in batch]


ProgressCallback = Callable[[int, int], None]


def score_leads(leads: list[Lead], icp_text: str, icp_name: str, *,
                client=None, batch_size: int = BATCH_SIZE,
                progress_cb: Optional[ProgressCallback] = None,
                stats: Optional[dict] = None,
                profile: Optional[icp_profile.ICPProfile] = None) -> list[ScoringResult]:
    """Score every lead against one ICP. Same public return type as before.

    Flow: build the ICPProfile once → deterministic Python pre-qualification (no model call) →
    only the remaining leads go to the model batches → merge and sort. Deterministic
    disqualifications spend zero model tokens and are never sent to the model or retried.

    ``stats`` (optional): if a dict is passed it is populated with run metrics.

    ``profile`` (optional, Sprint 5.6): when supplied (e.g. an Approved GeneratedICP converted by
    ``icp_adapter`` and passed through ``qualification_bridge``), it is used verbatim instead of
    parsing ``icp_text`` into a profile. ``icp_text`` is then used only as semantic context for the
    model. When ``profile is None`` behavior is identical to before (the PDF path).
    """
    if client is None:
        client, _ = get_client()
    is_mock = isinstance(client, MockClient)
    profile = profile if profile is not None else build_scoring_profile(icp_name, icp_text)
    system_blocks = build_system_blocks(load_system_prompt(), build_icp_context(profile, icp_text))
    total = len(leads)

    # --- Python pre-qualification (before ANY model/mock call) --------------
    seen_slugs: set = set()
    prequal_results: list[ScoringResult] = []
    to_model: list[Lead] = []
    for lead in leads:
        evs = evidence.extract_evidence(lead.fields, lead.previous_roles)
        pr = prequalification.prequalify(profile, lead, evs, seen_slugs=seen_slugs)
        slug = prequalification.lead_identifier(lead)
        if slug:
            seen_slugs.add(slug)
        if pr.is_disqualified:
            prequal_results.append(_result_from_prequal(lead, profile.name, pr))
        else:
            to_model.append(lead)

    logger.info("Scoring %d lead(s) vs ICP '%s' | prequalified=%d | to_model=%d | model=%s | mock=%s",
                total, icp_name, len(prequal_results), len(to_model),
                getattr(client, "model", "?"), is_mock)
    if progress_cb:
        progress_cb(len(prequal_results), total)

    # --- model scoring for the remaining leads only ------------------------
    call_counter = {"calls": 0}
    model_results: list[ScoringResult] = []
    for start in range(0, len(to_model), batch_size):
        batch = to_model[start:start + batch_size]
        model_results.extend(_score_batch(client, system_blocks, profile, batch, is_mock, call_counter))
        done = len(prequal_results) + min(start + batch_size, len(to_model))
        logger.info("Progress: %d/%d leads scored", done, total)
        if progress_cb:
            progress_cb(done, total)
    if progress_cb and not to_model:
        progress_cb(total, total)

    results = prequal_results + model_results
    results.sort(key=lambda r: r.score, reverse=True)

    if stats is not None:
        model_ok = [r for r in model_results if r.error is None]
        stats.update(total_input=total, prequalified=len(prequal_results),
                     sent_to_model=len(to_model), model_calls=call_counter["calls"],
                     model_analyzed=len(model_ok),
                     failed=sum(1 for r in model_results if r.error is not None),
                     avoided_model_leads=len(prequal_results))
    return results
