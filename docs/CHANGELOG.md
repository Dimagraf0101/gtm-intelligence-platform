# Changelog — GTM Intelligence Platform

High-level, human-readable history. Grouped by phase, newest first. This is a summary, not a
commit log; see git history for detail and **`docs/REPOSITORY_STATUS.md`** for current status.

## Sprint 12.1.1 — Duplicate-submission guard (idempotent submit)
- **Prevents duplicate Vayne orders.** A `SearchExecution` now carries a deterministic
  `execution_fingerprint` (additive field) computed from **domain inputs only** — owning hypothesis id +
  Search Strategy id + normalized Sales Navigator URL + lead-limit intent — via
  `search_execution.execution_fingerprint()`. No provider value (no Vayne order id) participates.
- `create_and_submit` now, **before** creating anything, calls `find_active_duplicate(...)`: if an
  **active** (non-terminal) execution with the same fingerprint exists it **resumes** it and returns
  `resumed=True`, calling `VayneClient.submit` **exactly zero** times. A terminal (Completed/Failed)
  execution never blocks a fresh request. A deliberate rerun of an active request is supported only via
  an explicit `force=True` (never an automatic retry). `refresh_execution` and transient
  download-retry remain strictly submit-free (they only poll/download the existing order).
- URL normalization (`normalize_sales_navigator_url`) is conservative and used **only** for duplicate
  detection — lower-cases scheme/host, drops the fragment, strips a trailing `/`; the query string is
  kept **verbatim** so genuinely different searches never collapse. The raw URL is still preserved on
  the execution for audit.
- Domain additions are additive: `execution_fingerprint` field (old JSON loads with `""`), `is_active`
  property, and a `resumed` flag on `SearchExecutionResult`. The state machine is unchanged (no
  `EXEC_READY` introduced — the existing statuses already resume/retry safely). Page 10 now shows a
  distinct "resumed existing execution — no duplicate order" message.
- Scope intentionally minimal: **no auto-save / persistence changes** this sprint (durable
  restart-resume remains manual save/load, as before). +tests (**573 total across 34 files**);
  regression proofs: duplicate active submit prevented; normalized-URL equivalence resumes; different
  lead_limit is not a duplicate; refresh/retry never submit; force supports a deliberate new run; a
  Failed execution does not block resubmit; fingerprint is provider-free and round-trips; old JSON loads.

## Sprint 12.1 — Configurable lead retrieval for Search Execution
- Users can now choose, per Search Execution, to **scrape all available leads** or **cap at N**
  (presets 10/25/50/100/250/500 or a custom positive integer). Provider-independent by design.
- `SearchExecution` owns the retrieval request via an additive `lead_limit: Optional[int]` — **`None`
  means no limit** (scrape everything available), a positive int is the maximum requested. No 0/-1/
  huge-number sentinels. Added a `name` field (optional human label for history) and a `requested_label`
  helper ("All" or the number). Deterministic `validate_lead_limit` (None ok; else a positive int within
  a sane upper bound; rejects 0, negatives, non-ints/bools). Both fields are additive (schema v1; old
  JSON loads as `lead_limit=None`, `name=""`).
- **All provider-specific translation stays inside `VayneClient`**: `submit(..., lead_limit=None)` omits
  the `limit` key (verified Vayne contract = "all available"); a positive `lead_limit` sends
  `limit: N`. Vayne has no null/-1 sentinel, so unlimited simply omits the key. No provider logic leaked
  into the domain, service, or UI.
- The application service `create_and_submit` validates `lead_limit`, stores it + the run name on the
  execution, and passes provider-independent intent to the client. **Requested vs Imported are
  independent**: requesting 500 but importing 327 (fewer available) is a normal **Completed**, not an
  error — the imported count comes from the derived LeadBatch's stats, never the requested amount.
- Page 10 gains a **Lead Retrieval** control (radio: all / specific number; presets + custom; the
  numeric input is hidden when unlimited; validation rejects 0 and negatives) and an execution history
  that shows **Name · Requested · Imported · Status** so the two counts are always distinguishable.
- Unchanged: manual CSV import, Qualification Engine, LeadBatch, scoring, Search Strategy, exports.
  New tests cover unlimited/limited storage + adapter reach + payload translation + validation +
  requested-vs-imported independence + history + round-trip/back-fill. +tests (**564 total across 34
  files**).

## Sprint 12.0.2 — Decision engine cleanup & single source of truth
- **Behavior-preserving cleanup.** No qualification outcome, stored schema, or historical compatibility
  changed; the only externally visible change is that setup/docs no longer expose the misleading
  `SCORE_THRESHOLD` setting.
