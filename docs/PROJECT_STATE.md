# Project State — GTM Intelligence Platform

**Authoritative current snapshot.** Updated after Sprint 2A (Release 0.5: approval gate +
Generated-ICP → Engine bridge; follows Sprints 5.2–5.5: Workspace wizard, Run Campaign, ICP
library, pluggable storage, containerised deployment). This file is the fastest way to see what
is real *today* versus what is designed or planned. Where a design doc (PRD/UX/IQS) describes a
target, this file records the actual implementation status. Verified against the codebase, not
against prior sprint reports.

Companion: `docs/ARCHITECTURE.md` (layers), `docs/ROADMAP.md` (what's next),
`docs/PRODUCT_CONSTITUTION.md` (principles), `docs/DEPLOYMENT.md` (cloud overlay).

---

## The platform is two subsystems

1. **Lead Qualification + Workbook Export** — fully integrated and runnable via the **Run
   Campaign** wizard (`pages/2_Run_Campaign.py`): select a library ICP (or import an ICP PDF —
   the legacy path) → get leads (Vayne scrape, credit-gated, or CSV upload) → score → review →
   export/persist artifacts. `app.py` is the entrypoint: `st.navigation` + a home landing page
   (Sprint 5.6); the former standalone upload-PDF+CSV screen was folded into Run Campaign.
2. **ICP Workspace** (the ICP-generation subsystem) — now a **connected product journey**: a 3-step
   wizard (`pages/1_Business_Knowledge_Review.py`: upload materials → review & approve knowledge →
   generate a Draft ICP), the IQS-gated **human approval act** (Sprint 2A,
   `pipeline/icp_approval.py`), a durable **ICP library** (`pipeline/icp_library.py`, pluggable
   local/OCI storage), and the **Generated-ICP → Engine bridge**: an Approved, IQS-valid generated
   ICP is adapted (`icp_adapter.to_engine_profile`) into the structured profile the engine scores
   with (`scoring.score_leads(profile=…)`). **Still missing:** AI Interview, Strategy Review, and
   Business-Knowledge persistence (curation is in-session only).

**Architectural principle (finalized):** **Business Knowledge is the single Source of Truth.**
A Generated ICP is a *derived projection* of Business Knowledge. Curation and (future) interview
operate on Business Knowledge; the ICP is regenerated from it.

---

## Status by capability

| Capability | Implemented | Integrated (runnable UI) | Tested |
|---|---|---|---|
| Lead Qualification (ICP + leads → scored leads) | ✅ | ✅ Run Campaign (PDF import + CSV upload = legacy path) | ✅ |
| Workbook Export (3-sheet XLSX / CSV) | ✅ | ✅ Run Campaign step 3 | ✅ |
| Document extraction (PDF/DOCX/PPTX/TXT/MD) | ✅ | ✅ via BK Review page | ✅ |
| Source Package (merge/dedupe/budget) | ✅ | ✅ via BK Review page | ✅ |
| Business Knowledge extraction (AI proposals → Python-validated knowledge) | ✅ | ✅ via BK Review page | ✅ (mock + 2 real pilots) |
| Knowledge Gap detection | ✅ | ✅ via BK Review page | ✅ |
| **Business Knowledge Review Workspace** (browse/filter/search, confirm/reject/edit/add/merge, resolve conflicts, summary) | ✅ **(Sprint 5.1)** | ✅ `pages/1_Business_Knowledge_Review.py` | ✅ |
| Draft ICP generation (derived from Business Knowledge) | ✅ | ✅ read-only, via Workspace wizard | ✅ (mock + 1 real pilot) |
| IQS validation | ✅ | ✅ (runs on the draft) | ✅ |
| **Approval workflow** (IQS-gated Draft → Approved; human act recorded in history) | ✅ **(Sprint 2A)** | ✅ Workspace step 3 + Run Campaign step 1 | ✅ |
| **Generated ICP → Qualification bridge** (`icp_adapter` → `score_leads(profile=…)`) | ✅ **(Sprint 2A)** | ✅ Run Campaign (Approved ICPs only) | ✅ |
| ICP Library (durable, storage-backed; generated ICPs + PDF imports) | ✅ (Sprint 5.3/5.5) | ✅ both pages | ✅ (Sprint 2A, partial) |
| ICP Workspace wizard (upload → review & approve → generate) | ✅ (Sprint 5.2) | ✅ `pages/1_Business_Knowledge_Review.py` | ❌ (view layer) |
| Run Campaign wizard (select ICP → get leads → score → export) | ✅ (Sprint 5.3) | ✅ `pages/2_Run_Campaign.py` | ❌ (view layer) |
| Vayne lead scraping (URL check + credit-gated scrape; mock offline) | ✅ (Sprint 5.3) | ✅ Run Campaign step 2 | ❌ |
| Sales-Navigator criteria suggestion (deterministic + optional AI refine) | ✅ (Sprint 5.3) | ✅ Run Campaign step 2 | ❌ |
| Pluggable storage (local filesystem / OCI Object Storage) + deployment | ✅ (Sprint 5.5) | ✅ (library + campaign artifacts) | ❌ |
| **AI Interview** (resolve gaps/conflicts on Business Knowledge) | ❌ not implemented | ❌ | ❌ |
| **Strategy Review** (ICP-level weight/threshold tuning) | ❌ not implemented | ❌ | ❌ |
| Business-Knowledge persistence (curation survives refresh) | ❌ not implemented (in-session only) | ❌ | ❌ |
| Google Sheets API / CRM | ❌ not implemented (out of scope) | ❌ | ❌ |

## Numbers (verified)

- **Test suite:** 295 offline tests across 17 files (no pytest; each `tests/test_*.py` self-runs).
  Four `test_icp_profile.py` tests read the sample ICP PDFs under `icp/` and require them locally.
  No test calls the live model — the AI stages use mock/fake clients offline and have been
  separately validated by single real-API pilots (see `outputs/pilots/`).
- **Pipeline modules:** 24 production modules + an empty package marker.
- **Runtime UI:** `app.py` (entrypoint — `st.navigation` + home landing page) +
  `pages/1_Business_Knowledge_Review.py` (ICP Workspace wizard) + `pages/2_Run_Campaign.py`
  (Run Campaign wizard).
- **Model:** `claude-haiku-4-5-20251001`.

## Backward compatibility

The **legacy ICP-PDF path remains a supported qualification input** (ADR-012): import the PDF into
the library in Run Campaign step 1 — it is scored exactly as before (`score_leads(icp_text, …)`).
Sprint 5.6 retired only the *separate screen* (`app.py` is now the home page); the ICP-text scoring
path is unchanged. The Sprint 2A bridge is **additive** — Approved generated ICPs go through the
structured profile; PDFs keep the text path; Lead Qualification is never blocked on the Workspace.

## Known risks / debt (see `docs/ROADMAP.md` for sequencing)

- **No AI Interview / Strategy Review:** gaps and conflicts are resolved manually in the Workspace;
  per-ICP strategy (weights/thresholds) cannot be tuned without regenerating.
- **`temporal_context` is not persisted** on knowledge items; the draft generator re-derives
  current-vs-historical heuristically.
- **Business Knowledge is not persisted:** the review workspace is in-session only; a refresh loses
  curation. (`GeneratedICP` now round-trips via `from_dict`/`from_json` — Sprint 2A; a
  `BusinessKnowledge` deserializer is still missing.)
- **No automated tests for most of the Sprint 5.2–5.5 surface:** `campaign`/`icp_library` gained
  partial coverage in Sprint 2A; `vayne`, `storage`, `search_criteria`, and both pages have none.
- **Quadruplicated LLM-client scaffolding** across `scoring`, `knowledge_extractor`,
  `icp_draft_generator`, `search_criteria` (repeated `MODEL` constants, cache/parse helpers).
- **Real-API behavior is pilot-verified only**, not covered by the automated suite.
- **Single-instance UI state:** the deployed app must not run replicated (wizard state is
  per-browser-session; only the library/artifacts are shared via storage).
