# Repository Cleanup Report

**Date:** 2026-07-11
**Executed against:** `docs/REPOSITORY_AUDIT.md`
**Result:** Completed and verified. No unique source, dataset, prompt, or result was deleted.
No Anthropic API call was made. No scoring logic or business behavior was changed.

---

## 1. Backup

| | |
|---|---|
| **Path** | `/Users/dmytrohraf/Documents/sales-pipeline-ui/sales-pipeline-master-before-cleanup-20260711-200831.zip` |
| **Size** | 25 MB |
| **Files** | 70 |
| **Excluded** | `.venv/`, `.env`, `__pycache__/`, `*.pyc`, `.DS_Store` |
| **Verified** | 0 excluded-pattern hits inside the zip; `.env` left in place |

The backup captures the **pre-cleanup** state and is the rollback source (see §7).

## 2. Files moved (old → new)

### Raw data → `data/raw/`
| Old | New |
|---|---|
| `data/fintech_raw_vayne.csv` | `data/raw/fintech_raw_vayne.csv` |
| `data/raw_leads.csv` | `data/raw/ai_raw_vayne.csv` *(renamed)* |

### Legacy benchmarks → `data/benchmarks/legacy/` *(renamed for clarity)*
| Old | New |
|---|---|
| `data/fintech-scored.csv` | `data/benchmarks/legacy/fintech_manual_v1.csv` |
| `data/fintech-scored.xlsx` | `data/benchmarks/legacy/fintech_manual_v1.xlsx` |
| `data/fintech2-scored.csv` | `data/benchmarks/legacy/fintech_manual_v2_unvalidated.csv` |
| `data/fintech2-scored.xlsx` | `data/benchmarks/legacy/fintech_manual_v2_unvalidated.xlsx` |
| `data/scored_leads.csv` | `data/benchmarks/legacy/ai_manual_legacy.csv` |
| `data/scored_leads.xlsx` | `data/benchmarks/legacy/ai_manual_legacy.xlsx` |
| *(new)* | `data/benchmarks/legacy/README.md` — "not validated ground truth" notice |

### Other-campaign outputs → `archive/old_campaign_outputs/`
| Old | New |
|---|---|
| `data/ecom-scored.csv` / `.xlsx` | `archive/old_campaign_outputs/ecom-scored.csv` / `.xlsx` |
| `data/wordpress-scored.csv` / `.xlsx` | `archive/old_campaign_outputs/wordpress-scored.csv` / `.xlsx` |
| `data/xamarin-us-tech-scored.csv` / `.xlsx` | `archive/old_campaign_outputs/xamarin-us-tech-scored.csv` / `.xlsx` |
| `data/segments/AI.csv` | `archive/old_campaign_outputs/segments/AI.csv` |

### Pilot outputs → `outputs/pilots/` *(both runs preserved, not merged)*
| Old | New |
|---|---|
| `pilot_results/fintech_20_20260711-191603/` | `outputs/pilots/fintech_20_20260711-191603/` |
| `pilot_results/fintech_20_v2_20260711-193246/` | `outputs/pilots/fintech_20_v2_20260711-193246/` |

### Pilot v1 (superseded) → `archive/pilot_v1/`
| Old | New |
|---|---|
| `scripts/pilot_fintech.py` | `archive/pilot_v1/pilot_fintech.py` |
| `prompts/pilot_fintech_system.md` | `archive/pilot_v1/pilot_fintech_system.md` |

### Legacy pipeline → `archive/legacy_pipeline/`
| Old | New |
|---|---|
| `pipeline/scrape.py` | `archive/legacy_pipeline/scrape.py` |
| `pipeline/post_enrich.py` | `archive/legacy_pipeline/post_enrich.py` |
| `pipeline/segment.py` | `archive/legacy_pipeline/segment.py` |

### Legacy tooling → `archive/legacy_tooling/`
| Old | New |
|---|---|
| `sales-pipeline.skill` | `archive/legacy_tooling/sales-pipeline.skill` |
| `openapi-en.yaml` | `archive/legacy_tooling/openapi-en.yaml` |
| `skill/` (SKILL.md + references/scoring-guide.md) | `archive/legacy_tooling/skill/` |
| `../.claude/launch.json` *(redundant duplicate at parent working dir)* | `archive/legacy_tooling/redundant_parent_launch.json` |

