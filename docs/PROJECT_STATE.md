> **STATUS: ARCHIVED — NOT AUTHORITATIVE**
> Retained for history; do not treat as current architecture or status.
> REPLACED BY: docs/REPOSITORY_STATUS.md
> (Archived in the Sprint 7.3 documentation audit. See docs/README.md for the authority map.)

# Project State — GTM Intelligence Platform

**Authoritative current snapshot.** Updated at Sprint 5.1. This file is the fastest way to see what
is real *today* versus what is designed or planned. Where a design doc (PRD/UX/IQS) describes a
target, this file records the actual implementation status. Verified against the codebase, not
against prior sprint reports.

Companion: `docs/ARCHITECTURE.md` (layers), `docs/ROADMAP.md` (what's next),
`docs/PRODUCT_CONSTITUTION.md` (principles).

---

## The platform is two subsystems

1. **Lead Qualification + Workbook Export** — the only **fully integrated, runnable** product.
   Upload an ICP PDF + a lead CSV → score every lead → ranked, explainable results → XLSX/CSV export.
2. **ICP Workspace** (the ICP-generation subsystem) — a set of **built-and-tested backend modules**
   plus, as of Sprint 5.1, one **Business Knowledge Review** page. It is **not yet a connected
   end-to-end product**: there is no AI Interview, no Approval, and no bridge from a generated ICP
   into the Qualification Engine.

**Architectural principle (finalized):** **Business Knowledge is the single Source of Truth.**
A Generated ICP is a *derived projection* of Business Knowledge. Curation and (future) interview
operate on Business Knowledge; the ICP is regenerated from it.

---

## Status by capability

| Capability | Implemented | Integrated (runnable UI) | Tested |
|---|---|---|---|
| Lead Qualification (ICP PDF + CSV → scored leads) | ✅ | ✅ `app.py` | ✅ |
| Workbook Export (3-sheet XLSX / CSV) | ✅ | ✅ `app.py` | ✅ |
| Document extraction (PDF/DOCX/PPTX/TXT/MD) | ✅ | ✅ via BK Review page | ✅ |
| Source Package (merge/dedupe/budget) | ✅ | ✅ via BK Review page | ✅ |
| Business Knowledge extraction (AI proposals → Python-validated knowledge) | ✅ | ✅ via BK Review page | ✅ (mock + 2 real pilots) |
| Knowledge Gap detection | ✅ | ✅ via BK Review page | ✅ |
| **Business Knowledge Review Workspace** (browse/filter/search, confirm/reject/edit/add/merge, resolve conflicts, summary) | ✅ **(Sprint 5.1)** | ✅ `pages/1_Business_Knowledge_Review.py` | ✅ |
| Draft ICP generation (derived from Business Knowledge) | ✅ | ✅ read-only, via BK Review page button | ✅ (mock + 1 real pilot) |
| IQS validation | ✅ | ✅ (runs on the draft) | ✅ |
| Generator → Engine adapter (`GeneratedICP → ICPProfile`) | ✅ | ❌ used only by tests | ✅ |
| **AI Interview** (resolve gaps/conflicts on Business Knowledge) | ❌ not implemented | ❌ | ❌ |
| **Approval workflow** (Draft → Approved) | ❌ not implemented | ❌ | ❌ |
| **Generated ICP → Qualification bridge** | ❌ not implemented | ❌ | ❌ |
| **Strategy Review** (ICP-level weight/threshold tuning) | ❌ not implemented | ❌ | ❌ |
| Persistence / ICP Library | ❌ not implemented (in-session only) | ❌ | ❌ |
| Vayne API automation / Google Sheets API / CRM | ❌ not implemented (out of scope) | ❌ | ❌ |

## Numbers (verified)

- **Test suite:** 282 offline tests across 16 files, all passing (no pytest; each `tests/test_*.py`
  self-runs). No test calls the live model — the three AI stages use mock/fake clients offline and
  have been separately validated by single real-API pilots (see `outputs/pilots/`).
- **Pipeline modules:** 18 production modules + an empty package marker.
- **Runtime UI:** `app.py` (Lead Qualification) + `pages/1_Business_Knowledge_Review.py` (Streamlit
  multipage — the second page appears in the sidebar).
- **Model:** `claude-haiku-4-5-20251001`.

## Backward compatibility

The **legacy ICP-PDF path remains the supported qualification input** (`app.py`: upload ICP PDF →
`score_leads(icp.text, …)`). It stays available until the Generated-ICP → Engine bridge is built, so
Lead Qualification is never blocked on the ICP Workspace.

## Known risks / debt (see `docs/ROADMAP.md` for sequencing)

- **Two disconnected halves:** an approved generated ICP cannot yet drive qualification (no bridge;
  `scoring.score_leads` consumes ICP *text*, not an `ICPProfile`).
- **No approval / no interview:** a Draft ICP cannot yet be responsibly finished or approved.
- **`temporal_context` is not persisted** on knowledge items; the draft generator re-derives
  current-vs-historical heuristically.
- **No deserializers / no persistence:** the review workspace is in-session only; a refresh loses
  curation.
- **Triplicated LLM-client scaffolding** across `scoring`, `knowledge_extractor`,
  `icp_draft_generator` (3× `MODEL`, parallel cache/parse helpers).
- **Real-API behavior is pilot-verified only**, not covered by the automated suite.
- Minor: dead Vayne config in `config.py`; unused `requests` dependency; product-name drift being
  reconciled by this sprint.
