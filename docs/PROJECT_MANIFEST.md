# Project Manifest

The authoritative, concise definition of the **GTM Intelligence Platform** — what this repository is
and how to work in it. Read this together with `docs/ARCHITECTURE.md` (architecture),
`docs/PROJECT_STATE.md` (current status), and `docs/REPOSITORY_AUDIT.md` before implementing any task.

The platform has **two subsystems**: (1) **Lead Qualification + Workbook Export** — the fully
integrated runtime (the **Run Campaign** wizard); and (2) the **ICP Workspace** — a connected
journey (materials → Business Knowledge review → Draft ICP → IQS → **human approval** → ICP
library), bridged into qualification since Sprint 2A. Still missing: AI Interview, Strategy
Review, Business-Knowledge persistence (`docs/PROJECT_STATE.md`).
Architectural principle: **Business Knowledge is the single Source of Truth; the ICP is derived.**

*(The internal repository folder is named `sales-pipeline-master`; that is the local development
folder name, not the product name.)*

## Product goal

Turn an ICP definition (PDF) + a Vayne lead export (CSV) into **ranked, qualified B2B
leads** a non-technical user can review and export — locally, with no cloud, database,
or automation.

## Current MVP scope

A **Streamlit** app with two working areas over one engine (`app.py` is the entrypoint —
`st.navigation` + home landing page): the **ICP Workspace** wizard (materials → Business Knowledge
review → Draft ICP → IQS → human approval → ICP library) and **Run Campaign** (select a library
ICP or import an ICP PDF → get leads via credit-gated Vayne scrape or CSV upload → score with the
**Qualification Engine** → ranked preview → XLSX/CSV/report export, Google-Sheets-compatible).
Runs locally or containerised (`docs/DEPLOYMENT.md`). Still out of scope: enrichment, outreach,
CRM, Google Sheets API (see `docs/PROJECT_STATE.md`).

## Human Review Gate principle

The system **qualifies and ranks; it never acts.** A human reviews the scored leads
before anything leaves the tool. No outreach, message, or campaign is ever launched
automatically by this software.

## Lead Qualification runtime surface (fully integrated)

```
app.py                       # entrypoint: st.navigation + home landing page
pages/2_Run_Campaign.py      # select ICP -> get leads -> score -> export
pipeline/__init__.py
pipeline/config.py           # loads .env, BASE_DIR, storage/Vayne settings
pipeline/icp_pdf.py          # PDF -> ICP text
pipeline/scoring.py          # Qualification Engine (loads prompts/scoring_system.md)
pipeline/export.py           # results -> XLSX/CSV
pipeline/campaign.py         # campaign glue + Generated-ICP -> Engine bridge
pipeline/icp_library.py      # durable ICP library (storage-backed)
pipeline/vayne.py            # Vayne scraping client (mock offline)
pipeline/search_criteria.py  # Sales-Navigator filter suggestions
pipeline/storage.py          # local filesystem / OCI Object Storage
prompts/scoring_system.md    # production scoring system prompt
requirements.txt
```

Run: `./.venv/bin/streamlit run app.py`

The **ICP Workspace** subsystem additionally provides `pages/1_Business_Knowledge_Review.py` (the
3-step wizard) and `pipeline/{source_documents,source_package,business_knowledge,knowledge_gaps,
knowledge_extractor,generated_icp,iqs_validator,icp_adapter,icp_approval,icp_draft_generator,
knowledge_review}.py` + `prompts/{business_knowledge_system,icp_draft_system}.md`. Since Sprint 2A
an **Approved** generated ICP qualifies leads through the engine bridge; AI Interview, Strategy
Review, and Business-Knowledge persistence remain open — see `docs/PROJECT_STATE.md`.

## Authoritative input locations

| Purpose | Location |
|---|---|
| ICP definitions (reference PDFs) | `icp/` |
| Raw, unscored Vayne exports | `data/raw/` |
| Scoring rubric reference | `data/reference/Lead_Scoring_Guide.xlsx` |
| Production prompt | `prompts/scoring_system.md` |

The MVP itself takes **user uploads** at runtime — it does not read repo data files.

## Raw data location

`data/raw/` only (currently `fintech_raw_vayne.csv`, `ai_raw_vayne.csv`). Raw exports are
**not regenerable** without re-scraping Vayne. Keep them separate from any scored output.

## Benchmark status

`data/benchmarks/legacy/` holds **historical manual benchmarks that are NOT validated
ground truth** and require re-baselining (see that folder's `README.md`). They must never
be loaded by production code and must not be used as accuracy labels without cleaning.

## Output locations

| Output | Location |
|---|---|
| Pilot run artifacts | `outputs/pilots/` |
| MVP-produced exports | `outputs/exports/` |
| Logs | `outputs/logs/` |

## Files/folders Claude Code must ignore unless explicitly requested

- `archive/**` — legacy pipeline, old prompts, old Claude commands, old tooling, old
  campaign outputs, superseded pilot v1. **Never an active implementation source.**
- `data/benchmarks/legacy/**` — not ground truth.
- `.venv/`, `.env`, `__pycache__/`, `.DS_Store` — local/secret/machine-only.

## Current model

`claude-haiku-4-5-20251001` (single constant `MODEL` in `pipeline/scoring.py`; the v2
pilot uses the same).

## Standing rules

- **ICP-specific rubrics override generic scoring guides.** If an ICP defines its own
  dimensions/weights/thresholds/dealbreakers (e.g. the FinTech playbook), use *that*
  rubric — do not impose the generic 5-dimension model.
- **Historical manual benchmarks are not ground truth** (see Benchmark status).
- **No outreach is ever launched automatically** (Human Review Gate).
- Production code uses **explicit configured paths**, never fuzzy filename discovery, and
  never auto-selects among multiple benchmarks.
- Never read or print secret values from `.env`.
