"""Vayne adapter — the anti-corruption layer for lead acquisition (Sprint 10).

The ONLY place that knows about the Vayne / Sales-Navigator-export CSV shape. It parses CSV bytes,
normalizes values, validates structure, maps each row to a source-agnostic domain ``Lead``, and
assembles an immutable ``LeadBatch`` — mirroring how ``icp_adapter`` is the sole ICP→engine boundary.

It performs **no** scoring, qualification, ranking, or business judgement. Vayne is replaceable: a
future Apollo / Clay / ZoomInfo / manual-CSV adapter would produce the same domain ``LeadBatch``
without changing the domain. Deterministic Python only — no LLM.
"""
from __future__ import annotations

import csv
import io

import lead_batch as lb

# Vayne / Sales-Navigator-export column aliases (case-insensitive). This mapping is the ACL's private
# knowledge; the domain never sees a CSV column name. First matching alias wins.
_COLUMN_ALIASES = {
    "person_name": ["full name", "name", "lead name", "person name"],
    "first_name": ["first name", "firstname"],
    "last_name": ["last name", "lastname"],
    "current_title": ["job title", "title", "current title", "position"],
    "company_name": ["company", "company name", "current company", "organization"],
    "company_size": ["linkedin employees", "linkedin company size", "company size",
                     "employee count", "headcount"],
    "geography": ["location", "geography", "country", "region", "city"],
    "linkedin_url": ["linkedin url", "linkedin_url", "profile url", "linkedin profile"],
    "company_url": ["company linkedin url", "corporate linkedin url", "company url", "website",
                    "company website"],
    "industry": ["linkedin industry", "industry", "company industry"],
}


class VayneImportError(ValueError):
    """Raised when the uploaded CSV cannot be parsed or has no recognizable lead columns."""


def _norm(s) -> str:
    return " ".join(str(s or "").split()).strip()


def _pick(row_lower: dict, field_name: str) -> str:
    for alias in _COLUMN_ALIASES.get(field_name, []):
        if alias in row_lower and _norm(row_lower[alias]):
            return _norm(row_lower[alias])
    return ""


def parse_csv(csv_bytes) -> list:
    """Parse CSV bytes into raw rows (list[dict]). Raises VayneImportError on unparseable input."""
    try:
        text = csv_bytes.decode("utf-8-sig", errors="replace") if isinstance(csv_bytes, (bytes, bytearray)) \
            else str(csv_bytes)
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:  # noqa: BLE001
        raise VayneImportError(f"Could not parse the CSV: {exc}") from exc
    return rows


def _row_to_lead(row: dict, source_kind: str, imported_at: str) -> lb.Lead:
    lower = {(_norm(k)).lower(): v for k, v in row.items()}
    person = _pick(lower, "person_name")
    if not person:
        first, last = _pick(lower, "first_name"), _pick(lower, "last_name")
        person = _norm(f"{first} {last}")
    return lb.Lead(
        lead_id=lb.bk._new_id("ld"),
        company_name=_pick(lower, "company_name"),
        person_name=person,
        current_title=_pick(lower, "current_title"),
        company_size=_pick(lower, "company_size"),
        geography=_pick(lower, "geography"),
        linkedin_url=_pick(lower, "linkedin_url"),
        company_url=_pick(lower, "company_url"),
        industry=_pick(lower, "industry"),
        source=source_kind, imported_at=imported_at)


def leads_from_csv(csv_bytes, *, source_kind: str = lb.SOURCE_VAYNE_SALESNAV) -> list:
    """Parse a Vayne CSV export and map every row to a source-agnostic domain ``Lead``.

    This is the pure ACL: it validates only CSV *structure* (recognizable lead columns, non-empty),
    and knows nothing about MarketHypothesis ownership or SearchStrategy approval — the application
    layer owns those. Raises ``VayneImportError`` on unparseable/empty/unrecognized input."""
    rows = parse_csv(csv_bytes)                    # raises VayneImportError on unparseable input
    if not rows:
        raise VayneImportError("The CSV has no data rows.")
    header_lower = {(_norm(k)).lower() for k in rows[0].keys()}
    recognized = any(alias in header_lower
                     for fld in ("company_name", "person_name", "first_name", "linkedin_url")
                     for alias in _COLUMN_ALIASES[fld])
    if not recognized:
        raise VayneImportError(
            "Unrecognized CSV structure — no company, person, or LinkedIn column found. Is this a "
            "Sales Navigator export processed by Vayne?")
    imported_at = lb.bk._now()
    return [_row_to_lead(r, source_kind, imported_at) for r in rows]


def preview_vayne_csv(csv_bytes, *, limit: int = 10) -> list:
    """Map the first ``limit`` rows to domain leads for a read-only preview (no batch, no append)."""
    rows = parse_csv(csv_bytes)[:limit]
    at = lb.bk._now()
    return [_row_to_lead(r, lb.SOURCE_VAYNE_SALESNAV, at) for r in rows]
