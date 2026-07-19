# Changelog — GTM Intelligence Platform

High-level, human-readable history. Grouped by phase, newest first. This is a summary, not a
commit log; see git history for detail and `docs/PROJECT_STATE.md` for current status.

## Sprint 5.6 — Two-area navigation (app.py becomes the home page)
- `app.py` is now the Streamlit entrypoint + home landing page (`st.navigation`: **Home / ICP
  Workspace / Run Campaign**): explains the journey, shows ICP-library counts and environment
  status (Anthropic/Vayne/storage), and links the two wizards.
- The former one-screen "upload ICP PDF + CSV → score" flow was retired as a separate screen; the
  same legacy ICP-text path lives in **Run Campaign** (import the PDF in step 1, upload the CSV in
  step 2) — ADR-012 preserved. Matches the two-area navigation in `docs/product/ICP_WORKSPACE_UX.md` §1.

## Sprint 2A (Release 0.5) — Approval gate + Generated-ICP → Engine bridge
- **Human approval act** (`pipeline/icp_approval.py`): IQS-gated Draft → Approved transition —
  blocked on IQS errors, requires explicit acknowledgment of IQS warnings, recorded in the ICP's
  history (who/when/what was acknowledged). Surfaced in Workspace step 3 ("Approve & save" vs
  "Save as Draft") and as a remedy panel in Run Campaign step 1 for stored drafts.
- **Generated-ICP → Engine bridge:** `scoring.score_leads` gains an additive `profile=` parameter;
  `campaign.load_icp_for_scoring` adapts an Approved, IQS-valid generated ICP via
  `icp_adapter.to_engine_profile` (previously orphaned) and scores through the structured profile
  with `GeneratedICP.to_markdown()` as semantic context. Run Campaign now **refuses unapproved
  generated ICPs** (PRD §1 / IQS §10); PDF imports keep the legacy text path (ADR-012).
- **`GeneratedICP.from_dict` / `from_json`** deserializers (persistence round-trip) +
  `icp_library.load_generated` / `approve_entry` / `is_ready_for_qualification`. +13 tests (295
  total).

## Sprints 5.2–5.5 — Connected UI journey, campaign pipeline, durable storage
- **5.2 ICP Workspace wizard:** `pages/1_Business_Knowledge_Review.py` became a guided 3-step
  wizard (upload materials → review & approve candidates → generate Draft ICP).
- **5.3 Run Campaign pipeline:** `pages/2_Run_Campaign.py` (select ICP → get leads → score →
  export) over new modules `icp_library`, `vayne` (URL check + credit-gated scrape, mock offline),
  `search_criteria` (Sales-Nav filter suggestions), `campaign` (orchestration glue + persisted
  artifacts).
- **5.5 Pluggable storage + deployment:** `pipeline/storage.py` (local filesystem / OCI Object
  Storage), containerisation (Dockerfile, docker-compose, Caddy TLS + shared password), and
  `docs/DEPLOYMENT.md` (Oracle Cloud runbook).

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
