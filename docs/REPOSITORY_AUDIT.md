# Repository Cleanup Audit

**Status:** Audit only — **no files were deleted, moved, renamed, or modified.**
**Scope:** `sales-pipeline-master/` (the repo), plus the parent working directory `../.claude/` where a dev launcher lives.
**Date of audit:** 2026-07-11

> Secrets are never printed in this document. `.env` and `.venv` are listed by path and category only.

---

## 0. How "active" was determined (import chain)

The current MVP is `app.py` (Streamlit) + the Qualification Engine. The **only** runtime import/dependency chain is:

```
app.py
 ├─ pipeline/icp_pdf.py      (extract_icp_from_bytes)   → pypdf
 ├─ pipeline/scoring.py      (normalize_lead, score_leads, get_client)
 │    ├─ pipeline/config.py  (BASE_DIR; loads .env)     → python-dotenv
 │    └─ reads prompts/scoring_system.md  (explicit path: BASE_DIR/"prompts"/"scoring_system.md")
 └─ pipeline/export.py       (build_dataframe, to_csv_bytes, to_xlsx_bytes)
       └─ pipeline/scoring.py (Lead, ScoringResult)     → pandas, openpyxl
```

Everything **not** in that chain is legacy, reference, test/pilot, generated, or local-only.

**Filename-matching note (per the rules):** No active application code discovers `data/`, benchmark, or pilot files by glob or ambiguous name. `app.py` consumes user uploads; `scoring.py` loads exactly one prompt by explicit path. The only fuzzy matching that exists (`"linkedin url"` column detection) lives in **legacy** `scrape.py` / `post_enrich.py` / `segment.py` and in `scoring.normalize_lead` (which matches columns of the *uploaded* CSV, not repo files). ✅ No active code reads a benchmark or pilot file.

---

## 1. Full inventory by category

Paths are relative to `sales-pipeline-master/` unless prefixed with `../`.

### Category 1 — ACTIVE_PRODUCTION (required by the MVP / engine)

| Path | Imported/used by active code? | Why this category | Action | Risk if removed |
|---|---|---|---|---|
| `app.py` | Entry point (Streamlit) | The MVP UI | **KEEP** | Critical — app cannot run |
| `pipeline/__init__.py` | Package marker | Makes `pipeline` importable | **KEEP** | Import breakage |
| `pipeline/config.py` | Imported by `scoring.py` | Loads `.env`, `BASE_DIR`, paths | **KEEP** | Engine cannot resolve paths/keys |
| `pipeline/icp_pdf.py` | Imported by `app.py` | PDF → ICP text extraction | **KEEP** | ICP upload breaks |
| `pipeline/scoring.py` | Imported by `app.py`, `export.py` | The Qualification Engine | **KEEP** | Scoring breaks |
| `pipeline/export.py` | Imported by `app.py` | Results → XLSX/CSV | **KEEP** | Download breaks |
| `prompts/scoring_system.md` | Loaded at runtime by `scoring.py` | Canonical scoring system prompt | **KEEP** | Live scoring breaks (no prompt) |
| `requirements.txt` | Defines the venv | Dependency manifest | **KEEP** | Cannot rebuild environment |
| `.env.example` | Template | Documents required env vars | **KEEP** | Onboarding friction |
| `.gitignore`, `.gitattributes` | Repo config | Git hygiene (ignores `.env`, `.venv`, `data/`, etc.) | **KEEP** | Secrets/large files could be committed |
| `.claude/launch.json` **and** `../.claude/launch.json` | Dev preview launcher | Starts the app for local preview | **KEEP (one of them)** | Preview convenience only — see Duplicate note below |

### Category 2 — ACTIVE_REFERENCE (useful reference material)

