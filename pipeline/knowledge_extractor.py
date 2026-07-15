"""Production-grade AI Business Knowledge extraction (Sprint 4.1D, hardened in 4.1D.2).

Claude *proposes* knowledge; Python *decides*. This module drives the extraction stage to the same
engineering standard as the Lead Qualification Engine — real/mock client pattern, deterministic
batching with per-batch retry, tolerant parsing + strict validation, prompt caching of stable
content only, and full token/run metrics.

    SourcePackage -> [Claude proposals] -> Python validation/merge/conflict -> BusinessKnowledge
                                                                            -> KnowledgeGapReport

4.1D.2 hardening (stability/quality only — no new functionality):
  - larger output budget + smaller batches + deterministic splitting of oversized sources, so a
    batch is never silently lost to output truncation;
  - salvage parsing recovers complete proposal objects from a truncated JSON array;
  - list-valued attributes are consolidated into one multi-value item instead of creating artificial
    ConflictRecords (genuine single-valued contradictions still conflict);
  - excerpt verification tolerates whitespace / unicode dash-and-quote variants of a *literal* quote
    (recall up, precision unchanged — literal presence is still required);
  - prompt strengthened to demand the shortest verbatim quotation.

Guardrails are unchanged: a proposal is CONFIRMED only when Python verifies the cited source and a
literal excerpt, the item is a direct factual statement (not interpretation), confidence clears a
conservative threshold, and no conflicting active item exists. Missing information stays unknown;
absence is never negative evidence. No chain-of-thought is stored; full source text is never copied
into items. Behaviour is identical for both entry points.

Model: claude-haiku-4-5-20251001 (same as the Qualification Engine).
"""
from __future__ import annotations

import os
import re
import json
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import business_knowledge as bk
import knowledge_gaps as kg

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
# 4.1D.2: smaller batches + larger output budget keep proposal lists well under the token cap.
BATCH_CHAR_BUDGET = 18_000            # deterministic batching: chars of source text per batch
MAX_OUTPUT_TOKENS = 16_000            # safe production value (was 8000 — caused truncation)
MAX_PROPOSALS_PER_BATCH = 60          # safety cap; the prompt asks the model for far fewer
MAX_RETRIES = 2                       # max retries per FAILED batch (3 attempts total)

CONFIRM_THRESHOLD = 0.6               # conservative confidence floor for promotion to confirmed
MAX_FIELD_CHARS = 4000               # oversized-payload guard per proposal field
MIN_EXCERPT_CHARS = 4                # excerpts shorter than this can match spuriously — never confirm

VALID_PROPOSAL_STATUSES = ("proposed", "conflicting", "unknown")  # Claude may never return confirmed

# Haiku 4.5's minimum cacheable prefix (see issue #4 note under build_system_blocks).
HAIKU_MIN_CACHE_TOKENS = 4096

# Categories whose values are interpretations, not verifiable facts — they always stay PROPOSED,
# even with an excerpt (likely buyer, suggested market, size preference, hard-exclusion/evidence-rule
# candidates, qualification-dimension suggestions live under these).
INTERPRETIVE_CATEGORIES = frozenset({
    "buyer", "excluded_buyer", "industry", "subsegment", "company_size",
    "hard_exclusion_candidate", "evidence_rule",
})

# List-valued attributes: multiple distinct values are a LIST, not a contradiction. Consolidate them
# into one "; "-joined item rather than emitting ConflictRecords. Genuinely single-valued facts
# (company identity, business model, company size) are excluded and still conflict on disagreement.
MULTI_VALUE_CATEGORIES = frozenset({
    "buyer", "excluded_buyer", "evidence_rule", "hard_exclusion_candidate", "technology",
    "capability", "service", "commercial_constraint", "product", "customer", "other",
    "industry", "subsegment", "geography",
})

# Temporal contexts that permit "current" confirmation.
_CURRENT_CONTEXTS = frozenset({"", "current", "present"})

