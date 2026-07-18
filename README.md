# GTM Intelligence Platform

**v1.0.0 — Production-Validated MVP Candidate**

> **Internal repository folder:** `sales-pipeline-master` — the local development folder name, **not**
> the product name.

A local, single-user, human-in-the-loop platform for go-to-market teams. It turns a company's own
materials into validated **Market Hypotheses**, derives an ICP and a LinkedIn Sales Navigator search
strategy from them, acquires and qualifies leads, and puts every lead in front of a human for
**explicit approval** before anything leaves the platform. The software qualifies, ranks and
explains — **it never contacts anyone.**

**Core principle: AI proposes → Python validates → Human approves.** The model only ever proposes
per-dimension evidence and points; deterministic Python owns every final verdict (score, priority,
dealbreakers); and a person approves or rejects each lead in the Human Review workbench.

---

## Current capabilities

Everything below is implemented, tested and runnable today.

| Stage | What it does | Page |
|---|---|---|
| **Business Knowledge** | Ingest company materials → AI extraction with Python validation, provenance, conflicts | 1 · Business Knowledge Review |
| **Knowledge Interview** | Gap-driven questions that fill only what's missing | 2 · Knowledge Interview |
| **Strategy Review** | Dimension weights + hard-exclusion activation | 3 · Strategy Review |
| **Approval** | The ICP approval gate (IQS-validated, warning acknowledgement) | 4 · Approval |
| **General ICP** | A company-wide, industry-agnostic ICP; immutable append-only versions; workspace save/reload | 5 · General ICP |
| **Market Hypotheses** | Create hypotheses; each adapts the General ICP into its own **Adapted ICP** | 6 · Market Hypotheses |
| **Search Strategy** | A versioned, immutable strategy derived from the Approved Adapted ICP, with Sales Navigator **filter recommendations** (Draft → Reviewed → Approved → Archived) | 7 · Search Strategy |
| **Lead Import** | Manual Vayne CSV → immutable **Lead Batch** (lineage-checked) | 8 · Lead Import |
| **Qualification** | Qualify a Lead Batch against **the exact Adapted ICP its strategy came from** → immutable **Qualified Lead Batch** | 9 · Qualification |
| **Search Execution** | Paste a Sales Navigator URL → submit to **Vayne** → poll → CSV → Lead Batch (all-or-capped retrieval, idempotent submission) | 10 · Search Execution |
| **Human Review** | Approve / Reject / Skip every qualified lead, then export | 11 · Human Review |

**Export targets:** canonical XLSX and CSV, plus **Google Sheets publishing** (three managed
worksheets) — all from the same canonical schema.

## Current architecture

```
Company materials
      ↓
Business Knowledge  ──►  General ICP
      ↓
Market Hypothesis   ──►  Adapted ICP  ──►  Search Strategy (Approved)
                                                ↓
                          Search Execution (Vayne)  ──or──  manual CSV import
                                                ↓
                                           Lead Batch (immutable)
                                                ↓
                                          Qualification (frozen engine)
                                                ↓
                                      Qualified Lead Batch (immutable)
                                                ↓
                                      Human Review (append-only decisions)
                                                ↓
                                            ReviewRow
                                                ↓
                          review_export  ──►  canonical MAIN / AI / Summary rows
                                                ↓
                                   XLSX  ·  CSV  ·  Google Sheets
```

Design rules that hold throughout: artifacts are **immutable and append-only**; every artifact records
its **lineage**; external systems sit behind **anti-corruption layers**; unknown values stay unknown
(never invented); and **priority is owned solely by Python** (`decision.operational_priority`, driven by
the canonical bands in `priority_policy`): `90+ P1 · 75+ P2 · 60+ P3 · 45+ P4 · 30+ P5 · below 30
Disqualified`, with confirmed dealbreakers disqualifying regardless of score.

## Human Review workflow

Human Review is the platform's primary workbench — the screen is meant to look like the deliverable.

1. Pick a Market Hypothesis and a Qualified Lead Batch (lineage is shown).
2. Read the summary metrics and priority distribution.
3. Filter and search (review status, priority, score range, industry, company size, location; free-text
   over company/contact).
4. **Approve / Reject / Skip** each lead inline, or use **bulk actions** on an explicit selection or a
   confirmed filtered set (the exact affected count is always shown).
5. Open a lead for full detail: business identity and attributes, the AI proposal (priority, score,
   confidence, reason, evidence, score breakdown, warnings), clearly-labelled audit data, and the
   append-only decision history.
6. Export — **Approved only** (default), Selected, or All.

