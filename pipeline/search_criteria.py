"""Sales Navigator search-criteria suggester (Sprint 5.4).

Turns a selected ICP into concrete **Sales Navigator filter suggestions** so an operator can build
the search quickly. Two layers, matching the project's "Python decides; AI proposes" pattern:

  - `suggest_from_targets(...)` — deterministic, offline. Maps the ICP's structured targets onto
    Sales Navigator's actual filter vocabulary: employee ranges -> the fixed headcount buckets, buyer
    roles -> seniority levels + a copy-paste title boolean, plus geographies / industries / exclusions.
  - `refine_with_ai(...)` — optional. Asks the model to map fuzzy industries to Sales Navigator's
    taxonomy and expand title synonyms, then Python re-validates against the fixed vocabularies.

Vayne consumes a Sales Navigator search *URL*, so this never fabricates a URL — it hands the operator
the filters to apply. No network in the deterministic path.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# --- Sales Navigator fixed vocabularies --------------------------------------

# (label, low, high) — high=None means "and up". Mirrors Sales Navigator's headcount filter.
_BUCKET_BOUNDS = [
    ("1-10", 1, 10), ("11-50", 11, 50), ("51-200", 51, 200), ("201-500", 201, 500),
    ("501-1000", 501, 1000), ("1001-5000", 1001, 5000), ("5001-10000", 5001, 10000),
    ("10001+", 10001, None),
]
HEADCOUNT_BUCKETS = [b[0] for b in _BUCKET_BOUNDS]
_BUCKET_INDEX = {b[0]: i for i, b in enumerate(_BUCKET_BOUNDS)}

SENIORITY_LEVELS = ["Owner", "Partner", "CXO", "VP", "Director", "Manager", "Senior", "Entry"]

# role keyword -> seniority level (word-boundary matched so "director" is not read as "cto")
_SENIORITY_RULES = [
    (r"\b(founder|co-founder|owner|proprietor)\b", "Owner"),
    (r"\bpartner\b", "Partner"),
    (r"\b(chief|cxo|ceo|cto|cfo|cmo|coo|ciso|cpo|cro|cio)\b", "CXO"),
    (r"\b(vp|svp|evp)\b|vice president", "VP"),
    (r"\bdirector\b|head of", "Director"),
    (r"\bmanager\b|\blead\b|\bprincipal\b", "Manager"),
    (r"\bsenior\b|\bsr\b", "Senior"),
]


@dataclass
class SalesNavCriteria:
    geographies: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    subsegments: list[str] = field(default_factory=list)
    headcount_buckets: list[str] = field(default_factory=list)
    seniority_levels: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    title_boolean: str = ""
    excluded_titles: list[str] = field(default_factory=list)
    keyword_boolean: str = ""
    notes: list[str] = field(default_factory=list)
    source: str = "deterministic"          # deterministic | ai

    def to_dict(self) -> dict:
        return asdict(self)


# --- helpers -----------------------------------------------------------------

def _dedup(values) -> list[str]:
    out, seen = [], set()
    for v in values or []:
        s = str(v).strip()
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _headcount_buckets(ranges: list[str]) -> list[str]:
    """Map ICP employee ranges onto the Sales Navigator headcount buckets they overlap."""
    from icp_profile import parse_employee_range
    picked: set[str] = set()
    for rng in ranges or []:
        r = parse_employee_range(rng)
        lo = r.low
        if lo is None:
            continue
        hi = r.high if r.high is not None else float("inf")
        for name, blo, bhi in _BUCKET_BOUNDS:
            bhi_eff = bhi if bhi is not None else float("inf")
            if blo <= hi and bhi_eff >= lo:       # ranges overlap
                picked.add(name)
    return sorted(picked, key=lambda n: _BUCKET_INDEX[n])


def _seniority_from_roles(roles: list[str]) -> list[str]:
    found: set[str] = set()
    for role in roles or []:
        low = f" {str(role).lower().strip()} "
        for pattern, level in _SENIORITY_RULES:
            if re.search(pattern, low):
                found.add(level)
    return [lvl for lvl in SENIORITY_LEVELS if lvl in found]


def _boolean(titles: list[str]) -> str:
    titles = _dedup(titles)
    if not titles:
        return ""
    return "(" + " OR ".join(f'"{t}"' for t in titles) + ")"


# --- deterministic suggestion ------------------------------------------------

def suggest_from_targets(targets: Optional[dict], icp_text: str = "") -> SalesNavCriteria:
    """Deterministic Sales Navigator filter suggestion from an ICP's stored target attributes."""
    t = targets or {}
    geographies = _dedup(t.get("geographies"))
    industries = _dedup(t.get("industries"))
    subsegments = _dedup(t.get("subsegments"))
    sizes = list(t.get("preferred_sizes") or []) + list(t.get("acceptable_sizes") or [])
    primary = _dedup(t.get("primary_roles"))
    secondary = _dedup(t.get("secondary_roles"))
    titles = _dedup(primary + secondary)
    excluded = _dedup(t.get("excluded_roles"))

    notes: list[str] = []
    if industries:
        notes.append("Sales Navigator uses a fixed industry list — pick the closest official match "
                     "to each industry above (or click 'Refine with AI').")
    if not sizes:
        notes.append("No company-size preference in the ICP — leave headcount unfiltered or set it "
                     "from your own judgement.")
    if not titles:
        notes.append("No buyer roles in the ICP — add current-title / seniority filters manually.")

    return SalesNavCriteria(
        geographies=geographies, industries=industries, subsegments=subsegments,
        headcount_buckets=_headcount_buckets(sizes),
        seniority_levels=_seniority_from_roles(titles),
        title_keywords=titles, title_boolean=_boolean(titles),
        excluded_titles=excluded, keyword_boolean="",
        notes=notes, source="deterministic")