- Removed the **dead `SCORE_THRESHOLD`** configuration (it was never read by any runtime code): dropped
  from `pipeline/config.py`, `.env.example`, `install-mac.command`, and the `README.md` env table.
  Qualification is not controlled by any environment variable; an old `.env` that still defines it is
  harmless and does not affect startup. (The archived `docs/REPOSITORY_AUDIT.md` snapshot is left as-is.)
- Introduced **one canonical operational priority policy**, `pipeline/priority_policy.py` — an immutable
  leaf module (`OPERATIONAL_PRIORITY_BANDS`, a tuple of `(min_score, label)`; `DISQUALIFIED_LABEL`;
  `default_priority_band_rows()`). The decision engine (`decision._OPERATIONAL_BANDS`) now *is* that
  tuple, and `generated_icp.standard_priority_bands()` **derives** the default ICP bands from it while
  still returning a fresh, mutable `list[PriorityBand]` each call. Dependency direction:
  `priority_policy → decision → ICP generation defaults → UI/export` (no cycle; the two former
  hard-coded copies of the bands are gone).
- Preserved the architectural separation: `operational_priority` remains the **sole** qualification
  authority (Priority 1-5 / Disqualified, `QualifiedLead.decision`, export ordering); `internal_category`
  (from an ICP's own `category_thresholds`) stays **audit-only** and author-controlled — custom
  thresholds are never synchronized to the operational policy on load/migration.
- Added `docs/REPOSITORY_STATUS.md` **Decision Engine architecture** section documenting both paths and
  the invariants (score < 30 → Disqualified; 30+ available; confirmed dealbreakers override; suspected
  do not; env vars never decide). New `tests/test_priority_policy.py` (9 tests). +tests
  (**557 total across 34 files**). Frozen engine behavior unchanged.

## Sprint 12 — Search Execution & Vayne integration
- Automatic lead acquisition for an **Approved Search Strategy**: the user configures LinkedIn Sales
  Navigator **manually**, pastes the resulting search URL, and the platform submits it to **Vayne**,
  which scrapes it into a CSV that flows through the **existing** `vayne_adapter` + `lead_import` gate
  into an immutable Lead Batch — the **same** importer, domain, validation, and qualification the manual
  CSV upload uses (no second mapping implementation). Vayne is one **replaceable** external `LeadSource`
  integration.
- New immutable domain artifact `search_execution.SearchExecution` = one operational scraping run (not
  an ExperimentRun): forward-only status **Draft → Submitted → Running → Completed/Failed** (terminals
  immutable), append-only event log, set-once provenance (`execution_id`, `hypothesis_id`,
  `derived_from_search_strategy`, `sales_navigator_url`, `external_job_id`, `derived_lead_batch_id`), and
  deterministic Sales Navigator URL validation (HTTPS, LinkedIn Sales Navigator host/path, no embedded
  credentials, length). The pasted URL is preserved verbatim as **evidence**; the Approved Search
  Strategy remains the filter authority — the platform never builds or interprets the URL.
- New Vayne boundary `integrations/vayne_client.py` (the sole HTTP/Vayne surface: authenticate, submit
  URL, read job status, retrieve CSV, translate transport errors — transient vs terminal). It knows
  nothing of `MarketHypothesis`, never validates ICP lineage, never constructs/persists a LeadBatch, and
  contains no Streamlit. `requests` is lazy-imported so the fake-client tests never need it.
- New application service `search_execution_service.py` orchestrates submit → user-triggered **Refresh
  status** → on completion download the CSV and import via `lead_import` (which re-validates strategy
  approval + ownership). Async is user-driven — **no** background workers/Celery/Redis/queues/webhooks;
  bounded timeouts; idempotent retries. **Idempotent:** one completed execution yields **at most one**
  LeadBatch (`derived_lead_batch_id` set-once; terminal executions refresh to a no-op).
- Lineage stays authoritative: LeadBatch continues to derive from the Approved Search Strategy
  (`derived_from_search_strategy`); the execution id is recorded **additively** on the batch
  (`derived_from_search_execution`) and never weakens Search Strategy provenance. `MarketHypothesis`
  gained an append-only `search_executions` list; `lead_batch`/`lead_import` gained the optional additive
  `derived_from_search_execution` (schema v1; old JSON loads with defaults; no schema bump).
- Secrets: Vayne credentials come from `config` (`VAYNE_API_TOKEN`, existing convention) and are never
  hardcoded, printed, logged, serialized into workspace JSON, or shown in UI errors.
- New thin page `pages/10_Search_Execution.py` (select hypothesis + Approved Search Strategy, show
  recommended filters, paste URL, submit, show execution id + status, Refresh status, on completion show
  Lead Batch stats and link to Qualification). The manual CSV fallback on page 8 is preserved and
  labeled. Deterministic tests use a **fake** Vayne client (no real API calls). Frozen engine/identity/
  approval/adapter modules untouched. +tests (**548 total across 33 files**).

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
