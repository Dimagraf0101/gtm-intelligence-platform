"""Qualification mapper — domain Lead → engine Lead (Sprint 11).

The ONLY responsibility of this module: convert a source-agnostic domain ``lead_batch.Lead`` into the
qualification engine's ``scoring.Lead``, by reusing the engine's own normalizer
(``scoring.normalize_lead``) — no scoring, no filtering, no ranking, and no duplicated normalization.
The frozen engine is treated as a black box: this maps *into* it.
"""
from __future__ import annotations

import scoring   # frozen engine — used only to call its public normalizer


def _split_name(full_name: str) -> tuple:
    parts = " ".join(str(full_name or "").split()).split(" ", 1)
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def to_scoring_lead(domain_lead, index: int):
    """Map one domain ``Lead`` to a ``scoring.Lead`` via the engine's own ``normalize_lead``.

    The raw dict uses the exact column aliases the engine recognizes, so no engine code changes and no
    normalization logic is duplicated. Unknown domain fields stay empty (never invented)."""
    first, last = _split_name(getattr(domain_lead, "person_name", ""))
    raw = {
        "first name": first,
        "last name": last,
        "job title": getattr(domain_lead, "current_title", ""),
        "company": getattr(domain_lead, "company_name", ""),
        "linkedin employees": getattr(domain_lead, "company_size", ""),
        "location": getattr(domain_lead, "geography", ""),
        "linkedin industry": getattr(domain_lead, "industry", ""),
        "linkedin url": getattr(domain_lead, "linkedin_url", ""),
        "company linkedin url": getattr(domain_lead, "company_url", ""),
    }
    return scoring.normalize_lead(raw, index)
