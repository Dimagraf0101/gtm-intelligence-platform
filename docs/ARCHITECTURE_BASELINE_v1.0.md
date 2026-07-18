# GTM Intelligence Platform — Architecture Baseline v1.0

**Status:** Frozen constitution. Ratified after Sprint 6.
**Scope:** Describes the architecture exactly as it exists today and defines the rules every future
sprint must comply with. It does not redesign the system or invent future functionality.
**Authority:** Where any other document conflicts with this one on architecture, this document wins.
Amendments require an explicit, versioned revision (v1.1, v2.0, …) with rationale.

---

## 1. Product Mission

The GTM Intelligence Platform turns a company's own materials into validated go-to-market market
hypotheses and prioritized outbound lead pipelines. It extracts durable Business Knowledge from
company documents, lets a user stand up many independent Market Hypotheses (Healthcare, Logistics,
Real Estate, …), adapts an ICP and qualification strategy for each, and — after explicit human
approval — qualifies scraped leads against that approved ICP so a human reviewer can act on a ranked
shortlist. It is a **human-in-the-loop GTM experimentation system**, not an ICP generator and not a
lead scorer: the ICP is a supporting artifact, the hypothesis is the unit of value, and the platform
qualifies and ranks but never acts.

---

## 2. Core Principles

1. **Company Knowledge is the Source of Truth** for company facts; every ICP is derived from it.
2. **Market Hypotheses are independent.** One hypothesis never contaminates or overwrites another.
3. **Approved artifacts are immutable.** An Approved ICP and its records are frozen and versioned.
4. **LLM proposes; Python validates; the human approves.** No model output is trusted as a verdict.
5. **Unknown is better than hallucinated.** Missing information is declared, never invented; ambiguous
   or unsupported state is refused, not guessed.
6. **One source of truth per concept.** Everything else is a labeled derivation or a frozen snapshot.
7. **Determinism at the core, probability at the edges.** Identity, validation, gap detection,
   pre-qualification, decisions, fingerprints, and persistence are deterministic Python.
8. **Compatibility before optimization.** Changes are additive; the PDF qualification path and public
   contracts stay backward-compatible; fingerprints and approval behavior never silently change.
9. **No duplicated business logic.** Each rule has exactly one home (identity, validation, adaptation).
10. **The platform qualifies and ranks; it never acts** (Human Review Gate — no automated outreach).
11. **Plain dataclasses and focused services.** No premature databases, ORMs, repositories-as-framework,
    event buses, DI containers, microservices, or generic workflow engines.

---

## 3. Domain Model

Legend — **SoT** = Source of Truth; **Derived** = computed/regenerable; **Snapshot** = frozen copy.

