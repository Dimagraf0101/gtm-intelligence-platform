"""Campaign orchestration (Sprint 5.3) — glue between scraped/uploaded leads, the scoring engine,
and the exports, so the Run Campaign page stays a thin view.

Adds no scoring logic: it normalizes rows into Leads, calls ``scoring.score_leads``, pairs results
back to their leads, and projects the qualifying subset (score >= threshold) into the existing
workbook/CSV exports. Human Review Gate preserved — callers review before anything is downloaded;
nothing is auto-sent.

Sprint 2A adds the **Generated-ICP → Engine bridge**: ``load_icp_for_scoring`` loads a library
entry for qualification. An **Approved, IQS-valid** generated ICP is adapted into a structured
``ICPProfile`` (``icp_adapter.to_engine_profile``) and scored through the engine's additive
``profile=`` entrypoint, with ``GeneratedICP.to_markdown()`` as the semantic context. Draft
generated ICPs are refused (approval is mandatory before qualification — PRD §1 / IQS §10).
PDF imports keep the backward-compatible text path unchanged (ADR-012).
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Callable, Optional

import export
import icp_adapter
import icp_library
import scoring
import storage
from icp_profile import ICPProfile
from scoring import Lead, ScoringResult

Pair = tuple[Lead, ScoringResult]


class CampaignError(ValueError):
    """Raised when a campaign cannot proceed (e.g. an unapproved generated ICP)."""


def load_icp_for_scoring(entry_id: str) -> dict:
    """Load a library ICP for qualification — the Generated-ICP → Engine bridge.

    Returns ``{"entry", "name", "text", "profile"}``:
      - generated ICP → must be Approved + IQS-valid; ``profile`` is the adapted ``ICPProfile``
        and ``text`` is the regenerated ``to_markdown()`` semantic context;
      - PDF import → ``profile`` is None and ``text`` is the stored ICP text (legacy path).

    Raises :class:`CampaignError` when a generated ICP is not usable (not Approved / IQS-invalid).
    """
    entry, text = icp_library.load_text(entry_id)
    if entry.source != icp_library.SOURCE_GENERATED:
        return {"entry": entry, "name": entry.name, "text": text, "profile": None}
    icp = icp_library.load_generated(entry_id)
    try:
        profile = icp_adapter.to_engine_profile(icp)
    except icp_adapter.AdapterError as exc:
        raise CampaignError(str(exc)) from exc
    return {"entry": entry, "name": entry.name, "text": icp.to_markdown(), "profile": profile}


def leads_from_rows(rows: list[dict]) -> list[Lead]:
    return [scoring.normalize_lead(row, i) for i, row in enumerate(rows)]


def score_rows(rows: list[dict], icp_text: str, icp_name: str, *,
               client=None, progress_cb: Optional[Callable[[int, int], None]] = None,
               stats: Optional[dict] = None,
               profile: Optional[ICPProfile] = None) -> list[Pair]:
    """Score raw lead rows against an ICP. Returns (Lead, ScoringResult) pairs, best first.

    ``profile`` (optional): a prebuilt structured ``ICPProfile`` from the Generated-ICP bridge
    (``load_icp_for_scoring``); without it the engine parses the profile from ``icp_text``."""
    leads = leads_from_rows(rows)
    results = scoring.score_leads(leads, icp_text, icp_name, client=client,
                                  progress_cb=progress_cb, stats=stats, profile=profile)
    by_index = {lead.index: lead for lead in leads}
    # score_leads returns results already sorted best-first; keep that order.
    return [(by_index[r.lead_index], r) for r in results if r.lead_index in by_index]


def _score_of(result: ScoringResult) -> int:
    v = result.score
    return int(v) if v is not None else 0


def qualified(pairs: list[Pair], threshold: int) -> list[Pair]:
    """Pairs whose lead score is at or above the threshold."""
    return [(lead, r) for (lead, r) in pairs if _score_of(r) >= threshold]


# --- exports -----------------------------------------------------------------

def workbook_bytes(pairs: list[Pair], icp_name: str) -> bytes:
    """Full 3-sheet scoring workbook (all leads, sorted, priority-coloured)."""
    return export.to_workbook_bytes(pairs, icp_name, campaign=icp_name)


def qualifying_csv_bytes(pairs: list[Pair], threshold: int) -> bytes:
    """CSV of only the leads at/above the threshold (the working sheet's columns)."""
    df = export.build_main_dataframe(qualified(pairs, threshold))
    return export.to_main_csv_bytes(df)


def all_csv_bytes(pairs: list[Pair]) -> bytes:
    return export.to_main_csv_bytes(export.build_main_dataframe(pairs))


def persist_exports(pairs: list[Pair], icp_name: str, threshold: int, *,
                    is_mock: bool = False, source: str = "") -> dict:
    """Save the campaign's exports (workbook, qualified CSV, report) to durable storage
    (local dir or OCI Object Storage). Returns {location, key, files}."""
    store = storage.get_storage()
    slug = re.sub(r"[^a-z0-9]+", "-", (icp_name or "campaign").lower()).strip("-")[:50] or "campaign"
    base = f"{storage.ARTIFACT_PREFIX}/{slug}/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    files = {
        "scored-workbook.xlsx": workbook_bytes(pairs, icp_name),
        f"qualified-{threshold}.csv": qualifying_csv_bytes(pairs, threshold),
        "scoring-report.md": summary_report(pairs, icp_name, threshold,
                                            is_mock=is_mock, source=source).encode("utf-8"),
    }
    for name, data in files.items():
        store.put_bytes(f"{base}/{name}", data)
    return {"location": store.describe(base), "key": base, "files": list(files)}


def summary_report(pairs: list[Pair], icp_name: str, threshold: int, *,
                   is_mock: bool = False, source: str = "") -> str:
    """A short, human-readable scoring report (Markdown text)."""
    results = [r for _, r in pairs]
    total = len(results)
    q = qualified(pairs, threshold)
    errors = sum(1 for r in results if r.error is not None)
    mock = sum(1 for r in results if getattr(r, "is_mock", False))
    dist = Counter(getattr(r, "operational_priority", None) or r.category for r in results)

    lines = [
        f"# Scoring Report — {icp_name}",
        f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
        + (f" · source: {source}" if source else ""),
        "",
        f"- **Leads scored:** {total}",
        f"- **Threshold:** score ≥ {threshold}",
        f"- **Qualified (≥ {threshold}):** {len(q)}",
        f"- **Below threshold:** {total - len(q)}",
        f"- **Errors:** {errors}",
    ]
    if mock:
        lines.append(f"- **Mock/offline results:** {mock} (placeholders — not production judgements)")
    lines += ["", "## Priority distribution"]
    for label in ["Priority 1", "Priority 2", "Priority 3", "Priority 4", "Priority 5",
                  "Disqualified"]:
        if dist.get(label):
            lines.append(f"- {label}: {dist[label]}")
    lines += ["", f"## Top qualified leads (≥ {threshold})"]
    if not q:
        lines.append("- _(none reached the threshold)_")
    for lead, r in q[:15]:
        f = lead.fields
        name = f"{f.get('first_name', '')} {f.get('last_name', '')}".strip() or "(unknown)"
        title = f.get("job_title", "")
        company = f.get("company", "")
        lines.append(f"- **{_score_of(r)}** · {name} — {title} @ {company}")
    if is_mock:
        lines += ["", "> ⚠️ MOCK/OFFLINE run — leads and/or scores are placeholders, "
                  "not production results."]
    return "\n".join(lines) + "\n"
