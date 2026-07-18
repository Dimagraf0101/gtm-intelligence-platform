"""Strategy Review — a small, audited decision overlay on a Draft ICP (Sprint 5.4).

Knowledge stays the source of truth for business facts. ``GeneratedICP`` stays the only ICP authoring
schema. ``ICPProfile`` stays the derived engine contract produced solely by ``icp_adapter``. This
module adds **only the explicit human decisions** about a freshly generated draft — chosen dimension
weights, which proposed dimensions to keep, and which of the draft's exclusion *candidates* to
activate — and applies them onto a **new** ``GeneratedICP``:

    reviewed_draft = apply_strategy_decisions(strategy_decisions, freshly_generated_draft)

It never mutates a previous draft, never re-declares an ICP schema (dimensions, bands, exclusions and
evidence requirements are the existing ``generated_icp`` dataclasses), and never validates the ICP
itself — ``iqs_validator`` remains the sole ICP authority. Strategy Review adds only *decision-layer*
checks (stale references, explicit-decision completeness, activation evidence, version match).

Scope guardrails (Sprint 5.4): decisions may only **activate or decline exclusion candidates already
present in the generated draft** — no preference→exclusion promotion, no arbitrary new rejection
rules (that would be knowledge authoring). Weights are never auto-normalized and thresholds are never
auto-corrected; IQS reports those.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

import generated_icp as gi
import icp_project as ip
import knowledge_gaps as kg
import icp_draft_generator as dg
import icp_identity as idy       # low-level identity (Sprint 5.7B): no cycle back into approval


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).strip().lower()


# --- audited decision structures (reuse gi types; declare no ICP schema) -----

@dataclass
class DimensionDecision:
    """An explicit human decision about one proposed qualification dimension (keyed by its name)."""
    name: str = ""
    weight: Optional[int] = None          # the chosen weight; None until explicitly reviewed
    included: bool = True                 # False = explicitly declined (dropped from the ICP)
    purpose: str = ""                     # "" keeps the draft's proposed purpose
    scoring_guidance: str = ""            # "" keeps the draft's proposed guidance
    external_enrichment_required: Optional[bool] = None   # None keeps the draft's value
    decided_at: str = ""
    decided_by: str = "user"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExclusionDecision:
    """An explicit human decision about one exclusion CANDIDATE already in the draft (keyed by rule).

    Sprint 5.4 may only activate or decline; it never invents a rule or promotes a preference."""
    rule: str = ""
    activated: bool = False               # explicit: default off, never silently active
    evaluation_mode: str = ""             # "" keeps the candidate's mode
    scope: str = ""                       # "" keeps the candidate's scope
    evidence_required: str = ""           # "" keeps the candidate's evidence
    reason: str = ""
    decided_at: str = ""
    decided_by: str = "user"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyDecisions:
    project_id: str = ""
    revision: int = 0
    based_on_version: str = ""
    dimensions: dict = field(default_factory=dict)     # normalized name -> DimensionDecision
    exclusions: dict = field(default_factory=dict)     # normalized rule -> ExclusionDecision
    priority_bands_override: Optional[list] = None      # list[gi.PriorityBand] or None
    evidence_requirements_override: Optional[gi.EvidenceRequirements] = None
    notes: list = field(default_factory=list)
    updated_at: str = ""
    decided_by: str = "user"
    # Operational snapshot of the proposed draft these decisions were authored against. Not a new
    # schema — a reference to the existing GeneratedICP — kept so completeness and stale detection
    # have a stable, deterministic target without regenerating via the AI client.
    based_on_draft: Optional[gi.GeneratedICP] = None
    # Stable content fingerprint of the most recent reviewed draft these decisions produced, plus its
    # stored version. Approval (Sprint 5.5) matches a draft to its strategy via this fingerprint —
    # never via object identity or based_on_draft.
    reviewed_fingerprint: str = ""
    reviewed_version: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # keep the audit compact and free of the full draft snapshot
        d.pop("based_on_draft", None)
        d["dimensions"] = {k: v.to_dict() for k, v in self.dimensions.items()}
        d["exclusions"] = {k: v.to_dict() for k, v in self.exclusions.items()}
        d["priority_bands_override"] = ([asdict(b) for b in self.priority_bands_override]
                                        if self.priority_bands_override is not None else None)
        d["evidence_requirements_override"] = (asdict(self.evidence_requirements_override)
                                               if self.evidence_requirements_override is not None
                                               else None)
        return d

    def to_persist_dict(self) -> dict:
        """Full, lossless serialization for persistence (Sprint 6). Unlike ``to_dict`` (which drops
        the ``based_on_draft`` snapshot to keep the audit compact), this includes it so a reloaded
        Strategy Review can still validate completeness / staleness and re-approve."""
        d = self.to_dict()
        d["based_on_draft"] = (self.based_on_draft.to_dict()
                               if self.based_on_draft is not None else None)
        return d

    @classmethod
    def from_persist_dict(cls, d: dict) -> "StrategyDecisions":
        base = d.get("based_on_draft")
        bands = d.get("priority_bands_override")
        ev = d.get("evidence_requirements_override")
        return cls(
            project_id=d.get("project_id", ""),
            revision=d.get("revision", 0),
            based_on_version=d.get("based_on_version", ""),
            dimensions={k: DimensionDecision(**v) for k, v in d.get("dimensions", {}).items()},
            exclusions={k: ExclusionDecision(**v) for k, v in d.get("exclusions", {}).items()},
            priority_bands_override=([gi.PriorityBand(**b) for b in bands]
                                     if bands is not None else None),
            evidence_requirements_override=(gi.EvidenceRequirements(**ev) if ev is not None else None),
            notes=list(d.get("notes", [])),
            updated_at=d.get("updated_at", ""),
            decided_by=d.get("decided_by", "user"),
            based_on_draft=(gi.GeneratedICP.from_dict(base) if base is not None else None),
            reviewed_fingerprint=d.get("reviewed_fingerprint", ""),
            reviewed_version=d.get("reviewed_version", ""),
        )


# --- apply -------------------------------------------------------------------

def apply_strategy_decisions(decisions: Optional[StrategyDecisions],
                             draft: gi.GeneratedICP) -> gi.GeneratedICP:
    """Return a NEW ``GeneratedICP`` = draft with the human strategy decisions applied.

    The input draft is never mutated (deep-copied). With ``decisions is None`` the copy is returned
    unchanged, preserving legacy draft behavior. With decisions present: reviewed dimensions get the
    chosen weight and ``weight_is_default=False``; declined dimensions are dropped; only explicitly
    activated exclusion candidates are kept; band/evidence overrides apply only when not None. Weights
    are never normalized and thresholds never corrected. Status stays Draft; a HistoryEntry is added.
    """
    new = copy.deepcopy(draft)
    if decisions is None:
        return new

    kept_dims = []
    for dim in new.dimensions:
        dec = decisions.dimensions.get(_norm(dim.name))
        if dec is None:
            kept_dims.append(dim)                       # undecided -> keep as proposed (still default)
            continue
        if not dec.included:
            continue                                    # explicitly declined -> dropped
        if dec.weight is not None:
            dim.weight = dec.weight
            dim.weight_is_default = False               # explicitly reviewed by a human
        if dec.purpose:
            dim.purpose = dec.purpose
        if dec.scoring_guidance:
            dim.scoring_guidance = dec.scoring_guidance
        if dec.external_enrichment_required is not None:
            dim.external_enrichment_required = dec.external_enrichment_required
        kept_dims.append(dim)
    new.dimensions = kept_dims

    kept_excl = []
    for ex in new.hard_exclusions:
        dec = decisions.exclusions.get(_norm(ex.rule))
        if dec is None or not dec.activated:
            continue                                    # undecided or declined -> omitted
        if dec.evaluation_mode:
            ex.evaluation_mode = dec.evaluation_mode
        if dec.scope:
            ex.scope = dec.scope
        if dec.evidence_required:
            ex.evidence_required = dec.evidence_required
        if dec.reason:
            ex.reason = dec.reason
        kept_excl.append(ex)
    new.hard_exclusions = kept_excl

    if decisions.priority_bands_override is not None:
        new.priority_thresholds = copy.deepcopy(decisions.priority_bands_override)
    if decisions.evidence_requirements_override is not None:
        new.evidence_requirements = copy.deepcopy(decisions.evidence_requirements_override)

    new.metadata.status = gi.STATUS_DRAFT               # Strategy Review never approves
    new.history = list(new.history) + [gi.HistoryEntry(
        version=new.metadata.version, date=date.today().isoformat(), author="strategy-review",
        change_summary=(f"Strategy Review revision {decisions.revision}: "
                        f"{len(kept_dims)} dimension(s), {len(kept_excl)} active exclusion(s)."))]
    return new


# --- decision-layer validation (NOT ICP validation — IQS owns that) ----------

def stale_decisions(decisions: StrategyDecisions, draft: gi.GeneratedICP) -> dict:
    """Decisions whose referenced dimension / exclusion candidate no longer exists in ``draft``.
    Kept for audit; never silently dropped."""
    dim_names = {_norm(d.name) for d in draft.dimensions}
    ex_rules = {_norm(e.rule) for e in draft.hard_exclusions}
    return {
        "dimensions": [dec.name for k, dec in decisions.dimensions.items() if k not in dim_names],
        "exclusions": [dec.rule for k, dec in decisions.exclusions.items() if k not in ex_rules],
    }


def validate_decisions(decisions: StrategyDecisions, draft: gi.GeneratedICP) -> list:
    """Blocking *decision-layer* issues only. IQS separately validates the ICP itself."""
    issues: list[str] = []
    stale = stale_decisions(decisions, draft)
    for n in stale["dimensions"]:
        issues.append(f"Stale dimension decision '{n}': no longer proposed in the draft.")
    for r in stale["exclusions"]:
        issues.append(f"Stale exclusion decision '{r}': no longer a candidate in the draft.")
    for ex in draft.hard_exclusions:
        dec = decisions.exclusions.get(_norm(ex.rule))
        if dec is not None and dec.activated:
            evidence = dec.evidence_required or ex.evidence_required
            if not _norm(evidence):
                issues.append(f"Activated exclusion '{ex.rule}' has no evidence_required.")
    if decisions.based_on_version and decisions.based_on_version != draft.metadata.version:
        issues.append(f"Decisions were authored against version '{decisions.based_on_version}' "
                      f"but the draft is version '{draft.metadata.version}'.")
    return issues


def is_review_complete(decisions: Optional[StrategyDecisions],
                       draft: Optional[gi.GeneratedICP]) -> bool:
    """Strategy Review is complete only when every included dimension has an explicit reviewed
    weight, every exclusion candidate is explicitly activated or declined, priority bands are present
    (draft or override), there are no unresolved stale decisions, and decision-layer validation is
    clean. This is SEPARATE from IQS validity: a review may be complete but still fail IQS."""
    if decisions is None or draft is None:
        return False
    for d in draft.dimensions:
        dec = decisions.dimensions.get(_norm(d.name))
        if dec is None:
            return False                                # every dimension must be decided
        if dec.included and dec.weight is None:
            return False                                # included -> must have a reviewed weight
    for e in draft.hard_exclusions:
        if _norm(e.rule) not in decisions.exclusions:
            return False                                # every candidate must be decided
    if not (draft.priority_thresholds or decisions.priority_bands_override):
        return False
    st = stale_decisions(decisions, draft)
    if st["dimensions"] or st["exclusions"]:
        return False
    return not validate_decisions(decisions, draft)


# --- thin workspace (all logic here; the Streamlit page stays a view) --------

class StrategyReviewWorkspace:
    """Review the freshly proposed Draft ICP for one ICP Project and produce a reviewed version."""

    def __init__(self, company, project: ip.ICPProject, *, draft_client=None):
        self.company = company
        self.project = project
        self.draft_client = draft_client
        self._base: Optional[gi.GeneratedICP] = None

    # --- base (proposed) draft ----------------------------------------------

    def _generate_base_draft(self) -> gi.GeneratedICP:
        composed = ip.ComposedProjectKnowledge(self.company, self.project).composed()
        report = kg.detect_gaps(composed)
        client = self.draft_client
        if client is None:
            client, _ = dg.get_draft_client()
        result = dg.generate_draft_icp(composed, report, icp_name=self.project.name, client=client)
        return result.generated_icp

    def base_draft(self) -> gi.GeneratedICP:
        """The pristine, freshly generated proposed draft (AI weights, all exclusion candidates)."""
        if self._base is None:
            self._base = self._generate_base_draft()
        return self._base

    def start_review(self) -> StrategyDecisions:
        """Begin (or resume) Strategy Review for this project against a fresh proposed draft."""
        base = self.base_draft()
        if self.project.strategy is None:
            self.project.strategy = StrategyDecisions(
                project_id=self.project.project_id, based_on_version=base.metadata.version,
                based_on_draft=copy.deepcopy(base), updated_at=ip.bk._now())
            self.project.touch()
        return self.project.strategy

    def regenerate_base(self) -> gi.GeneratedICP:
        """Regenerate the proposed draft from current knowledge and rebase decisions onto it,
        preserving compatible decisions and surfacing any that became stale."""
        self._base = self._generate_base_draft()
        if self.project.strategy is not None:
            self.rebase(self._base)
        return self._base

    def rebase(self, new_base: gi.GeneratedICP) -> None:
        d = self._decisions()
        d.based_on_draft = copy.deepcopy(new_base)
        d.based_on_version = new_base.metadata.version
        d.revision += 1
        d.updated_at = ip.bk._now()
        self.project.touch()

    # --- decision access -----------------------------------------------------

    def _decisions(self) -> StrategyDecisions:
        if self.project.strategy is None:
            self.start_review()
        return self.project.strategy

    def _touch(self) -> None:
        d = self._decisions()
        d.revision += 1
        d.updated_at = ip.bk._now()
        self.project.touch()

    def _dim_decision(self, name: str) -> DimensionDecision:
        d = self._decisions()
        key = _norm(name)
        dec = d.dimensions.get(key)
        if dec is None:
            dec = DimensionDecision(name=name, weight=None, decided_at=ip.bk._now())
            d.dimensions[key] = dec
        return dec

    def _excl_decision(self, rule: str) -> ExclusionDecision:
        d = self._decisions()
        key = _norm(rule)
        dec = d.exclusions.get(key)
        if dec is None:
            dec = ExclusionDecision(rule=rule, decided_at=ip.bk._now())
            d.exclusions[key] = dec
        return dec

    # --- operations ----------------------------------------------------------

    def set_weight(self, name: str, weight: int) -> None:
        dec = self._dim_decision(name)
        dec.weight = int(weight)
        dec.included = True
        dec.decided_at = ip.bk._now()
        self._touch()

    def include_dimension(self, name: str) -> None:
        self._dim_decision(name).included = True
        self._touch()

    def exclude_dimension(self, name: str) -> None:
        dec = self._dim_decision(name)
        dec.included = False
        dec.decided_at = ip.bk._now()
        self._touch()

    def activate_exclusion(self, rule: str, *, evaluation_mode: str = "", scope: str = "",
                           evidence_required: str = "", reason: str = "") -> None:
        dec = self._excl_decision(rule)
        dec.activated = True
        dec.evaluation_mode = evaluation_mode or dec.evaluation_mode
        dec.scope = scope or dec.scope
        dec.evidence_required = evidence_required or dec.evidence_required
        dec.reason = reason or dec.reason
        dec.decided_at = ip.bk._now()
        self._touch()

    def decline_exclusion(self, rule: str, *, reason: str = "") -> None:
        dec = self._excl_decision(rule)
        dec.activated = False
        dec.reason = reason or dec.reason
        dec.decided_at = ip.bk._now()
        self._touch()

    def set_priority_bands(self, bands: Optional[list]) -> None:
        self._decisions().priority_bands_override = (list(bands) if bands is not None else None)
        self._touch()

    def set_evidence_requirements(self, ev: Optional[gi.EvidenceRequirements]) -> None:
        self._decisions().evidence_requirements_override = ev
        self._touch()

    def discard_stale_decision(self, *, dimension: Optional[str] = None,
                               exclusion: Optional[str] = None) -> None:
        """Explicitly drop a stale decision (never automatic). Custom-dimension retention is not
        supported in this sprint (see module/limitations); discard is the only rebase action."""
        d = self._decisions()
        if dimension is not None:
            d.dimensions.pop(_norm(dimension), None)
        if exclusion is not None:
            d.exclusions.pop(_norm(exclusion), None)
        self._touch()

    # --- read models ---------------------------------------------------------

    def proposed_dimensions(self) -> list:
        d = self._decisions()
        out = []
        for dim in self.base_draft().dimensions:
            dec = d.dimensions.get(_norm(dim.name))
            out.append({
                "name": dim.name,
                "proposed_weight": dim.weight,
                "proposed_is_default": dim.weight_is_default,
                "reviewed_weight": (dec.weight if dec is not None else None),
                "included": (dec.included if dec is not None else True),
                "decided": dec is not None,
            })
        return out

    def exclusion_candidates(self) -> list:
        d = self._decisions()
        out = []
        for ex in self.base_draft().hard_exclusions:
            dec = d.exclusions.get(_norm(ex.rule))
            out.append({
                "rule": ex.rule, "evidence_required": ex.evidence_required,
                "evaluation_mode": ex.evaluation_mode, "scope": ex.scope,
                "decided": dec is not None,
                "activated": (dec.activated if dec is not None else None),
            })
        return out

    def weight_total(self) -> int:
        """Effective total of included dimension weights (reviewed value where chosen, else proposed)."""
        d = self._decisions()
        total = 0
        for dim in self.base_draft().dimensions:
            dec = d.dimensions.get(_norm(dim.name))
            if dec is not None and not dec.included:
                continue
            total += dec.weight if (dec is not None and dec.weight is not None) else dim.weight
        return total

    def stale(self) -> dict:
        return stale_decisions(self._decisions(), self.base_draft())

    def validate(self) -> list:
        return validate_decisions(self._decisions(), self.base_draft())

    def is_complete(self) -> bool:
        return is_review_complete(self._decisions(), self.base_draft())

    def apply(self) -> gi.GeneratedICP:
        return apply_strategy_decisions(self._decisions(), self.base_draft())

    def generate_reviewed_draft(self):
        """Apply decisions to the proposed draft, append the result as a NEW version, and return
        (reviewed_icp, iqs_result). Previous versions are never mutated; status stays Draft."""
        import iqs_validator as iqs
        reviewed = self.apply()
        reviewed.metadata.version = str(len(self.project.draft_versions) + 1)
        self.project.draft_versions.append(reviewed)
        # Stamp the strategy with this reviewed draft's stable content fingerprint so Approval can
        # match the draft to the strategy that produced it, without object identity.
        d = self._decisions()
        d.reviewed_fingerprint = idy.content_fingerprint(reviewed)
        d.reviewed_version = reviewed.metadata.version
        self.project.touch()
        return reviewed, iqs.validate(reviewed)
