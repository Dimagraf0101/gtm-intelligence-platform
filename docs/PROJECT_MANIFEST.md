# Project Manifest

The authoritative, concise definition of the **GTM Intelligence Platform** — what this repository is
and how to work in it. Read this together with `docs/ARCHITECTURE.md` (architecture),
`docs/PROJECT_STATE.md` (current status), and `docs/REPOSITORY_AUDIT.md` before implementing any task.

The platform has **two subsystems**: (1) **Lead Qualification + Workbook Export** — the fully
integrated runtime described below; and (2) the **ICP Workspace** — built-and-tested backend plus a
Business Knowledge Review page, **not yet a connected end-to-end product** (`docs/PROJECT_STATE.md`).
Architectural principle: **Business Knowledge is the single Source of Truth; the ICP is derived.**

*(The internal repository folder is named `sales-pipeline-master`; that is the local development
folder name, not the product name.)*

## Product goal

Turn an ICP definition (PDF) + a Vayne lead export (CSV) into **ranked, qualified B2B
leads** a non-technical user can review and export — locally, with no cloud, database,
or automation.

## Current MVP scope

A local **Streamlit** app (`app.py`) that: uploads one ICP PDF + one Vayne CSV → extracts
the ICP → scores every lead with the **Qualification Engine** → shows a ranked preview →
exports XLSX/CSV (Google-Sheets-compatible). Nothing else is in scope right now (no
scraping, no enrichment, no outreach, no CRM).

## Human Review Gate principle

The system **qualifies and ranks; it never acts.** A human reviews the scored leads
before anything leaves the tool. No outreach, message, or campaign is ever launched
automatically by this software.

## Lead Qualification runtime surface (fully integrated)

```
app.py
pipeline/__init__.py
pipeline/config.py        # loads .env, BASE_DIR
pipeline/icp_pdf.py       # PDF -> ICP text
pipeline/scoring.py       # Qualification Engine (loads prompts/scoring_system.md)
pipeline/export.py        # results -> XLSX/CSV
prompts/scoring_system.md # production scoring system prompt
requirements.txt
```

Run: `./.venv/bin/streamlit run app.py`

The **ICP Workspace** subsystem additionally provides `pages/1_Business_Knowledge_Review.py` (a
Streamlit multipage view) and `pipeline/{source_documents,source_package,business_knowledge,
knowledge_gaps,knowledge_extractor,generated_icp,iqs_validator,icp_adapter,icp_draft_generator,
knowledge_review}.py` + `prompts/{business_knowledge_system,icp_draft_system}.md`. These are built and
tested but not yet a connected end-to-end product — see `docs/PROJECT_STATE.md`.

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