# Map the extractor prompt's temporal vocabulary onto the persisted KnowledgeItem temporal contexts
# (bk.TEMPORAL_*). A fact is persisted as "current" ONLY when the model explicitly says so; a missing
# or unrecognized label persists as "unknown" (Sprint 5.2.1: missing temporal never means current).
_TEMPORAL_PERSIST_MAP = {
    "": bk.TEMPORAL_UNKNOWN,
    "current": bk.TEMPORAL_CURRENT,
    "present": bk.TEMPORAL_CURRENT,
    "historical": bk.TEMPORAL_HISTORICAL,
    "historical_market": bk.TEMPORAL_HISTORICAL,
    "past": bk.TEMPORAL_HISTORICAL,
    "former": bk.TEMPORAL_FORMER,
    "former_customer": bk.TEMPORAL_FORMER,
    "proposed_future": bk.TEMPORAL_FUTURE,
    "future": bk.TEMPORAL_FUTURE,
    "unknown": bk.TEMPORAL_UNKNOWN,
}


def _persist_temporal(temporal: str) -> str:
    """Normalize a model-proposed temporal_context to a persisted KnowledgeItem temporal context.
    Missing/unrecognized labels persist as 'unknown' so a fact is never *silently* treated as current;
    an explicit non-current label the model asserts is preserved for downstream filtering."""
    return _TEMPORAL_PERSIST_MAP.get(temporal, bk.TEMPORAL_UNKNOWN)

_COT_MARKERS = ("chain of thought", "chain-of-thought", "let's think", "let me think",
                "reasoning:", "step 1:", "step 1 ", "i think ", "my reasoning")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeExtractionResult:
    source_count: int = 0
    successful_batches: int = 0
    failed_batches: int = 0
    retries: int = 0
    parser_failures: int = 0
    proposals_received: int = 0
    proposals_accepted: int = 0
    proposals_confirmed: int = 0        # item-level: knowledge items ending confirmed
    proposals_left_proposed: int = 0    # item-level: knowledge items ending proposed
    proposals_rejected: int = 0
    conflicts_created: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_calls: int = 0
    model: str = MODEL
    is_mock: bool = False
    warnings: list[str] = field(default_factory=list)
    business_knowledge: Optional[bk.BusinessKnowledge] = None
    gap_report: Optional[kg.KnowledgeGapReport] = None

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "source_count", "successful_batches", "failed_batches", "retries", "parser_failures",
            "proposals_received", "proposals_accepted", "proposals_confirmed",
            "proposals_left_proposed", "proposals_rejected", "conflicts_created",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "model_calls", "model", "is_mock", "warnings")}
        d["business_knowledge"] = self.business_knowledge.to_dict() if self.business_knowledge else None
        d["gap_report"] = self.gap_report.to_dict() if self.gap_report else None
        return d


# ---------------------------------------------------------------------------
# Prompt / caching
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "business_knowledge_system.md"
    return path.read_text(encoding="utf-8")


def build_system_blocks(system_prompt: str) -> list[dict]:
    """Stable content only (instructions + schema + vocabulary + safety rules), marked cacheable.

    Company documents are NEVER placed here — they go in the per-batch user message, so private
    source text is never cached as shared content.

    Issue #4 (caching didn't activate) — investigated: this is a *model minimum*, not a bug. Haiku
    4.5 only caches a prefix of at least HAIKU_MIN_CACHE_TOKENS (4096) tokens; the extraction system
    prompt is ~2k tokens, below that floor, so the API silently returns
    ``cache_creation_input_tokens = 0`` (no error). The ``cache_control`` marker and the uncached
    fallback are correct and left in place for forward-compatibility: if the stable prefix ever grows
    past the floor, caching engages automatically. We do not pad the prompt to force caching — that
    would trade real tokens for a marginal, size-dependent discount, and the per-batch documents
    (the bulk of the input) are never cacheable regardless.
    """
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]


def build_user_prompt(batch_units) -> str:
    """User message — the batch's source units, with boundaries preserved. Never cached."""
    parts = []
    for u in batch_units:
        parts.append(
            "--- SOURCE START ---\n"
            f"Filename: {u.filename}\n"
            f"Category: {u.source_category}\n"
            "--- CONTENT ---\n"
            f"{u.extracted_text}\n"
            "--- SOURCE END ---")
    return (
        "Extract candidate business-knowledge proposals from the following source material. "
        f"Return ONLY a JSON array of at most {MAX_PROPOSALS_PER_BATCH} proposal objects following "
        "the system-prompt schema. For every non-unknown proposal, copy the SHORTEST possible "
        "VERBATIM quotation into evidence_excerpt — it must appear literally in the source.\n\n"
        + "\n\n".join(parts))