### Legacy Claude commands → `archive/legacy_claude_commands/`
| Old | New |
|---|---|
| `.claude/commands/score.md` | `archive/legacy_claude_commands/score.md` |
| `.claude/commands/scrape.md` | `archive/legacy_claude_commands/scrape.md` |
| `.claude/commands/segment.md` | `archive/legacy_claude_commands/segment.md` |
| `.claude/commands/post-enrich.md` | `archive/legacy_claude_commands/post-enrich.md` |
| `.claude/commands/sales-pipeline.md` | `archive/legacy_claude_commands/sales-pipeline.md` |

### Reference → `data/reference/`
| Old | New |
|---|---|
| `data/Lead_Scoring_Guide.xlsx` | `data/reference/Lead_Scoring_Guide.xlsx` |

**Kept in place (unchanged):** all `icp/*.pdf`; the active engine (`app.py`, `pipeline/{__init__,config,icp_pdf,scoring,export}.py`, `prompts/scoring_system.md`); the current v2 pilot (`scripts/pilot_fintech_v2.py`, `prompts/pilot_fintech_system_v2.md`); root docs (`README.md`, `INSTALL-MAC.md`, `install-mac.command`, `CLAUDE.md`); `.claude/{launch.json, settings.local.json}`; `.env`, `.venv`.

## 3. Files deleted (safe machine-generated clutter only)

- `pipeline/__pycache__/`, `scripts/__pycache__/` (regenerated on run)
- `.DS_Store` at: repo root, `pipeline/`, `icp/`, `data/`, `data/segments/`, `skill/`, and parent `../.DS_Store`
- No `*.pyc` outside `.venv` were present

**No unique CSV, XLSX, PDF, JSON, prompt, script, or source file was deleted.**

## 4. Explicit paths updated in active code

Only the corrected **v2 pilot** referenced moved files. Updated in `scripts/pilot_fintech_v2.py`:

| Constant | Old | New |
|---|---|---|
| `RAW_CSV` | `data/fintech_raw_vayne.csv` | `data/raw/fintech_raw_vayne.csv` |
| `BENCHMARK_CSV` | `data/fintech2-scored.csv` | `data/benchmarks/legacy/fintech_manual_v2_unvalidated.csv` |

`ICP_PDF` and `PROMPT_FILE` were unchanged (their targets did not move). **No fuzzy discovery was
added; no benchmark auto-selection was introduced.** The archived `archive/pilot_v1/pilot_fintech.py`
still references the old `data/fintech-scored.csv` path — intentionally left as-is because it is
archived and not run.

`CLAUDE.md` gained a mandatory "Read first" section pointing to `docs/PROJECT_MANIFEST.md` and
`docs/REPOSITORY_AUDIT.md`, with the archive / legacy-benchmark / secrets rules. The MVP
(`app.py`, `pipeline/*`, `prompts/scoring_system.md`) was **not** modified — no production path
needed changing (it reads uploads + `prompts/scoring_system.md`, neither of which moved).

## 5. New documentation created

- `docs/PROJECT_MANIFEST.md` — product goal, MVP scope, Human Review Gate, active files,
  input/raw/benchmark/output locations, ignore-list, model id, and standing rules.
- `data/benchmarks/legacy/README.md` — "not validated ground truth" notice.
- `docs/CLEANUP_REPORT.md` — this file.

## 6. Verification results

| Check | Result |
|---|---|
| Active production imports (`config`, `icp_pdf`, `scoring`, `export`) | ✅ import OK |
| `app.py` parses | ✅ OK |
| No active code/prompt references `archive/` | ✅ PASS |
| Streamlit boots headless | ✅ HTTP 200, no errors in boot log |
| Leads scored during verification | ✅ none (no API call) |
| v2 pilot locates `data/raw/fintech_raw_vayne.csv` | ✅ exists |
| v2 pilot locates `data/benchmarks/legacy/fintech_manual_v2_unvalidated.csv` | ✅ exists |
| v2 pilot locates ICP PDF + v2 prompt | ✅ exist |
| `.env` present; no secret values printed | ✅ present, not printed |
| No unique file content lost (hash reconciliation vs backup) | ✅ 68/70 byte-identical; only `CLAUDE.md` + `pilot_fintech_v2.py` differ (the two intentional edits) |

