# Changelog — GTM Intelligence Platform

High-level, human-readable history. Grouped by phase, newest first. This is a summary, not a
commit log; see git history for detail and **`docs/REPOSITORY_STATUS.md`** for current status.

## v1.0.0 — Production-Validated MVP Candidate

The first tagged release. The platform runs end to end: Business Knowledge → General ICP → Market
Hypothesis → Adapted ICP → Search Strategy → Search Execution (Vayne) or manual CSV → Lead Batch →
Qualification → Human Review → XLSX / CSV / Google Sheets.

- **646 tests across 38 files**, fully offline — no API keys and no network required.
- Immutable, append-only artifacts with full lineage; priority owned solely by deterministic Python;
  human approval required before anything leaves the platform.
- **Honest scope:** the **Vayne** and **Google Sheets** integrations are implemented and tested against
  deterministic fake clients, but **neither has been validated against its live API**. Live validation is
  the first task of the next release.
- Composed of Sprints 1–14.2 below; Sprint 14.1 froze the documentation and Sprint 14.2 resolved the
  release-readiness findings (LICENSE, repository-structure accuracy, deprecated installer).

## Sprint 14.2 — Release-readiness fixes (documentation & metadata only)
- Added the MIT `LICENSE` file the README had been claiming.
- README: marked `icp/`, `data/`, `outputs/` and `archive/` as **local workspace directories that are
  git-ignored and absent from a fresh clone**, rather than listing them as repository contents; added the
  `v1.0.0 — Production-Validated MVP Candidate` release line; corrected the Legacy section.
- Deprecated `install-mac.command` (it downloads a *different* repository and installs the legacy CLI
  skill): it now refuses to run and points to the README. `INSTALL-MAC.md` was already archived.
- Added this release entry. **No production code, architecture, or business logic was changed.**

## Sprint 14 — Google Sheets publisher
- Publish reviewed leads to **Google Sheets** from the Human Review page, as a downstream publisher
  behind the existing `review_export` seam. New boundary `pipeline/integrations/google_sheets_publisher.py`
  consumes **already-assembled canonical rows only** — it never sees a Lead, QualifiedLead, ReviewRow or
  workspace object, never rebuilds names, reruns qualification, infers values, or touches priority.
- **API surface verified, not assumed:** `gspread` was not previously installed, so it was added
  (`gspread>=6.0.0`, `google-auth>=2.30.0`) and every call used here was confirmed by introspection
  against gspread 6.2.1 (`Client.create/open_by_key`, `Spreadsheet.add_worksheet/worksheet/worksheets/
  id/url`, `Worksheet.clear/resize/update/freeze/format/set_basic_filter/columns_auto_resize`,
  `APIError/SpreadsheetNotFound/WorksheetNotFound`).
- **Auth:** service account only, loaded from the environment (`GOOGLE_SHEETS_CREDENTIALS_FILE` path or
  `GOOGLE_SHEETS_CREDENTIALS_JSON` inline, e.g. from Streamlit secrets). Credentials are never committed,
  printed, echoed in errors, returned, or serialized into a workspace.
- **Three managed worksheets** — *Leads* (`MAIN_COLUMNS`), *AI Details* (`AI_COLUMNS`), *Summary* — in
  exact canonical order. Deterministic **full replacement** (clear → resize → write), so repeated
  publishing updates in place and never appends duplicates; unrelated worksheets are never touched.
- **Explicit modes:** *Create New* (needs a name) and *Update Existing* (needs a validated spreadsheet ID
  or URL). The target is never auto-detected; an unrecognized URL/ID is refused rather than guessed.
- **Python validates before any external call** — confirmation, credentials, non-empty scope, exact
  canonical columns/order, duplicate rows, valid target. A rejected publish performs zero API calls.
  Failures translate to user-safe messages (access denied / not found / rate limit / transient network /
  write failure), never a secret or stack trace; formatting failure degrades to a warning rather than
  falsely reporting failure of an already-correct data write.
- UI: scope (Approved-only default / Selected / All), mode, target, live row counts, an explicit
  confirmation checkbox, and a Publish button disabled until every precondition holds; success shows the
  spreadsheet URL. Existing XLSX/CSV exports, the canonical schema, qualification and priority are all
  unchanged. Tests use a deterministic fake client — **no live Google credentials**. +tests
  (**646 total across 38 files**).

## Sprint 13c — Human Review stabilization & end-to-end validation
- **Stabilization sprint — no new features.** The full workflow (Import → Qualification → Review →
  Save → Reload → Continue → Export) was *executed* against real repository code, not just inspected.
- **Fixed a real export-determinism bug:** `review_view.format_score_breakdown` iterated the engine's
  `dimensions` dict in insertion order, but `workspace_store` serializes with `sort_keys=True`, so the
  order became alphabetical after a save/reload — the same batch exported a *different* `Score Breakdown`
  cell before vs after reload. Dimension names are now sorted, so the value is stable in both directions.
- **Fixed a real UI bug that could block review:** the lead-details selector keyed its options on
  company/contact/priority/status, so two leads sharing those values collapsed into one entry and a lead
  became **unreachable and un-reviewable**. Labels now include the row ordinal, guaranteeing every
  visible lead is individually addressable.
- **Session safety:** the rejection-reason / reviewer-comment inputs now key on the lead's last decision
  timestamp, so after a new decision (or a workspace reload) they re-seed from the **persisted** values
  instead of showing stale typed text.
- **Explicit persistence (Task 10 — no hidden autosave):** the review page now carries its own
  **Save workspace** control plus an explicit warning naming how many decisions are session-only, so
  review work cannot be lost silently. Deterministic and user-triggered by design; reload remains on the
  General ICP page and restores every decision, comment, timestamp and the append-only history.
