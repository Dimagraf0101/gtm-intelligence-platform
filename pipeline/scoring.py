"""Qualification Engine — score leads against an ICP.

Division of labour (fixed by design):

* **Claude** returns, per lead: per-dimension ``points`` + evidence, a
  ``hard_dealbreaker`` flag, a short ``reason``, ``signals``, ``confidence`` and
  ``unknowns``. It does NOT decide the final number or the category.
* **Python** (this module) validates and clamps everything, sums the dimensions
  into a final 0-100 score, maps that score to a priority category, and applies
  hard dealbreakers.

The model client is pluggable. If ``ANTHROPIC_API_KEY`` is set we call Claude;
otherwise we fall back to a deterministic local ``MockClient`` so the full
workflow is runnable offline. Swapping to real scoring needs no code change.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

# Importing config loads .env (so ANTHROPIC_API_KEY becomes available) and gives paths.
from config import BASE_DIR  # noqa: E402  (config has side effect: load_dotenv)

logger = logging.getLogger("qualification")

# ---------------------------------------------------------------------------
# Canonical scoring model (source of truth: docs + Lead_Scoring_Guide.xlsx)
# ---------------------------------------------------------------------------

# Dimension name -> maximum points. Total ceiling = 108, final score is capped at 100.
DIMENSIONS: dict[str, int] = {
    "title": 45,
    "industry": 28,
    "company_size": 15,
    "location": 10,
    "signals": 10,
}

MAX_SCORE = 100

# Score -> priority category. Checked high to low; first match wins.
CATEGORY_BANDS: list[tuple[int, str]] = [
    (90, "A+ / Hot"),
    (75, "A / High"),
    (50, "B / Normal"),
    (30, "C / Low"),
    (0, "D / Disqualified"),
]

# A hard dealbreaker forces this category and caps the score at this value.
DEALBREAKER_CATEGORY = "D / Disqualified"
DEALBREAKER_SCORE_CAP = 20

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 5
MAX_RETRIES = 2
PROMPT_PATH = BASE_DIR / "prompts" / "scoring_system.md"


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------

# Raw CSV column -> normalized field. Values are lists of candidate column names
# (matched case-insensitively); the first present column wins.
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

# Fields sent to the model (order = readability in the prompt).
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

    def model_view(self) -> dict[str, str]:
        """Compact dict of non-empty fields to embed in the prompt."""
        return {k: self.fields[k] for k in _MODEL_FIELDS if self.fields.get(k)}


@dataclass
class Dimension:
    points: int
    max: int
    evidence: str = ""


@dataclass
class ScoringResult:
    """Engine output for one lead against one ICP. Single source of truth."""
    lead_index: int
    icp: str
    score: int                      # 0-100, computed by Python
    category: str                   # computed by Python
    dimensions: dict[str, Dimension]
    hard_dealbreaker: bool
    dealbreaker_reason: Optional[str]
    reason: str
    signals: list[str]
    confidence: str                 # high | medium | low
    unknowns: list[str]
    model: str
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dimensions"] = {k: asdict(v) for k, v in self.dimensions.items()}
        return d


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_lead(raw_row: dict[str, str], index: int) -> Lead:
    """Map a raw CSV row to a normalized :class:`Lead` (case-insensitive columns)."""
    lower = {(k or "").strip().lower(): (v or "") for k, v in raw_row.items()}
    fields: dict[str, str] = {}
    for norm, candidates in _FIELD_MAP.items():
        for cand in candidates:
            if cand in lower and str(lower[cand]).strip():
                fields[norm] = str(lower[cand]).strip()
                break
    return Lead(index=index, fields=fields, raw=dict(raw_row))


# ---------------------------------------------------------------------------
# Prompt building (no prompt strings hardcoded here — loaded from prompts/)
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def build_user_prompt(icp_text: str, icp_name: str, batch: list[Lead]) -> str:
    leads_payload = [dict(lead_index=lead.index, **lead.model_view()) for lead in batch]
    return (
        f"# ICP: {icp_name}\n\n"
        f"{icp_text}\n\n"
        f"# Leads to score ({len(batch)})\n\n"
        f"Score every lead below against the ICP. Return a JSON array only.\n\n"
        f"```json\n{json.dumps(leads_payload, ensure_ascii=False, indent=1)}\n```"
    )


# ---------------------------------------------------------------------------
# Model clients (pluggable)
# ---------------------------------------------------------------------------

class AnthropicClient:
    """Real Claude client."""

    def __init__(self, model: str = MODEL, api_key: Optional[str] = None):
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")


class MockClient:
    """Deterministic offline stand-in. Returns the same JSON schema as Claude.

    Uses only fields present in the data, so it never 'invents' information — it
    exists so the workflow is runnable without an API key, not to be accurate.
    """

    model = "mock"

    def complete(self, system: str, user: str) -> str:
        leads = json.loads(re.search(r"```json\n(.*)\n```", user, re.S).group(1))
        out = []
        for lead in leads:
            out.append(self._score_one(lead))
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def _score_one(lead: dict) -> dict:
        title = (lead.get("job_title") or "").lower()
        industry = (lead.get("industry") or "").lower()
        size = (lead.get("company_size_range") or "")
        loc = (lead.get("location") or "").lower()

        def pts(hay: str, strong: list[str], weak: list[str], hi: int, mid: int) -> int:
            if any(w in hay for w in strong):
                return hi
            if any(w in hay for w in weak):
                return mid
            return 0

        title_pts = pts(title, ["coo", "chief operating", "head of operations", "vp of operations",
                                 "revenue operations", "chief of staff"],
                        ["founder", "ceo", "cto", "cpo", "director", "head", "vp"], 45, 24)
        industry_pts = pts(industry, ["software", "saas", "information technology", ".ai"],
                           ["data", "internet", "technology", "fintech", "financial"], 28, 14)
        size_pts = 15 if size in ("11-50", "51-200") else (13 if size in ("201-500",) else
                                                            (8 if size in ("2-10", "501-1000") else 3))
        loc_pts = 10 if any(c in loc for c in ["san francisco", "bay area", "california"]) else (
            7 if any(c in loc for c in ["new york", "seattle", "boston", "austin"]) else (
                5 if "united states" in loc or "usa" in loc else 3))
        conns = str(lead.get("connections") or "").replace(",", "")
        signal_pts = 2 if conns.isdigit() and int(conns) >= 1000 else 0

        unknowns = ["funding: not confirmed", "tech stack: unknown"]
        return {
            "lead_index": lead["lead_index"],
            "dimensions": {
                "title": {"points": title_pts, "evidence": lead.get("job_title", "") or "no title"},
                "industry": {"points": industry_pts, "evidence": lead.get("industry", "") or "no industry"},
                "company_size": {"points": size_pts, "evidence": size or "unknown size"},
                "location": {"points": loc_pts, "evidence": lead.get("location", "") or "no location"},
                "signals": {"points": signal_pts, "evidence": f"{conns} connections" if signal_pts else "none"},
            },
            "hard_dealbreaker": False,
            "dealbreaker_reason": None,
            "reason": f"{lead.get('job_title', 'Unknown role')} at {lead.get('company', 'unknown company')} "
                      f"({size or 'size unknown'}, {lead.get('industry', 'industry unknown')}).",
            "signals": [f"{conns} connections"] if signal_pts else [],
            "confidence": "low",
            "unknowns": unknowns,
        }


def get_client() -> tuple[Any, bool]:
    """Return ``(client, is_live)``. Live when ANTHROPIC_API_KEY is present."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicClient(), True
    logger.warning("ANTHROPIC_API_KEY not set — using offline MockClient (results are placeholders).")
    return MockClient(), False