## 7. Final repository structure (excerpt)

```
app.py  requirements.txt  README.md  CLAUDE.md  INSTALL-MAC.md  install-mac.command
.env  .env.example  .gitignore  .gitattributes  .claude/{launch.json,settings.local.json}
pipeline/{__init__,config,icp_pdf,scoring,export}.py
prompts/{scoring_system.md, pilot_fintech_system_v2.md}
scripts/pilot_fintech_v2.py
tests/
icp/*.pdf
data/
  raw/{fintech_raw_vayne.csv, ai_raw_vayne.csv}
  benchmarks/legacy/{fintech_manual_v1.*, fintech_manual_v2_unvalidated.*, ai_manual_legacy.*, README.md}
  reference/Lead_Scoring_Guide.xlsx
  samples/
outputs/
  pilots/{fintech_20_20260711-191603/, fintech_20_v2_20260711-193246/}
  exports/  logs/
archive/
  legacy_pipeline/{scrape,post_enrich,segment}.py
  pilot_v1/{pilot_fintech.py, pilot_fintech_system.md}
  legacy_prompts/            (empty; reserved)
  legacy_claude_commands/*.md
  legacy_tooling/{sales-pipeline.skill, openapi-en.yaml, skill/, redundant_parent_launch.json}
  old_campaign_outputs/{ecom,wordpress,xamarin}-scored.*  segments/AI.csv
docs/{PROJECT_MANIFEST.md, REPOSITORY_AUDIT.md, CLEANUP_REPORT.md, + spec placeholders}
```

## 8. Unresolved ambiguity (for your later decision — nothing acted on)

- `archive/legacy_prompts/` was created per the target structure but nothing mapped to it
  (the only legacy prompt was the pilot-v1 prompt, which went to `archive/pilot_v1/`). It is
  currently empty — keep as a reserved slot or remove later.
- `tests/`, `data/samples/`, `outputs/exports/`, `outputs/logs/` are created but empty,
  awaiting content (e.g. promoting a real MVP smoke test into `tests/`).
- The large raw exports remain in-repo under `data/raw/` (45 MB + 18 MB). Whether to keep them
  in-repo or move to external storage is still open (they are not regenerable without Vayne).
- `README.md` was rewritten after this cleanup and now reflects the current MVP — it no longer
  documents the legacy pipeline. *(When this report was first written the README still described
  the legacy pipeline and was left unchanged during cleanup; that rewrite has since been completed
  in a later documentation-alignment step.)*

## 9. Rollback instructions

The cleanup only moved/renamed files and deleted regenerable machine clutter. To fully restore
the pre-cleanup state:

```bash
cd /Users/dmytrohraf/Documents/sales-pipeline-ui
# 1. Move the cleaned repo aside (do not delete)
mv sales-pipeline-master sales-pipeline-master-cleaned-$(date +%Y%m%d-%H%M%S)
# 2. Restore from the backup ZIP
mkdir sales-pipeline-master
unzip -q sales-pipeline-master-before-cleanup-20260711-200831.zip -d sales-pipeline-master
# 3. Restore local-only items the backup intentionally excluded:
#    - .env  : copy back from the cleaned copy (it was never moved) or recreate from .env.example
#    - .venv : recreate ->  python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp "sales-pipeline-master-cleaned-"*/.env sales-pipeline-master/.env   # if present
```

Because `.env` and `.venv` were excluded from the backup, they must be restored from the retained
cleaned copy (where they still exist untouched) or recreated. All other files in the backup are
byte-identical to their pre-cleanup form.

---

**Cleanup complete and verified. Stopped here — Sprint 3 not started, scoring logic unchanged,
no Anthropic API calls made.**