| Entity | Purpose | Owner | Lifecycle | Mutability | SoT / Derived |
|---|---|---|---|---|---|
| **CompanyWorkspace** *(alias `ICPPortfolio`)* | Top-level aggregate: one company, its knowledge, General-ICP lineage, and Market Hypotheses | itself (root) | created once per company; long-lived | mutable container | SoT (workspace identity) |
| **BusinessKnowledge (company scope)** | Reusable, hypothesis-independent company facts + evidence | CompanyWorkspace | grows as materials are extracted/curated | mutable | **SoT (company facts)** |
| **BusinessKnowledge (hypothesis scope)** *(`project_knowledge` / `hypothesis_knowledge`)* | Hypothesis-only facts overlay | MarketHypothesis | grows via interview/review | mutable | **SoT (hypothesis facts)** |
| **KnowledgeItem / SourceReference / ConflictRecord** | Evidence-attributed fact, its provenance, and recorded conflicts | BusinessKnowledge | append/curate; rejected items retained for audit | mutable within owner | part of knowledge SoT |
| **ComposedProjectKnowledge** | Read-time view = company + hypothesis knowledge | derived on demand | transient per read | **immutable (deep-copied)** | Derived |
| **KnowledgeGapReport** | What is missing/conflicting for readiness | knowledge_gaps | recomputed each call | transient | Derived |
| **GeneralICP** *(a `GeneratedICP`, workspace-owned)* | Company-wide capability ICP baseline hypotheses adapt from | CompanyWorkspace `general_icp_versions` | lineage exists; **generation deferred** | version immutable once created | SoT artifact (per version) |
| **MarketHypothesis** *(alias `ICPProject`)* | One GTM hypothesis with its own knowledge, Adapted-ICP lineage, strategy, approvals | CompanyWorkspace | created per hypothesis; independent lifecycle | mutable container | SoT (hypothesis identity) |
| **GeneratedICP (Draft / Adapted)** | The typed ICP authoring artifact | icp_draft_generator / strategy_review | Draft → reviewed → Approved; append-only versions | **immutable once appended** | SoT artifact (per version) |
| **StrategyDecisions (+ DimensionDecision, ExclusionDecision)** | Human weight/exclusion/threshold decisions overlaying a Draft | strategy_review, on the hypothesis | edited until applied/approved | mutable working state | SoT (the human decisions) |
| **ApprovalRecord** | Audit of one approval (who/when/IQS/fingerprint/strategy revision) | MarketHypothesis | created at approval; append-only | **immutable** | SoT (approval audit) |
| **GeneratedICP (Approved)** | Frozen approved ICP version | MarketHypothesis `approved_versions` | created at approval; never mutated | **immutable** | **SoT (the approved ICP)** |
| **ICPProfile** | Engine-facing qualification contract | icp_adapter (sole producer) | built per run from an Approved ICP (or PDF text) | transient value | Derived |
| **QualificationICPContext** | Frozen snapshot of the ICP used for exactly one run | qualification_bridge | created per run | **immutable (frozen)** | Snapshot (run SoT) |
| **ScoringResult** | Per-lead qualification outcome | scoring | produced per run | transient today | Derived |
| **Workbook / CSV export** | Deliverable for human review | export | produced per run | transient bytes | Derived |
| **ValidationResult (IQS)** | ICP validity + warnings | iqs_validator (sole validator) | computed on demand | transient | Derived |
| **InterviewSession / InterviewQuestion** | Transient interview plan/state | knowledge_interview | per session; self-heals | transient | Derived |
| **Transient workspaces** (KnowledgeReview, KnowledgeInterview, StrategyReview, Approval) | UI-facing services holding no durable truth | pages/session-state | rebuilt on revision change | transient | Derived (never persisted) |

---

## 4. Aggregate Roots

An aggregate root is a consistency boundary that owns its internals and is the only entry point for
changing them.

1. **CompanyWorkspace** — owns the company BusinessKnowledge, the General-ICP lineage, and the
   collection of Market Hypotheses. It is the persistence and (single-tenant) tenancy boundary; one
   CompanyWorkspace = one company. It is an aggregate because company knowledge, the General-ICP
   baseline, and hypotheses must be consistent and serialized as one unit.

2. **MarketHypothesis** — owns its hypothesis knowledge overlay, its Adapted-ICP lineage (draft +
   approved versions), its StrategyDecisions, its approval records, and its active-approved pointer.
   It is an aggregate because a hypothesis's knowledge → draft → strategy → approval must stay
   internally consistent and independent from every other hypothesis. Its Approved versions and
   ApprovalRecords are append-only within it.

*Not yet aggregate roots (see §11):* LeadBatch and ExperimentRun will become **separate** immutable
roots referenced by id/fingerprint when introduced; they must not be nested mutably inside
MarketHypothesis. Until then, in-object references inside the ICP pipeline are acceptable.

---

## 5. Bounded Contexts

Contexts are **logical module groupings** (packages), never deployment units.

**A. Materials & Sourcing**
- Modules: `source_documents`, `source_package`, `icp_pdf`.
- Responsibility: ingest raw company materials / PDFs into normalized text and source packages.
- Public interface: source package build; PDF → ICP text.
- Depends on: stdlib, `pypdf`. Forbidden: knowledge/ICP/qualification internals.

**B. Knowledge**
- Modules: `knowledge_extractor`, `business_knowledge`, `knowledge_gaps`, `knowledge_review`.
- Responsibility: extract, store, curate, and gap-analyze Business Knowledge (company + hypothesis).
  Python validates every AI-proposed fact; provenance and conflicts are preserved.
- Public interface: `BusinessKnowledge`, `detect_gaps`, `KnowledgeReviewWorkspace`.
- Depends on: `icp_profile` (for `parse_employee_range` only), Materials context. Forbidden:
  qualification engine, approval, adapter.

**C. Hypothesis & ICP Adaptation** *(the core authoring context)*
- Modules: `icp_project` (CompanyWorkspace / MarketHypothesis / ComposedProjectKnowledge),
  `knowledge_interview`, `icp_draft_generator`, `generated_icp`, `strategy_review`, `iqs_validator`,
  `icp_approval`, `icp_identity`.
