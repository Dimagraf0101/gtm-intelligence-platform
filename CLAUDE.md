# Lead Intelligence Platform (MVP) — Claude Code context

## ⚠️ Read first (mandatory)

Before implementing any task, read:
- `docs/PROJECT_MANIFEST.md`
- `docs/REPOSITORY_AUDIT.md`

Rules:
- **Never** use files under `archive/` as active implementation sources unless the user
  explicitly asks.
- **Never** treat `data/benchmarks/legacy/` as validated ground truth.
- **Never** read or print secret values from `.env`.
- The active app is `app.py` (entrypoint: `st.navigation` + home landing page) +
  `pages/{1_Business_Knowledge_Review,2_Run_Campaign}.py` (the two wizards) + the `pipeline/`
  modules + `prompts/*.md`. Current model: `claude-haiku-4-5-20251001`.

> Note: the sections below describe the **legacy** Claude-Code / Vayne pipeline. It has been
> **superseded by the Streamlit MVP** and is **no longer active** — everything below is historical.
> The legacy scripts (`scrape`, `post_enrich`, `segment`) now live under `archive/legacy_pipeline/`,
> not in `pipeline/`. The legacy data outputs it mentions (raw_leads, post_enriched_leads,
> scored_leads, per-ICP segments) no longer exist at their old `data/` locations; retained copies
> are under `archive/` and `data/benchmarks/legacy/`. Do **not** treat any command or path below as
> an active source.

## Python

- **Mac / Linux:** `python3`
- **Windows with Claude Code (Codex runtime):** use the full path, substituting your Windows username:
  `/c/Users/<username>/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`

Always prepend `PYTHONIOENCODING=utf-8`.

**Do NOT run any detection or verification commands.** Just pick the right command above and run the script directly. If Python is wrong, the script will error clearly.

## Running pipeline scripts

```bash
# Scrape        (legacy — now under archive/legacy_pipeline/)
PYTHONIOENCODING=utf-8 <PYTHON> archive/legacy_pipeline/scrape.py "<url>" [--limit N] [--name NAME]

# Post-enrich   (legacy — now under archive/legacy_pipeline/)
PYTHONIOENCODING=utf-8 <PYTHON> archive/legacy_pipeline/post_enrich.py [--min-score N] [--max-score N]

# Segment       (legacy — now under archive/legacy_pipeline/)
PYTHONIOENCODING=utf-8 <PYTHON> archive/legacy_pipeline/segment.py [--threshold N]
```

## Scoring (done by Claude, no script)

1. Use the **Read tool** to read `icp/*.pdf` and the raw leads CSV (legacy paths — see the historical note above).
2. Score all leads **inline** — do NOT use bash to extract fields or reformat data.
3. After scoring all leads, write the scored CSV in a **single** `python -c` command or Write tool call.
4. Do NOT create intermediate files, temp scripts, or multiple write passes.

## Rules — follow strictly

- Do NOT run Python detection or version-check commands
- Do NOT check if `.env` exists before running — scripts report missing config clearly
- Do NOT read pipeline scripts before running them
- Do NOT use PowerShell — use Bash only
- Do NOT use bash to extract/reformat CSV rows for scoring — use the Read tool

## Project layout

```
archive/legacy_pipeline/   — legacy scripts, historical (scrape.py, post_enrich.py, segment.py)
pipeline/config.py         — loads .env (still active; shared by the MVP engine)
icp/                       — ICP PDF definitions (one file = one ICP)
```

Historical data outputs (raw_leads, post_enriched_leads, scored_leads, per-ICP segments) are no
longer produced and no longer exist at their old `data/` paths; retained copies are under
`data/benchmarks/legacy/` and `archive/old_campaign_outputs/`.
