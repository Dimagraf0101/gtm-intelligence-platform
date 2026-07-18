# Changelog — GTM Intelligence Platform

High-level, human-readable history. Grouped by phase, newest first. This is a summary, not a
commit log; see git history for detail and **`docs/REPOSITORY_STATUS.md`** for current status.

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
