# Repository Status — GTM Intelligence Platform

**Status:** CURRENT (code-grounded). Last reconciled: Sprint 14 (Google Sheets publisher).
**Authority:** This document describes *what exists today*. The architecture it must comply with is
**`docs/ARCHITECTURE_BASELINE_v1.0.md`** (the frozen constitution). Where a historical document
disagrees with this file about current state, this file is correct; where anything disagrees with the
Baseline about architecture rules, the Baseline wins. See `docs/README.md` for the authority map.

---

## Completed capabilities (end to end, offline-testable)

- **Company materials → Business Knowledge** — deterministic ingest + AI extraction with Python
  validation, provenance, conflicts, and temporal context.
- **Knowledge Review, Knowledge Interview, Strategy Review, Approval** — the full human-in-the-loop
  authoring pipeline for a hypothesis's ICP.
- **General ICP lifecycle** — generate a company-wide (industry-agnostic) ICP from company knowledge
  only; immutable, append-only versions on `CompanyWorkspace`; reviewable in the UI.
- **Approved ICP → Qualification** — the Generator→Qualification bridge converts an Approved ICP to
  the engine profile via `icp_adapter` and qualifies leads; the legacy PDF path is unchanged.
- **Adapted ICP per hypothesis** — each Market Hypothesis adapts the General ICP (recording the
  source General ICP's `ArtifactIdentity`).
- **Search Strategy per hypothesis** — a hypothesis-owned, versioned, immutable artifact **derived
  from that hypothesis's approved Adapted ICP** (recording its `ArtifactIdentity`). It describes
  company/person criteria, geography, signals, exclusions, and structured **LinkedIn Sales Navigator
  filter recommendations** for **manual** configuration. Filters are derived deterministically in
  Python; confidence is computed from evidence completeness. Lifecycle Draft → Reviewed → Approved →
  Archived (its own small forward-only status, **not** the ICP approval framework). It does **not**
  scrape or qualify leads and generates **no** Sales Navigator URL — the user configures Sales
  Navigator manually and pastes the resulting URL at execution time (see **Search Execution** below).
- **Lead Acquisition boundary** — a hypothesis-owned, immutable **Lead Batch** imported from a
  **Lead Source** via an **anti-corruption layer** (`vayne_adapter`). The domain (`lead_batch`) knows
  nothing about Vayne / CSV columns / Sales Navigator export — those live in the adapter. Deterministic
  parse + normalize + de-duplicate (by LinkedIn URL, else person+company) + validate + statistics; no
  scoring, qualification, ranking, or outreach. Re-importing creates a new batch (append-only). Vayne
  is one replaceable source (Apollo / Clay / ZoomInfo / manual CSV are future adapters producing the
  same domain `LeadBatch`). **A persisted Lead Batch must derive from exactly one Approved Search
  Strategy owned by the same hypothesis** — the application service (`lead_import`) resolves and
  validates ownership + Approved status and stamps immutable provenance (`derived_from_search_strategy`);
  the CSV adapter never resolves ownership or approval.
- **Qualification of a Lead Batch** — qualify an imported `LeadBatch` against **the exact Adapted ICP
  its Search Strategy was derived from** by **reusing the frozen engine** (`qualification_run` →
  `qualification_mapper` → `qualification_bridge`). Lineage resolves deterministically: Approved Adapted
  ICP → Approved Search Strategy → LeadBatch → QualifiedLeadBatch. A **newer active ICP never silently
  changes** an existing batch's qualification; the immutable `QualifiedLeadBatch` records the full chain
  (`derived_from_lead_batch`, `derived_from_search_strategy`, exact `derived_from_adapted_icp`) and
  per-lead score/decision/evidence/warnings (leads referenced by id). An Archived-formerly-Approved
  Search Strategy remains a valid historical lineage source. Cross-version requalification is deferred
  to a future ExperimentRun. Deterministic refusals (malformed/missing/cross-hypothesis references,
  disagreeing lineage, empty batch/qualifier). No scoring duplicated; no source artifact edited; no
  export.
- **Search Execution (Vayne route)** — turn an **Approved Search Strategy** into a Lead Batch
  automatically: the user configures Sales Navigator **manually**, pastes the resulting search URL, and
  the platform submits it to **Vayne** (one replaceable external `LeadSource` integration). A
  hypothesis-owned, immutable **`SearchExecution`** records one operational scraping run (not a GTM
  experiment): set-once provenance (`execution_id`, `hypothesis_id`, `derived_from_search_strategy`,
  `sales_navigator_url`, `external_job_id`, `derived_lead_batch_id`), a forward-only status
  (Draft → Submitted → Running → Completed/Failed; terminals immutable), and an append-only event log.
  The pasted URL is validated deterministically (HTTPS, LinkedIn Sales Navigator host/path, no embedded
  credentials, length) and preserved verbatim as **evidence** — the Approved Search Strategy remains the
  filter authority; the platform never builds or interprets the URL. All HTTP lives behind a single
  boundary (`integrations/vayne_client`) that knows nothing of the domain, persistence, or UI; the
  application service (`search_execution_service`) orchestrates submit → user-triggered **Refresh
  status** → on completion, download the CSV and import through the **same** `vayne_adapter` +
  `lead_import` gate the manual upload uses. Async is user-driven (no background workers/queues/webhooks;
  bounded timeouts; transient vs terminal failures separated). **Idempotent:** one completed execution
  yields **at most one** LeadBatch (`derived_lead_batch_id` set-once); the resulting batch keeps its
  authoritative `derived_from_search_strategy` provenance and records the execution id only additively
  (`derived_from_search_execution`). Vayne credentials come from `config` (`VAYNE_API_TOKEN`) and are
  never serialized, logged, or shown in the UI. The **manual CSV upload remains a supported fallback**;
  both routes produce identical Lead Batches. **Configurable retrieval (Sprint 12.1):** each execution
  requests either *all* available leads or a capped count via `SearchExecution.lead_limit: Optional[int]`
  (`None` = unlimited; a positive int = the maximum requested — never a 0/-1 sentinel). This is
  provider-independent intent; `VayneClient` alone translates it into the Vayne payload (omit the
  `limit` key = all available; `limit: N` = capped). The **requested** amount and the **imported**
  amount are tracked independently — fewer available than requested is a normal Completed, not an error.
  **Idempotent submission (Sprint 12.1.1):** each execution carries a deterministic
  `execution_fingerprint` from domain inputs only (hypothesis id + Search Strategy id + normalized URL +
  lead-limit). Before creating a new execution, `create_and_submit` resumes any **active** execution with
  the same fingerprint (returning `resumed=True`, submitting **zero** new provider orders); terminal
  executions never block, and a deliberate rerun requires an explicit `force=True`. `refresh_execution`
  and download-retry are strictly submit-free — once a provider order id exists, that execution never
  submits another. (Durable restart-resume remains manual save/load — no auto-save this sprint.)
- **Human Review (primary review workbench)** — the human-approval gate over a `QualifiedLeadBatch`.
  A hypothesis-owned, **append-only** `ReviewedLeadBatch` records immutable `LeadReviewDecision`s
  (Pending / Approved / Rejected / Skipped); re-deciding a lead **appends** a new decision (latest wins,
  history preserved) and **never mutates** the Lead or the QualifiedLead. `review_status` is the single
  workflow authority — the canonical export **Human Decision** is *derived* from it, never stored, so
  they cannot drift; a `rejection_reason` is deterministically cleared unless the status is Rejected, so
  contradictory states are unrepresentable. One deterministic projection (`review_view.ReviewRow`) joins
  Lead + QualifiedLead + decision by `lead_id` (undecided → Pending) and feeds **both** the UI and the
  exporter, with canonical sorting (priority ascending, then score descending), filtering
  (status/priority/score/industry/size/geography) and Company+Contact search. Export reuses the
  **unchanged** canonical schema via a thin adapter (`review_export`) and one additive rows-based writer
  entry point — scopes Approved-only (default) / Selected / All, as XLSX or CSV. No priority override, no
  score/business-data editing, no requalification, no outreach/CRM/Google Sheets.
  **Stabilized (Sprint 13c):** the full Import → Qualification → Review → Save → Reload → Continue →
  Export flow is executed end-to-end in tests. Score Breakdown is order-stable across a save/reload
  (the workspace serializes with `sort_keys=True`); every visible lead stays individually addressable in
  the details selector; decision inputs re-seed from persisted values after a decision or reload. Review
  decisions are **session-held until an explicit workspace save** — the review page carries its own Save
  control and warns how many decisions are unsaved (deterministic, no hidden autosave).
- **Workspace persistence** — deterministic JSON save/load of the whole `CompanyWorkspace` (schema
  v1; adapted-ICP provenance, search strategies, lead batches, search executions, qualified batches, and
  reviewed batches persist additively).
- **Typed artifact identity** — General vs Adapted ICPs are distinguishable and status-stable.

## Current module map (`pipeline/`, 42 modules + `integrations/{vayne_client, google_sheets_publisher}`)

- **Knowledge:** `source_documents`, `source_package`, `icp_pdf`, `knowledge_extractor`,
  `business_knowledge`, `knowledge_gaps`, `knowledge_review`.
- **Hypothesis & ICP authoring:** `icp_project` (CompanyWorkspace / MarketHypothesis /
  ComposedProjectKnowledge), `knowledge_interview`, `icp_draft_generator`, `generated_icp`,
  `general_icp`, `adapted_icp`, `strategy_review`, `iqs_validator`, `icp_approval`, `icp_identity`.
- **Search:** `search_strategy` (hypothesis-owned Search Strategy derived from the approved Adapted
  ICP; deterministic filter recommendations + validation + Draft→Reviewed→Approved→Archived).
- **Lead acquisition:** `lead_batch` (domain: source-agnostic `Lead` / immutable `LeadBatch` /
  `LeadSource` + validation + stats), `business_attributes` (canonical, source-agnostic business-attribute
  registry — the single vocabulary adapters normalize into), `vayne_adapter` (anti-corruption layer: CSV →
  domain leads incl. business attributes, parse/map only), and `lead_import` (application service:
  resolves + validates the **Approved** source Search Strategy, then builds/persists the batch with
  immutable provenance).
  - The domain `Lead` is the **complete immutable business entity**: a typed core (identity +
    qualification-relevant fields) plus a frozen `attributes` map of canonical business attributes
    (first_name, last_name, job_started, connections, company_linkedin_url, employee_count, founded_year,
    specialities). `company_url` is the company **website**; the company LinkedIn URL lives in
    `attributes` (no duplication). Qualification consumes only the typed core (via `qualification_mapper`);
    attributes never enter scoring. Additive + backward compatible (old JSON loads with `attributes={}`).
- **Engine boundary (ACL):** `icp_adapter`, `icp_profile`.
- **Qualification engine (reused, frozen):** `qualification_bridge`, `scoring`, `prequalification`,
  `decision`, `evidence`.
- **Operational priority policy:** `priority_policy` — the single canonical, immutable source of the
  standard operational bands (Priority 1-5 / Disqualified). The decision engine and the ICP-generation
  defaults both derive from it (leaf module; no internal imports).
- **Qualification integration:** `qualification_mapper` (domain Lead → engine Lead via the engine's
  own normalizer), `qualified_lead` (immutable `QualifiedLead` / `QualifiedLeadBatch` + stats), and
  `qualification_run` (application service: validate lineage → map → engine → append results).
- **Search execution:** `search_execution` (domain: immutable `SearchExecution` — forward-only status,
  append-only events, deterministic Sales Navigator URL validation) and `search_execution_service`
  (application service: validate lineage/ownership/URL/requester → submit via the Vayne client → refresh
  → on completion import through the existing `lead_import` gate; idempotent).
- **Integrations (ACL, HTTP-only):** `integrations/vayne_client` — the sole Vayne/HTTP boundary
  (authenticate, submit URL, read job status, retrieve CSV, translate transport errors). Knows nothing
  of the domain, persistence, or Streamlit; never constructs a LeadBatch; credentials never printed.
- **Human Review (Sprint 13b):** `lead_review` (append-only domain: immutable `LeadReviewDecision` /
  `ReviewedLeadBatch`; `review_status` is the sole workflow authority and the export "Human Decision" is
  *derived*, never stored), `review_view` (the ONE deterministic projection joining Lead + QualifiedLead +
  decision, plus canonical sort/filter/search), and `review_export` (thin adapter → canonical
  `MAIN_COLUMNS`/`AI_COLUMNS` rows; scopes Approved-only / Selected / All). Human review never mutates the
  Lead or the QualifiedLead.
- **Delivery:** `export` (canonical workbook; gained one additive rows-based entry point
  `workbook_bytes_from_rows` — schema, column names and order unchanged).
- **External publishing (ACL):** `integrations/google_sheets_publisher` — the sole Google Sheets
  boundary. Consumes canonical rows only (no domain objects), publishes three managed worksheets
  (*Leads* / *AI Details* / *Summary*) by deterministic full replacement, supports explicit Create-New
  and Update-Existing targets, validates everything before any external call, and requires explicit human
  confirmation. Service-account credentials come from the environment and are never logged or persisted.
- **Persistence & infra:** `workspace_store`, `workspace_revision`, `config`.

## Current Streamlit pages (`pages/`)

1. `1_Business_Knowledge_Review.py` — curate company / hypothesis knowledge.
2. `2_Knowledge_Interview.py` — gap-driven interview.
3. `3_Strategy_Review.py` — dimension weights + exclusion activation.
4. `4_Approval.py` — the approval gate.
5. `5_General_ICP.py` — generate/review the General ICP; save/reload the workspace.
6. `6_Market_Hypotheses.py` — create/edit/delete hypotheses; generate an Adapted ICP.
7. `7_Search_Strategy.py` — generate/review/approve a Search Strategy (Sales Navigator filter recommendations).
8. `8_Lead_Import.py` — upload a Vayne CSV → immutable Lead Batch (no scoring/qualification).
9. `9_Qualification.py` — qualify a Lead Batch against the Approved ICP → immutable Qualified Lead Batch.
10. `10_Search_Execution.py` — submit an Approved Search Strategy's manually-pasted Sales Navigator URL
    to Vayne, choose lead retrieval (all available or a capped count), refresh status, and on completion
    view the resulting Lead Batch; history shows Name · Requested · Imported · Status (manual CSV
    fallback on page 8 preserved). Secrets never shown.

11. `11_Human_Review.py` — the primary review workbench: lineage header, summary metrics + priority
    distribution, search/filters, review table with selection, bulk Approve/Reject/Skip, lead-details
    panel (business + AI + labelled audit + decision history), and Approved-only XLSX/CSV export.

Plus `app.py` — Lead Qualification (PDF **or** Approved ICP source) + workbook/CSV export.

## Persistence model

- One JSON file per `CompanyWorkspace` via `pipeline/workspace_store.py`.
- Envelope: `{"schema_version": 1, "kind": "gtm_company_workspace", "workspace": {…}}`.
- Deterministic `to_dict` / `from_dict` round-trip for the whole aggregate (company + hypothesis
  knowledge, draft/approved ICP versions, strategy decisions, approval records, active pointer,
  General ICP lineage, search strategies, lead batches, search executions, qualified batches, reviewed
  batches).
  Malformed/unsupported payloads are refused explicitly; older JSON (Sprint 6/7 onward, and pre-Sprint-12
  envelopes without `search_executions`) loads with safe defaults. No database, ORM, migration
  framework, or event bus.

## Identity model (owned solely by `pipeline/icp_identity.py`)

Three distinct, deliberately separate concepts:

- **`content_fingerprint(icp)`** — semantic content equality; excludes version *and* status.
- **`fingerprint_generated_icp(icp)`** — the legacy/approval fingerprint (content + version +
  status); its behavior is frozen and byte-for-byte stable (approvals, warning-ack ids, active-version
  resolution depend on it).
- **`ArtifactIdentity`** — the typed, status-stable identity of a specific ICP *version*, serialized
  as `<artifact_type>:<version>:<content_fingerprint>` (e.g. `general_icp:2:<cf>` vs
  `adapted_icp:2:<cf>`). Draft and Approved forms of one version share it; different type, version, or
  content differ. `icp_identity` is the sole authority; nothing else assembles it.

## Decision Engine architecture (qualification authority)

Final qualification is owned entirely by **deterministic Python** — never by the LLM and never by any
environment variable. Two parallel paths, one authoritative and one audit-only:

**Operational path (the sole final authority):**

```
LLM proposes dimension-level evidence + scores
  → deterministic Python validation (bounds, summation, coverage; decision.py)
  → operational lead score (raw score; 0 on a confirmed dealbreaker)
  → operational priority policy (priority_policy.OPERATIONAL_PRIORITY_BANDS)
  → Priority 1-5 or Disqualified (operational_priority)
  → Human Review
```

**Audit path (informational only, never a qualification decision):**

```
raw score → the ICP's own category_thresholds → internal_category → audit / debug only
```

Rules (all enforced + tested):
- **`operational_priority` is the sole final decision authority** — for qualify vs disqualify, for the
  Priority 1-5 label, for `QualifiedLead.decision`, and for operational UI + export ordering.
- The standard operational bands live **once** in `priority_policy` (immutable tuple): `90→P1, 75→P2,
  60→P3, 45→P4, 30→P5`, and **below 30 → Disqualified**. `decision._OPERATIONAL_BANDS` *is* that tuple;
  `generated_icp.standard_priority_bands()` derives the default ICP bands from it (returning a fresh,
  mutable `list[PriorityBand]` each call so callers can safely mutate their copy).
- **A score below 30 is operationally Disqualified; 30 or above remains available for ranking/review.**
- **Confirmed dealbreakers override the score** (force Disqualified, zero the operational score; raw
  score preserved for audit). **Suspected dealbreakers never auto-disqualify.**
- **`internal_category` is not a qualification decision.** It is derived from the ICP artifact's own
  `category_thresholds`, which remain an author-controlled degree of freedom (the IQS validator only
  enforces 0-100 coverage). Custom thresholds change only the audit label, never the operational verdict,
  and are never synchronized to the operational policy on load/migration.
- **`SCORE_THRESHOLD` no longer exists as an active setting** (removed in Sprint 12.0.2). Environment
  variables do not control final qualification; an old `.env` that still defines it is harmless.

## Compatibility guarantees (current)

- `ICPPortfolio` (= `CompanyWorkspace`) and `ICPProject` (= `MarketHypothesis`) aliases preserved.
- Existing PDF qualification path and `score_leads(profile=None)` unchanged.
- `icp_adapter` is the only `GeneratedICP → ICPProfile` boundary; `iqs_validator` is the only ICP
  validator; `icp_identity` is the only fingerprint/identity authority.
- Approved ICPs and approval records are immutable; pinned fingerprints are unchanged.

## Test count

**646 test functions across 38 files.** Tests are self-running (no pytest); each file exposes a
`_run()` and exits non-zero on failure. Run all with:

```
for f in tests/test_*.py; do PYTHONIOENCODING=utf-8 ./.venv/bin/python "$f"; done
```

## Next planned product phase

**Sprint 13 — Human review & export of a Qualified Lead Batch:** a review surface over an immutable
`QualifiedLeadBatch` (accept/reject decisions) and workbook/CSV export via the existing `export`
module. Deferred (per Baseline §11): ExperimentRun/comparison analytics, Google Sheets, Linked Helper,
outreach.
