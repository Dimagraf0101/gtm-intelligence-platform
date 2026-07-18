"""Adapted ICP generation service (Sprint 8).

Generates an **Adapted ICP** for one Market Hypothesis: the General ICP baseline adapted to that
hypothesis's market. It is derived from **Company + Hypothesis knowledge** (the composed view) using
the existing draft generator and IQS validator verbatim — no duplicated prompts, generation, or
validation logic — and it records the **ArtifactIdentity of the source General ICP** it adapted, so
the derivation is unambiguous and survives the General ICP's later lifecycle transitions.

Constraints (frozen architecture): the General ICP is only *read* (never modified); Company Business
Knowledge is read-only; composition is a read-only deep-copied view; ``icp_adapter`` / ``iqs_validator``
/ ``icp_identity`` / fingerprints / approval / persistence are untouched. LLM proposes; Python
validates; the human reviews and approves later through the existing Strategy Review / Approval flows.
This service does no file IO and no Streamlit access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import icp_project as ip
import knowledge_gaps as kg
import icp_draft_generator as dg
import generated_icp as gi
import icp_identity as idy


@dataclass
class AdaptedICPResult:
    """Deterministic, UI-facing outcome of an Adapted ICP generation attempt."""
    ok: bool = False
    refusal_reason: str = ""
    generated_icp: Optional[gi.GeneratedICP] = None
    validation_result: object = None            # iqs_validator.ValidationResult (sole authority)
    gap_report: object = None                   # knowledge_gaps.KnowledgeGapReport
    warnings: list = field(default_factory=list)
    is_mock: bool = False
    derived_from_general_icp: str = ""          # ArtifactIdentity string of the source General ICP

    @property
    def is_valid(self) -> bool:
        return bool(self.validation_result is not None and self.validation_result.is_valid)

    def summary(self) -> dict:
        gap = self.gap_report
        return {
            "ok": self.ok,
            "refusal_reason": self.refusal_reason,
            "is_valid": self.is_valid,
            "is_mock": self.is_mock,
            "derived_from_general_icp": self.derived_from_general_icp,
            "warnings": list(self.warnings),
            "blocking_gaps": [g.field for g in getattr(gap, "blocking_gaps", [])],
            "important_gaps": [g.field for g in getattr(gap, "important_gaps", [])],
            "unknown_fields": list(getattr(self.generated_icp, "unknown_fields", []) or []),
        }


def generate_adapted_icp(workspace, hypothesis, *, client=None, icp_name: Optional[str] = None,
                         user_notes: Optional[str] = None) -> AdaptedICPResult:
    """Generate an Adapted ICP for ``hypothesis`` from the composed Company + Hypothesis knowledge,
    tagged as derived from the workspace's current General ICP.

    Refuses deterministically when no General ICP exists (the workflow is Company → General ICP →
    hypothesis → Adapted ICP). On success, appends a new immutable draft version to the hypothesis and
    returns the typed result. Never mutates the General ICP, company knowledge, or a previous draft.
    """
    source_general = workspace.latest_general_icp()
    if source_general is None:
        return AdaptedICPResult(
            ok=False,
            refusal_reason=("No General ICP exists yet. Generate the company's General ICP first — it "
                            "is the baseline every Market Hypothesis adapts."))

    composed = ip.ComposedProjectKnowledge(workspace.company, hypothesis).composed()  # read-only view
    gap_report = kg.detect_gaps(composed)
    name = icp_name or (f"{hypothesis.name} — Adapted ICP" if hypothesis.name else "Adapted ICP")
    result = dg.generate_draft_icp(composed, gap_report, icp_name=name, user_notes=user_notes,
                                   client=client)
    icp = result.generated_icp
    if icp is None:
        return AdaptedICPResult(ok=False, refusal_reason="ICP generation produced no output.",
                                gap_report=gap_report, is_mock=result.is_mock)

    # Stamp scope + provenance. The reference is the source General ICP's status-stable ArtifactIdentity
    # (built on content_fingerprint), so it stays valid if the General ICP is later approved/updated.
    derived = idy.artifact_identity_str(source_general)
    icp.metadata.icp_scope = gi.ICP_SCOPE_ADAPTED
    icp.metadata.derived_from_general_icp = derived
    icp.metadata.version = str(len(hypothesis.draft_versions) + 1)   # position in this hypothesis's lineage
    hypothesis.draft_versions.append(icp)
    hypothesis.touch()

    return AdaptedICPResult(
        ok=True, generated_icp=icp, validation_result=result.validation_result,
        gap_report=gap_report, warnings=list(result.warnings), is_mock=result.is_mock,
        derived_from_general_icp=derived)
