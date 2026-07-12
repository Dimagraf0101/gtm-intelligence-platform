"""AI Draft ICP Generation (Sprint 4.1E).

Generates a **Draft** GeneratedICP from structured BusinessKnowledge + a KnowledgeGapReport. Claude
proposes the judgment parts (business context, qualification dimensions, examples); Python
deterministically structures everything safety-critical (targets, buyers, hard exclusions, fixed
priority thresholds, evidence rules, unknown/enrichment) and the IQS validator remains the authority.

Input contract — the generator receives ONLY:
  - structured BusinessKnowledge;
  - a KnowledgeGapReport;
  - an optional ICP name and optional user notes.
It never receives raw PDF/DOCX/PPTX/TXT content (there is no package/document parameter).

Same client / retries / parsing / metrics / mock patterns as the Qualification Engine and the
Business Knowledge Extractor. Model: claude-haiku-4-5-20251001. Reuses the existing GeneratedICP
serializers and the IQS validator — no second export format, no engine changes.
"""
from __future__ import annotations

import os
import re
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import generated_icp as gi
import iqs_validator as iqs
import business_knowledge as bk
import knowledge_gaps as kg  # noqa: F401  (type reference in the public contract)
from knowledge_extractor import _is_cache_error, _strip_cache_control, _usage_dict

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_RETRIES = 2
MAX_OUTPUT_TOKENS = 8000

_HISTORICAL = ("historical", "former", "past", "legacy", "previous", "ex-")
_ENRICHMENT_HINTS = ("funding", "hiring", "revenue", "traffic", "headcount", "tech stack",
                     "technology stack", "reachab", "engagement", "layoff")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class ICPDraftGenerationResult:
    generated_icp: Optional[gi.GeneratedICP] = None
    validation_result: Optional[iqs.ValidationResult] = None
    successful: bool = False
    parser_failures: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_calls: int = 0
    model: str = MODEL
    is_mock: bool = False
    warnings: list[str] = field(default_factory=list)
    generation_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "successful": self.successful,
            "parser_failures": self.parser_failures,
            "retries": self.retries,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "model_calls": self.model_calls,
            "model": self.model,
            "is_mock": self.is_mock,
            "warnings": self.warnings,
            "generation_notes": self.generation_notes,
            "generated_icp": self.generated_icp.to_dict() if self.generated_icp else None,
            "validation": {
                "is_valid": self.validation_result.is_valid,
                "blocking_errors": self.validation_result.blocking_errors,
                "warnings": self.validation_result.warnings,
                "completeness_score": self.validation_result.completeness_score,
            } if self.validation_result else None,
        }


# ---------------------------------------------------------------------------
# Prompt / caching
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "icp_draft_system.md"
    return path.read_text(encoding="utf-8")


def build_system_blocks(system_prompt: str) -> list[dict]:
    """Stable instructions/schema/safety — cacheable. Knowledge digest goes in the user message."""
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]


def _describe_knowledge(knowledge) -> str:
    lines = []
    for cat in bk.CATEGORIES:
        items = [it for it in knowledge.get_items(category=cat)
                 if it.is_active and (it.has_value or it.status == bk.UNKNOWN)]
        if not items:
            continue
        lines.append(f"### {cat}")
        for it in items:
            ex = f'  | quote: "{it.evidence_excerpt[:80]}"' if it.evidence_excerpt else ""
            lines.append(f"- [{it.status}] {it.attribute}: {str(it.value)[:120]}{ex}")
    if knowledge.conflicts:
        lines.append("### conflicts (unresolved)")
        for c in knowledge.conflicts:
            lines.append(f"- {c.category}/{c.attribute}: {c.conflicting_values}")
    if knowledge.unknown_fields:
        lines.append("### declared unknowns")
        lines.append("- " + ", ".join(knowledge.unknown_fields))
    return "\n".join(lines)


