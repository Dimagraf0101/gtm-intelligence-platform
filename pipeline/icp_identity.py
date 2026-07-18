"""ICP identity — deterministic canonical fingerprints for a GeneratedICP (Sprint 5.7B).

A low-level module owning ONLY the pure identity of an ICP: the canonical serialization used for
content hashing, the two fingerprints, and the warning-id derivation. It has no business logic, no
UI, no LLM, no project/approval/strategy dependency — it depends downward only on ``generated_icp``
plus the standard library.

Why it exists: both ``strategy_review`` (to match a reviewed draft to its strategy) and
``icp_approval`` (for approval identity + warning acknowledgement) need the same fingerprints. Owning
them here lets both depend downward on this module and removes the previous
``strategy_review`` ⇄ ``icp_approval`` cycle.

Backward compatibility: the algorithm below is byte-for-byte identical to the original
``icp_approval`` implementation (Sprint 5.5), so every previously computed fingerprint, warning
acknowledgement id, and active-approved resolution is unchanged.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass

import generated_icp as gi


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).strip().lower()


def _canonical(draft: gi.GeneratedICP, *, include_version: bool, include_status: bool) -> dict:
    """Approval-relevant ICP content in a canonical, JSON-serializable form. Excludes volatile,
    display-only state (history, warnings, ambiguous_definitions, timestamps, source files) that must
    not, by itself, invalidate an approval fingerprint."""
    tc, tb = draft.target_companies, draft.target_buyers
    d = {
        "name": _norm(draft.metadata.name),
        "product_or_service": _norm(draft.business_context.product_or_service),
        "description": _norm(draft.business_context.description),
        "business_model": _norm(draft.business_context.business_model),
        "capabilities": [str(x) for x in draft.business_context.capabilities],
        "target_industries": [str(x) for x in tc.target_industries],
        "target_subsegments": [str(x) for x in tc.target_subsegments],
        "target_company_types": [str(x) for x in tc.target_company_types],
        "preferred_employee_ranges": [str(x) for x in tc.preferred_employee_ranges],
        "acceptable_employee_ranges": [str(x) for x in tc.acceptable_employee_ranges],
        "target_geographies": [str(x) for x in tc.target_geographies],
        "target_business_models": [str(x) for x in tc.target_business_models],
        "preferred_attributes": [str(x) for x in tc.preferred_attributes],
        "primary_buyer_roles": [str(x) for x in tb.primary_buyer_roles],
        "secondary_buyer_roles": [str(x) for x in tb.secondary_buyer_roles],
        "excluded_buyer_roles": [str(x) for x in tb.excluded_buyer_roles],
        "title_tiers": [str(x) for x in tb.title_tiers],
        "dimensions": [[_norm(x.name), x.weight, bool(x.external_enrichment_required)]
                       for x in draft.dimensions],
        "priority_thresholds": [[x.label, x.min_score, x.max_score] for x in draft.priority_thresholds],
        "hard_exclusions": [[_norm(x.rule), x.evidence_required, x.evaluation_mode, x.scope]
                            for x in draft.hard_exclusions],
        "evidence_requirements": asdict(draft.evidence_requirements),
        "unknown_fields": [str(x) for x in draft.unknown_fields],
        "enrichment_fields": [str(x) for x in draft.enrichment_fields],
    }
    if include_version:
        d["version"] = str(draft.metadata.version)
    if include_status:
        d["status"] = str(draft.metadata.status)
    return d


def _sha(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def fingerprint_generated_icp(draft: gi.GeneratedICP) -> str:
    """Stable identity of an ICP for approval: content + version + status. Deterministic across
    process restarts (hashlib, canonical JSON); never uses object identity or ``based_on_draft``."""
    return _sha(_canonical(draft, include_version=True, include_status=True))


def content_fingerprint(draft: gi.GeneratedICP) -> str:
    """Content-only fingerprint (excludes version + status). Used to match a reviewed draft to the
    strategy that produced it, independent of the stored version number or Draft/Approved status."""
    return _sha(_canonical(draft, include_version=False, include_status=False))


def warning_id(draft_fingerprint: str, message: str) -> str:
    """Deterministic id for one IQS warning, bound to the exact draft fingerprint. Because the
    fingerprint is part of the id, acknowledging a warning on one draft version never carries over to
    a changed draft (a new fingerprint yields new ids), and a changed warning message yields a new id.
    Uses hashlib — never Python's non-stable ``hash()``."""
    return hashlib.sha256(f"{draft_fingerprint}\n{_norm(message)}".encode("utf-8")).hexdigest()[:16]


# --- artifact semantic identity (Sprint 7.1; corrected in Sprint 7.2) ---------
#
# The fingerprints above are INTENTIONALLY blind to whether an ICP is the company-wide General ICP or
# a hypothesis-scoped Adapted ICP. But those are semantically different artifacts and must not share
# one *identity*. Sprint 7.1 combined the artifact type with ``fingerprint_generated_icp``, which
# includes lifecycle **status** — so the identity changed when the same version transitioned Draft →
# Approved, making it unusable as a durable lineage reference.
#
# Corrected artifact identity = "<artifact_type>:<version>:<content_fingerprint>":
#   * ``content_fingerprint`` already excludes version AND status (semantic content only);
#   * ``version`` is stamped once when an artifact is appended and is unchanged by approval;
#   * so Draft and Approved forms of the same typed version collapse to ONE identity, while different
#     types, versions, or content stay distinct. It reuses ``content_fingerprint`` — no new hashing.
# This module is the sole authority for identity; nobody else assembles the composite.

