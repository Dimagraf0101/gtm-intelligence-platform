# Repository Status — GTM Intelligence Platform

**Status:** CURRENT (code-grounded). Last reconciled: Sprint 9 (Search Strategy).
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
  scrape or qualify leads and generates **no** Sales Navigator URL. Vayne / lead acquisition remain
  unimplemented.
- **Workspace persistence** — deterministic JSON save/load of the whole `CompanyWorkspace` (schema
  v1; adapted-ICP provenance and search strategies persist additively).
- **Typed artifact identity** — General vs Adapted ICPs are distinguishable and status-stable.

## Current module map (`pipeline/`, 29 modules)

- **Knowledge:** `source_documents`, `source_package`, `icp_pdf`, `knowledge_extractor`,
  `business_knowledge`, `knowledge_gaps`, `knowledge_review`.
- **Hypothesis & ICP authoring:** `icp_project` (CompanyWorkspace / MarketHypothesis /
  ComposedProjectKnowledge), `knowledge_interview`, `icp_draft_generator`, `generated_icp`,
  `general_icp`, `adapted_icp`, `strategy_review`, `iqs_validator`, `icp_approval`, `icp_identity`.
- **Search:** `search_strategy` (hypothesis-owned Search Strategy derived from the approved Adapted
  ICP; deterministic filter recommendations + validation + Draft→Reviewed→Approved→Archived).
- **Engine boundary (ACL):** `icp_adapter`, `icp_profile`.
- **Qualification engine:** `qualification_bridge`, `scoring`, `prequalification`, `decision`,
  `evidence`.
- **Delivery:** `export`.
- **Persistence & infra:** `workspace_store`, `workspace_revision`, `config`.

## Current Streamlit pages (`pages/`)

1. `1_Business_Knowledge_Review.py` — curate company / hypothesis knowledge.
2. `2_Knowledge_Interview.py` — gap-driven interview.
3. `3_Strategy_Review.py` — dimension weights + exclusion activation.
4. `4_Approval.py` — the approval gate.
5. `5_General_ICP.py` — generate/review the General ICP; save/reload the workspace.
6. `6_Market_Hypotheses.py` — create/edit/delete hypotheses; generate an Adapted ICP.
7. `7_Search_Strategy.py` — generate/review/approve a Search Strategy (Sales Navigator filter recommendations).

Plus `app.py` — Lead Qualification (PDF **or** Approved ICP source) + workbook/CSV export.

## Persistence model

- One JSON file per `CompanyWorkspace` via `pipeline/workspace_store.py`.
- Envelope: `{"schema_version": 1, "kind": "gtm_company_workspace", "workspace": {…}}`.
- Deterministic `to_dict` / `from_dict` round-trip for the whole aggregate (company + hypothesis
  knowledge, draft/approved ICP versions, strategy decisions, approval records, active pointer,
  General ICP lineage). Malformed/unsupported payloads are refused explicitly; Sprint 6/7 JSON loads
  with safe defaults. No database, ORM, migration framework, or event bus.

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

## Compatibility guarantees (current)

- `ICPPortfolio` (= `CompanyWorkspace`) and `ICPProject` (= `MarketHypothesis`) aliases preserved.
- Existing PDF qualification path and `score_leads(profile=None)` unchanged.
- `icp_adapter` is the only `GeneratedICP → ICPProfile` boundary; `iqs_validator` is the only ICP
  validator; `icp_identity` is the only fingerprint/identity authority.
- Approved ICPs and approval records are immutable; pinned fingerprints are unchanged.

## Test count

**476 test functions across 28 files.** Tests are self-running (no pytest); each file exposes a
`_run()` and exits non-zero on failure. Run all with:

```
for f in tests/test_*.py; do PYTHONIOENCODING=utf-8 ./.venv/bin/python "$f"; done
```

## Next planned product phase

**Sprint 10 — Vayne / Lead Source (anti-corruption layer):** a Sales Navigator URL → Vayne request →
immutable `LeadBatch`, mirroring `icp_adapter` as the ACL. Deferred (per Baseline §11): LeadBatch,
ExperimentRun, experimentation analytics, Google Sheets, Linked Helper. Sales Navigator configuration
remains manual; the Search Strategy only recommends filters.
