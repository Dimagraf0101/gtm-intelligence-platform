# Changelog — GTM Intelligence Platform

High-level, human-readable history. Grouped by phase, newest first. This is a summary, not a
commit log; see git history for detail and **`docs/REPOSITORY_STATUS.md`** for current status.

## Sprint 11.1 — Qualification lineage hardening
- Qualification now uses **the exact Adapted ICP referenced by the LeadBatch's Search Strategy**, not
  the hypothesis's currently active ICP. `qualification_run` resolves the full chain deterministically
  (LeadBatch.derived_from_search_strategy → resolve Search Strategy in the same hypothesis → its
  derived_from_adapted_icp → resolve the exact approved ICP version) and builds the context via the
  existing `qualification_bridge.context_from_approved_icp` (the specific ICP). Added
  `search_strategy.parse_search_strategy_reference` + `was_ever_approved` (historical policy: Approved,
  or Archived-after-Approved, remains a valid lineage source). `QualifiedLeadBatch` gained
  `derived_from_search_strategy` (additive; schema v1; old JSON loads with ""). A newer active ICP no
  longer silently changes an existing batch's qualification; cross-version requalification is deferred
  to ExperimentRun. Deterministic refusals for malformed/missing/cross-hypothesis/disagreeing
  references. Frozen identity/approval/engine modules untouched. +tests (**520 total**).