# ---------------------------------------------------------------------------
# Parsing + validation + final scoring (Python owns the math)
# ---------------------------------------------------------------------------

def _extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array out of a model response, tolerating fences/prose."""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON array found in model response")
        candidate = text[start:end + 1]
    data = json.loads(candidate)
    if not isinstance(data, list):
        raise ValueError("model response was not a JSON array")
    return data


def _clamp(value: Any, lo: int, hi: int) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = 0
    return max(lo, min(hi, n))


def _category_for(score: int) -> str:
    for threshold, label in CATEGORY_BANDS:
        if score >= threshold:
            return label
    return CATEGORY_BANDS[-1][1]


def build_result(raw: dict, icp_name: str, model_name: str) -> ScoringResult:
    """Validate one raw model object and compute the final score + category."""
    dims: dict[str, Dimension] = {}
    raw_dims = raw.get("dimensions") or {}
    for name, cap in DIMENSIONS.items():
        d = raw_dims.get(name) or {}
        dims[name] = Dimension(points=_clamp(d.get("points"), 0, cap), max=cap,
                               evidence=str(d.get("evidence", ""))[:200])

    total = min(MAX_SCORE, sum(d.points for d in dims.values()))
    hard = bool(raw.get("hard_dealbreaker"))
    reason = str(raw.get("reason", ""))[:240]
    db_reason = raw.get("dealbreaker_reason")
    db_reason = str(db_reason)[:240] if db_reason else None

    if hard:
        total = min(total, DEALBREAKER_SCORE_CAP)
        category = DEALBREAKER_CATEGORY
    else:
        category = _category_for(total)

    confidence = str(raw.get("confidence", "low")).lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    signals = [str(s) for s in (raw.get("signals") or []) if str(s).strip()]
    unknowns = [str(u) for u in (raw.get("unknowns") or []) if str(u).strip()]

    return ScoringResult(
        lead_index=int(raw.get("lead_index", -1)),
        icp=icp_name,
        score=total,
        category=category,
        dimensions=dims,
        hard_dealbreaker=hard,
        dealbreaker_reason=db_reason,
        reason=reason,
        signals=signals,
        confidence=confidence,
        unknowns=unknowns,
        model=model_name,
    )


def _error_result(lead: Lead, icp_name: str, model_name: str, message: str) -> ScoringResult:
    return ScoringResult(
        lead_index=lead.index, icp=icp_name, score=0, category="D / Disqualified",
        dimensions={n: Dimension(0, cap, "") for n, cap in DIMENSIONS.items()},
        hard_dealbreaker=False, dealbreaker_reason=None,
        reason="Scoring failed for this lead.", signals=[], confidence="low",
        unknowns=[], model=model_name, error=message,
    )


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------

def _score_batch(client, system: str, icp_text: str, icp_name: str,
                 batch: list[Lead]) -> list[ScoringResult]:
    """Score one batch, with retries on transport / JSON errors."""
    user = build_user_prompt(icp_text, icp_name, batch)
    model_name = getattr(client, "model", "unknown")
    last_err = "unknown error"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            text = client.complete(system, user)
            parsed = _extract_json_array(text)
            by_index = {int(o.get("lead_index", -1)): o for o in parsed if isinstance(o, dict)}
            results = []
            for lead in batch:
                obj = by_index.get(lead.index)
                if obj is None:
                    results.append(_error_result(lead, icp_name, model_name,
                                                 "lead missing from model response"))
                else:
                    results.append(build_result(obj, icp_name, model_name))
            return results
        except Exception as exc:  # noqa: BLE001 — we want to retry any failure
            last_err = f"{type(exc).__name__}: {exc}"
            logger.warning("Batch attempt %d/%d failed: %s", attempt, MAX_RETRIES, last_err)
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * attempt)

    logger.error("Batch permanently failed after %d attempts: %s", MAX_RETRIES, last_err)
    return [_error_result(lead, icp_name, model_name, last_err) for lead in batch]


ProgressCallback = Callable[[int, int], None]


def score_leads(leads: list[Lead], icp_text: str, icp_name: str, *,
                client=None, batch_size: int = BATCH_SIZE,
                progress_cb: Optional[ProgressCallback] = None) -> list[ScoringResult]:
    """Score every lead against one ICP. Reports progress as (done, total)."""
    if client is None:
        client, _ = get_client()
    system = load_system_prompt()
    total = len(leads)
    results: list[ScoringResult] = []
    logger.info("Scoring %d lead(s) against ICP '%s' using model '%s' (batch=%d)",
                total, icp_name, getattr(client, "model", "?"), batch_size)

    for start in range(0, total, batch_size):
        batch = leads[start:start + batch_size]
        results.extend(_score_batch(client, system, icp_text, icp_name, batch))
        done = min(start + batch_size, total)
        logger.info("Progress: %d/%d leads scored", done, total)
        if progress_cb:
            progress_cb(done, total)

    results.sort(key=lambda r: r.score, reverse=True)
    return results