# ---------------------------------------------------------------------------
# Model clients (pluggable)
# ---------------------------------------------------------------------------

def _is_cache_error(exc: Exception) -> bool:
    return "cache" in str(exc).lower()


def _strip_cache_control(system: list[dict]) -> list[dict]:
    return [{k: v for k, v in b.items() if k != "cache_control"} for b in system]


def _usage_dict(usage) -> dict:
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    }


_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "attribute": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                    "status": {"type": "string", "enum": list(VALID_PROPOSAL_STATUSES)},
                    "source_filename": {"type": "string"},
                    "source_category": {"type": "string"},
                    "source_section_reference": {"type": "string"},
                    "evidence_excerpt": {"type": "string"},
                    "temporal_context": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["category", "attribute", "value", "confidence", "status"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["proposals"],
    "additionalProperties": False,
}


class KnowledgeExtractionClient:
    """Real Claude client for extraction. Captures token usage (incl. cache read/write), falls back
    cleanly when prompt caching or structured output is unavailable.

    ``_create`` is injectable for offline testing (defaults to anthropic ``messages.create``)."""

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
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": _PROPOSAL_SCHEMA}}
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
                msg = self._create(**base)     # tolerant parsing handles the plain response
            else:
                raise
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return text, _usage_dict(getattr(msg, "usage", None))


class MockKnowledgeClient:
    """Deterministic OFFLINE stand-in. Proposes candidates only; results are marked is_mock and must
    never be mistaken for production knowledge. A mock proposal is confirmed only if its excerpt
    genuinely appears in a fixture source (same rule Python applies to real proposals)."""

    model = "mock"

    def complete(self, system, user: str, *, structured: bool = False):
        proposals = []
        for filename, category, content in _parse_boundaries(user):
            head = content.strip().splitlines()[0] if content.strip() else ""
            excerpt = content.strip()[:30]
            proposals.append({
                "category": "company", "attribute": "overview", "value": head[:120] or "(unnamed)",
                "confidence": 0.7, "status": "proposed",
                "source_filename": filename, "source_category": category,
                "source_section_reference": "", "evidence_excerpt": excerpt,
                "temporal_context": "current", "notes": "[MOCK] placeholder — not production knowledge.",
            })
            proposals.append({
                "category": "buyer", "attribute": "role", "value": "decision maker",
                "confidence": 0.4, "status": "proposed",
                "source_filename": filename, "source_category": category,
                "source_section_reference": "", "evidence_excerpt": "",
                "temporal_context": "current", "notes": "[MOCK] interpretive — stays proposed.",
            })
        text = json.dumps(proposals, ensure_ascii=False)
        return text, {"input_tokens": 0, "output_tokens": 0,
                      "cache_write_tokens": 0, "cache_read_tokens": 0}