def build_user_prompt(knowledge, gap_report, icp_name=None, user_notes=None) -> str:
    """Structured knowledge digest ONLY (values + short quotes already stored in BusinessKnowledge).
    No raw document text is ever included."""
    parts = ["## Structured Business Knowledge\n" + _describe_knowledge(knowledge)]
    if gap_report is not None and gap_report.suggested_questions:
        parts.append("## Open questions (gaps — do NOT answer, do not guess)\n"
                     + "\n".join(f"- {q}" for q in gap_report.suggested_questions[:20]))
    if icp_name:
        parts.append(f"## Requested ICP name\n{icp_name}")
    if user_notes:
        parts.append(f"## User notes\n{user_notes}")
    parts.append(
        "## Task\nPropose ONLY: business_context (description, value_proposition, business_model); "
        "qualification `dimensions` whose weights total 100, each with purpose, scoring_guidance and "
        "required_evidence_attributes (mark enrichment-dependent dimensions); and `examples` "
        "(ideal / acceptable / non_ideal). Return one JSON object with keys "
        "business_context, dimensions, examples, generation_notes. Do not set status or thresholds, "
        "do not invent facts, and never give negative scoring guidance for missing information.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Model clients (pluggable)
# ---------------------------------------------------------------------------

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "business_context": {
            "type": "object",
            "properties": {"description": {"type": "string"},
                           "value_proposition": {"type": "string"},
                           "business_model": {"type": "string"}},
            "additionalProperties": False,
        },
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}, "purpose": {"type": "string"},
                    "weight": {"type": "number"}, "scoring_guidance": {"type": "string"},
                    "required_evidence_attributes": {"type": "array", "items": {"type": "string"}},
                    "external_enrichment_required": {"type": "boolean"},
                },
                "required": ["name", "weight"], "additionalProperties": False,
            },
        },
        "examples": {
            "type": "object",
            "properties": {"ideal": {"type": "array", "items": {"type": "string"}},
                           "acceptable": {"type": "array", "items": {"type": "string"}},
                           "non_ideal": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
        "generation_notes": {"type": "string"},
    },
    "required": ["business_context", "dimensions"],
    "additionalProperties": False,
}


class ICPDraftClient:
    """Real Claude client for draft generation. Captures token usage (incl. cache read/write); falls
    back cleanly when prompt caching or structured output is unavailable. ``_create`` is injectable
    for offline testing."""

    model = MODEL

    def __init__(self, model: str = MODEL, api_key: Optional[str] = None, *,
                 use_structured: bool = True, _create=None):
        self.model = model
        self.use_structured = use_structured
        if _create is not None:
            self._create = _create
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
            self._create = client.messages.create

    def complete(self, system, user: str, *, structured: bool = False):
        base = dict(model=self.model, max_tokens=MAX_OUTPUT_TOKENS, system=system,
                    messages=[{"role": "user", "content": user}])
        kwargs = dict(base)
        if structured and self.use_structured:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": _DRAFT_SCHEMA}}
        try:
            msg = self._create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if isinstance(system, list) and _is_cache_error(exc):
                logger.warning("Prompt caching unavailable (%s); retrying without cache_control.",
                               type(exc).__name__)
                fb = dict(kwargs)
                fb["system"] = _strip_cache_control(system)
                msg = self._create(**fb)
            elif structured and self.use_structured:
                logger.warning("Structured output unavailable (%s); retrying as plain text.",
                               type(exc).__name__)
                msg = self._create(**base)
            else:
                raise
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return text, _usage_dict(getattr(msg, "usage", None))