ARTIFACT_GENERAL_ICP = "general_icp"
ARTIFACT_ADAPTED_ICP = "adapted_icp"
ARTIFACT_TYPES = frozenset({ARTIFACT_GENERAL_ICP, ARTIFACT_ADAPTED_ICP})

# The typed scope on GeneratedICP.metadata is the source of the artifact type; this map validates it.
_SCOPE_TO_ARTIFACT = {
    gi.ICP_SCOPE_GENERAL: ARTIFACT_GENERAL_ICP,
    gi.ICP_SCOPE_ADAPTED: ARTIFACT_ADAPTED_ICP,
}


class ArtifactIdentityError(ValueError):
    """Raised for an unknown ICP scope / artifact type, a malformed version, or a malformed
    serialized artifact identity string."""


def _validate_version(version) -> str:
    """A version component must be a non-empty token with no delimiter/whitespace (so the three-part
    identity string stays unambiguous). Not random, not a UUID — the artifact's stamped version."""
    v = "" if version is None else str(version).strip()
    if not v or ":" in v or any(ch.isspace() for ch in v):
        raise ArtifactIdentityError(f"Malformed artifact version {version!r}.")
    return v


@dataclass(frozen=True)
class ArtifactIdentity:
    """Deterministic, status-stable identity of one typed ICP artifact version:
    ``artifact_type`` + ``version`` + ``content_fingerprint``. Immutable and value-equal; its string
    form is ``<artifact_type>:<version>:<content_fingerprint>``."""
    artifact_type: str
    version: str
    content_fingerprint: str

    def __str__(self) -> str:
        return f"{self.artifact_type}:{self.version}:{self.content_fingerprint}"

    def to_str(self) -> str:
        return str(self)


def artifact_type_for_scope(scope: str) -> str:
    """Validate a metadata ``icp_scope`` and return its artifact type. Unknown scope is rejected."""
    artifact_type = _SCOPE_TO_ARTIFACT.get(scope)
    if artifact_type is None:
        raise ArtifactIdentityError(
            f"Unknown ICP scope {scope!r}; expected one of {sorted(_SCOPE_TO_ARTIFACT)}.")
    return artifact_type


def artifact_type_of(icp: gi.GeneratedICP) -> str:
    """The validated artifact type of an ICP (from its ``metadata.icp_scope``). A missing scope
    defaults to 'adapted' for backward compatibility with pre-Sprint-7 objects; an *unknown* scope is
    rejected explicitly."""
    scope = getattr(icp.metadata, "icp_scope", gi.ICP_SCOPE_ADAPTED) or gi.ICP_SCOPE_ADAPTED
    return artifact_type_for_scope(scope)


def artifact_identity(icp: gi.GeneratedICP) -> ArtifactIdentity:
    """The deterministic, status-stable typed identity of an ICP: artifact type + version +
    content fingerprint. Reuses ``content_fingerprint`` — never a second hashing implementation."""
    return ArtifactIdentity(artifact_type_of(icp),
                            _validate_version(getattr(icp.metadata, "version", "")),
                            content_fingerprint(icp))


def artifact_identity_str(icp: gi.GeneratedICP) -> str:
    return str(artifact_identity(icp))


def parse_artifact_identity(text: str) -> ArtifactIdentity:
    """Parse ``<artifact_type>:<version>:<content_fingerprint>``; reject malformed strings, unknown
    artifact types, and malformed versions."""
    if not isinstance(text, str):
        raise ArtifactIdentityError(f"Malformed artifact identity {text!r}.")
    parts = text.split(":")
    if len(parts) != 3:
        raise ArtifactIdentityError(f"Malformed artifact identity {text!r}.")
    artifact_type, version, fingerprint = parts
    if artifact_type not in ARTIFACT_TYPES:
        raise ArtifactIdentityError(
            f"Unknown artifact type {artifact_type!r}; expected one of {sorted(ARTIFACT_TYPES)}.")
    version = _validate_version(version)
    if not fingerprint:
        raise ArtifactIdentityError("Artifact identity is missing its content fingerprint.")
    return ArtifactIdentity(artifact_type, version, fingerprint)


def require_artifact_type(icp: gi.GeneratedICP, expected_type: str) -> str:
    """Validate that an ICP is of the expected artifact type; raise otherwise. Used by lineage guards
    so the type authority stays in this module (never a raw string compare in the domain/UI)."""
    if expected_type not in ARTIFACT_TYPES:
        raise ArtifactIdentityError(f"Unknown expected artifact type {expected_type!r}.")
    actual = artifact_type_of(icp)
    if actual != expected_type:
        raise ArtifactIdentityError(
            f"Expected artifact type {expected_type!r} but got {actual!r}.")
    return actual