Decisions live in a separate **append-only** artifact: re-deciding a lead appends a new decision (latest
wins, history preserved) and **never mutates** the Lead or the qualification result. `review_status` is
the single workflow authority; the exported "Human Decision" is *derived* from it, so the two cannot
drift. The AI's score, priority and evidence are read-only — there is deliberately **no priority
override** and no editing of AI or business fields.

> Review decisions are held **in the session** until you click **Save workspace** (on the Human Review
> page or page 5). This is deliberate — explicit persistence, no hidden autosave.

## Google Sheets Publisher

From the Human Review export area you can publish the reviewed leads to Google Sheets.

- **Scopes:** Approved only (default), Selected, All.
- **Modes:** *Create new spreadsheet* (needs a name) or *Update existing* (needs an explicit spreadsheet
  ID or URL — the target is never guessed).
- **Three managed worksheets:** `Leads` (canonical MAIN columns), `AI Details` (canonical AI columns),
  `Summary`. They are fully replaced on each publish, so re-publishing updates in place and never
  duplicates rows; **unrelated worksheets are never touched.**
- **Always requires explicit confirmation**, and Python validates everything (credentials, scope,
  canonical column order, duplicates, target) *before* any API call.
- **Auth:** a Google **service account**, read only from the environment
  (`GOOGLE_SHEETS_CREDENTIALS_FILE` or `GOOGLE_SHEETS_CREDENTIALS_JSON`). Credentials are never
  committed, printed, echoed in errors, or written into a workspace file.

> The service account must be given access to any spreadsheet you update, and spreadsheets it creates
> are owned by the service account — share them with your team.

## Installation

Requires **Python 3.11+** (developed on 3.11).

```bash
cd sales-pipeline-master

# 1. Create a virtual environment
python3.11 -m venv .venv

# 2. Install dependencies
./.venv/bin/pip install -r requirements.txt

# 3. Configure secrets
cp .env.example .env      # then edit .env (never commit it)
```

### `.env` variables

| Variable | Required for | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Real AI output | Knowledge extraction, ICP drafting, interview, and scoring. **Without it the app still runs**, using a deterministic offline mock (clearly labelled; the numbers are placeholders). |
| `VAYNE_API_TOKEN` | Search Execution (page 10) | Submits a Sales Navigator URL to Vayne and retrieves the CSV. Not needed for manual CSV import. |
| `GOOGLE_SHEETS_CREDENTIALS_FILE` *or* `GOOGLE_SHEETS_CREDENTIALS_JSON` | Google Sheets publishing | Service-account key, as a file path or inline JSON. |
| `VAYNE_WEBHOOK_URL` | — | Optional; used only by the archived legacy scraper, not by the app. |

Qualification is **not** controlled by any environment variable — final qualification is owned entirely
by deterministic Python. (The former `SCORE_THRESHOLD` setting was dead configuration and was removed in
Sprint 12.0.2; an old `.env` that still defines it is harmless.)

## Quick start

```bash
./.venv/bin/streamlit run app.py
# opens http://localhost:8501
```

The app opens on **Home** — a read-only control center that shows where you are and recommends your
next step. From there, work down the sidebar: **Foundation** to build company knowledge, **ICP Strategy**
to generate and approve an ICP and a Search Strategy, **Lead Acquisition** to acquire leads (Search
Execution *or* Lead Import — they are alternatives), and **Qualification** to qualify, review and export.

Save your workspace to disk (page 5 or page 11) — state otherwise lives only in the browser session.

Run the test suite:

```bash
for f in tests/test_*.py; do PYTHONIOENCODING=utf-8 ./.venv/bin/python "$f"; done
```

Tests are self-running (no pytest); each file exits non-zero on failure. **646 tests across 38 files**,
fully offline — no API keys, no network.

## Architecture

| Layer | Modules |
|---|---|
| **Knowledge** | `source_documents`, `source_package`, `icp_pdf`, `knowledge_extractor`, `business_knowledge`, `knowledge_gaps`, `knowledge_review` |
| **Hypothesis & ICP authoring** | `icp_project` (CompanyWorkspace / MarketHypothesis), `knowledge_interview`, `icp_draft_generator`, `generated_icp`, `general_icp`, `adapted_icp`, `strategy_review`, `iqs_validator`, `icp_approval`, `icp_identity` |
| **Search** | `search_strategy`, `search_execution`, `search_execution_service` |
| **Lead acquisition** | `lead_batch` (domain), `business_attributes` (canonical attribute registry), `vayne_adapter` (ACL), `lead_import` (lineage gate) |
| **Qualification engine (frozen)** | `scoring`, `decision`, `evidence`, `prequalification`, `qualification_bridge` |
| **Qualification integration** | `qualification_mapper`, `qualified_lead`, `qualification_run` |
| **Priority policy** | `priority_policy` — the single canonical source of the operational bands |
| **Human Review** | `lead_review` (append-only decisions), `review_view` (the one projection), `review_export` (canonical row adapter) |
| **Delivery** | `export` (canonical workbook) |
| **Integrations (ACL)** | `integrations/vayne_client`, `integrations/google_sheets_publisher` |
| **Persistence & infra** | `workspace_store`, `workspace_revision`, `config` |

