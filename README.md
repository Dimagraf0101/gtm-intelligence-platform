# GTM Intelligence Platform

> **Internal repository folder:** `sales-pipeline-master`
> This is only the local development folder name and is **not** the product name.

A local, human-in-the-loop platform for go-to-market teams, made of **two subsystems**:

1. **Lead Qualification + Workbook Export — the fully integrated, runnable product.** Turn an **ICP
   (PDF)** and a **lead CSV** into a **prioritized, explainable** lead list — scored locally by AI,
   with a **mandatory human review** before any outreach.
2. **ICP Workspace — built-and-tested backend + a Business Knowledge Review page.** Turn company
   materials into a standardized ICP: extract **Business Knowledge**, review/curate it, and generate
   a **derived Draft ICP**. This subsystem is **not yet a connected end-to-end product** (no AI
   Interview, no Approval, no bridge into the engine — see **What's real vs planned** below and
   `docs/PROJECT_STATE.md`).

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
| ✅ **Integrated & runnable** | Lead Qualification (ICP PDF + CSV → scored leads) + Workbook Export (`app.py`) |
| ✅ **Built, tested, surfaced (Sprint 5.1)** | Document extraction → Business Knowledge → gap detection → **Business Knowledge Review Workspace** → deterministic Draft ICP (read-only) → IQS (`pages/1_Business_Knowledge_Review.py`) |
| ✅ **Built, tested, unintegrated** | Generator→Engine adapter (`GeneratedICP → ICPProfile`) — used only by tests today |
| 🔻 **Planned (not built)** | AI Interview, Approval workflow, Generated-ICP → Qualification **bridge**, Strategy Review, persistence / ICP Library |
| 🚫 **Out of scope (for now)** | Vayne API automation, Google Sheets API, CRM, auth, hosting |

The **legacy ICP-PDF qualification path remains the supported input** and stays available until the
Generated-ICP → Engine bridge is built.

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

## Current MVP workflow

1. **Upload an ICP PDF** (e.g. one of the playbooks in `icp/`).
2. **Upload a raw Vayne CSV** export.
3. **Run qualification** — the engine extracts the ICP and scores every lead.
4. **Preview results** — a ranked table with scores, categories, reasons, and unknowns.
5. **Export CSV / XLSX** — Google-Sheets-compatible files.
6. **Human review** — a person reviews and approves leads **before** importing into Linked
   Helper or any outreach platform.

## Current active architecture (Lead Qualification)

The runtime surface of the **Lead Qualification** subsystem. (The **ICP Workspace** adds
`pipeline/{source_documents,source_package,business_knowledge,knowledge_gaps,knowledge_extractor,generated_icp,iqs_validator,icp_adapter,icp_draft_generator,knowledge_review}.py`
and the `pages/1_Business_Knowledge_Review.py` page — see `docs/PROJECT_STATE.md`.)

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI — upload → run → preview → export |
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
| `app.py`, `pipeline/`, `prompts/` | **Active MVP** — the app, the engine, the production prompt |
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

Then in the browser: upload an ICP PDF (e.g. `icp/AI.pdf`) and a raw Vayne CSV
(e.g. `data/raw/ai_raw_vayne.csv`), click **Start Qualification**, review the ranked table, and
download CSV/XLSX.

### `.env` variables

Add these to `.env` (never commit it):

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes, for real scoring** | Authenticates the Qualification Engine to the model. Without it, the app runs in offline placeholder mode. |
| `SCORE_THRESHOLD` | Optional | Legacy threshold value (default `60`). |
| `VAYNE_API_TOKEN` | Optional / legacy | Only used by the archived Vayne scraping code; not needed for the MVP. |

> Note: the shipped `.env.example` predates the MVP and lists only the legacy Vayne variables —
> add `ANTHROPIC_API_KEY` to your `.env` manually to enable real scoring.

## Security

- **Never commit or expose `.env` or any API key.** `.env` and `.venv/` are git-ignored and are
  local-only.
- Do not print or paste secret values into logs, issues, or chats.
- Rotate a key immediately if it is ever exposed.

---

## Current limitations

- **No direct Google Sheets API** yet — exports are **CSV/XLSX files** that import cleanly into
  Google Sheets.
- **No Vayne API automation** yet — you upload a raw Vayne CSV manually.
- **No production Human Review UI** yet — review currently happens on the exported file.
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
