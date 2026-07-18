"""Canonical GTM business-attribute registry (Sprint 13a).

The single, source-agnostic vocabulary of business attributes that downstream workflows (Human Review,
Google Sheets, CRM, LinkedHelper, future enrichment) consume. **Adapters normalize their
source-specific columns INTO these canonical keys** — the domain never sees a Vayne/LinkedIn/Apollo
column name. This is an immutable leaf module (no internal imports), so ``lead_batch`` and every adapter
can share it without a dependency cycle, mirroring the ``priority_policy`` pattern.

These keys are deliberately NOT the qualification/scoring inputs (those are the typed core of
``lead_batch.Lead``). They are the extra business fields needed to render the canonical deliverable.
Free-form keys are not allowed: ``normalize_attributes`` keeps only registered keys, and only when they
carry a non-empty value (unknown stays unknown — never invented).
"""
from __future__ import annotations

# Canonical business-attribute keys (bounded; extend deliberately as new workflows require).
FIRST_NAME = "first_name"
LAST_NAME = "last_name"
JOB_STARTED = "job_started"
CONNECTIONS = "connections"
COMPANY_LINKEDIN_URL = "company_linkedin_url"
EMPLOYEE_COUNT = "employee_count"
FOUNDED_YEAR = "founded_year"
SPECIALITIES = "specialities"

BUSINESS_ATTRIBUTE_KEYS = frozenset({
    FIRST_NAME, LAST_NAME, JOB_STARTED, CONNECTIONS,
    COMPANY_LINKEDIN_URL, EMPLOYEE_COUNT, FOUNDED_YEAR, SPECIALITIES,
})


def _norm(value) -> str:
    return " ".join(str(value or "").split()).strip()


def is_known(key: str) -> bool:
    """True if ``key`` is a registered canonical business-attribute key."""
    return key in BUSINESS_ATTRIBUTE_KEYS


def normalize_attributes(mapping) -> dict:
    """Return a clean dict containing ONLY registered keys with non-empty, whitespace-normalized string
    values. Unknown/free-form keys are dropped (the registry is authoritative); empty values are omitted
    (unknown stays unknown). Deterministic and idempotent, so serialization round-trips are stable."""
    if not mapping:
        return {}
    out = {}
    for key in BUSINESS_ATTRIBUTE_KEYS:
        val = _norm(mapping.get(key))
        if val:
            out[key] = val
    return out