- Responsibility: adapt knowledge into ICPs, run the gap-driven interview, apply strategy decisions,
  validate via IQS, and gate approval. Produces immutable Approved ICPs.
- Public interface: `GeneratedICP`, `StrategyReviewWorkspace`, `iqs_validator.validate`,
  `icp_approval` (eligibility/approve/active), `icp_identity` fingerprints.
- Internal dependency direction: `generated_icp → icp_identity → {strategy_review, icp_approval}`;
  `icp_approval → strategy_review` (downward). **Forbidden: `strategy_review → icp_approval`.**

**D. Engine Boundary (Anti-Corruption Layer)**
- Modules: `icp_adapter`, `icp_profile`.
- Responsibility: the **sole** translation from an Approved `GeneratedICP` to the engine's
  `ICPProfile`. Rejects anything not Approved and IQS-valid.
- Public interface: `icp_adapter.can_use`, `icp_adapter.to_engine_profile`.
- Forbidden: any other module producing an `ICPProfile` from a `GeneratedICP`.

**E. Qualification Engine**
- Modules: `qualification_bridge`, `scoring`, `prequalification`, `decision`, `evidence`.
- Responsibility: converge both ICP inputs (PDF text or Approved ICP) into one run context, then
  pre-qualify (Python) and semantically rank (LLM) leads deterministically ordered by Python.
- Public interface: `qualification_bridge` (context builders, `score_with_context`),
  `scoring.score_leads(..., profile=None)`.
- Depends on: Engine Boundary, Approval (read-only, via bridge). Forbidden: mutating any ICP; being
  the place the "only Approved may qualify" rule is bypassed (that rule lives in the bridge).

**F. Delivery**
- Modules: `export`.
- Responsibility: render qualified results into workbook/CSV for human review + Linked Helper handoff.
- Forbidden: automated outreach; mutating results.

**G. Persistence & Infrastructure**
- Modules: `workspace_store`, `workspace_revision`, `config`.
- Responsibility: deterministic JSON save/load of the CompanyWorkspace with schema versioning and
  explicit refusal of bad data; content-based workspace-revision tokens; env config.
- Forbidden: databases, ORMs, repositories-as-framework, migrations engine.

**H. Presentation**
- Modules: `app.py`, `pages/1..4`.
- Responsibility: thin Streamlit views that forward to context services and own only transient
  session state. **No business rules live here.**
- Forbidden: domain mutation logic, validation, scoring, or persistence rules embedded in pages.

---

## 6. Source of Truth Map

| Concept | The one Source of Truth |
|---|---|
| Company facts | `CompanyWorkspace.company` (BusinessKnowledge) |
| Hypothesis facts | `MarketHypothesis.project_knowledge` |
| Composed knowledge | *derived* (transient, deep-copied view) |
| General ICP (a version) | the approved `GeneratedICP` in `CompanyWorkspace.general_icp_versions` |
| Adapted ICP (a version) | the `GeneratedICP` in `MarketHypothesis.draft_versions` / `approved_versions` |
| Strategy decisions | `MarketHypothesis.strategy` (StrategyDecisions) |
| ICP validity | `iqs_validator.validate(icp)` (derived, but the sole authority) |
| ICP identity / fingerprint | `icp_identity` (sole algorithm) |
| The Approved ICP | frozen version in `approved_versions` + `active_approved_version` fingerprint |
| Approval audit | `MarketHypothesis.approval_records` (append-only) |
| Qualification profile | *derived* via `icp_adapter` (transient) |
| One qualification run's ICP | `QualificationICPContext` (frozen snapshot) |
| Qualification results / export | *derived* from a run (transient today) |
| Persisted workspace on disk | `workspace_store` envelope (`schema_version` + `CompanyWorkspace.to_dict`) |

No concept has two sources of truth. `GeneratedICP` and `ICPProfile` coexist, but `ICPProfile` is a
one-directional derivation through the single adapter — a projection, not a rival truth.

---

## 7. Immutable Objects