def to_markdown(crit: SalesNavCriteria) -> str:
    """Plain-text rendering for copy / edit."""
    def line(label, vals):
        return f"{label}: {', '.join(vals)}" if vals else None
    parts = [x for x in [
        line("Geography", crit.geographies),
        line("Industry", crit.industries),
        line("Company headcount", crit.headcount_buckets),
        line("Seniority", crit.seniority_levels),
        line("Job titles", crit.title_keywords),
        line("Exclude titles", crit.excluded_titles),
    ] if x]
    if crit.title_boolean:
        parts.append(f"Title keywords: {crit.title_boolean}")
    if crit.keyword_boolean:
        parts.append(f"Keywords: {crit.keyword_boolean}")
    return "\n".join(parts) + "\n"


# --- AI refinement (optional) ------------------------------------------------

MODEL = "claude-haiku-4-5-20251001"


def _load_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "search_criteria_system.md"
    return path.read_text(encoding="utf-8")


class CriteriaClient:
    """Real Claude client. ``_create`` is injectable for offline testing."""

    model = MODEL

    def __init__(self, model: str = MODEL, api_key: Optional[str] = None, _create=None):
        self.model = model
        if _create is not None:
            self._create = _create
        else:
            import anthropic
            self._create = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY")).messages.create

    def complete(self, system: str, user: str) -> str:
        msg = self._create(model=self.model, max_tokens=1500,
                           system=[{"type": "text", "text": system}],
                           messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


class MockCriteriaClient:
    """Offline stand-in — no refinement is possible without the model."""
    model = "mock"

    def complete(self, system: str, user: str) -> str:
        return "{}"


def get_criteria_client() -> tuple[Any, bool]:
    if os.getenv("ANTHROPIC_API_KEY"):
        return CriteriaClient(), True
    return MockCriteriaClient(), False


def _parse_obj(text: str) -> dict:
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {}


def refine_with_ai(base: SalesNavCriteria, icp_text: str, *, client=None) -> SalesNavCriteria:
    """Refine the deterministic suggestion with the model. Offline (mock) returns ``base`` unchanged
    with a note. Python re-validates headcount/seniority against the fixed vocabularies."""
    if client is None:
        client, _ = get_criteria_client()
    if isinstance(client, MockCriteriaClient):
        out = SalesNavCriteria(**{**base.to_dict(), "source": "deterministic"})
        out.notes = base.notes + ["Offline — AI refine unavailable; showing the deterministic "
                                  "suggestion (set ANTHROPIC_API_KEY to enable)."]
        return out

    user = ("## ICP (for context)\n" + (icp_text or "")[:6000]
            + "\n\n## Deterministic first pass (refine this)\n"
            + json.dumps(base.to_dict(), ensure_ascii=False))
    try:
        data = _parse_obj(client.complete(_load_prompt(), user))
    except Exception:  # noqa: BLE001 — a failed refine falls back to the deterministic base
        out = SalesNavCriteria(**{**base.to_dict()})
        out.notes = base.notes + ["AI refine failed — kept the deterministic suggestion."]
        return out
    if not data:
        return base

    def clean(v):
        return _dedup(v) if isinstance(v, list) else []

    hc = [b for b in clean(data.get("headcount_buckets")) if b in HEADCOUNT_BUCKETS]
    sen = [s for s in clean(data.get("seniority_levels")) if s in SENIORITY_LEVELS]
    return SalesNavCriteria(
        geographies=clean(data.get("geographies")) or base.geographies,
        industries=clean(data.get("industries")) or base.industries,
        subsegments=base.subsegments,
        headcount_buckets=hc or base.headcount_buckets,
        seniority_levels=sen or base.seniority_levels,
        title_keywords=clean(data.get("title_keywords")) or base.title_keywords,
        title_boolean=(str(data.get("title_boolean", "")).strip() or base.title_boolean),
        excluded_titles=clean(data.get("excluded_titles")) or base.excluded_titles,
        keyword_boolean=str(data.get("keyword_boolean", "")).strip(),
        notes=clean(data.get("notes"))[:3],
        source="ai")
