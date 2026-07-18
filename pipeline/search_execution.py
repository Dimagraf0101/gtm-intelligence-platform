"""Search Execution domain (Sprint 12).

A ``SearchExecution`` is one **operational** run of an Approved Search Strategy: the user configures
LinkedIn Sales Navigator manually, pastes the resulting search URL, and the platform submits it to a
lead source (Vayne) to produce a CSV that becomes a LeadBatch. It is NOT a GTM experiment
(ExperimentRun) — it is a single scraping execution.

Set-once provenance (execution_id, hypothesis_id, derived_from_search_strategy, sales_navigator_url,
requested_by/at, external_job_id, derived_lead_batch_id) is never overwritten; ``status`` advances
forward-only and each transition is recorded in an append-only ``events`` log. ``Completed`` and
``Failed`` are immutable terminal records. Deterministic Python only — no HTTP, no persistence, no UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse

import business_knowledge as bk

# --- lifecycle ---------------------------------------------------------------

EXEC_DRAFT = "Draft"
EXEC_SUBMITTED = "Submitted"
EXEC_RUNNING = "Running"
EXEC_COMPLETED = "Completed"
EXEC_FAILED = "Failed"
EXEC_STATUSES = (EXEC_DRAFT, EXEC_SUBMITTED, EXEC_RUNNING, EXEC_COMPLETED, EXEC_FAILED)
_TERMINAL = (EXEC_COMPLETED, EXEC_FAILED)

_ALLOWED = {
    EXEC_DRAFT: {EXEC_SUBMITTED, EXEC_FAILED},
    EXEC_SUBMITTED: {EXEC_RUNNING, EXEC_COMPLETED, EXEC_FAILED},
    EXEC_RUNNING: {EXEC_COMPLETED, EXEC_FAILED},
    EXEC_COMPLETED: set(),
    EXEC_FAILED: set(),
}

_MAX_URL_LEN = 4000
# Upper sanity bound on a requested lead count. Not a provider limit — just a guard against absurd
# input; the provider (and the Sales Navigator search itself) governs what is actually available.
_MAX_LEAD_LIMIT = 100_000


class SearchExecutionError(ValueError):
    """Raised for an illegal transition, mutation of a terminal record, or an invalid Sales Nav URL."""


def validate_sales_navigator_url(url: str) -> list:
    """Deterministic validation of a pasted LinkedIn Sales Navigator search URL. Returns a list of
    blocking issues ([] when acceptable). The raw URL is preserved verbatim by the caller for audit;
    this never rewrites or interprets filters (the Approved Search Strategy remains the filter
    authority — the URL is only evidence of the manually configured search)."""
    issues = []
    raw = url or ""
    if not raw.strip():
        return ["A Sales Navigator search URL is required."]
    if len(raw) > _MAX_URL_LEN:
        issues.append(f"URL is too long (> {_MAX_URL_LEN} characters).")
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return ["The URL could not be parsed."]
    if parsed.scheme.lower() != "https":
        issues.append("URL must be HTTPS.")
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        issues.append("URL host is not a LinkedIn domain.")
    if "/sales/" not in (parsed.path or "") and not (parsed.path or "").startswith("/sales"):
        issues.append("URL is not a LinkedIn Sales Navigator search path.")
    if "@" in (parsed.netloc or "") or parsed.username or parsed.password:
        issues.append("URL must not contain embedded credentials.")
    return issues


def validate_lead_limit(lead_limit) -> list:
    """Deterministic validation of a requested lead count. Returns blocking issues ([] when acceptable).

    ``None`` means "no limit" (scrape everything available) and is always acceptable — provider-agnostic
    intent. A limit, when given, must be a positive integer within a sane upper bound. Sentinel values
    (0, negatives) are NOT a way to express "unlimited"; use ``None`` for that."""
    if lead_limit is None:
        return []
    if isinstance(lead_limit, bool) or not isinstance(lead_limit, int):
        return ["Lead count must be a whole number (or leave it unlimited)."]
    if lead_limit <= 0:
        return ["Lead count must be greater than 0 (use 'all' for unlimited)."]
    if lead_limit > _MAX_LEAD_LIMIT:
        return [f"Lead count is unreasonably large (> {_MAX_LEAD_LIMIT:,})."]
    return []


@dataclass
class SearchExecution:
    execution_id: str = ""
    hypothesis_id: str = ""
    name: str = ""                              # optional human label for this run (audit / history)
    derived_from_search_strategy: str = ""      # the Approved Search Strategy reference
    source: str = ""                            # lead source kind (e.g. sales_navigator_export_via_vayne)
    sales_navigator_url: str = ""               # raw pasted URL, preserved for audit
    # Requested retrieval size (provider-independent intent): None = no limit (scrape all available);
    # a positive int = the maximum number of leads requested. Never uses 0/-1 as an "unlimited" sentinel.
    # This is the REQUESTED amount and is independent of how many were actually imported (the LeadBatch
    # stats own the imported count).
    lead_limit: Optional[int] = None
    requested_by: str = ""
    requested_at: str = ""
    status: str = EXEC_DRAFT
    external_job_id: str = ""                   # the Vayne order id
    completed_at: str = ""
    failed_at: str = ""
    failure_reason: str = ""
    derived_lead_batch_id: str = ""             # set-once: the one LeadBatch this execution produced
    events: list = field(default_factory=list)  # append-only [{status, at, note}]

    def __post_init__(self):
        if not self.execution_id:
            self.execution_id = bk._new_id("sx")
        if not self.requested_at:
            self.requested_at = bk._now()
        if not self.events:
            self.events = [{"status": self.status, "at": self.requested_at, "note": "created"}]

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def requested_label(self) -> str:
        """Human label for the REQUESTED retrieval size: 'All' when unlimited, else the number. This is
        distinct from the actually-imported count (owned by the derived LeadBatch's stats)."""
        return "All" if self.lead_limit is None else str(self.lead_limit)

    def advance(self, new_status: str, *, note: str = "", failure_reason: str = "") -> "SearchExecution":
        """Forward-only status transition with an append-only event. Terminal records are immutable."""
        if self.is_terminal:
            raise SearchExecutionError(f"Execution is terminal ({self.status}); cannot change it.")
        if new_status not in EXEC_STATUSES:
            raise SearchExecutionError(f"Unknown status {new_status!r}.")
        if new_status not in _ALLOWED[self.status]:
            raise SearchExecutionError(f"Illegal transition {self.status} -> {new_status}.")
        now = bk._now()
        self.status = new_status
        if new_status == EXEC_COMPLETED:
            self.completed_at = now
        elif new_status == EXEC_FAILED:
            self.failed_at = now
            self.failure_reason = failure_reason or note or "failed"
        self.events.append({"status": new_status, "at": now, "note": note})
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SearchExecution":
        d = d or {}
        return cls(
            execution_id=d.get("execution_id", ""), hypothesis_id=d.get("hypothesis_id", ""),
            name=d.get("name", ""),
            derived_from_search_strategy=d.get("derived_from_search_strategy", ""),
            source=d.get("source", ""), sales_navigator_url=d.get("sales_navigator_url", ""),
            lead_limit=d.get("lead_limit", None),
            requested_by=d.get("requested_by", ""), requested_at=d.get("requested_at", ""),
            status=d.get("status", EXEC_DRAFT), external_job_id=d.get("external_job_id", ""),
            completed_at=d.get("completed_at", ""), failed_at=d.get("failed_at", ""),
            failure_reason=d.get("failure_reason", ""),
            derived_lead_batch_id=d.get("derived_lead_batch_id", ""),
            events=list(d.get("events", [])))
