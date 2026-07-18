"""Lead domain — source-agnostic Leads and immutable Lead Batches (Sprint 10).

This is the **domain** side of the Lead Acquisition boundary. It knows nothing about Vayne, CSV
column names, Sales Navigator export formats, or any external API — those live behind an adapter
(``vayne_adapter``). A ``LeadBatch`` is owned by exactly one Market Hypothesis, is immutable, and a
re-import always creates a new batch (append-only history).

No scoring, qualification, ranking, or outreach here — just typed leads, deterministic validation,
and batch statistics. Unknown values remain unknown (never invented).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import business_knowledge as bk

# --- lead source kinds (extensible; the domain only knows kinds, never how they parse) -----------

SOURCE_VAYNE_SALESNAV = "sales_navigator_export_via_vayne"
SOURCE_MANUAL_CSV = "manual_csv"
SOURCE_APOLLO = "apollo"
SOURCE_CLAY = "clay"
SOURCE_ZOOMINFO = "zoominfo"
LEAD_SOURCE_KINDS = frozenset({SOURCE_VAYNE_SALESNAV, SOURCE_MANUAL_CSV, SOURCE_APOLLO,
                               SOURCE_CLAY, SOURCE_ZOOMINFO})


class LeadDomainError(ValueError):
    """Raised for an unknown lead source kind or a structurally invalid batch."""


@dataclass(frozen=True)
class LeadSource:
    """A named lead source. Initial adapter: Sales Navigator export via Vayne CSV. The kind is an
    extensible label (Apollo / Clay / ZoomInfo / manual CSV / API); the domain never depends on how a
    given source is fetched or parsed."""
    kind: str = SOURCE_VAYNE_SALESNAV
    label: str = ""

    def __post_init__(self):
        if self.kind not in LEAD_SOURCE_KINDS:
            raise LeadDomainError(f"Unknown lead source kind {self.kind!r}.")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> "LeadSource":
        d = d or {}
        return cls(kind=d.get("kind", SOURCE_VAYNE_SALESNAV), label=d.get("label", ""))


@dataclass(frozen=True)
class Lead:
    """One person lead, source-agnostic. Empty string means unknown (never invented)."""
    lead_id: str = ""
    company_name: str = ""
    person_name: str = ""
    current_title: str = ""
    company_size: str = ""
    geography: str = ""
    linkedin_url: str = ""
    company_url: str = ""
    industry: str = ""
    source: str = ""
    imported_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Lead":
        d = d or {}
        return cls(**{k: d.get(k, "") for k in (
            "lead_id", "company_name", "person_name", "current_title", "company_size",
            "geography", "linkedin_url", "company_url", "industry", "source", "imported_at")})


@dataclass
class LeadBatch:
    """An immutable, hypothesis-owned set of leads from one import. Re-importing creates a NEW batch;
    the domain provides no mutators."""
    batch_id: str = ""
    hypothesis_id: str = ""
    source: LeadSource = field(default_factory=LeadSource)
    imported_at: str = ""
    imported_by: str = ""
    search_strategy_id: str = ""                # the source strategy's id (kept for compatibility)
    # Immutable provenance (Sprint 10.1): the source Approved Search Strategy reference
    # (search_strategy.search_strategy_reference). Required for batches persisted from Sprint 10.1 on;
    # pre-10.1 batches load with "" (backward compatible).
    derived_from_search_strategy: str = ""
    leads: list = field(default_factory=list)   # list[Lead]
    stats: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = bk._new_id("lb")
        if not self.imported_at:
            self.imported_at = bk._now()

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id, "hypothesis_id": self.hypothesis_id,
            "source": self.source.to_dict(), "imported_at": self.imported_at,
            "imported_by": self.imported_by, "search_strategy_id": self.search_strategy_id,
            "derived_from_search_strategy": self.derived_from_search_strategy,
            "leads": [ld.to_dict() for ld in self.leads], "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LeadBatch":
        return cls(
            batch_id=d.get("batch_id", ""), hypothesis_id=d.get("hypothesis_id", ""),
            source=LeadSource.from_dict(d.get("source")), imported_at=d.get("imported_at", ""),
            imported_by=d.get("imported_by", ""), search_strategy_id=d.get("search_strategy_id", ""),
            derived_from_search_strategy=d.get("derived_from_search_strategy", ""),
            leads=[Lead.from_dict(x) for x in d.get("leads", [])], stats=dict(d.get("stats", {})))


# --- deterministic validation + statistics (Python only; no LLM) -------------

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _norm(s) -> str:
    return " ".join(str(s or "").split()).strip()


def looks_like_url(value: str) -> bool:
    return bool(_URL_RE.match(_norm(value)))


def _dedup_key(lead: Lead):
    """Identity for de-duplication: a **valid** LinkedIn URL when present, else person+company. A
    malformed URL never drives de-duplication (Sprint 10.1) — it is preserved on the lead as raw
    evidence but is not treated as a canonical identifier."""
    url = _norm(lead.linkedin_url)
    if url and looks_like_url(url):
        return ("url", url.lower())
    return ("person", _norm(lead.person_name).lower(), _norm(lead.company_name).lower())


def build_lead_batch(hypothesis_id: str, leads, *, source: LeadSource, imported_by: str = "",
                     search_strategy_id: str = "",
                     derived_from_search_strategy: str = "") -> LeadBatch:
    """Assemble an immutable LeadBatch from already-mapped domain ``leads``.

    Deterministically: drops rows with no company name (cannot identify a company), de-duplicates by
    LinkedIn URL (else person+company), and computes batch statistics. Unknown values stay unknown."""
    kept, seen, dupes, skipped_no_company = [], set(), 0, 0
    for ld in leads:
        if not _norm(ld.company_name):
            skipped_no_company += 1
            continue
        key = _dedup_key(ld)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        kept.append(ld)

    stats = batch_statistics(kept)
    stats.update(input_rows=len(leads), imported=len(kept),
                 skipped_missing_company=skipped_no_company, duplicates_removed=dupes)
    return LeadBatch(hypothesis_id=hypothesis_id, source=source, imported_by=imported_by,
                     search_strategy_id=search_strategy_id,
                     derived_from_search_strategy=derived_from_search_strategy,
                     leads=kept, stats=stats)


def batch_statistics(leads) -> dict:
    """Deterministic batch stats over a domain lead list."""
    total = len(leads)
    return {
        "total_leads": total,
        "unique_companies": len({_norm(l.company_name).lower() for l in leads if _norm(l.company_name)}),
        "with_linkedin_url": sum(1 for l in leads if looks_like_url(l.linkedin_url)),
        "missing_title": sum(1 for l in leads if not _norm(l.current_title)),
        "missing_geography": sum(1 for l in leads if not _norm(l.geography)),
        "malformed_linkedin_url": sum(1 for l in leads
                                      if _norm(l.linkedin_url) and not looks_like_url(l.linkedin_url)),
        "industries": sorted({_norm(l.industry) for l in leads if _norm(l.industry)}),
    }


def batch_warnings(batch: LeadBatch) -> list:
    """Non-blocking, human-readable warnings derived from a batch's stats (no LLM)."""
    s = batch.stats
    out = []
    if s.get("skipped_missing_company"):
        out.append(f"{s['skipped_missing_company']} row(s) skipped — no company name.")
    if s.get("duplicates_removed"):
        out.append(f"{s['duplicates_removed']} duplicate row(s) removed.")
    if s.get("malformed_linkedin_url"):
        out.append(f"{s['malformed_linkedin_url']} lead(s) have a malformed LinkedIn URL.")
    if s.get("missing_title"):
        out.append(f"{s['missing_title']} lead(s) missing a title (kept as unknown).")
    if s.get("missing_geography"):
        out.append(f"{s['missing_geography']} lead(s) missing geography (kept as unknown).")
    return out