- **UX polish:** export scopes show live counts, a warning fires when the "Selected" scope is empty
  (selection resets on filter change), fully-reviewed and no-decisions-yet states are called out, and
  missing AI evidence / mock results are labelled rather than silently blank.
- Validated: statistics never go stale (single, bulk, changed decision, revert-to-Pending, reload,
  multiple isolated batches); impossible states normalized or refused; exports have no duplicated or
  missing rows and reconstruct nothing; `ReviewRow` remains the only projection (a guard test forbids the
  page from re-joining domain objects). Scale (deterministic code, 5000 rows): sort 2.5 ms, filter 0.5 ms,
  search 1.0 ms, canonical rows 7 ms, 5000-decision artifact round-trip 4.8 ms. +tests
  (**625 total across 37 files**).

## Sprint 13b — Human Review MVP
- **Human Review is now the primary review workbench.** New append-only domain `pipeline/lead_review.py`:
  immutable `LeadReviewDecision` (frozen) + `ReviewedLeadBatch` owned by a MarketHypothesis and derived
  from exactly one `QualifiedLeadBatch`. Re-deciding a lead **appends** a new decision (latest wins,
  earlier ones kept as audit history) — nothing is ever overwritten. Statuses: Pending / Approved /
  Rejected / Skipped. Human review **never mutates** the Lead or the QualifiedLead (asserted by tests
  comparing before/after snapshots).
- **`review_status` is the single workflow authority.** The canonical export **Human Decision** value is
  *derived* (`human_decision_for`) and never stored, so the two cannot drift. Contradictory states are
  impossible: `rejection_reason` is deterministically cleared unless the status is Rejected.
- **One deterministic view model** `pipeline/review_view.py` (`ReviewRow`) joins Lead + QualifiedLead +
  decision by `lead_id` (missing decision → Pending) and is consumed by **both** the UI and the export
  adapter, so screen and workbook can never disagree. Includes canonical sorting (priority ascending,
  then score descending), filtering (status/priority/score/industry/size/geography), and free-text
  search over Company + Contact. Pure projection — no qualification recomputed, no source mutated.
- **Export reuses the canonical schema unchanged.** New thin adapter `pipeline/review_export.py` maps
  the view model into `MAIN_COLUMNS`/`AI_COLUMNS` rows (scopes: **Approved only** (default), Selected,
  All) and serializes via **one additive** entry point `export.workbook_bytes_from_rows(...)`;
  `to_workbook_bytes` now delegates to it, so behavior is byte-identical and no legacy Lead/ScoringResult
  objects are rehydrated. Column names/order untouched (`test_export` still 22/22). The summary sheet's
  previously-hardcoded `Review — Pending/Approved/Rejected/Skipped` counters now carry real counts.
- `MarketHypothesis` gained an append-only `reviewed_batches` list + `list_reviewed_batches()` /
  `review_for_qualified_batch()` (additive; schema still v1; old JSON loads with `[]`). New page
  `pages/11_Human_Review.py`: lineage header, summary metrics + priority distribution, search/filters,
  review table with row selection and clickable links, bulk Approve/Reject/Skip (explicit selection or
  confirmed filtered scope, with exact counts), lead-details panel (business identity, attributes, AI
  proposal, labelled audit data, decision history) and XLSX/CSV export. No priority override, no score or
  business-data editing, no requalification, no Google Sheets/CRM/outreach. +tests (**609 total across
  36 files**).

## Sprint 13a — Lead business-entity restoration
- Restored the domain `Lead` as the **complete immutable business entity** without turning it into a
  source-specific dataclass. `Lead` now has two immutable layers: the existing **typed core** (identity +
  qualification-relevant fields) plus an additive **`attributes`** map of **canonical business
  attributes** (first_name, last_name, job_started, connections, company_linkedin_url, employee_count,
  founded_year, specialities). `attributes` is frozen (a read-only `MappingProxyType`, set via
  `object.__setattr__` in `__post_init__`) and only ever holds registered keys with non-empty values.
- New leaf registry `pipeline/business_attributes.py` (`BUSINESS_ATTRIBUTE_KEYS` + `normalize_attributes`)
  is the single source-agnostic vocabulary; adapters normalize their source columns **into** these keys,
  the domain never sees a source column name (mirrors the `priority_policy` leaf pattern). Free-form keys
  are dropped; unknown/empty values stay unknown (never invented).
- `vayne_adapter` now **preserves** the business attributes it previously discarded (added `_ATTR_ALIASES`
  + `_extract_attributes`) and **disambiguates** company data: `Lead.company_url` resolves to the company
  **website** only, while the company LinkedIn URL is captured as the `company_linkedin_url` attribute —
  the canonical schema needs both, without duplication. `company_size` keeps the size band; a numeric
  head-count is captured as `employee_count`.
- Immutability, `LeadBatch` immutability, and workspace persistence are preserved: `Lead.to_dict` now
  emits `attributes` as a plain dict (replacing `asdict`, which can't serialize a mappingproxy);
  `from_dict` back-fills `attributes={}`. Old LeadBatch/Lead JSON still loads. **Qualification is
  unchanged** — `qualification_mapper` still projects only the typed core into the scoring engine;
  attributes never enter scoring. No change to the Qualification Engine, Priority logic, or export schema.
- New `tests/test_lead_business_attributes.py` (11 tests: registry filtering, core+attribute immutability,
  round-trip, backward compat, Vayne import preservation, website/company-linkedin split, unknown-stays-
  unknown, qualification compatibility). +tests (**584 total across 35 files**).

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
