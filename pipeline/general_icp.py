"""General ICP generation service (Sprint 7).

Generates a **General ICP** — the company-wide, industry-agnostic capability baseline — from
**company-level BusinessKnowledge only**. It never touches Market Hypothesis knowledge, never creates
a hypothesis, and never adapts to a market. It reuses the existing draft generator and the existing
IQS validator verbatim (no duplicated prompts, generation, or validation logic), then stamps the
produced ICP as ``icp_scope = general``.

Contract (per Architecture Baseline v1.0):

* LLM proposes; Python validates; the human reviews. Offline/mock clients are fully supported.
* Insufficient company knowledge is **refused deterministically** with a clear reason — never guessed.
* Unknown information stays unknown (the underlying generator already declares unknowns/enrichment).
* This service does no file IO and no Streamlit access; it returns a typed result the UI renders.
* It produces a typed ``GeneratedICP`` (status Draft) — it never approves it and never creates an
  ApprovalRecord (a General ICP is a baseline artifact, not a qualification-bound ICP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import business_knowledge as bk
import knowledge_gaps as kg
import icp_draft_generator as dg
import generated_icp as gi

# Company knowledge is "sufficient" to seed a General ICP when at least one of these describes the
# company itself (what it is / what it sells / what it can do). Target-market fields are deliberately
# NOT required — those are hypothesis-level and are expected to be unknown for a General ICP.
_SUFFICIENCY_FIELDS = ("company_overview", "products", "services", "capabilities")


@dataclass
class GeneralICPResult:
    """Deterministic, UI-facing outcome of a General ICP generation attempt."""
    ok: bool = False
    refusal_reason: str = ""
    generated_icp: Optional[gi.GeneratedICP] = None
    validation_result: object = None            # iqs_validator.ValidationResult (sole authority)
    gap_report: object = None                   # knowledge_gaps.KnowledgeGapReport
    warnings: list = field(default_factory=list)
    is_mock: bool = False

    @property
    def is_valid(self) -> bool:
        """IQS validity of the produced ICP (separate from generation success)."""
        return bool(self.validation_result is not None and self.validation_result.is_valid)

    def summary(self) -> dict:
        gap = self.gap_report
        return {
            "ok": self.ok,
            "refusal_reason": self.refusal_reason,
            "is_valid": self.is_valid,
            "is_mock": self.is_mock,
            "warnings": list(self.warnings),
            "blocking_gaps": [g.field for g in getattr(gap, "blocking_gaps", [])],
            "important_gaps": [g.field for g in getattr(gap, "important_gaps", [])],
            "unknown_fields": list(getattr(self.generated_icp, "unknown_fields", []) or []),
        }


def sufficiency_reason(company_knowledge: bk.BusinessKnowledge) -> str:
    """Return "" when the company knowledge can seed a General ICP, else a clear refusal reason."""
    if company_knowledge is None:
        return "No company knowledge is available."
    if not any(company_knowledge.field(name) for name in _SUFFICIENCY_FIELDS):
        return ("Insufficient company knowledge: no company overview, product, service, or capability "
                "is recorded. Upload and extract company materials before generating a General ICP.")
    return ""


def generate_general_icp(company_knowledge: bk.BusinessKnowledge, *, client=None,
                         icp_name: Optional[str] = None,
                         user_notes: Optional[str] = None) -> GeneralICPResult:
    """Generate a General ICP from company-level knowledge only. Deterministic refusal on insufficient
    knowledge or a failed generation; otherwise a typed Draft ICP stamped ``icp_scope=general``."""
    reason = sufficiency_reason(company_knowledge)
    if reason:
        return GeneralICPResult(ok=False, refusal_reason=reason)

    gap_report = kg.detect_gaps(company_knowledge)          # company knowledge ONLY (no composition)
    name = icp_name or "General ICP"
    result = dg.generate_draft_icp(company_knowledge, gap_report, icp_name=name,
                                   user_notes=user_notes, client=client)

    icp = result.generated_icp
    if icp is None:
        return GeneralICPResult(ok=False, refusal_reason="ICP generation produced no output.",
                                gap_report=gap_report, is_mock=result.is_mock)

    # Stamp the produced artifact as the company-wide baseline. Fingerprint is unaffected (scope is
    # not part of the canonical form). Status stays Draft — never auto-approved.
    icp.metadata.icp_scope = gi.ICP_SCOPE_GENERAL
    return GeneralICPResult(
        ok=True, generated_icp=icp, validation_result=result.validation_result,
        gap_report=gap_report, warnings=list(result.warnings), is_mock=result.is_mock)


def generate_and_append(workspace, *, client=None, icp_name: Optional[str] = None,
                        user_notes: Optional[str] = None) -> GeneralICPResult:
    """Convenience for the UI: generate a General ICP from ``workspace.company`` and, on success,
    append it as a new immutable version to the workspace's General ICP lineage. Keeps the page thin
    while all business logic stays here / in the domain."""
    default_name = f"{workspace.name} — General ICP" if getattr(workspace, "name", "") \
        else "General ICP"
    result = generate_general_icp(workspace.company, client=client,
                                  icp_name=icp_name or default_name, user_notes=user_notes)
    if result.ok and result.generated_icp is not None:
        workspace.append_general_icp(result.generated_icp)
    return result
