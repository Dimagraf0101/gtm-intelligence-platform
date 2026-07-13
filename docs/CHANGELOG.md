# Changelog — GTM Intelligence Platform

High-level, human-readable history. Grouped by phase, newest first. This is a summary, not a
commit log; see git history for detail and `docs/PROJECT_STATE.md` for current status.

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