def get_client() -> tuple[Any, bool]:
    """Return ``(client, is_live)``. Live when ANTHROPIC_API_KEY is present."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return KnowledgeExtractionClient(), True
    logger.warning("ANTHROPIC_API_KEY not set — using offline MockKnowledgeClient (placeholders).")
    return MockKnowledgeClient(), False


# ---------------------------------------------------------------------------
# Parsing (tolerant + salvage)
# ---------------------------------------------------------------------------

_BOUNDARY_RE = re.compile(
    r"--- SOURCE START ---\nFilename: (?P<f>.*?)\nCategory: (?P<c>.*?)\n--- CONTENT ---\n"
    r"(?P<body>.*?)\n--- SOURCE END ---", re.S)
_FLAT_OBJ_RE = re.compile(r"\{[^{}]*\}", re.S)     # flat proposal objects (schema has no nesting)


def _parse_boundaries(user: str):
    for m in _BOUNDARY_RE.finditer(user):
        yield m.group("f"), m.group("c"), m.group("body")


def _coerce(data):
    if isinstance(data, dict) and isinstance(data.get("proposals"), list):
        return data["proposals"]
    if isinstance(data, list):
        return data
    return None


def parse_proposals(text: str) -> list[dict]:
    """Tolerant extraction with salvage. Accepts a bare JSON array, a {"proposals": [...]} object
    (structured output), or a fenced/embedded block. If the array is truncated, salvages the complete
    proposal objects so a partially-truncated response never loses the whole batch. Raises ValueError
    only when nothing usable is found."""
    text = (text or "").strip()
    try:
        got = _coerce(json.loads(text))
        if got is not None:
            return got
    except json.JSONDecodeError:
        pass
    for m in re.finditer(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.S):
        try:
            got = _coerce(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
        if got is not None:
            return got
    m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    if m:
        try:
            got = _coerce(json.loads(m.group(1)))
            if got is not None:
                return got
        except json.JSONDecodeError:
            pass
    # Salvage: recover complete proposal objects from a truncated/garbled array.
    salvaged = []
    for om in _FLAT_OBJ_RE.finditer(text):
        try:
            obj = json.loads(om.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "category" in obj:
            salvaged.append(obj)
    if salvaged:
        return salvaged
    raise ValueError("no JSON proposals found in model output")


# ---------------------------------------------------------------------------
# Validation (Python authority)
# ---------------------------------------------------------------------------

def _looks_like_cot(*values: str) -> bool:
    blob = " ".join(v.lower() for v in values if isinstance(v, str))
    return any(marker in blob for marker in _COT_MARKERS)


def _validate_shape(p: dict) -> Optional[str]:
    if not isinstance(p, dict):
        return "proposal is not an object"
    category = str(p.get("category", "")).strip()
    attribute = str(p.get("attribute", "")).strip()
    value = str(p.get("value", "") or "")
    status = str(p.get("status", "")).strip()
    excerpt = str(p.get("evidence_excerpt", "") or "")
    notes = str(p.get("notes", "") or "")
    if category not in bk.CATEGORIES:
        return f"unsupported category '{category}'"
    if not attribute:
        return "empty attribute"
    if status not in VALID_PROPOSAL_STATUSES:
        return f"invalid status '{status}' (Claude may not assert 'confirmed')"
    if status != "unknown" and not value.strip():
        return "empty value for non-unknown proposal"
    conf = p.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= float(conf) <= 1.0):
        return f"invalid confidence {conf!r}"
    if len(value) > MAX_FIELD_CHARS or len(excerpt) > MAX_FIELD_CHARS:
        return "oversized proposal payload"
    if _looks_like_cot(value, excerpt, notes):
        return "proposal contains chain-of-thought-like content"
    return None


def _index_sources(package):
    from source_documents import SUCCESS, PARTIAL
    idx = {}
    for doc in getattr(package, "source_documents", []):
        if doc.extraction_status in (SUCCESS, PARTIAL) and doc.extracted_text:
            idx.setdefault(doc.filename.lower(), doc)
    return idx


# Precision-preserving excerpt normalization: unify whitespace and common unicode dash/quote variants
# so a genuinely LITERAL quote still matches when it differs only in punctuation encoding. Literal
# presence is still required — this never lets a paraphrase confirm.
_DASHES = {"–": "-", "—": "-", "−": "-"}
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"'}


def _norm_excerpt(s: str) -> str:
    for a, b in {**_DASHES, **_QUOTES}.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


def _excerpt_in(doc, excerpt: str) -> bool:
    e = excerpt.strip()
    if len(e) < MIN_EXCERPT_CHARS:
        return False
    return _norm_excerpt(e) in _norm_excerpt(doc.extracted_text)


# ---------------------------------------------------------------------------
# Batching + orchestration
# ---------------------------------------------------------------------------

class _Chunk:
    """A deterministic slice of an oversized source. Duck-types the fields build_user_prompt uses;
    keeps the original filename so excerpts still verify against the full source text."""
    __slots__ = ("filename", "source_category", "extracted_text", "source_id",
                 "extraction_status", "original_order", "part", "of")

    def __init__(self, doc, text, part, of):
        self.filename = doc.filename
        self.source_category = doc.source_category
        self.extracted_text = text
        self.source_id = doc.source_id
        self.extraction_status = doc.extraction_status
        self.original_order = doc.original_order
        self.part = part
        self.of = of


def _build_batches(package, char_budget: int):
    """Deterministic batches of usable, non-duplicate sources in upload order. Oversized sources are
    split into contiguous chunks so no unit exceeds the budget — a single large source can never
    become one unsplit (and truncation-prone) batch. Failed/unsupported/duplicate sources excluded."""
    from source_documents import SUCCESS, PARTIAL
    usable = [d for d in sorted(package.source_documents, key=lambda x: x.original_order)
              if d.extraction_status in (SUCCESS, PARTIAL) and d.extracted_text
              and getattr(d, "duplicate_of", None) is None]

    units = []
    for doc in usable:
        text = doc.extracted_text
        if len(text) <= char_budget:
            units.append(doc)
        else:
            parts = [text[i:i + char_budget] for i in range(0, len(text), char_budget)]
            for k, part in enumerate(parts, 1):
                units.append(_Chunk(doc, part, k, len(parts)))

    batches, current, size = [], [], 0
    for u in units:
        n = len(u.extracted_text)
        if current and size + n > char_budget:
            batches.append(current)
            current, size = [], 0
        current.append(u)
        size += n
    if current:
        batches.append(current)
    return batches


def extract_business_knowledge(package, *, client=None, entry_point: Optional[str] = None,
                               char_budget: int = BATCH_CHAR_BUDGET,
                               structured: bool = True) -> KnowledgeExtractionResult:
    """Extract a BusinessKnowledge (+ gap report + metrics) from a SourcePackage.

    Claude proposes; Python validates/confirms/merges and builds the gap report. Entry-point neutral.
    """
    if client is None:
        client, _ = get_client()
    is_mock = isinstance(client, MockKnowledgeClient)

    knowledge = bk.from_source_package(package, entry_point=entry_point)
    result = KnowledgeExtractionResult(
        model=getattr(client, "model", MODEL), is_mock=is_mock, business_knowledge=knowledge)
    result.warnings.extend(knowledge.warnings)

    system_blocks = build_system_blocks(load_system_prompt())
    src_index = _index_sources(package)
    batches = _build_batches(package, char_budget)
    result.source_count = len(src_index)        # original usable source documents (not batches)

    for batch in batches:
        proposals, ok = _run_batch(client, system_blocks, batch, structured, result)
        if not ok:
            result.failed_batches += 1
            continue
        result.successful_batches += 1
        for p in proposals:
            _ingest_proposal(p, knowledge, src_index, result)

    # Item-level status tallies (consolidation means one item may absorb several proposals).
    items = knowledge.knowledge_items
    result.proposals_confirmed = sum(1 for it in items if it.status == bk.CONFIRMED)
    result.proposals_left_proposed = sum(1 for it in items if it.status == bk.PROPOSED)
    result.proposals_accepted = result.proposals_received - result.proposals_rejected
    result.conflicts_created = len(knowledge.conflicts)
    result.gap_report = kg.detect_gaps(knowledge)
    return result


def _run_batch(client, system_blocks, batch, structured, result):
    """Call the model for one batch, retrying only this batch (max MAX_RETRIES). Returns
    (proposals, ok). A parser failure counts against retries and never invalidates other batches."""
    user = build_user_prompt(batch)
    last_err = "unknown error"
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            result.model_calls += 1
            text, usage = client.complete(system_blocks, user, structured=structured)
            result.input_tokens += usage["input_tokens"]
            result.output_tokens += usage["output_tokens"]
            result.cache_read_tokens += usage["cache_read_tokens"]
            result.cache_write_tokens += usage["cache_write_tokens"]
            proposals = parse_proposals(text)
            if len(proposals) > MAX_PROPOSALS_PER_BATCH:
                result.warnings.append(
                    f"Batch returned {len(proposals)} proposals; capped to {MAX_PROPOSALS_PER_BATCH}.")
                proposals = proposals[:MAX_PROPOSALS_PER_BATCH]
            result.proposals_received += len(proposals)
            return proposals, True
        except ValueError as exc:               # parser failure — isolated to this batch
            result.parser_failures += 1
            last_err = f"parser: {exc}"
        except Exception as exc:                # noqa: BLE001 — transport/model failure
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt <= MAX_RETRIES:
            result.retries += 1
    result.warnings.append(f"Batch of {len(batch)} unit(s) failed after "
                           f"{MAX_RETRIES + 1} attempts: {last_err}")
    return [], False


def _merge_values(existing: str, new: str) -> str:
    parts = [p.strip() for p in str(existing).split(";") if p.strip()]
    seen = {p.lower() for p in parts}
    nv = str(new).strip()
    if nv and nv.lower() not in seen:
        parts.append(nv)
    return "; ".join(parts)


def _first_active(knowledge, category, attribute):
    for it in knowledge.get_items(category=category):
        if it.attribute == attribute and it.is_active:
            return it
    return None


def _ingest_proposal(p: dict, knowledge, src_index, result) -> None:
    """Validate one proposal and merge it into BusinessKnowledge as confirmed / proposed / unknown /
    rejected, per Python authority. List-valued attributes are consolidated, not conflicted."""
    reason = _validate_shape(p)
    if reason:
        result.proposals_rejected += 1
        result.warnings.append(f"Rejected proposal ({reason}).")
        return

    category = str(p["category"]).strip()
    attribute = str(p["attribute"]).strip()
    value = str(p.get("value", "") or "")
    status = str(p["status"]).strip()
    confidence = float(p["confidence"])
    filename = str(p.get("source_filename", "") or "")
    section_ref = str(p.get("source_section_reference", "") or "")
    excerpt = str(p.get("evidence_excerpt", "") or "")
    temporal = str(p.get("temporal_context", "") or "").strip().lower()
    persisted_temporal = _persist_temporal(temporal)
    notes = [str(p.get("notes", "") or "")] if p.get("notes") else []

    if status == "unknown":
        knowledge.add_item(category, attribute, value, status=bk.UNKNOWN, origin=bk.ORIGIN_AI,
                           confidence=confidence, notes=notes, temporal_context=persisted_temporal)
        return

    doc = src_index.get(filename.lower())
    excerpt_ok = bool(doc) and _excerpt_in(doc, excerpt)
    if not excerpt_ok:
        result.warnings.append(
            f"Unverified attribution for '{category}/{attribute}' "
            f"(source or literal excerpt not found); kept as proposed.")

    refs = []
    if excerpt_ok:
        refs = [bk.SourceReference(
            source_id=doc.source_id, filename=doc.filename, source_category=doc.source_category,
            section_index=_as_int(section_ref),
            excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest()[:16],
            extraction_status=doc.extraction_status)]

    # Multi-value consolidation: append a DIFFERENT value to the existing list item (no conflict).
    if category in MULTI_VALUE_CATEGORIES:
        existing = _first_active(knowledge, category, attribute)
        norm_new = bk._normalize_for(category, value)
        if existing is not None and existing.normalized_value != norm_new:
            _consolidate(existing, value, refs, confidence, notes)
            return

    confirmable = (
        excerpt_ok
        and category not in INTERPRETIVE_CATEGORIES
        and confidence >= CONFIRM_THRESHOLD
        and temporal in _CURRENT_CONTEXTS
        and not _has_conflicting_active(knowledge, category, attribute, value)
    )
    final_status = bk.CONFIRMED if confirmable else bk.PROPOSED
    knowledge.add_item(category, attribute, value, status=final_status, origin=bk.ORIGIN_AI,
                       confidence=confidence, source_references=refs,
                       evidence_excerpt=(excerpt if excerpt_ok else ""), notes=notes,
                       temporal_context=persisted_temporal)


def _consolidate(item, value, refs, confidence, notes) -> None:
    """Fold a distinct value into an existing list item. A multi-value list is never asserted as
    confirmed (precision), so the item is held at PROPOSED."""
    item.value = _merge_values(item.value, value)
    item.normalized_value = bk._normalize_for(item.category, item.value)
    item.status = bk.PROPOSED
    keys = {r._key() for r in item.source_references}
    for r in refs:
        if r._key() not in keys:
            item.source_references.append(r)
            keys.add(r._key())
    item.confidence = max(item.confidence, confidence)
    for n in notes:
        if n and n not in item.notes:
            item.notes.append(n)
    item.updated_at = bk._now()


def _has_conflicting_active(knowledge, category, attribute, value) -> bool:
    norm = bk._normalize_for(category, value)
    for it in knowledge.get_items(category=category):
        if it.attribute == attribute and it.has_value and it.normalized_value != norm:
            return True
    return False


def _as_int(text: str):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None
