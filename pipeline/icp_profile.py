"""Knowledge Layer (ICP Profile) — Release 0.3, Sprint 3.2.

Convert an extracted ICP (plain text) — or an explicit structured definition — into a normalized,
validated ``ICPProfile`` that later checkpoints (Decision, Confidence, Export) can consume.

Principles (docs/ARCHITECTURE.md §5, docs/DECISIONS.md ADR-002/003/004):

* **Never invent missing ICP information.** Fields that cannot be confidently extracted are left
  empty and recorded in ``unknown_fields`` with a warning.
* **Preserve unknown values explicitly** — no silent defaults.
* **Commercial exclusions are ICP-specific** (``excluded_company_types`` / ``hard_exclusions``);
  **universal exclusions are validity-only** and constant (``UNIVERSAL_VALIDITY_EXCLUSIONS``).
* **Validate, don't fix.** Problems are returned as ``warnings``; the profile is never silently
  corrected.
* Generic enough to run on every ICP PDF in the repo (extraction quality varies by document — that
  is surfaced honestly, not faked).

Pure Python (stdlib only). No LLM, no network, no external dependencies. Operates on a *string* of
already-extracted ICP text, so it does not depend on the PDF library.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, asdict, field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ADR-002: universal exclusions are minimal and about data/process validity only. They are NOT
# derived from any ICP and NEVER include commercial fit rules.
UNIVERSAL_VALIDITY_EXCLUSIONS: tuple[str, ...] = (
    "duplicate record",
    "invalid or missing profile identifier",
    "no identifiable current employment",
    "unusable or corrupt source data",
)

# Dimension-name signals that require data a raw lead export cannot supply (drives
# ``enrichment_required_fields``). Matching is by name only — no data is invented.
_ENRICHMENT_KEYWORDS: tuple[str, ...] = (
    "funding", "stage", "hiring", "headcount", "revenue", "traffic",
    "tech stack", "technology stack", "recent activity", "reachability", "engagement",
)

_WEIGHT_SUM_TARGET = 100
_WEIGHT_SUM_TOLERANCE = 5


# ---------------------------------------------------------------------------
# Structured pieces
# ---------------------------------------------------------------------------

@dataclass
class ScoringDimension:
    name: str
    weight: Optional[int] = None      # None = unknown weight

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CategoryThreshold:
    label: str
    min_score: Optional[int] = None
    max_score: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmployeeRange:
    raw: str
    low: Optional[int] = None
    high: Optional[int] = None         # None = open-ended (e.g. "5000+")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ICPProfile:
    name: str
    target_industries: list[str] = field(default_factory=list)
    target_subsegments: list[str] = field(default_factory=list)
    target_company_types: list[str] = field(default_factory=list)
    excluded_company_types: list[str] = field(default_factory=list)
    target_geographies: list[str] = field(default_factory=list)
    employee_ranges: list[EmployeeRange] = field(default_factory=list)
    target_buyer_personas: list[str] = field(default_factory=list)
    buyer_title_tiers: list[str] = field(default_factory=list)
    scoring_dimensions: list[ScoringDimension] = field(default_factory=list)
    scoring_weights: dict[str, Optional[int]] = field(default_factory=dict)
    category_thresholds: list[CategoryThreshold] = field(default_factory=list)
    hard_exclusions: list[str] = field(default_factory=list)          # ICP-specific commercial
    ambiguous_definitions: list[str] = field(default_factory=list)
    enrichment_required_fields: list[str] = field(default_factory=list)
    universal_exclusions: list[str] = field(default_factory=lambda: list(UNIVERSAL_VALIDITY_EXCLUSIONS))
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scoring_dimensions"] = [x.to_dict() for x in self.scoring_dimensions]
        d["category_thresholds"] = [x.to_dict() for x in self.category_thresholds]
        d["employee_ranges"] = [x.to_dict() for x in self.employee_ranges]
        return d


# Mandatory sections — absence produces a warning + an entry in ``unknown_fields`` (never invented).
_MANDATORY = {
    "scoring_dimensions": "no scoring dimensions detected",
    "category_thresholds": "no category thresholds detected",
    "target_buyer_personas": "no target buyer personas detected",
    "hard_exclusions": "no hard exclusions detected",
}


# ---------------------------------------------------------------------------
# Low-level parsing helpers
# ---------------------------------------------------------------------------

def _to_int(value: object) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_employee_range(text: str) -> EmployeeRange:
    """Parse '50-500', '50–500', '10001+', '200' -> EmployeeRange (low/high None on failure)."""
    raw = str(text).strip()
    t = raw.replace(",", "").replace(" ", "")
    m = re.match(r"^(\d+)[-–—](\d+)$", t)
    if m:
        return EmployeeRange(raw=raw, low=int(m.group(1)), high=int(m.group(2)))
    m = re.match(r"^(\d+)\+$", t)
    if m:
        return EmployeeRange(raw=raw, low=int(m.group(1)), high=None)
    m = re.match(r"^(\d+)$", t)
    if m:
        return EmployeeRange(raw=raw, low=int(m.group(1)), high=int(m.group(1)))
    return EmployeeRange(raw=raw, low=None, high=None)


def _is_enrichment_dimension(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in _ENRICHMENT_KEYWORDS)


# ---------------------------------------------------------------------------
# Best-effort text extraction (conservative; unknown stays unknown)
# ---------------------------------------------------------------------------

def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]


# A clean scoring-dimension name: letters/spaces/&/-/ only — no digits, parens, '+', '=' or '.'.
# This deliberately rejects prose fragments like "code) (+5). Max 20." so nothing garbage is emitted.
_DIM_NAME_RE = re.compile(r"[A-Za-z][A-Za-z &/\-]{0,44}$")
# A category label anchored at the start of a line (allowing leading bullets/whitespace).
_CAT_RE = re.compile(r"^\W*((?:A\+|A|B|C)\s*/\s*(?:hot|high|normal|low)|not\s+relevant)\b", re.I)
_RANGE_RE = re.compile(r"(\d{1,3})\s*[-–—]\s*(\d{1,3})")


def _extract_dimensions(text: str) -> list[tuple[str, int]]:
    """Extract (dimension, weight) pairs from a weighted-rubric table. Conservative: only emits a
    dimension when the name is clean and immediately followed by an integer weight 1-100."""
    lines = _lines(text)
    start: Optional[int] = None
    for i, ln in enumerate(lines):                       # prefer the real rubric marker
        if "weighted model" in ln.lower():
            start = i + 1
            break
    if start is None:                                    # fallback: a 'Dimension' + 'Wt' header pair
        for i in range(len(lines) - 1):
            if lines[i].lower() == "dimension" and lines[i + 1].lower() in ("wt", "weight"):
                start = i + 2
                break
    if start is None:
        return []

    dims: list[tuple[str, int]] = []
    i = start
    while i < len(lines) and len(dims) < 12:
        name = lines[i]
        low = name.lower()
        if low.startswith("total") or low.startswith("bonus") or "score → category" in low:
            break
        if _DIM_NAME_RE.fullmatch(name) and low not in (
                "dimension", "wt", "weight", "how to evaluate", "evaluation criteria", "criteria"):
            j = i + 1
            while j < len(lines) and lines[j] == "":
                j += 1
            if j < len(lines) and re.fullmatch(r"\d{1,3}", lines[j]) and 1 <= int(lines[j]) <= 100:
                dims.append((name, int(lines[j])))
                i = j + 1
                continue
        i += 1
    return dims


def _extract_thresholds(text: str) -> list[tuple[str, Optional[int], Optional[int]]]:
    """Extract clean category thresholds. Labels are reduced to the category token only (e.g.
    'A+ / Hot'), so prose sentences that merely mention a category are not captured."""
    lines = _lines(text)
    out: list[tuple[str, Optional[int], Optional[int]]] = []
    seen: set[str] = set()
    for idx, ln in enumerate(lines):
        m = _CAT_RE.match(ln)
        if not m:
            continue
        label = re.sub(r"\s+", " ", m.group(1)).strip()
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        lo = hi = None
        for k in range(idx, min(idx + 3, len(lines))):
            rm = _RANGE_RE.search(lines[k])
            if rm:
                lo, hi = int(rm.group(1)), int(rm.group(2))
                break
        out.append((label, lo, hi))
    return out


def _extract_employee_ranges(text: str) -> list[str]:
    found = re.findall(r"(\d{1,5}\s*[-–—]\s*\d{1,5})\s*(?:employees|emp\b|people|staff|fte)",
                       text, re.I)
    seen, out = set(), []
    for r in found:
        key = re.sub(r"\s", "", r)
        if key not in seen:
            seen.add(key)
            out.append(r.strip())
    return out


def _extract_buyer_personas(text: str) -> list[str]:
    lines = _lines(text)
    for i, ln in enumerate(lines):
        if ln.lower() == "buyer":
            j = i + 1
            while j < len(lines) and lines[j] == "":
                j += 1
            if j < len(lines):
                desc = lines[j]
                parts = re.split(r"[·+/,]|\(primary\)|\(secondary[^)]*\)|—|-", desc)
                personas = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1
                            and not p.strip().lower().startswith("hybrid")]
                return personas[:8]
    return []


def _extract_dealbreakers(text: str) -> list[str]:
    lines = _lines(text)
    # locate a dealbreakers section
    start = None
    for i, ln in enumerate(lines):
        if "dealbreaker" in ln.lower():
            start = i
            break
    if start is None:
        return []
    out: list[str] = []
    i = start
    while i < len(lines) and len(out) < 15:
        if re.fullmatch(r"\d{1,2}", lines[i]):        # a lone item number
            j = i + 1
            while j < len(lines) and lines[j] == "":
                j += 1
            if j < len(lines) and not lines[j].isdigit() and len(lines[j]) > 3:
                out.append(lines[j])
                i = j + 1
                continue
        # stop when we clearly leave the section
        if lines[i].lower().startswith("part 6") or "scoring rubric" in lines[i].lower():
            break
        i += 1
    return out


def parse_text(name: str, text: str) -> dict:
    """Best-effort, conservative extraction of a definition dict from ICP text.

    Returns a plain dict (see build_profile_from_definition). Nothing is invented; each extractor
    is defensive and simply yields nothing when it cannot find a confident match.
    """
    defn: dict = {"name": name, "notes": [], "warnings": []}
    try:
        defn["dimensions"] = [{"name": n, "weight": w} for n, w in _extract_dimensions(text)]
    except Exception as exc:  # noqa: BLE001
        defn["dimensions"] = []
        defn["warnings"].append(f"dimension extraction failed: {type(exc).__name__}")
    try:
        defn["category_thresholds"] = [{"label": lab, "min": lo, "max": hi}
                                       for lab, lo, hi in _extract_thresholds(text)]
    except Exception as exc:  # noqa: BLE001
        defn["category_thresholds"] = []
        defn["warnings"].append(f"threshold extraction failed: {type(exc).__name__}")
    try:
        defn["employee_ranges"] = _extract_employee_ranges(text)
    except Exception:  # noqa: BLE001
        defn["employee_ranges"] = []
    try:
        defn["buyer_personas"] = _extract_buyer_personas(text)
    except Exception:  # noqa: BLE001
        defn["buyer_personas"] = []
    try:
        defn["hard_exclusions"] = _extract_dealbreakers(text)
    except Exception:  # noqa: BLE001
        defn["hard_exclusions"] = []
    defn["notes"].append("Profile built by best-effort text extraction; unspecified sections are "
                         "recorded as unknown, not inferred.")
    return defn


# ---------------------------------------------------------------------------
# Normalization + validation
# ---------------------------------------------------------------------------

def _norm_list(value) -> list[str]:
    if not value:
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def build_profile_from_definition(name: str, definition: dict) -> ICPProfile:
    """Normalize a structured definition dict into a validated ICPProfile (validation appends
    warnings; nothing is silently corrected)."""
    d = definition or {}
    warnings: list[str] = list(_norm_list(d.get("warnings")))
    notes: list[str] = list(_norm_list(d.get("notes")))

    # dimensions + weights
    dims: list[ScoringDimension] = []
    for raw in (d.get("dimensions") or []):
        if isinstance(raw, dict):
            dims.append(ScoringDimension(name=str(raw.get("name", "")).strip(),
                                         weight=_to_int(raw.get("weight"))))
        else:
            dims.append(ScoringDimension(name=str(raw).strip(), weight=None))
    dims = [x for x in dims if x.name]
    weights = {x.name: x.weight for x in dims}

    # thresholds
    thresholds: list[CategoryThreshold] = []
    for raw in (d.get("category_thresholds") or []):
        if isinstance(raw, dict):
            thresholds.append(CategoryThreshold(label=str(raw.get("label", "")).strip(),
                                                min_score=_to_int(raw.get("min")),
                                                max_score=_to_int(raw.get("max"))))

    # employee ranges
    emp_ranges = [parse_employee_range(r) for r in _norm_list(d.get("employee_ranges"))]

    profile = ICPProfile(
        name=name,
        target_industries=_norm_list(d.get("target_industries")),
        target_subsegments=_norm_list(d.get("target_subsegments")),
        target_company_types=_norm_list(d.get("target_company_types")),
        excluded_company_types=_norm_list(d.get("excluded_company_types")),
        target_geographies=_norm_list(d.get("target_geographies")),
        employee_ranges=emp_ranges,
        target_buyer_personas=_norm_list(d.get("buyer_personas") or d.get("target_buyer_personas")),
        buyer_title_tiers=_norm_list(d.get("buyer_title_tiers")),
        scoring_dimensions=dims,
        scoring_weights=weights,
        category_thresholds=thresholds,
        hard_exclusions=_norm_list(d.get("hard_exclusions")),
        ambiguous_definitions=_norm_list(d.get("ambiguous_definitions")),
        notes=notes,
        warnings=warnings,
    )

    profile.enrichment_required_fields = [x.name for x in dims if _is_enrichment_dimension(x.name)]
    _validate(profile)
    return profile


def build_profile_from_text(name: str, text: str) -> ICPProfile:
    """Extract a definition from ICP text, then normalize + validate it into an ICPProfile."""
    return build_profile_from_definition(name, parse_text(name, text))


def build_profile(name: str, *, text: Optional[str] = None,
                  definition: Optional[dict] = None) -> ICPProfile:
    """Convenience entry point: pass ``definition`` (structured) or ``text`` (extracted ICP)."""
    if definition is not None:
        return build_profile_from_definition(name, definition)
    if text is not None:
        return build_profile_from_text(name, text)
    raise ValueError("build_profile requires either `definition` or `text`")


# ---------------------------------------------------------------------------
# Validation (returns warnings via profile.warnings; never mutates values silently)
# ---------------------------------------------------------------------------

def _validate(p: ICPProfile) -> None:
    w = p.warnings

    # missing mandatory sections -> warning + explicit unknown_fields
    for attr, msg in _MANDATORY.items():
        if not getattr(p, attr):
            w.append(msg)
            if attr not in p.unknown_fields:
                p.unknown_fields.append(attr)

    # other fields with no data -> recorded as unknown (not invented)
    for attr in ("target_industries", "target_subsegments", "target_company_types",
                 "excluded_company_types", "target_geographies", "employee_ranges",
                 "buyer_title_tiers", "ambiguous_definitions"):
        if not getattr(p, attr) and attr not in p.unknown_fields:
            p.unknown_fields.append(attr)

    # duplicated dimensions
    names = [x.name.lower() for x in p.scoring_dimensions]
    for dim, n in Counter(names).items():
        if n > 1:
            w.append(f"duplicated scoring dimension: '{dim}' appears {n} times")

    # invalid weights
    known = [x.weight for x in p.scoring_dimensions if x.weight is not None]
    for x in p.scoring_dimensions:
        if x.weight is None:
            w.append(f"dimension '{x.name}' has an unknown weight")
        elif x.weight < 0 or x.weight > 100:
            w.append(f"invalid weight {x.weight} for dimension '{x.name}' (expected 0-100)")
    if p.scoring_dimensions and known:
        total = sum(known)
        if abs(total - _WEIGHT_SUM_TARGET) > _WEIGHT_SUM_TOLERANCE:
            w.append(f"scoring weights sum to {total}, expected ~{_WEIGHT_SUM_TARGET}")

    # threshold validity + overlaps
    for t in p.category_thresholds:
        if t.min_score is not None and t.max_score is not None and t.min_score > t.max_score:
            w.append(f"threshold '{t.label}' has min {t.min_score} > max {t.max_score}")
    bands = sorted([t for t in p.category_thresholds
                    if t.min_score is not None and t.max_score is not None],
                   key=lambda t: t.min_score)
    for a, b in zip(bands, bands[1:]):
        if a.max_score >= b.min_score:
            w.append(f"overlapping thresholds: '{a.label}' ({a.min_score}-{a.max_score}) and "
                     f"'{b.label}' ({b.min_score}-{b.max_score})")

    # contradictory exclusions (a value both targeted and excluded)
    excluded_low = {e.lower() for e in p.excluded_company_types}
    for group in (p.target_company_types, p.target_industries, p.target_subsegments):
        for v in group:
            if v.lower() in excluded_low:
                w.append(f"contradictory exclusion: '{v}' is both targeted and excluded")

    # malformed employee ranges
    for r in p.employee_ranges:
        if r.low is None and r.high is None:
            w.append(f"malformed employee range: '{r.raw}'")
        elif r.low is not None and r.high is not None and r.low > r.high:
            w.append(f"employee range low>high: '{r.raw}'")
