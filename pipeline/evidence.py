"""Evidence Engine (Evidence Layer) — Release 0.3, Sprint 3.1.

Turns normalized lead data into structured, status-tagged, **sourced** evidence, and computes
**Data Coverage**. This module answers only: *do we know a fact, from where, and how certainly?*
It makes **no** judgment about fit — that belongs to the Qualification/Decision layers.

Design rules (from docs/ARCHITECTURE.md §6, docs/DECISIONS.md ADR-002/003/004, and the
implementation plan §13 guardrails):

* **Missing data stays `unknown`** — never a negative signal, never a dealbreaker.
* **Current vs previous employment are separate.** Facts about the *current company* are only
  ever derived from current-employment data; a previous role never becomes evidence about the
  current company.
* Conflicting sources are marked `conflicting` (which later lowers confidence and flags review),
  not silently resolved and never used to auto-reject.
* Every item carries a **source** and **provenance** so the decision is auditable.

Pure Python, no I/O, no API. Consumes the normalized field keys produced by
``pipeline.scoring.normalize_lead`` so it can be wired in during a later checkpoint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from typing import Optional

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

# Evidence status — the honest state of a single fact.
CONFIRMED = "confirmed"            # present in the data with an identifiable source
CONFLICTING = "conflicting"        # sources disagree
UNKNOWN = "unknown"                # not present; NEVER negative, NEVER confirms a dealbreaker
NOT_APPLICABLE = "not_applicable"  # the attribute does not apply to this lead
EVIDENCE_STATUSES = frozenset({CONFIRMED, CONFLICTING, UNKNOWN, NOT_APPLICABLE})

# Which employer a fact describes.
SCOPE_CURRENT = "current"
SCOPE_PREVIOUS = "previous"
SCOPE_NONE = "none"                # person-level fact (not tied to an employer)
EMPLOYMENT_SCOPES = frozenset({SCOPE_CURRENT, SCOPE_PREVIOUS, SCOPE_NONE})

# Confidence in the *evidence* (directness of the source), not in the overall lead verdict.
HIGH, MEDIUM, LOW = "high", "medium", "low"

_MAX_PROVENANCE = 160


# ---------------------------------------------------------------------------
# Evidence item
# ---------------------------------------------------------------------------

@dataclass
class EvidenceItem:
    """One status-tagged, sourced claim about a lead."""
    attribute: str
    observed_value: Optional[str]
    source_field: Optional[str]
    status: str                       # one of EVIDENCE_STATUSES
    confidence: str                   # high | medium | low
    explanation: str
    provenance: str
    employment_scope: str = SCOPE_NONE  # one of EMPLOYMENT_SCOPES

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DataCoverage:
    """Coverage of the rubric-required attributes by confirmed evidence."""
    required: int
    confirmed: int
    conflicting: int
    unknown: int
    coverage_pct: int                 # confirmed / required, rounded to an int percent
    unknown_attributes: list[str] = field(default_factory=list)
    conflicting_attributes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Canonical attribute names (what evidence is *about*)
# ---------------------------------------------------------------------------

ATTR_JOB_TITLE = "current_job_title"
ATTR_JOB_DESCRIPTION = "current_job_description"
ATTR_COMPANY = "current_company"
ATTR_INDUSTRY = "company_industry"
ATTR_COMPANY_SIZE = "company_size"
ATTR_LOCATION = "location"
ATTR_SPECIALITIES = "company_specialities"
ATTR_FOUNDED_YEAR = "company_founded_year"
ATTR_COMPANY_DESCRIPTION = "company_description"
ATTR_COMPANY_WEBSITE = "company_website"
ATTR_CONNECTIONS = "number_of_connections"
ATTR_SUMMARY = "profile_summary"
ATTR_HEADLINE = "profile_headline"
ATTR_SKILLS = "profile_skills"
ATTR_LINKEDIN_URL = "linkedin_url"
ATTR_PREVIOUS_ROLE = "previous_role"

# Map: canonical attribute -> (normalized field key from scoring.normalize_lead, scope, confidence,
# short human label). Only current-employment / person-level facts here; previous roles handled
# separately so they can never masquerade as current-company evidence.
_DIRECT_FIELDS: list[tuple[str, str, str, str, str]] = [
    # attribute,            field key,            scope,          confidence, label
    (ATTR_JOB_TITLE,        "job_title",          SCOPE_CURRENT,  HIGH,   "current job title"),
    (ATTR_JOB_DESCRIPTION,  "job_description",     SCOPE_CURRENT,  MEDIUM, "current role description"),
    (ATTR_COMPANY,          "company",            SCOPE_CURRENT,  HIGH,   "current company"),
    (ATTR_INDUSTRY,         "industry",           SCOPE_CURRENT,  HIGH,   "company industry"),
    (ATTR_LOCATION,         "location",           SCOPE_NONE,     HIGH,   "location"),
    (ATTR_SPECIALITIES,     "specialities",       SCOPE_CURRENT,  MEDIUM, "company specialities"),
    (ATTR_FOUNDED_YEAR,     "founded_year",       SCOPE_CURRENT,  HIGH,   "company founded year"),
    (ATTR_COMPANY_DESCRIPTION, "company_description", SCOPE_CURRENT, MEDIUM, "company description"),
    (ATTR_COMPANY_WEBSITE,  "company_website",    SCOPE_CURRENT,  HIGH,   "company website"),
    (ATTR_CONNECTIONS,      "connections",        SCOPE_NONE,     HIGH,   "number of connections"),
    (ATTR_SUMMARY,          "summary",            SCOPE_NONE,     MEDIUM, "profile summary"),
    (ATTR_HEADLINE,         "headline",           SCOPE_NONE,     MEDIUM, "profile headline"),
    (ATTR_SKILLS,           "skills",             SCOPE_NONE,     MEDIUM, "profile skills"),
    (ATTR_LINKEDIN_URL,     "linkedin_url",       SCOPE_NONE,     HIGH,   "LinkedIn URL"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _provenance(source_field: str, value: str) -> str:
    snippet = value if len(value) <= _MAX_PROVENANCE else value[:_MAX_PROVENANCE].rsplit(" ", 1)[0] + "…"
    return f"{source_field} = \"{snippet}\""


def _parse_size_range(text: str) -> Optional[tuple[int, Optional[int]]]:
    """Parse an employee range like '51-200', '51–200', '10001+', '1-10' -> (lo, hi|None)."""
    t = text.replace(",", "").replace(" ", "")
    m = re.match(r"^(\d+)\s*[-–—]\s*(\d+)$", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+)\+$", t)
    if m:
        return int(m.group(1)), None
    m = re.match(r"^(\d+)$", t)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _company_size_item(fields: dict) -> Optional[EvidenceItem]:
    """Build a single company_size evidence item, flagging range/count conflicts."""
    size_range = _clean(fields.get("company_size_range"))
    count_raw = _clean(fields.get("employee_count"))
    count = None
    if count_raw:
        digits = re.sub(r"[^\d]", "", count_raw)
        count = int(digits) if digits else None

    if not size_range and count is None:
        return None

    if size_range and count is not None:
        parsed = _parse_size_range(size_range)
        if parsed:
            lo, hi = parsed
            within = count >= lo and (hi is None or count <= hi)
            if not within:
                return EvidenceItem(
                    attribute=ATTR_COMPANY_SIZE,
                    observed_value=f"range {size_range} vs count {count}",
                    source_field="linkedin employees / employee count",
                    status=CONFLICTING, confidence=HIGH,
                    explanation="Employee-count field disagrees with the size range; "
                                "lower confidence and flag for review — do not auto-reject.",
                    provenance=f'linkedin employees = "{size_range}"; employee count = "{count}"',
                    employment_scope=SCOPE_CURRENT,
                )
        # consistent (or unparseable range) -> confirmed on the range
        return EvidenceItem(
            attribute=ATTR_COMPANY_SIZE, observed_value=size_range,
            source_field="linkedin employees", status=CONFIRMED, confidence=HIGH,
            explanation=f"Company size {size_range} (employee count {count} consistent).",
            provenance=_provenance("linkedin employees", size_range), employment_scope=SCOPE_CURRENT,
        )

    value = size_range or str(count)
    src = "linkedin employees" if size_range else "employee count"
    return EvidenceItem(
        attribute=ATTR_COMPANY_SIZE, observed_value=value, source_field=src,
        status=CONFIRMED, confidence=HIGH, explanation=f"Company size {value}.",
        provenance=_provenance(src, value), employment_scope=SCOPE_CURRENT,
    )


def extract_evidence(fields: dict, previous_roles: Optional[list[dict]] = None) -> list[EvidenceItem]:
    """Build the confirmed/conflicting evidence items present in the data.

    ``fields`` uses the normalized keys from ``scoring.normalize_lead``. ``previous_roles`` is an
    optional list of dicts ``{title, company, industry, started, ended}`` — each becomes a
    PREVIOUS-scope item and is **never** used to populate current-company attributes.
    """
    items: list[EvidenceItem] = []

    for attribute, key, scope, conf, label in _DIRECT_FIELDS:
        value = _clean(fields.get(key))
        if not value:
            continue
        items.append(EvidenceItem(
            attribute=attribute, observed_value=value, source_field=key,
            status=CONFIRMED, confidence=conf, explanation=f"Observed {label}.",
            provenance=_provenance(key, value), employment_scope=scope,
        ))

    size_item = _company_size_item(fields)
    if size_item is not None:
        items.append(size_item)

    for role in (previous_roles or []):
        title = _clean(role.get("title"))
        company = _clean(role.get("company"))
        if not title and not company:
            continue
        industry = _clean(role.get("industry"))
        dates = " ".join(x for x in (_clean(role.get("started")), _clean(role.get("ended"))) if x)
        value = " / ".join(x for x in (title, company, industry) if x)
        items.append(EvidenceItem(
            attribute=ATTR_PREVIOUS_ROLE, observed_value=value, source_field="previous employment",
            status=CONFIRMED, confidence=HIGH,
            explanation="Previous employment (historical context only; not evidence about the "
                        "current company).",
            provenance=_provenance("previous employment", (value + (f" [{dates}]" if dates else ""))),
            employment_scope=SCOPE_PREVIOUS,
        ))

    return items


# ---------------------------------------------------------------------------
# Coverage & unknowns
# ---------------------------------------------------------------------------

def enumerate_unknowns(evidence: list[EvidenceItem], required_attributes: list[str]) -> list[EvidenceItem]:
    """Return an UNKNOWN evidence item for every required attribute lacking current/person-level
    evidence. Previous-employment items never satisfy a required (current-company) attribute."""
    have = {
        it.attribute for it in evidence
        if it.employment_scope in (SCOPE_CURRENT, SCOPE_NONE) and it.status in (CONFIRMED, CONFLICTING)
    }
    unknowns: list[EvidenceItem] = []
    for attr in required_attributes:
        if attr not in have:
            unknowns.append(EvidenceItem(
                attribute=attr, observed_value=None, source_field=None,
                status=UNKNOWN, confidence=LOW,
                explanation="Not present in the source data — unknown, not a negative signal.",
                provenance="no source field", employment_scope=SCOPE_NONE,
            ))
    return unknowns


def compute_coverage(evidence: list[EvidenceItem], required_attributes: list[str]) -> DataCoverage:
    """Compute Data Coverage over the rubric-required attributes.

    Coverage counts an attribute as confirmed only from CURRENT/person-level evidence with status
    ``confirmed``. ``conflicting`` counts separately (present but disputed); anything else is
    unknown. Previous-employment evidence is ignored for coverage of current-company attributes.
    """
    by_attr: dict[str, set[str]] = {}
    for it in evidence:
        if it.employment_scope == SCOPE_PREVIOUS:
            continue
        by_attr.setdefault(it.attribute, set()).add(it.status)

    confirmed = conflicting = unknown = 0
    unknown_attrs: list[str] = []
    conflicting_attrs: list[str] = []
    for attr in required_attributes:
        statuses = by_attr.get(attr, set())
        if CONFIRMED in statuses:
            confirmed += 1
        elif CONFLICTING in statuses:
            conflicting += 1
            conflicting_attrs.append(attr)
        else:
            unknown += 1
            unknown_attrs.append(attr)

    total = len(required_attributes)
    pct = round(confirmed / total * 100) if total else 0
    return DataCoverage(
        required=total, confirmed=confirmed, conflicting=conflicting, unknown=unknown,
        coverage_pct=pct, unknown_attributes=unknown_attrs, conflicting_attributes=conflicting_attrs,
    )