| Path | Used by active code? | Why this category | Action | Risk if removed |
|---|---|---|---|---|
| `data/Lead_Scoring_Guide.xlsx` | No | **Canonical rubric** (tiers, dimension weights) — source of truth behind the engine's model | **KEEP** (move to `docs/reference/`) | High — loses the documented rubric |
| `skill/references/scoring-guide.md` | No | Prose scoring rubric that seeded `scoring_system.md` | **KEEP** (move to `docs/reference/`) | Medium — reference for prompt design |
| `data/fintech_raw_vayne.csv` (45 MB) | No (pilot only) | **Original raw Vayne export** — unscored source input for the FinTech pilot | **KEEP** (move to `data/raw/`) | **High — cannot regenerate without re-scraping Vayne (credits)** |
| `data/raw_leads.csv` (18 MB) | No | Original raw Vayne export for the **AI** campaign (5,000 leads) | **KEEP** (move to `data/raw/`, rename `ai_raw_leads.csv`) | High — cannot regenerate without Vayne |
| `data/fintech2-scored.csv` | No (pilot benchmark) | **Legacy/manual benchmark — NOT validated ground truth; requires re-baselining** (74% of Sprint 2.1 mismatches were pollution in this file). Matches `fintech_raw_vayne.csv` 100% by slug | **KEEP** (move to `data/benchmarks/`, flag legacy) | Medium — only manual comparison baseline, but known-polluted |
| `icp/Innotechfy_Fintech_Playbook.pdf` | No (pilot input) | The FinTech ICP under test; sample ICP | **KEEP** | Medium — needed to reproduce pilots |
| `icp/AI.pdf` | No (used in engine smoke tests) | Sample ICP used to validate the MVP | **KEEP** | Low |
| `icp/Innotechfy_Ecom_Playbook (1).pdf` | No | Sample ICP (other campaign) | **KEEP** (candidate to thin later) | Low |
| `icp/Innotechfy_Headless_WordPress_Playbook.pdf` | No | Sample ICP (other campaign) | **KEEP** (candidate to thin later) | Low |
| `icp/Innotechfy_Xamarin_Playbook.pdf` | No | Sample ICP (other campaign) | **KEEP** (candidate to thin later) | Low |
| `README.md` | No | Project README — **rewritten after cleanup; now describes the current MVP** (see `docs/CLEANUP_REPORT.md`) | **KEEP** | Low |
| `openapi-en.yaml` (119 KB) | No | Vayne API spec — reference if the scrape phase is revived | **KEEP** (move to `docs/reference/`) | Low unless Vayne revived |
| `docs/*.md` (9 files: `AI_ENGINE_SPEC.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `CLAUDE.md`, `DECISIONS.md`, `LEAD_QUALIFICATION_STANDARD.md`, `PRODUCT_REQUIREMENTS.md`, `README.md`, `ROADMAP.md`) | No | Documentation scaffolding — **currently empty placeholders** | **KEEP** | Low (empty) |
| `docs/REPOSITORY_AUDIT.md` | No | This document | **KEEP** | — |

### Category 3 — TEST_OR_PILOT (pilot scripts, runners, comparison logic)

| Path | Imported by active code? | Why this category | Action | Risk if removed |
|---|---|---|---|---|
| `scripts/pilot_fintech_v2.py` | No (standalone) | **Current** corrected FinTech pilot harness (Sprint 2.1) | **KEEP** (move to `scripts/pilots/`) | Low — re-derivable but useful |
| `prompts/pilot_fintech_system_v2.md` | Read by `pilot_fintech_v2.py` | Current pilot system prompt (v2, tightened) | **KEEP** (move to `prompts/pilots/`) | Low |
| `scripts/pilot_fintech.py` | No | **Superseded** Sprint 2 pilot (v1) — kept for provenance | **MOVE_TO_ARCHIVE** | Low — historical only |
| `prompts/pilot_fintech_system.md` | Read by `pilot_fintech.py` (v1) | **Superseded** pilot prompt (v1) | **MOVE_TO_ARCHIVE** | Low — historical only |

> Note: v1 (`pilot_fintech.py` + `pilot_fintech_system.md`) is functionally replaced by v2. It is TEST_OR_PILOT by nature but **obsolete** — archive rather than keep in the working tree.

### Category 4 — GENERATED_OUTPUT (regenerable outputs / renderings)

| Path | Regenerable by | Why this category | Action | Risk if removed |
|---|---|---|---|---|
| `pilot_results/fintech_20_20260711-191603/{comparison.csv, results.json, summary.txt}` | Re-running `pilot_fintech.py` (v1) | Sprint 2 pilot outputs | **MOVE_TO_ARCHIVE** then `outputs/pilots/` | Low — re-run reproduces (non-deterministic, costs API) |
| `pilot_results/fintech_20_v2_20260711-193246/{comparison.csv, results.json, summary.txt}` | Re-running `pilot_fintech_v2.py` | **Current** Sprint 2.1 pilot outputs (basis of the review) | **KEEP** (move to `outputs/pilots/`) | Medium — the analyzed result set; keep until Sprint 3 supersedes |
| `data/*-scored.xlsx` (`fintech`, `fintech2`, `ecom`, `wordpress`, `xamarin-us-tech`, `scored_leads`) | Re-render from the matching `.csv` | XLSX renderings of scored CSVs | **REGENERATE** (drop; recreate from CSV on demand) | Low — duplicate of CSV content |
| `data/segments/AI.csv` | `pipeline/segment.py` on a scored AI CSV | Output of the legacy segment step | **MOVE_TO_ARCHIVE** / REGENERATE | Low |
| `../scratchpad/manual_test.py`, `../scratchpad/test_output.csv`, `../scratchpad/test_output.xlsx` | Re-running the MVP test | **External** session scratchpad (outside the repo) | **DELETE_LATER** (ephemeral) | None — not part of the repo |

### Category 5 — DUPLICATE_OR_OBSOLETE (superseded)

| Path | Used by active code? | Why this category | Action | Risk if removed |
|---|---|---|---|---|
| `pipeline/scrape.py` | No | Legacy Vayne scraping (old pipeline); not in MVP | **MOVE_TO_ARCHIVE** | Medium — loses scraping if a future Scrape tab wants it |
| `pipeline/post_enrich.py` | No | Legacy LinkedIn post-enrichment; not in MVP | **MOVE_TO_ARCHIVE** | Medium — loses enrichment code |
| `pipeline/segment.py` | No | Legacy 3-tier segmenter; **superseded** by the 4-tier engine model | **MOVE_TO_ARCHIVE** | Low |
| `data/fintech-scored.csv` | No | Older manual FinTech benchmark (Jun 29) — **orphaned** (0% overlap with the raw export; superseded by `fintech2`) | **MOVE_TO_ARCHIVE** | Low — no matching raw input |
| `data/ecom-scored.csv`, `data/wordpress-scored.csv`, `data/xamarin-us-tech-scored.csv` | No | Legacy manual scored outputs for **other** campaigns (old pipeline) | **MOVE_TO_ARCHIVE** | Low |
| `data/scored_leads.csv` | No | Legacy ad-hoc AI-campaign scored output (inconsistent columns) | **MOVE_TO_ARCHIVE** | Low |
| `.claude/commands/{score,scrape,segment,post-enrich,sales-pipeline}.md` | No | Old Claude-Code slash commands for the Vayne pipeline | **MOVE_TO_ARCHIVE** | Low — historical workflow |
| `skill/SKILL.md` | No | Old packaged-skill orchestration instructions | **MOVE_TO_ARCHIVE** | Low |
| `sales-pipeline.skill` | No | Packaged old skill artifact | **MOVE_TO_ARCHIVE** | Low |
| `CLAUDE.md` (root) | No | Claude-Code context for the **old** pipeline (Python invocation notes) — stale re: MVP | **MOVE_TO_ARCHIVE** (or rewrite) | Low |
| `INSTALL-MAC.md`, `install-mac.command` | No | One-click installer for the old Claude-Code pipeline | **MOVE_TO_ARCHIVE** | Low |
| `.claude/launch.json` (the copy under `sales-pipeline-master/.claude/`) | No (the parent `../.claude/launch.json` is the one the preview tool uses) | **Duplicate** dev launcher | **MOVE_TO_ARCHIVE / DELETE_LATER** | None — redundant |

### Category 6 — SECRET_OR_LOCAL_ONLY (never commit; local/machine-specific)

| Path | Why this category | Action | Risk if removed |
|---|---|---|---|
| `.env` | **Secret** — holds `VAYNE_API_TOKEN`, `ANTHROPIC_API_KEY`, `SCORE_THRESHOLD` (values not shown) | **LOCAL_ONLY** (already gitignored) | High locally — scoring loses its key; must be recreated by the user |
| `.venv/` | Local Python 3.11 virtualenv (interpreter + installed deps) | **LOCAL_ONLY** (gitignored; rebuild from `requirements.txt`) | None — regenerable |
| `pipeline/__pycache__/`, `scripts/__pycache__/` | Python bytecode caches | **LOCAL_ONLY / DELETE_LATER** (gitignored) | None — regenerated on run |
| `.DS_Store` (root, `pipeline/`, `data/`, `data/segments/`, `icp/`, `skill/`, and `../.DS_Store`) | macOS Finder metadata | **DELETE_LATER** (gitignored) | None |
| `.claude/settings.local.json` and `../.claude/settings.local.json` | Machine-specific Claude settings | **LOCAL_ONLY** (gitignored) | None |

---

## 2. Special-attention items (explicitly requested)

| Item | Category | Verdict |
|---|---|---|
| `data/fintech-scored.csv` | DUPLICATE_OR_OBSOLETE | Orphaned older benchmark (0% match to the raw export). **Archive.** |
| `data/fintech2-scored.csv` | ACTIVE_REFERENCE (flagged **legacy / needs re-baselining**) | **Keep** in `data/benchmarks/`, clearly labeled **not validated ground truth**. Do not use as pass/fail truth until cleaned. |
| `data/fintech_raw_vayne.csv` | ACTIVE_REFERENCE (raw source) | **Keep** in `data/raw/`, separated from scored outputs. Cannot regenerate without Vayne. |
| `scripts/pilot_fintech.py` | TEST_OR_PILOT (obsolete v1) | **Archive.** |
| `scripts/pilot_fintech_v2.py` | TEST_OR_PILOT (current) | **Keep** in `scripts/pilots/`. |
| `prompts/pilot_fintech_system.md` (v1) | TEST_OR_PILOT (obsolete) | **Archive.** |
| `prompts/pilot_fintech_system_v2.md` (v2) | TEST_OR_PILOT (current) | **Keep** in `prompts/pilots/`. |
| `pilot_results/` directories | GENERATED_OUTPUT | v1 dir → archive; v2 dir → keep under `outputs/pilots/` until Sprint 3. |
| `comparison.csv` / `results.json` / `summary.txt` (both runs) | GENERATED_OUTPUT | Regenerable; move with their pilot dirs. |
| scratchpad outputs (`../scratchpad/*`) | GENERATED_OUTPUT / external | Not in repo; ephemeral — **delete later**. |
| old ICP files (`icp/*.pdf`) | ACTIVE_REFERENCE | None are truly obsolete — all are valid sample ICPs. Keep; optionally thin unused ones later (ambiguous — your call). |
| duplicate `.claude` folders | mixed | `../.claude/launch.json` is the **active** dev launcher; `sales-pipeline-master/.claude/launch.json` is a **duplicate** (archive/delete). `settings.local.json` in both = local-only. `.claude/commands/*` = obsolete. |
| `__pycache__` | SECRET_OR_LOCAL_ONLY | Delete anytime; regenerated. |
| `.venv` | SECRET_OR_LOCAL_ONLY | Local only; rebuild from `requirements.txt`. |
| `.env` | SECRET_OR_LOCAL_ONLY | Local only; **never commit; never print**. |

---

## 3. Proposed clean target structure

> **Pre-cleanup proposal.** This section was written *before* the cleanup ran and shows a
> *proposed* layout. The executed cleanup used slightly different final names and folders
> (e.g. `data/raw/ai_raw_vayne.csv`, `data/benchmarks/legacy/`, and `archive/legacy_pipeline/`,
> `archive/pilot_v1/`, `archive/legacy_tooling/`, `archive/legacy_claude_commands/`,
> `archive/old_campaign_outputs/`). **`docs/CLEANUP_REPORT.md` is the authoritative record of the
> final executed paths and names.**

```
sales-pipeline-master/
├── app.py                       # MVP entry point
├── requirements.txt
├── .env / .env.example          # .env local-only
├── .gitignore / .gitattributes
│
├── pipeline/                    # ACTIVE engine only
│   ├── __init__.py
│   ├── config.py
│   ├── icp_pdf.py
│   ├── scoring.py
│   └── export.py
│
├── prompts/
│   ├── scoring_system.md        # production prompt
│   └── pilots/
│       └── fintech_v2.md        # current pilot prompt
│
├── scripts/
│   └── pilots/
│       └── pilot_fintech_v2.py  # current pilot harness
│
├── tests/                       # (new) promote a real MVP smoke test here
│
├── docs/
│   ├── *.md                     # spec/architecture/roadmap/etc.
│   ├── REPOSITORY_AUDIT.md
│   └── reference/
│       ├── Lead_Scoring_Guide.xlsx
│       ├── scoring-guide.md
│       └── openapi-en.yaml
│
├── data/
│   ├── raw/                     # unscored source exports ONLY
│   │   ├── fintech_raw_vayne.csv
│   │   └── ai_raw_leads.csv
│   ├── benchmarks/              # manual scored refs (LEGACY, needs re-baselining)
│   │   ├── fintech2-scored.csv
│   │   └── README.md            # "NOT validated ground truth"
│   └── samples/                 # tiny curated CSVs for demos/tests
│
├── icp/                         # sample/reference ICP PDFs
│
├── outputs/
│   ├── pilots/                  # pilot run artifacts (comparison/results/summary)
│   └── exports/                 # MVP-produced XLSX/CSV downloads
│
└── archive/                     # nothing here is imported by active code
    ├── legacy_pipeline/         # scrape.py, post_enrich.py, segment.py
    ├── legacy_claude_pipeline/  # CLAUDE.md, README(old), install-mac.*, sales-pipeline.skill,
    │                            #   skill/SKILL.md, .claude/commands/*
    ├── legacy_outputs/          # *-scored.csv/.xlsx (ecom/wordpress/xamarin/scored_leads/fintech),
    │                            #   segments/AI.csv
    └── pilots_v1/               # pilot_fintech.py, pilot_fintech_system.md, Sprint-2 pilot_results
```

**Config recommendation (enforces the "explicit paths" rule):** introduce a small `paths` section in `pipeline/config.py` (or a `config.toml`) with named constants — `RAW_DIR`, `BENCHMARK_DIR`, `PILOT_OUTPUT_DIR` — so pilots and any future tooling reference **explicit configured paths**, never a directory scan. Production `app.py` already avoids repo-file discovery (uploads only) and should stay that way.

---

## 4. Final summary lists

### 4.1 Files definitely required by the current MVP
- `app.py`
- `pipeline/__init__.py`, `pipeline/config.py`, `pipeline/icp_pdf.py`, `pipeline/scoring.py`, `pipeline/export.py`
- `prompts/scoring_system.md`
- `requirements.txt`, `.env.example`, `.gitignore`, `.gitattributes`
- Local-only but required to run: `.env` (secret), `.venv/` (rebuildable), one `.claude/launch.json` (dev preview only)

### 4.2 Files safe to archive (move out of the working tree; keep for provenance)
- Legacy engine: `pipeline/scrape.py`, `pipeline/post_enrich.py`, `pipeline/segment.py`
- Old Claude-Code pipeline: `CLAUDE.md`, `INSTALL-MAC.md`, `install-mac.command`, `sales-pipeline.skill`, `skill/SKILL.md`, `.claude/commands/*.md`, stale `README.md` (until rewritten)
- Superseded pilot v1: `scripts/pilot_fintech.py`, `prompts/pilot_fintech_system.md`, `pilot_results/fintech_20_20260711-191603/*`
- Legacy/other-campaign outputs: `data/fintech-scored.csv`, `data/ecom-scored.*`, `data/wordpress-scored.*`, `data/xamarin-us-tech-scored.*`, `data/scored_leads.*`, `data/segments/AI.csv`
- Duplicate launcher: `sales-pipeline-master/.claude/launch.json`

### 4.3 Files potentially safe to delete later (fully regenerable / machine noise)
- All `__pycache__/`, all `.DS_Store`, `.claude/worktrees/` (if any)
- Scored `*.xlsx` renderings **if** their `.csv` is retained (regenerate on demand)
- External session scratchpad: `../scratchpad/manual_test.py`, `test_output.csv`, `test_output.xlsx`
- Old pilot outputs after they are archived and Sprint 3 supersedes them

### 4.4 Ambiguous items requiring your decision
1. **`data/fintech2-scored.csv`** — keep as a labeled legacy benchmark, or archive entirely until a re-baselined benchmark exists? (Recommendation: keep, clearly flagged; do **not** use as ground truth.)
2. **Large raw exports** (`fintech_raw_vayne.csv` 45 MB, `raw_leads.csv` 18 MB) — keep in-repo under `data/raw/` (they're gitignored), or move to external/object storage given size? They **cannot** be regenerated without re-scraping Vayne.
3. **Legacy Vayne pipeline** (`scrape.py` / `post_enrich.py` / `segment.py` + `openapi-en.yaml`) — archive permanently, or retain for a planned Scrape/Enrich phase? Affects whether they go to `archive/` or stay in `pipeline/legacy/`.
4. **Other-campaign ICPs & scored outputs** (Ecom / WordPress / Xamarin) — keep as multi-ICP samples, or archive to focus the repo on FinTech + AI?
5. **`Lead_Scoring_Guide.xlsx`** and **`scoring-guide.md`** location — `docs/reference/` (recommended) vs leaving in `data/` / `skill/`.

### 4.5 Proposed move/rename plan (no execution — for your approval)

| From | To |
|---|---|
| `pipeline/scrape.py`, `post_enrich.py`, `segment.py` | `archive/legacy_pipeline/` |
| `scripts/pilot_fintech.py` | `archive/pilots_v1/pilot_fintech.py` |
| `prompts/pilot_fintech_system.md` | `archive/pilots_v1/pilot_fintech_system.md` |
| `pilot_results/fintech_20_20260711-191603/` | `archive/pilots_v1/results/` |
| `scripts/pilot_fintech_v2.py` | `scripts/pilots/pilot_fintech_v2.py` |
| `prompts/pilot_fintech_system_v2.md` | `prompts/pilots/fintech_v2.md` |
| `pilot_results/fintech_20_v2_20260711-193246/` | `outputs/pilots/fintech_v2_20260711-193246/` |
| `data/fintech_raw_vayne.csv` | `data/raw/fintech_raw_vayne.csv` |
| `data/raw_leads.csv` | `data/raw/ai_raw_leads.csv` (rename for clarity) |
| `data/fintech2-scored.csv` | `data/benchmarks/fintech2-scored.csv` (+ `data/benchmarks/README.md` legacy flag) |
| `data/fintech-scored.csv` | `archive/legacy_outputs/` |
| `data/ecom-scored.*`, `wordpress-scored.*`, `xamarin-us-tech-scored.*`, `scored_leads.*` | `archive/legacy_outputs/` |
| `data/*-scored.xlsx` (all) | drop → `REGENERATE` from CSV, or `outputs/exports/` if retained |
| `data/segments/AI.csv` | `archive/legacy_outputs/segments/AI.csv` |
| `data/Lead_Scoring_Guide.xlsx` | `docs/reference/Lead_Scoring_Guide.xlsx` |
| `skill/references/scoring-guide.md` | `docs/reference/scoring-guide.md` |
| `openapi-en.yaml` | `docs/reference/openapi-en.yaml` |
| `CLAUDE.md`, `INSTALL-MAC.md`, `install-mac.command`, `sales-pipeline.skill`, `skill/SKILL.md`, `.claude/commands/*` | `archive/legacy_claude_pipeline/` |
| `sales-pipeline-master/.claude/launch.json` | delete (keep `../.claude/launch.json`) |
| `icp/*.pdf` | unchanged (stay in `icp/`) |
| `__pycache__/`, `.DS_Store` | delete (regenerated / ignored) |

---

**End of audit. No cleanup was executed.** Await decisions on §4.4 before any move/delete.
