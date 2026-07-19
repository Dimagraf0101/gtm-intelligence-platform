# GTM Intelligence Platform

> **Internal repository folder:** `sales-pipeline-master`
> This is only the local development folder name and is **not** the product name.

A local, human-in-the-loop platform for go-to-market teams, made of **two subsystems**:

1. **Lead Qualification + Workbook Export — the Run Campaign wizard.** Turn an **ICP** and
   **leads** (Vayne scrape or CSV upload) into a **prioritized, explainable** lead list — scored
   by AI, decided by Python, with a **mandatory human review** before any outreach.
2. **ICP Workspace — a connected creation journey.** Turn company materials into a standardized
   ICP: extract **Business Knowledge**, review/curate it, generate a **derived Draft ICP**,
   validate it against **IQS**, and **approve** it (a human act) — the approved ICP then drives
   qualification through the engine bridge. Still missing: AI Interview, Strategy Review,
   Business-Knowledge persistence (see **What's real vs planned** and `docs/PROJECT_STATE.md`).

**Architectural principle (finalized):** **Business Knowledge is the single Source of Truth**; a
Generated ICP is a *derived projection* of it. Curation and the (planned) AI Interview operate on
Business Knowledge, not on the ICP.

This repository has moved on from the older Claude-Code / Vayne automation. The current product is a
small, local **Streamlit** app plus a reusable **Qualification Engine** and the ICP Workspace
backend. Historical code is preserved (see [Repository structure](#repository-structure)) but is no
longer the main workflow.

## What's real vs planned (read this first)

| Status | Capability |
|---|---|
| ✅ **Integrated & runnable** | **Run Campaign** (select/import ICP → Vayne scrape or CSV upload → score → review → export/persist) — `pages/2_Run_Campaign.py` |
| ✅ **Integrated & runnable** | **ICP Workspace wizard** (materials → Business Knowledge review → Draft ICP → IQS → **human approval** → ICP library) — `pages/1_Business_Knowledge_Review.py` |
| ✅ **Integrated (Sprint 2A)** | IQS-gated **Approval** + **Generated-ICP → Engine bridge** (`icp_approval`, `icp_adapter` → `score_leads(profile=…)`); Run Campaign refuses unapproved generated ICPs |
| ✅ **Integrated (Sprint 5.5/5.6)** | Durable ICP library + pluggable storage (local / OCI), containerised deployment, two-area navigation with a home page (`app.py`) |
| 🔻 **Planned (not built)** | AI Interview, Strategy Review, Business-Knowledge persistence (curation is in-session only) |
| 🚫 **Out of scope (for now)** | Google Sheets API, CRM |

The **legacy ICP-PDF qualification path remains supported** (ADR-012): import the PDF in Run
Campaign step 1 — it is scored as ICP text, exactly as before.

---

## Product purpose

Turn an ICP PDF + a raw Vayne lead export into a **ranked, explainable** list of qualified leads.
Every lead gets a 0–100 score, a priority category, per-dimension reasoning, and an explicit
record of what was **unknown** — so a human can prioritize outreach with full context. The
software **qualifies and ranks; it never contacts anyone.**

## Core principle — AI recommends, humans approve

There is a **Human Review Gate** between scoring and outreach. The app produces a scored,
sorted list; a person reviews it and decides who to contact. **No outreach is launched
automatically** by this software — not to Linked Helper, not to any platform.

---

## Current workflow (two areas, one journey)

**🧭 ICP Workspace** (define *who's a good fit*):
1. **Upload company materials** (PDF/DOCX/PPTX/TXT/MD) — each file is evidence with attribution.
2. **Review & approve** the extracted Business Knowledge (resolve conflicts, edit, add facts).
3. **Generate a Draft ICP** → IQS validation → **approve it** (human act; warnings must be
   acknowledged) → save to the ICP library.

**🚀 Run Campaign** (find and rank them):
1. **Select an ICP** from the library (approved generated ICP — or import an existing ICP PDF,
   the legacy path).
2. **Get leads** — scrape via Vayne (URL check first; an explicit credit confirmation gates the
   spend) or upload a raw Vayne CSV.
3. **Score, review, export** — ranked table with scores, categories, reasons, and unknowns;
   CSV / XLSX / report downloads, optionally persisted to storage.
4. **Human review** — a person reviews and approves leads **before** importing into Linked
   Helper or any outreach platform.

## Current active architecture (Lead Qualification)

The runtime surface of the **Lead Qualification** subsystem. (The **ICP Workspace** adds
`pipeline/{source_documents,source_package,business_knowledge,knowledge_gaps,knowledge_extractor,generated_icp,iqs_validator,icp_adapter,icp_draft_generator,knowledge_review}.py`
and the `pages/1_Business_Knowledge_Review.py` page — see `docs/PROJECT_STATE.md`.)

| File | Responsibility |
|---|---|
| `app.py` | Streamlit entrypoint — `st.navigation` (Home / ICP Workspace / Run Campaign) + home landing page |
| `pages/2_Run_Campaign.py` | Run Campaign wizard — select ICP → get leads → score → export |
| `pipeline/campaign.py` | Campaign glue + the Generated-ICP → Engine bridge |
| `pipeline/icp_library.py`, `pipeline/storage.py` | Durable ICP library over local / OCI object storage |
| `pipeline/vayne.py`, `pipeline/search_criteria.py` | Vayne scraping client + Sales-Nav filter suggestions |
| `pipeline/icp_pdf.py` | Extract and clean ICP text from the uploaded PDF |
| `pipeline/scoring.py` | **Qualification Engine** — prompts the model, validates output, and computes the final score & category in Python |
| `pipeline/export.py` | Project results into the canonical columns → CSV / XLSX |
| `pipeline/config.py` | Load `.env`, resolve base paths |
| `prompts/scoring_system.md` | Production scoring system prompt (no prompt strings hard-coded in Python) |

**Division of labor:** the model returns per-dimension points, evidence, a hard-dealbreaker flag,
a reason, unknowns, and confidence. **Python** validates/clamps everything, sums the score, maps
it to a category, and applies dealbreakers — so the verdict is deterministic and auditable.

## Current model

`claude-haiku-4-5-20251001` (a single `MODEL` constant in `pipeline/scoring.py`). When no
`ANTHROPIC_API_KEY` is present, the app falls back to a local placeholder scorer and shows an
"offline mode" banner, so the full workflow is runnable without a key (the numbers are
placeholders until a key is added).

---

## Repository structure

| Path | What it holds |
|---|---|
| `app.py`, `pages/`, `pipeline/`, `prompts/` | **Active app** — entrypoint/home, the two wizards, the engine, the prompts |
| `icp/` | Sample/reference **ICP PDFs** (FinTech, Ecom, WordPress, Xamarin, AI) — active reference assets |
| `data/raw/` | **Raw, unscored Vayne exports** only (`fintech_raw_vayne.csv`, `ai_raw_vayne.csv`). Not regenerable without re-scraping Vayne — keep separate from any scored output |
| `data/benchmarks/legacy/` | **Historical manual benchmarks — NOT validated ground truth** (see the warning below and that folder's `README.md`) |
| `data/reference/` | Reference material, e.g. `Lead_Scoring_Guide.xlsx` (a legacy rubric guide) |
| `data/samples/` | Small curated CSVs for demos/tests (reserved) |
| `outputs/pilots/` | Pilot run artifacts (comparison / results / summary per run) |
| `outputs/exports/` | MVP-produced exports (reserved) |
| `outputs/logs/` | Run logs (reserved) |
| `scripts/` | Current evaluation tooling (the corrected FinTech pilot v2) |
| `docs/` | Project documentation — `PROJECT_MANIFEST.md`, `REPOSITORY_AUDIT.md`, `CLEANUP_REPORT.md`, specs |
| `archive/` | **Preserved historical code — never an active source.** Legacy scrape/post-enrich/segment pipeline, old Claude-Code commands, old tooling, superseded pilot v1, other-campaign outputs |

> The legacy LinkedIn-scraping pipeline (scrape / post-enrich / segment) is **not** part of the
> current workflow. It lives under `archive/legacy_pipeline/` for historical reference only.

## Benchmark warning

The files under `data/benchmarks/legacy/` are **historical, manually-produced scored outputs**
and are **not validated ground truth**. A manual review found that the majority of
model-vs-benchmark disagreements were *pollution in the benchmark* (companies the ICP explicitly
excludes). Do **not** treat these files as correct labels or compute pass/fail accuracy against
them without first re-baselining. Active production code never loads them.

---

## Local setup and launch (macOS)

Requires **Python 3.11+**.

```bash
cd sales-pipeline-master

# 1. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables (see below)
cp .env.example .env      # then edit .env

# 4. Launch the app
streamlit run app.py
# opens http://localhost:8501
```

Then in the browser: open **🚀 Run Campaign**, import an ICP PDF (e.g. `icp/AI.pdf`) in step 1,
upload a raw Vayne CSV (e.g. `data/raw/ai_raw_vayne.csv`) in step 2, score in step 3, review the
ranked table, and download CSV/XLSX. Or start in **🧭 ICP Workspace** to create and approve an ICP
from your own materials first.

### `.env` variables

Add these to `.env` (never commit it):

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes, for real scoring/extraction** | Authenticates the engine to the model. Without it, the app runs with clearly-labelled offline mocks. |
| `VAYNE_API_TOKEN` | Optional | Enables live Vayne scraping in Run Campaign (credit-gated). Without it, scraping uses the offline mock. |
| `SCORE_THRESHOLD` | Optional | Default qualifying threshold (default `60`). |
| `STORAGE_BACKEND` | Optional | `local` (default, under `data/`) or `oci` — see `docs/DEPLOYMENT.md` for the OCI/deployment variables. |

> See `.env.example` for the full list, including the deployment (Caddy/TLS) variables.

## Security

- **Never commit or expose `.env` or any API key.** `.env` and `.venv/` are git-ignored and are
  local-only.
- Do not print or paste secret values into logs, issues, or chats.
- Rotate a key immediately if it is ever exposed.

---

## Current limitations

- **No direct Google Sheets API** yet — exports are **CSV/XLSX files** that import cleanly into
  Google Sheets.
- **No production Human Review UI** yet — review currently happens on the ranked table and the
  exported file.
- **Business Knowledge curation is in-session only** — a browser refresh loses unsaved review
  state (the ICP library itself is durable).
- **Qualification rules are still being calibrated** (dealbreaker discipline, buyer-persona
  scoring, and ICP subsegment boundaries are being tuned).
- Exports are CSV/XLSX compatible with Google Sheets (not a live Sheets integration).

## Current status

- ✅ MVP **technically validated** end-to-end (upload → score → preview → export).
- ✅ A **real-API pilot** (FinTech, `claude-haiku-4-5-20251001`) has been completed and reviewed.
- ✅ Repository **cleaned and reorganized** (see `docs/CLEANUP_REPORT.md`).
- 🔄 **Qualification calibration in progress** — scoring rules are being refined before scale-up.

---

## Documentation

- `docs/PROJECT_STATE.md` — **current snapshot** (implemented / integrated / tested / planned + test count). Start here.
- `docs/ROADMAP.md` — current roadmap (Release 0.5 + Phase 2 order; features vs technical debt).
- `docs/PRODUCT_CONSTITUTION.md` — highest-level, immutable principles every Sprint must follow.
- `docs/ARCHITECTURE.md` — authoritative architecture (full layered system + implemented/planned).
- `docs/product/ICP_WORKSPACE_PRD.md`, `docs/product/ICP_WORKSPACE_UX.md` — the ICP Workspace product & UX.
- `docs/iqs/IQS_v1.0.md`, `docs/iqs/ICP_PROFILE_SCHEMA.md` — the ICP Qualification Standard and profile schema.
- `docs/DECISIONS.md` — architecture decision records (ADRs).
- `docs/CHANGELOG.md` — high-level history. `docs/PROJECT_MANIFEST.md` — project definition & standing rules.
- `docs/REPOSITORY_AUDIT.md`, `docs/CLEANUP_REPORT.md` — historical file classification & cleanup record.

## License

MIT