class MockICPDraftClient:
    """Deterministic OFFLINE stand-in. Produces a valid-shape draft (weights total 100) marked as
    mock; results must never be treated as production-ready."""

    model = "mock"

    def complete(self, system, user: str, *, structured: bool = False):
        draft = {
            "business_context": {
                "description": "[MOCK] Draft business context derived from Business Knowledge.",
                "value_proposition": "[MOCK] placeholder value proposition.",
                "business_model": "[MOCK] placeholder business model.",
            },
            "dimensions": [
                {"name": "Segment fit", "purpose": "Match target industry/subsegment", "weight": 40,
                 "scoring_guidance": "Score higher when the company matches the target segment.",
                 "required_evidence_attributes": ["company_industry"],
                 "external_enrichment_required": False},
                {"name": "Buyer persona", "purpose": "Match the target buyer role", "weight": 30,
                 "scoring_guidance": "Score by how closely the contact matches a target role.",
                 "required_evidence_attributes": ["contact_title"],
                 "external_enrichment_required": False},
                {"name": "Company size", "purpose": "Match preferred company size", "weight": 30,
                 "scoring_guidance": "Score by headcount fit to the preferred range.",
                 "required_evidence_attributes": ["company_size"],
                 "external_enrichment_required": False},
            ],
            "examples": {"ideal": ["[MOCK] ideal-fit example"],
                         "acceptable": ["[MOCK] acceptable example"],
                         "non_ideal": ["[MOCK] poor-fit example"]},
            "generation_notes": "[MOCK/OFFLINE] draft — not production-ready.",
        }
        return json.dumps(draft, ensure_ascii=False), {
            "input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}


def get_draft_client() -> tuple[Any, bool]:
    if os.getenv("ANTHROPIC_API_KEY"):
        return ICPDraftClient(), True
    logger.warning("ANTHROPIC_API_KEY not set — using offline MockICPDraftClient (placeholder).")
    return MockICPDraftClient(), False


# ---------------------------------------------------------------------------
# Parsing (tolerant)
# ---------------------------------------------------------------------------

def parse_draft(text: str) -> dict:
    """Tolerant extraction of a single draft JSON object. Raises ValueError if none is found — a
    failed parse never yields a fabricated ICP."""
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise ValueError("no JSON draft object found in model output")


# ---------------------------------------------------------------------------
# Knowledge helpers (Python authority)
# ---------------------------------------------------------------------------

def _dedup(values):
    out, seen = [], set()
    for v in values:
        k = str(v).strip().lower()
        if v and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def _cat_values(knowledge, category, status=None):
    return [it.value for it in knowledge.get_items(category=category)
            if it.is_active and it.has_value and (status is None or it.status == status)]


def _is_historical(item) -> bool:
    blob = (item.attribute + " " + " ".join(item.notes) + " " + str(item.value)).lower()
    return any(h in blob for h in _HISTORICAL)


def _targets(knowledge, category):
    """(included_values, had_proposed, historical_values) for a target-company category. Historical
    experience is never promoted to a current target."""
    included, historical, had_proposed = [], [], False
    for it in knowledge.get_items(category=category):
        if not (it.is_active and it.has_value):
            continue
        if _is_historical(it):
            historical.append(it.value)
            continue
        included.append(it.value)
        if it.status == bk.PROPOSED:
            had_proposed = True
    return _dedup(included), had_proposed, _dedup(historical)


def _extract_range(text: str):
    t = str(text).replace(",", "")
    m = re.search(r"(\d+)\s*[-–—]\s*(\d+)", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"(\d+)\s*\+", t)
    if m:
        return f"{m.group(1)}+"
    return None


# ---------------------------------------------------------------------------
# Draft assembly (Python structures the ICP; Claude only proposes soft parts)
# ---------------------------------------------------------------------------

def _dimensions_from_draft(draft: dict, warnings: list[str]) -> list[gi.QualificationDimension]:
    dims = []
    for d in (draft.get("dimensions") or []):
        name = str(d.get("name", "")).strip()
        if not name:
            continue
        try:
            weight = int(round(float(d.get("weight", 0))))
        except (TypeError, ValueError):
            weight = 0
            warnings.append(f"Dimension '{name}' had a non-numeric weight; set to 0 (IQS will flag).")
        dims.append(gi.QualificationDimension(
            name=name, purpose=str(d.get("purpose", "")).strip(), weight=weight,
            scoring_guidance=str(d.get("scoring_guidance", "")).strip(),
            required_evidence_attributes=[str(a) for a in (d.get("required_evidence_attributes") or [])],
            external_enrichment_required=bool(d.get("external_enrichment_required", False))))
    names = [dd.name.lower() for dd in dims]
    if len(names) != len(set(names)):
        warnings.append("Duplicate qualification dimensions proposed (IQS will flag as blocking).")
    total = sum(dd.weight for dd in dims)
    if dims and total != 100:
        warnings.append(f"Dimension weights total {total}, not 100 (IQS will flag; not auto-normalized).")
    return dims


def _assemble(draft, knowledge, gap_report, icp_name, user_notes, is_mock, model_ok):
    warnings: list[str] = []
    today = date.today().isoformat()

    # --- metadata / source files ---
    source_files = _dedup([r.filename for it in knowledge.knowledge_items
                           for r in it.source_references if r.filename])
    company_names = _cat_values(knowledge, "company", bk.CONFIRMED) or _cat_values(knowledge, "company")
    name = (icp_name or (f"{company_names[0]} — Draft ICP" if company_names else "Draft ICP")).strip()
    meta = gi.Metadata(name=name, version="1", status=gi.STATUS_DRAFT,
                       created_date=today, updated_date=today,
                       entry_point=gi.ENTRY_GENERATE_NEW, source_files=source_files)

    # --- business context (Claude proposes; knowledge grounds product/service) ---
    bc_in = draft.get("business_context") or {}
    products = _cat_values(knowledge, "product") + _cat_values(knowledge, "service")
    business_ctx = gi.BusinessContext(
        description=str(bc_in.get("description", "")).strip()
                    or "; ".join(_cat_values(knowledge, "company"))[:400],
        product_or_service="; ".join(_dedup(products))[:400],
        value_proposition=str(bc_in.get("value_proposition", "")).strip(),
        business_model=str(bc_in.get("business_model", "")).strip()
                       or "; ".join(_cat_values(knowledge, "business_model", bk.CONFIRMED))[:200],
        capabilities=_dedup(_cat_values(knowledge, "capability") + _cat_values(knowledge, "technology")),
        notes=(user_notes or "").strip())

    # --- target companies (historical never promoted; proposed flagged) ---
    industries, ind_prop, ind_hist = _targets(knowledge, "industry")
    subsegs, sub_prop, _ = _targets(knowledge, "subsegment")
    geos, geo_prop, _ = _targets(knowledge, "geography")
    biz_models, _, _ = _targets(knowledge, "business_model")
    size_ranges, size_attrs = [], []
    for v in _cat_values(knowledge, "company_size"):
        r = _extract_range(v)
        (size_ranges if r else size_attrs).append(r or v)
    for label, hist in (("industries", ind_hist),):
        if hist:
            warnings.append(f"Historical experience NOT promoted to target {label}: {', '.join(hist)}")
    if ind_prop or sub_prop or geo_prop:
        warnings.append("Some target-company attributes are PROPOSED (unverified) — review before use.")
    target_co = gi.TargetCompanies(
        target_industries=industries, target_subsegments=subsegs,
        target_company_types=[], preferred_employee_ranges=_dedup(size_ranges),
        acceptable_employee_ranges=[], target_geographies=geos,
        target_business_models=biz_models, preferred_attributes=_dedup(size_attrs))

    # --- target buyers (confirmed populate roles; proposed only warn) ---
    confirmed_buyers = _dedup(_cat_values(knowledge, "buyer", bk.CONFIRMED))
    proposed_buyers = _dedup(_cat_values(knowledge, "buyer", bk.PROPOSED))
    excluded_roles = _dedup(_cat_values(knowledge, "excluded_buyer"))
    if proposed_buyers:
        warnings.append("Proposed buyer roles (uncertain — confirm in review): "
                        + ", ".join(proposed_buyers))
    buyers = gi.TargetBuyers(primary_buyer_roles=confirmed_buyers, secondary_buyer_roles=[],
                             excluded_buyer_roles=excluded_roles, title_tiers=[],
                             buyer_notes=("Proposed buyers pending confirmation." if proposed_buyers else ""))

    # --- dimensions (Claude) ---
    dims = _dimensions_from_draft(draft, warnings)

    # --- hard exclusions: ONLY from explicit candidates (never from preferences/mentions) ---
    hard_exclusions = []
    for it in knowledge.get_items(category="hard_exclusion_candidate"):
        if not (it.is_active and it.has_value):
            continue
        hard_exclusions.append(gi.HardExclusion(
            rule=str(it.value), reason=(it.notes[0] if it.notes else "Declared exclusion candidate."),
            evidence_required=(it.evidence_excerpt or "Direct statement in source material."),
            evaluation_mode=gi.EVAL_SEMANTIC, scope=gi.SCOPE_CURRENT_COMPANY))
        if not it.user_confirmed:
            warnings.append(f"Proposed hard exclusion (review before use): {it.value}")

    # --- evidence requirements (fixed, engine-consistent) ---
    evidence = gi.EvidenceRequirements(
        accepted_sources=_dedup(["Uploaded business materials"]
                                + [r.source_category for it in knowledge.knowledge_items
                                   for r in it.source_references if r.source_category]),
        current_employment_rules="Only current-company evidence qualifies a company; previous "
                                 "employment never defines the current company.",
        previous_employment_restrictions="Previous/historical roles never confirm current-company "
                                         "facts or exclusions.",
        conflict_handling="Conflicting evidence lowers confidence and is surfaced for review; never "
                          "resolved silently.",
        evidence_quality_rules="Direct, current evidence required for hard exclusions; missing "
                               "information is unknown, not negative.")

    # --- unknown / enrichment / examples / ambiguity ---
    gaps = list(gap_report.blocking_gaps) + list(gap_report.important_gaps) if gap_report else []
    unknown_fields = _dedup(list(knowledge.unknown_fields)
                            + [g.field for g in gaps if g.current_status == "missing"])
    enrichment_fields = _dedup([d.name for d in dims if d.external_enrichment_required]
                               + [u for u in unknown_fields
                                  if any(h in u.lower() for h in _ENRICHMENT_HINTS)])
    ex_in = draft.get("examples") or {}
    examples = gi.Examples(
        ideal_leads=[str(x) for x in (ex_in.get("ideal") or [])]
                    or _dedup([it.value for it in knowledge.get_items(category="customer")
                               if it.attribute == "best_customer" and it.has_value]),
        acceptable_leads=[str(x) for x in (ex_in.get("acceptable") or [])],
        non_ideal_leads=[str(x) for x in (ex_in.get("non_ideal") or [])]
                        or _dedup([it.value for it in knowledge.get_items(category="customer")
                                   if it.attribute == "lost_customer" and it.has_value]))
    ambiguous = [f"{c.category}/{c.attribute}: {c.conflicting_values}" for c in knowledge.conflicts]

    if not confirmed_buyers:
        unknown_fields = _dedup(unknown_fields + ["buyer roles"])
    if gap_report is not None:
        warnings.extend(gap_report.warnings)
    warnings.insert(0, "AI-generated DRAFT — human review and interview required before approval.")
    if is_mock:
        warnings.insert(0, "MOCK/OFFLINE draft — placeholder content, NOT production-ready.")
    if not model_ok:
        warnings.insert(0, "Model output could not be parsed — returned a minimal Python-only draft "
                           "(no proposed dimensions/examples); requires review.")

    icp = gi.GeneratedICP(
        metadata=meta, business_context=business_ctx, target_companies=target_co, target_buyers=buyers,
        dimensions=dims, priority_thresholds=gi.standard_priority_bands(),
        hard_exclusions=hard_exclusions, evidence_requirements=evidence,
        unknown_fields=unknown_fields, enrichment_fields=enrichment_fields,
        ambiguous_definitions=ambiguous, examples=examples, warnings=_dedup(warnings),
        history=[gi.HistoryEntry(version="1", date=today, author="ai-draft-generator",
                                 change_summary="Initial AI-generated draft from Business Knowledge.")])
    return icp, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_draft_icp(business_knowledge, gap_report, *, icp_name: Optional[str] = None,
                       user_notes: Optional[str] = None, client=None,
                       stats: Optional[dict] = None) -> ICPDraftGenerationResult:
    """Generate a Draft GeneratedICP from BusinessKnowledge + KnowledgeGapReport.

    Claude proposes; Python structures and enforces the safety rules; the IQS validator is the
    authority. Status is always Draft; priority thresholds are always the fixed operational bands.
    """
    if client is None:
        client, _ = get_draft_client()
    is_mock = isinstance(client, MockICPDraftClient)

    result = ICPDraftGenerationResult(model=getattr(client, "model", MODEL), is_mock=is_mock)
    system_blocks = build_system_blocks(load_system_prompt())
    user = build_user_prompt(business_knowledge, gap_report, icp_name, user_notes)

    draft, model_ok = _run_draft(client, system_blocks, user, result)

    icp, warnings = _assemble(draft, business_knowledge, gap_report, icp_name, user_notes,
                              is_mock, model_ok)
    icp.metadata.status = gi.STATUS_DRAFT          # Python authority: never Approved from generation
    icp.priority_thresholds = gi.standard_priority_bands()

    result.generated_icp = icp
    result.warnings = list(icp.warnings)
    result.validation_result = iqs.validate(icp)

    # Generation is successful only when parsing succeeded, the ICP is a serializable Draft.
    serializable = _serializable(icp)
    result.successful = bool(model_ok and serializable and icp.metadata.status == gi.STATUS_DRAFT)

    notes = []
    if isinstance(draft.get("generation_notes"), str) and draft["generation_notes"].strip():
        notes.append(draft["generation_notes"].strip())
    if gap_report is not None and gap_report.suggested_questions:
        notes.append("Suggested next actions (from gaps): "
                     + " | ".join(gap_report.suggested_questions[:8]))
    notes.append(f"IQS: {'valid' if result.validation_result.is_valid else 'blocking issues'} "
                 f"(completeness {result.validation_result.completeness_score}); "
                 "human review / interview required before approval.")
    result.generation_notes = notes

    if stats is not None:
        stats.update(successful=result.successful, is_mock=result.is_mock, model=result.model,
                     model_calls=result.model_calls, retries=result.retries,
                     parser_failures=result.parser_failures,
                     input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                     cache_read_tokens=result.cache_read_tokens,
                     cache_write_tokens=result.cache_write_tokens,
                     iqs_valid=result.validation_result.is_valid)
    return result


def _run_draft(client, system_blocks, user, result):
    """One model call with up to MAX_RETRIES retries. Returns (draft_dict, ok). On total failure
    returns an empty draft with ok=False — the caller then builds a minimal Python-only Draft."""
    last_err = "unknown error"
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            result.model_calls += 1
            text, usage = client.complete(system_blocks, user, structured=True)
            result.input_tokens += usage["input_tokens"]
            result.output_tokens += usage["output_tokens"]
            result.cache_read_tokens += usage["cache_read_tokens"]
            result.cache_write_tokens += usage["cache_write_tokens"]
            return parse_draft(text), True
        except ValueError as exc:
            result.parser_failures += 1
            last_err = f"parser: {exc}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt <= MAX_RETRIES:
            result.retries += 1
    result.warnings.append(f"Draft generation failed after {MAX_RETRIES + 1} attempts: {last_err}")
    return {}, False


def _serializable(icp) -> bool:
    try:
        json.loads(icp.to_json())
        icp.to_markdown()
        return True
    except Exception:  # noqa: BLE001
        return False