| Object | Why immutable |
|---|---|
| **Approved `GeneratedICP` versions** | An approved ICP is a committed decision; qualification and audit must reference exactly what was approved. Stored append-only; never mutated. |
| **Draft `GeneratedICP` versions (once appended)** | Version history must be stable; regeneration appends a new version rather than editing a prior one. |
| **`ApprovalRecord`** | It is the immutable audit of who approved what, when, against which fingerprint and strategy revision. |
| **`QualificationICPContext`** | A frozen run snapshot so a completed run is unaffected by later selection/knowledge changes; guarantees run reproducibility. |
| **`ComposedProjectKnowledge` output** | A read-only deep-copied view; mutating it must never affect company or hypothesis knowledge. |
| **Fingerprints / warning ids** (`icp_identity`) | Stable identity across process restarts and refactors; approval, acknowledgement, and active-version resolution depend on byte-for-byte stability. |
| **`active_approved_version` pointer semantics** | Resolves by stable fingerprint, never list order; re-pointed only by a new approval. |

---

## 8. Mutable Objects

| Object | Why mutable |
|---|---|
| **BusinessKnowledge (company + hypothesis)** | Curation is ongoing: facts are added, confirmed, rejected, merged; conflicts recorded. It is working truth, not a frozen artifact. |
| **CompanyWorkspace / MarketHypothesis containers** | They accumulate hypotheses and versions over time; the containers grow while the artifacts inside them are immutable. |
| **StrategyDecisions** | The human edits weights and exclusion activations until the review is complete and applied. |
| **Transient workspaces** (KnowledgeReview / Interview / StrategyReview / Approval) and **InterviewSession** | Per-session UI-facing derived state; rebuilt on revision change; never persisted. |
| **Draft `GeneratedICP` before it is appended** | It is regenerable working output until stored as a version. |

---

## 9. Dependency Rules

**Layering (inward-only):**
```
L0 kernel      generated_icp, icp_profile, evidence, config, icp_identity
L1 derive      knowledge_gaps, iqs_validator, decision, prequalification, business_knowledge
L2 domain      icp_project, knowledge_extractor, icp_draft_generator, source_*
L3 services    knowledge_review, strategy_review, icp_approval, knowledge_interview
L4 boundary    icp_adapter (ACL);  engine: qualification_bridge, scoring
L5 infra       workspace_store, workspace_revision
L6 UI          app.py, pages/*
```
Dependencies point **inward/downward** only. UI depends on services/domain; domain never imports UI.

**Explicitly allowed:**
- `strategy_review → icp_identity`, `icp_approval → icp_identity` (shared identity, downward).
- `icp_approval → strategy_review` (approval consumes strategy completeness — correct direction).
- `qualification_bridge → {icp_adapter, icp_approval, scoring, generated_icp, icp_profile}`.
- `icp_project` typed references to `StrategyDecisions` / `GeneratedICP` / `ApprovalRecord` **only via
  `TYPE_CHECKING`** forward references (no runtime import).
- Lazy (function-local) imports where a runtime cycle would otherwise form (e.g. `MarketHypothesis`
  serialization importing `strategy_review` / `icp_approval` inside methods).

**Forbidden:**
- `strategy_review → icp_approval` (the removed cycle — never reintroduce).
- Any module other than `icp_adapter` converting a `GeneratedICP` into an `ICPProfile`.
- Any module other than `iqs_validator` deciding ICP validity.
- Any module other than `icp_identity` computing ICP fingerprints / warning ids.
- Pages/`app.py` containing business rules, validation, scoring, or persistence logic.
- `scoring` / `icp_adapter` / `iqs_validator` / `export` importing UI or persistence modules.
- Top-level import cycles (use `TYPE_CHECKING` or lazy imports as above).

---

## 10. Architecture Invariants (future code must never violate)

1. **Fingerprints and warning ids never change** (byte-for-byte; pinned by test). Any change is a
   breaking event requiring an explicit versioned migration decision.
2. **Approved ICP versions and ApprovalRecords are immutable** and append-only.
3. **`iqs_validator` is the sole ICP validator.** No parallel or duplicated ICP validation.
4. **`icp_adapter` is the sole `GeneratedICP → ICPProfile` boundary**, and only for Approved,
   IQS-valid ICPs.
5. **`icp_identity` is the sole fingerprint/identity authority.**
6. **LLM proposes; Python validates; the human approves.** No LLM output is a verdict; every AI site
   has a deterministic offline mock and Python authority over the outcome.
7. **Approval requires the full gate**: complete Strategy Review, IQS with no blocking errors, all
   warnings explicitly acknowledged (fingerprint-scoped), non-stale/matching draft, and a named
   approver. Approval never mutates knowledge, the reviewed draft, or the profile.