## Sprint 11 — Qualification Engine integration
- Qualify an imported `LeadBatch` against a hypothesis's Approved Adapted ICP by **reusing the frozen
  engine** — no scoring duplicated, no engine module modified. New modules: `qualification_mapper`
  (domain `Lead` → engine `scoring.Lead` via the engine's own `normalize_lead`), `qualified_lead`
  (immutable `QualifiedLead` / `QualifiedLeadBatch` — leads referenced by id, not copied — + stats),
  and the `qualification_run` application service (validate lineage/ownership → map → run
  `qualification_bridge` → append an immutable `QualifiedLeadBatch`).
- Provenance `derived_from_lead_batch` + `derived_from_adapted_icp` (Adapted ICP `ArtifactIdentity`).
  Deterministic refusals (missing/empty batch, no Approved ICP, broken lineage, hypothesis mismatch).
  `MarketHypothesis` gained an append-only `qualified_batches` list (additive; schema v1; old JSON
  loads with `[]`). New page `pages/9_Qualification.py`. No source artifact edited; no export.
  +tests (**506 total**). Frozen: scoring/bridge/iqs/identity/adapter/approval unchanged.

## Sprint 10.1 — Lead Acquisition lineage hardening
- A persisted `LeadBatch` must now derive from exactly one **Approved** `SearchStrategy` owned by the
  same hypothesis. New application service `pipeline/lead_import.py` resolves the strategy and validates
  existence, ownership, and Approved status (+ non-empty importer, valid CSV) — refusing deterministically
  otherwise. `vayne_adapter` was slimmed to pure CSV parse/map (`leads_from_csv`); it no longer depends
  on `MarketHypothesis` or strategy approval. `LeadBatch` gained a required, immutable provenance field
  `derived_from_search_strategy` (via `search_strategy.search_strategy_reference` — the strategy's own
  reference authority, no new global identity system), round-trip stable. Malformed LinkedIn URLs are
  preserved as raw evidence, never counted as valid, and never drive de-duplication. Page 8 blocks
  import until a Search Strategy is approved (preview still allowed). Frozen components untouched.
  +tests (**495 total**).

## Sprint 10 — Lead Source & Lead Batch (Lead Acquisition boundary)
- Added the domain `pipeline/lead_batch.py`: source-agnostic `Lead`, immutable hypothesis-owned
  `LeadBatch`, extensible `LeadSource` kinds (Vayne Sales-Nav / manual CSV / Apollo / Clay /
  ZoomInfo), deterministic validation, de-duplication (LinkedIn URL, else person+company), and batch
  statistics. **No** Vayne/CSV/scoring knowledge in the domain.
- Added the anti-corruption layer `pipeline/vayne_adapter.py`: parses a Vayne CSV export, maps columns
  to domain leads, validates structure, and assembles a `LeadBatch` — mirroring `icp_adapter`. Vayne
  is replaceable; a future Apollo/Clay/ZoomInfo/manual adapter produces the same domain `LeadBatch`.
- `MarketHypothesis` gained an append-only `lead_batches` list + resolution helpers, serialized
  additively (schema still v1; old JSON loads with `[]`). New page `pages/8_Lead_Import.py` (thin).
- No scoring/qualification/outreach. Frozen components untouched (identity, fingerprints, approval,
  qualification, adapter, persistence model). +12 tests (**488 total**).

## Sprint 9 — Search Strategy
- Added `pipeline/search_strategy.py`: a **hypothesis-owned, versioned, immutable** Search Strategy
  **derived from the hypothesis's approved Adapted ICP**. Structured company/person criteria,
  geography, signals, exclusions, and **Sales Navigator filter recommendations** (values only — no URL,
  no LinkedIn ids). Filters derived deterministically in Python; confidence computed from evidence
  completeness; deterministic validation (normalization, include/exclude conflicts, size ranges,
  provenance). Lifecycle Draft → Reviewed → Approved → Archived (its own forward-only status — **not**
  the ICP approval framework, which is unchanged).
- `MarketHypothesis` gained a `search_strategies` list + resolution helpers, serialized additively
  (schema still v1; old JSON loads with `[]`).
- New page `pages/7_Search_Strategy.py` (thin). Provenance `derived_from_adapted_icp` via `icp_identity`.
- Frozen components untouched (ArtifactIdentity, fingerprints, approval, qualification, adapter,
  persistence model). +18 tests (**476 total**). It does not scrape or qualify leads; Vayne remains
  unimplemented.

## Sprint 8 — Market Hypothesis adaptation
- Adapted-ICP generation per hypothesis (`pipeline/adapted_icp.py`) reusing the draft generator over
  composed knowledge; records the source General ICP's `ArtifactIdentity` in an additive, fingerprint-
  neutral `Metadata.derived_from_general_icp`. Added `CompanyWorkspace.delete_hypothesis`. New page
  `pages/6_Market_Hypotheses.py`. +11 tests.

## Sprints 5.2 – 7.3 (consolidated)
- **5.2–5.3.1** ICP Project domain re-scope (Company + Project knowledge, composed views), Knowledge
  Interview engine + hardening.
- **5.4–5.5** Strategy Review (decision overlay) and Approval (immutable Approved versions + records).
- **5.6/5.7B** Generator→Qualification bridge (`score_leads(profile=…)`, PDF path unchanged); identity
  extracted to `pipeline/icp_identity.py`, `strategy_review ⇄ icp_approval` cycle removed, deterministic
  workspace invalidation.
- **6** Domain reframe: `CompanyWorkspace` / `MarketHypothesis` (aliases `ICPPortfolio` / `ICPProject`
  preserved) + JSON persistence (`pipeline/workspace_store.py`, schema v1).
- **7 / 7.1 / 7.2** General ICP generation + lineage; typed `ArtifactIdentity`, corrected to the
  status-stable form `<artifact_type>:<version>:<content_fingerprint>`.
- **7.3** Repository & documentation audit; `ARCHITECTURE_BASELINE_v1.0.md` declared the constitution,
  stale docs archived, `REPOSITORY_STATUS.md` + `docs/README.md` added.
- **Test count is now 447** (self-running, no pytest). See `docs/REPOSITORY_STATUS.md` for the current
  module/page map.

## Sprint 5.1 — Business Knowledge Review Workspace
- Added `pipeline/knowledge_review.py` (`KnowledgeReviewWorkspace`) — the mandatory human-review
  stage over Business Knowledge (browse/filter/search, confirm/reject/edit/add/merge, resolve
  conflicts, completeness/gap/conflict summary, and a deterministic "Generate Draft ICP" action).
- Added `pages/1_Business_Knowledge_Review.py` — a thin Streamlit view (multipage).
- Added `edit_item` to `pipeline/business_knowledge.py` (marks edits `origin=user_input`, preserves
  provenance). +15 tests (282 total).
- **Architecture finalized:** Business Knowledge is the single Source of Truth; the ICP is a derived
  artifact; the AI Interview will operate on Business Knowledge.

## Sprint 5.1.1 — Documentation reconciliation
- Renamed the product to **GTM Intelligence Platform** across docs.
- Reconciled README / Architecture / Constitution / Decisions / PRD / UX / IQS / Schema with the
  actual codebase; added `docs/PROJECT_STATE.md` and a real `docs/ROADMAP.md`; renamed
  `ICP_GENERATOR_*` → `ICP_WORKSPACE_*`; removed redundant empty placeholder docs.

## Sprint 4.1 — ICP Generator foundation (backend)
- Built the ICP Workspace backend and validated it with real-API pilots (offline suites use mocks):
  - 4.1A GeneratedICP model + IQS validator + engine adapter.
  - 4.1B document extraction (PDF/DOCX/PPTX/TXT/MD) + Source Package.
  - 4.1C Business Knowledge model + gap detection.
  - 4.1D/D.2/D.3 AI Business Knowledge extraction (hardened; real-API re-validated).
  - 4.1E/E.1 AI Draft ICP generation (real-API validated).
- Independent Release-Readiness Audits (4.2A–C) verified the foundation and flagged the integration
  gaps now tracked in `docs/ROADMAP.md`.

## Sprint 4.0 — ICP Workspace design
- IQS v1.0, ICP Profile Schema, ICP Workspace PRD & UX (design-only); two entry points
  (Create New / Standardize Existing).

## Release 0.3 — Modular Qualification Engine
- Evidence / Decision / Confidence / Pre-qualification layers; operational priority bands
  (90–100 → Priority 1 … 0–29 → Disqualified); enrichment guard; 3-sheet workbook export.
- ADR-001…007 recorded in `docs/DECISIONS.md`.

## MVP (Sprints 1–3)
- Local Streamlit Lead Qualification app: upload ICP PDF + lead CSV → AI-proposed, Python-decided
  scores → ranked, explainable results → XLSX/CSV export. Repository cleaned/reorganized; legacy
  Vayne/Claude-Code pipeline archived.