42 pipeline modules plus 2 integration boundaries. Persistence is one JSON file per `CompanyWorkspace`
(schema v1, additive) — no database, ORM, migrations, queues, or background workers.

## Repository structure

| Path | What it holds |
|---|---|
| `app.py` | Streamlit entry point — declares the sidebar pages via `st.navigation` and runs the selected one |
| `legacy_qualification.py` | The legacy PDF + CSV qualification screen; hidden from the sidebar, still reachable at `/legacy_qualification` |
| `pages/` | Home plus the 11 workflow pages, in sidebar order |
| `pipeline/` | Domain, services, engine, and `integrations/` |
| `prompts/` | Production prompts (no prompt strings hard-coded in Python) |
| `assets/` | Sidebar wordmark used by `st.logo` |
| `tests/` | 38 self-running offline test files |
| `docs/` | Documentation (see the map below) |
| `scripts/` | Evaluation tooling |

### Local workspace directories (not tracked — created by you)

These are **git-ignored** and will **not** exist in a fresh clone. Create them as needed; nothing in the
application requires them to be present at startup.

| Path | What you put there |
|---|---|
| `icp/` | Your ICP PDFs (used by the legacy PDF qualification screen) |
| `data/` | Your own lead exports and reference material |
| `outputs/` | Run artifacts and exports you choose to save |
| `archive/` | Preserved historical code, if you keep a local copy — **never an active source** |

> If you keep historical manually-scored files locally, treat them as **not validated ground truth** —
> a review found most model-vs-benchmark disagreements were pollution in the benchmark. No active code
> loads them.

## Documentation map

Start with **`docs/README.md`** — the index and authority map.

- **`docs/ARCHITECTURE_BASELINE_v1.0.md`** — the frozen architecture constitution (authoritative).
- **`docs/REPOSITORY_STATUS.md`** — current, code-grounded state (authoritative for "what exists today").
- `docs/PRODUCT_CONSTITUTION.md` — product principles.
- `docs/CHANGELOG.md` — sprint-by-sprint history.
- `docs/PROJECT_MANIFEST.md` — project definition and standing rules.
- `docs/DECISIONS.md` — architecture decision records.
- `docs/iqs/`, `docs/product/` — the ICP Qualification Standard and ICP Workspace PRD/UX.
- **Archived** (historical, non-authoritative — each carries a `STATUS: ARCHIVED` header):
  `ARCHITECTURE.md`, `PROJECT_STATE.md`, `ROADMAP.md`, `REPOSITORY_AUDIT.md`, `CLEANUP_REPORT.md`,
  the Sprint 3 docs, and `INSTALL-MAC.md`.

## Current limitations

- **Google Sheets publishing has not been exercised against the live API.** Every deterministic layer is
  implemented and tested against a fake client, and the client surface was verified against gspread
  6.2.1, but no call has yet reached Google's servers. A live smoke test is the next step.
- **Review decisions are session-held until you explicitly save** the workspace.
- **Sales Navigator is configured manually** — you build the search in LinkedIn and paste its URL; the
  platform never constructs or interprets the URL.
- **Local and single-user** — no authentication, hosting, database, or multi-user review.
- **Without `ANTHROPIC_API_KEY` the app runs in offline mock mode** (labelled; placeholder numbers).
- XLSX generation takes a few seconds for very large batches (~3.4 s at 5000 rows).
- Qualification calibration is ongoing (dealbreaker discipline, buyer-persona scoring, subsegment
  boundaries).

## Roadmap

Next: a **live Google Sheets publish validation**. Then, in rough order: durable auto-persistence for
review decisions, additional export targets (Linked Helper, CRM), lead enrichment, and ExperimentRun
(cross-version comparison analytics). Nothing here is implemented — see
`docs/REPOSITORY_STATUS.md` for what actually exists.

## Legacy

The original Claude-Code / Vayne CLI pipeline (`scrape` / `post_enrich` / `segment`) has been superseded
by this Streamlit application. It is **not tracked in this repository** (kept only in local `archive/`
copies) and is never an active source. The deprecated `INSTALL-MAC.md` and `install-mac.command` install
that legacy CLI skill from a different repository — **do not use them**; follow
[Installation](#installation) above.

## License

MIT — see [`LICENSE`](LICENSE).