8. **Only Approved, IQS-valid ICPs may qualify leads** (enforced by `qualification_bridge`); Drafts
   never qualify; the two ICP input modes (PDF vs Approved) are mutually exclusive per run.
9. **The PDF qualification path stays unchanged**; `score_leads(profile=None)` is backward-compatible.
10. **Market Hypotheses are isolated** — one never reads or mutates another's knowledge, ICPs,
    strategy, approvals, or results.
11. **Composed knowledge and run contexts are read-only snapshots** — they never mutate their sources.
12. **Unknown/invalid input is refused, never guessed** (extraction, approval, persistence load).
13. **Serialization is deterministic Python** with a `schema_version`; malformed/unsupported payloads
    refuse explicitly; no silent migration.
14. **No duplicated business logic** — identity, validation, adaptation, and qualification each have
    exactly one home.
15. **Changes are additive and compatibility-preserving**; public signatures and `to_dict` outputs
    remain stable unless an additive change is unavoidable; the full existing test suite stays green.
16. **The platform never automates outreach** (Human Review Gate).

---

## 11. Evolution Roadmap (expected, intentionally absent today)

| Future entity / capability | Why deferred | Intended shape when introduced |
|---|---|---|
| **General ICP generation** | Lineage/storage exists; generation not yet wired | Reuse `icp_draft_generator` on company-only knowledge; record `derived_from_fingerprint` on Adapted ICPs; no ICP-schema change |
| **SearchStrategy** | Sourcing workflow (Sales Navigator recommendations) not yet modeled | Versioned entity owned by MarketHypothesis; deterministic recommendation; snapshotted into a run |
| **LeadSource / Vayne Adapter** | External scraping integration out of scope; API is closed | Anti-corruption layer mirroring `icp_adapter`: `URL → vayne_adapter → LeadBatch` |
| **LeadBatch** | Persisted, immutable scraped datasets not yet needed | Separate immutable aggregate root, referenced by id; not regenerable without re-scraping |
| **ExperimentRun / QualificationRun** | Results are transient today; comparison not yet required | Separate immutable, append-only aggregate root capturing {hypothesis, approved-ICP fingerprint, search-strategy snapshot, lead batch, results, export} |
| **Experimentation analytics / comparison** | "Which hypothesis wins" needs persisted runs first | Read-only projection over ExperimentRuns; never a source of truth |
| **Google Sheets / Linked Helper delivery** | Manual export suffices for MVP | Delivery-context adapters; still no automated outreach |
| **CRM / accounts / auth / learning loop** | Out of MVP scope; would add premature complexity | Only after experimentation is proven and persistence matures |

Deferral rationale: each is additive at an existing seam (ACL, aggregate reference, or projection) and
must **not** force changes to the frozen qualification core, the identity/validation/adapter
singletons, or the immutability guarantees.

---

## 12. Definition of Architectural Success

After another 100 sprints, this architecture is successful if all of the following remain true:

1. **The qualification core is untouched by change velocity.** `scoring`, `icp_adapter`,
   `iqs_validator`, `icp_identity`, and the immutability/fingerprint guarantees have not required a
   rewrite; new capability attached at seams (ACLs, aggregate references, projections), not by
   editing them.
2. **Every concept still has exactly one source of truth**, and every derived artifact is still a
   labeled derivation or a frozen snapshot.
3. **Approved artifacts remain immutable and reproducible** — an old approval, run, or fingerprint can
   still be resolved and explained years later.
4. **Market Hypotheses remain independent** and now number in the hundreds without cross-contamination
   or overwrite, with results comparable across runs.
5. **The LLM boundary is unchanged in principle** — Python still owns every verdict; every AI site is
   still offline-testable with deterministic mocks.
6. **Persistence evolved additively** — from local JSON to whatever scale demands — without leaking a
   database/ORM/repository framework into the domain, which stayed plain serializable dataclasses.
7. **The dependency graph is still acyclic and inward-pointing**, with no business logic in the UI and
   no second home for identity, validation, or adaptation logic.
8. **A new engineer can read this document and the module layering and correctly predict where any new
   feature belongs** — the architecture still explains itself.

If any of these ceases to hold, that is the signal to amend this baseline deliberately (a new version),
not to accrete around it silently.

---

*End of Architecture Baseline v1.0. This document is the reference architecture for every future
sprint. It is a specification only — it contains no implementation.*
