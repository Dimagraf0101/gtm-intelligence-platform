# Architecture — Lead Intelligence Platform (MVP)

**Type:** Authoritative architecture specification. Planning/documentation only — no code.
**Governing documents:** `docs/PRODUCT_CONSTITUTION.md` (supreme), `docs/PROJECT_MANIFEST.md`,
`docs/SPRINT_3_TECH_SPEC.md`.

Throughout, content is labeled **[NOW]** (exists today), **[0.3]** (planned for Release 0.3), or
**[FUTURE]** (direction only, not committed). The architecture must stay practical for a **single
local Streamlit application** — no databases, microservices, queues, cloud, auth, Docker, FastAPI,
or React are introduced here.

---

## 1. Architecture Purpose

This document is the reference that governs how the product is structured and how it may evolve. It
exists so that every future change can be checked against a single, coherent picture of layers,
boundaries, and responsibilities — and so that the local MVP does not accidentally drift into an
over-engineered platform.

It governs decisions about: what belongs in Python vs the LLM; how evidence, scoring, confidence,
and human review relate; what each folder is for; where integrations attach; and what is in or out
of scope for each release.

The current product is the **Lead Intelligence Platform (MVP)**. The physical repository folder may
still be named **`sales-pipeline-master`** — that is the local development folder name, **not** the
product name.

---

## 2. Product Boundary

The product's boundary, end to end:

```
ICP PDF  +  raw Vayne CSV
        → qualification (evidence-grounded scoring)
        → explainable, prioritized lead list
        → mandatory Human Review Gate
        → CSV/XLSX export (Google-Sheets-compatible)
        → manual use in Linked Helper or another outreach tool
```

Explicit boundary rules (from the constitution):

- **AI never launches outreach.** The system produces a ranked, explained list; it does not contact
  anyone.
- **The product does not automatically send messages** — no email, no LinkedIn action, no scheduling.
- **Human approval remains mandatory.** Acting on a lead is always a human decision taken outside
  this software.

Everything to the left of "manual use" is the product; everything to the right is a human operating
a separate tool.

---

## 3. Current MVP Architecture — [NOW]

Only what exists today. A single local Streamlit process, driven by user uploads.

**Capabilities that exist now:**

- **Streamlit UI** — upload ICP PDF + Vayne CSV, run qualification, preview the ranked table,
  download results.
- **ICP PDF extraction** — extract and clean text from the uploaded ICP PDF.
- **CSV normalization** — map raw Vayne columns to a normalized lead view (case-insensitive,
  candidate-name matching on the *uploaded* file only).
- **Anthropic client** — real scoring via `claude-haiku-4-5-20251001`.
- **Qualification prompt** — the production scoring system prompt, loaded from disk (no prompt
  strings hard-coded in Python).
- **Python validation & category calculation** — the model returns per-dimension judgment; Python
  validates, clamps, sums, and assigns the final score and category deterministically.
- **CSV/XLSX export** — project results into canonical columns and produce Google-Sheets-compatible
  files.
- **Offline MockClient fallback** — when no API key is present, a local placeholder scorer returns
  the same shape so the workflow runs; results are clearly labeled non-production.
- **Local `.env` configuration** — secrets and settings loaded locally.

**Active files and responsibilities:**

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI: uploads → run → progress → preview → export. |
| `pipeline/config.py` | Load `.env` and resolve base paths. |
| `pipeline/icp_pdf.py` | Extract and clean ICP text from the uploaded PDF. |
| `pipeline/scoring.py` | Qualification Engine: normalization, prompt build, model/mock client, output validation, deterministic score & category, dealbreaker application. |
| `pipeline/export.py` | Project results into canonical columns → CSV / XLSX. |
| `prompts/scoring_system.md` | Production scoring system prompt. |

Archived code (`archive/**`, e.g. the legacy scrape/post-enrich/segment scripts) is **not** part of
the active architecture and is never presented as such.

---

## 4. Target Release 0.3 Architecture — [0.3]

Release 0.3 refines the current engine into a **modular qualification system**. The modules below
are **conceptual responsibilities**, not services. In the local MVP they may remain a handful of
simple Python modules and functions — **each layer is a boundary of responsibility, not a process,
container, or microservice.**

| Layer | Responsibility | Boundary |
|---|---|---|
| **1. Knowledge Layer** | Represent the ICP as a normalized internal profile (targets, dimensions, weights, thresholds, exclusions, unknown-requirements). | Owns *what "good" means*; does not score leads. |
| **2. Evidence Layer** | Turn inputs into structured, status-tagged, sourced evidence items. | Owns *what is known and how certainly*; makes no judgments about fit. |
| **3. Qualification Engine** | Combine evidence + knowledge into per-dimension assessments and explanations. | Owns *interpretation*; proposes, does not finalize. |
| **4. Validation & Decision Layer** | Deterministically validate, apply governed dealbreakers, compute the final score and category. | Owns *the verdict*; the only place Disqualified is applied. |
| **5. Confidence Layer** | Quantify coverage, quality, and decision confidence; recommend review emphasis. | Owns *how much to trust the verdict*. |
| **6. Human Review Contract** | Assemble the guaranteed information a reviewer must see. | Owns *the review data contract*; not a UI. |
| **7. Export Layer** | Produce faithful, portable artifacts of the reviewed results. | Owns *output*; never triggers outreach. |

Flow of authority: Knowledge + Evidence feed the Qualification Engine (judgment) → Validation &
Decision (verdict) → Confidence → Human Review Contract → Export. Action on the world happens only
after a human decides, outside the system.

---

## 5. Knowledge Layer — [0.3]

Represents campaign knowledge — what a good-fit lead looks like for a given ICP.

- **Current source [NOW]:** the ICP PDF (the user-facing input).
- **Future-compatible sources [FUTURE]:** structured ICP JSON, manual notes, website content, user
  edits, CRM data. These are anticipated, not built in 0.3.

For the MVP:

- the **PDF remains the user-facing source**;
- extracted ICP text must be converted into a **normalized internal representation** that the rest
  of the system consumes;
- that representation must distinguish, explicitly:
  - **target attributes** (who/what we want),
  - **scoring dimensions**,
  - **weights**,
  - **category thresholds**,
  - **hard exclusions** (dealbreakers),
  - **ambiguous definitions** (concepts the ICP does not crisply separate),
  - **unavailable enrichment requirements** (dimensions the input cannot supply — e.g. funding,
    hiring).

**ICP-specific rules override generic scoring guides.** When an ICP defines its own dimensions,
weights, thresholds, or exclusions, those govern; the generic model must not be imposed over them.

Release 0.3 does **not** build a knowledge base or database — the normalized ICP profile is an
in-memory representation derived per run from the uploaded PDF.

---

## 6. Evidence Layer — [0.3]

Defines an **evidence item** conceptually. Every important qualification claim should carry:

- **attribute** — the fact under consideration;
- **observed value** — what the data says (or nothing);
- **source field** — where it came from;
- **evidence status** — see below;
- **confidence** — how direct/strong the source is;
- **explanation** — a human-readable justification;
- **provenance** — a quotable trace to the source for audit.

**Evidence status** must support:

- **confirmed** — present in the data with an identifiable source;
- **conflicting** — sources disagree (e.g. two contradictory size fields);
- **unknown** — not present; **never a negative signal, never confirms a dealbreaker**;
- **not applicable** — the attribute does not apply to this lead.

**Missing data must remain unknown** — it is never converted into an inferred negative.

**Evidence sources in the current MVP:**

- Vayne CSV fields;
- current employment;
- previous employment;
- company metadata;
- profile summary;
- ICP rules;
- (later) human review.

**Current vs previous employment** must be distinguished, and **previous employment must never be
treated as evidence about the current company.** A person's past role at a fintech does not make
their current employer a fintech.

---

## 7. Qualification Engine — [0.3]

The user-facing intelligence component. Internally it may combine deterministic rule evaluation,
LLM judgment, dimension scoring, evidence explanation, and unknown detection — but with a strict
responsibility split.

**Python owns:**

- normalization;
- deterministic checks;
- numeric validation;
- threshold application;
- final score calculation;
- category assignment;
- confirmed deterministic exclusions.

**The LLM owns:**

- semantic interpretation;
- ambiguous industry classification;
- nuanced buyer-persona judgment;
- explanation;
- evidence mapping;
- suspected-dealbreaker proposals.

**The LLM must not make the final irreversible business decision.** It informs and proposes;
Python decides. This preserves the constitution's determinism and auditability guarantees.

---

## 8. Validation and Decision Layer — [0.3]

The only place a final verdict — including **Disqualified** — is produced.

**Dealbreaker states:**

- **confirmed** — backed by direct evidence;
- **suspected** — indirect/inferred; a flag, not a verdict;
- **none** — no supporting evidence.

**Rules:**

- **confirmed requires direct evidence;**
- **suspected never overrides the numeric result** (it lowers confidence and flags review);
- **missing information cannot confirm a dealbreaker;**
- **only Python validation may apply the final Disqualified result;**
- **every decision must remain auditable** (evidence → judgment → verdict reconstructable).

**Clarified FinTech rules discovered in the pilot** (ICP-specific — see the caution below):

- literal **recruiter / HR / talent-acquisition / procurement** roles may be **hard exclusions**;
- **CRO, CMO, Sales, and Marketing** roles are **weak buyer fit, not automatic exclusions**;
- **embedded finance and BaaS** must be **distinguished from underlying payment rails** (the former
  are targets; the latter an exclusion);
- **conflicting company-size fields lower confidence and require review**, rather than triggering
  automatic rejection;
- **historical benchmarks are not ground truth.**

> **Caution:** these are **FinTech-ICP-specific** clarifications and must **not** be hardcoded as
> global rules for every ICP. They live with the FinTech knowledge profile, not in the generic
> engine.

---

## 9. Confidence Layer — [0.3]

Confidence is grounded in evidence, not self-reported by the model. It separates:

- **evidence coverage** — how much of the rubric is backed by confirmed evidence;
- **evidence quality** — how strong/direct that evidence is;
- **decision confidence** — how much to trust the final verdict;
- **data conflict** — presence of conflicting evidence;
- **unknown-field count** — how many rubric-relevant attributes are unknown.

**Why model self-reported confidence alone is insufficient:** a model can be fluently confident
about a judgment built on little or no evidence. Grounding confidence in actual coverage and
quality prevents a low-information lead from being presented as if it were well understood.

The system should later support these reported values **[0.3 defines them; exact formulas may be
refined]**:

- **Raw ICP Score;**
- **Evidence-Adjusted Fit;**
- **Data Coverage;**
- **Final Review Recommendation.**

Conceptual example (illustrative only):

```
Raw ICP Score:            56 / 100
Available Evidence Score: 56 / 70
Evidence-Adjusted Fit:    80%
Data Coverage:            70%
```

Exact thresholds are **not** prescribed here unless already defined by the ICP itself.

---

## 10. Human Review Contract — [0.3 data contract; UI is FUTURE]

Human Review is an **architectural contract**, not merely a future UI feature. For every lead, the
reviewer must be able to see:

- final priority;
- raw score;
- evidence-adjusted fit (when applicable);
- data coverage;
- evidence by dimension;
- unknown fields;
- confirmed dealbreakers;
- suspected dealbreakers;
- confidence;
- qualification reason;
- LinkedIn URL;
- the source data required to verify the recommendation.

The reviewer must **eventually** be able to: approve, reject, skip, add a comment, and choose a
rejection reason.

**Release 0.3 prepares the data contract only** — the human-review-ready result must *contain* all
of the above. It does **not** build the full production Human Review interface.

### 10.1 Operational output structure — [0.3 emits multi-sheet XLSX; Google Sheets API is FUTURE]

The **primary future operational document** is a **Google Sheets workbook** (ADR-007). Its structure
is informed by — but not scored from — the team's reference workbook
`data/reference/fintech_scored_output_example.xlsx` (**output-format reference only; not ground
truth**). Release 0.3 produces the equivalent **multi-sheet XLSX**; **CSV/XLSX remain fallback and
backup exports**; **Linked Helper import stays manual**.

Three sheets:

- **Sheet 1 — Qualified Leads.** The lead generator's main working view. Based closely on the
  reference workbook's format (Priority, score, identity, company, reason), **enhanced** with the
  evidence, confidence, coverage, and Human Review fields of the §7.1 contract (ADR-005). Diagnostic
  columns that are useful internally but clutter the working view — e.g. Score Breakdown, ICP
  Signals, Number of Connections, Job Started, Founded Year, Specialities — are retained but grouped
  as **internal/secondary** (placed after the review fields or collapsed), not removed.
- **Sheet 2 — Approved for Outreach.** **Approved leads only** (Human Decision = Approve). A
  **compact** field set suitable for **manual Linked Helper import**: First/Last Name, Job Title,
  Company, LinkedIn URL, Location, Priority, and a short Qualification Reason for personalization.
- **Sheet 3 — Summary.** Qualification distribution (counts per Priority incl. `A+ Candidate —
  Enrichment Required` and Excluded/Not Relevant), **review progress** (reviewed / approved /
  rejected / skipped / pending), and **data-quality & dealbreaker metrics** (average Data Coverage,
  share ≥ 80%, data-conflict count, confirmed/suspected dealbreaker counts, top rejection reasons).

Sheet 1's per-lead columns are exactly the §7.1 human-review contract; Sheet 2 is a projection of
approved rows; Sheet 3 is aggregate. No sheet triggers outreach.

---

## 11. Feedback and Learning Layer — [FUTURE]

Future direction, **not** Release 0.3. Human decisions should later become structured feedback:

- AI recommendation;
- human decision;
- disagreement reason;
- corrected label;
- reviewer comment.

This feedback may later support benchmark re-baselining, rule improvement, prompt calibration, ICP
refinement, and quality analytics.

> **Hard rule:** human feedback must **not** automatically retrain or rewrite production rules.
> Any change to rules, prompts, or thresholds requires explicit human review and approval
> (constitution: architecture over prompt engineering; every decision auditable).

---

## 12. Core Data Entities — [conceptual; no schema]

Conceptual entities and relationships. No database, ORM, or code.

| Entity | Purpose | Key information | Source of truth | Relationships |
|---|---|---|---|---|
| **ICP Profile** | Normalized campaign definition. | Targets, dimensions, weights, thresholds, exclusions, unknown-requirements. | The ICP PDF (extracted). | Governs how every Lead is assessed. |
| **Lead** | A single prospect under evaluation. | Identity, current role, profile signals, LinkedIn URL. | The raw Vayne CSV row. | Belongs to one run; has one current Company, Employment Records, one Qualification Result. |
| **Company** | The current employer being evaluated. | Name, industry, size, specialities, website. | Vayne fields (current-employment columns). | Referenced by the current Employment Record; the entity dealbreakers judge. |
| **Employment Record** | One role held by the Lead. | Title, company, dates, current-vs-previous flag. | Vayne current/previous columns. | Many per Lead; only the current one is evidence about the current Company. |
| **Evidence Item** | One status-tagged, sourced claim. | Attribute, value, status, source, confidence, provenance. | Derived from Lead/Company/ICP inputs. | Supports Dimension and Dealbreaker Assessments. |
| **Dimension Assessment** | Per-dimension judgment and points. | Dimension, points, state (Confirmed/Suspected/None), evidence refs. | Qualification Engine (judgment) + Validation (points). | Aggregates into the Qualification Result. |
| **Dealbreaker Assessment** | One dealbreaker's status. | Filter, state (confirmed/suspected/none), cited evidence. | LLM proposes; Python confirms. | May override the Qualification Result. |
| **Qualification Result** | The final verdict for a Lead. | Score, category, confidence, coverage, reason, unknowns. | Validation & Decision Layer (Python). | One per Lead; consumed by Review and Export. |
| **Review Decision** | A human's decision on a Lead. | Approve/reject/skip, rejection reason, comment. | The reviewer (FUTURE). | One per reviewed Lead; feeds the Feedback Layer. |
| **Export Record** | A produced output artifact. | Columns, file, run metadata. | Export Layer. | Derived from Qualification Results. |
| **Benchmark Record** | A reference label for calibration. | Lead ref, label, provenance, validated-or-legacy flag. | Human-adjudicated gold set (legacy = not ground truth). | Compared against Qualification Results during evaluation. |

---

## 13. Data Flow

**Current MVP — [NOW]:**

```
ICP PDF → extracted text → raw CSV → normalized leads → LLM scoring
        → Python validation → preview → CSV/XLSX export → human review outside the app
```

**Release 0.3 target — [0.3]:**

```
ICP PDF → normalized ICP profile → raw CSV → normalized leads → evidence extraction
        → deterministic rules → LLM judgment → validation → confidence calculation
        → qualification result → human-review-ready export
```

**Future — [FUTURE]:**

```
human review → structured feedback → reviewed benchmark → controlled calibration
```

---

## 14. Repository Architecture

Current folders and their roles:

| Folder | Role |
|---|---|
| `pipeline/` | Active engine modules (config, icp_pdf, scoring, export). |
| `prompts/` | Production and pilot prompts. |
| `scripts/` | Evaluation tooling (current pilot harness). |
| `tests/` | Automated tests (to be populated in 0.3). |
| `docs/` | Architecture, manifest, constitution, specs, audit, reports. |
| `data/raw/` | Raw, unscored source exports only. |
| `data/benchmarks/legacy/` | Historical manual benchmarks — **not** ground truth. |
| `data/reference/` | Reference material (e.g. the rubric guide). |
| `data/samples/` | Small curated CSVs for demos/tests. |
| `outputs/pilots/` | Pilot run artifacts. |
| `outputs/exports/` | MVP-produced exports. |
| `outputs/logs/` | Run logs. |
| `icp/` | Sample/reference ICP PDFs. |
| `archive/` | Preserved historical code and outputs. |

Standing rules:

- **`archive/` is never an active implementation source.**
- **Legacy benchmarks are never automatically selected** by production code.
- **Active code must use explicit paths**, never fuzzy filename discovery.
- **Secrets remain in `.env`** (local-only).
- **Generated outputs must not be mixed with raw data** (`outputs/**` vs `data/raw/**`).

---

## 15. Integration Boundaries

Integrations are **replaceable adapters** at the edge of the system, never part of the core
qualification logic.

- **Current [NOW]:** Anthropic API; local file upload; CSV/XLSX export.
- **Planned later [FUTURE]:** Vayne API; Google Sheets API; other lead sources; other LLM
  providers; other outreach export formats.

**Vendor-specific integrations must not define the core qualification logic.** The engine reasons
over normalized inputs and a normalized ICP profile, so a vendor can be swapped without touching
scoring. These integrations are **not** designed in detail here.

---

## 16. Reliability and Error Handling

Architectural expectations (applies to scoring runs and pilots):

- **batch isolation** — leads are processed in batches that fail independently;
- **retry only failed batches** — never restart the whole run for one failure;
- **successful batches are not repeated;**
- **structured-output validation** — model output is validated before use;
- **tolerant parsing fallback** — malformed-but-recoverable output is repaired, not dropped;
- **progress reporting** — the user sees run progress;
- **per-lead error visibility** — failures are attributable to specific leads;
- **no full-run silent failure;**
- **no secret logging;**
- **API usage and cost tracking** — tokens and cost are captured;
- **hard limits for pilot and test modes** — explicit caps prevent accidental full-dataset runs.

---

## 17. Security and Privacy

- **`.env` remains local** and is never committed.
- **API keys are never printed** to logs, UI, or reports.
- **Private ICPs and lead datasets are not placed in public logs.**
- **Exports may contain personal business-contact data** and must be handled carefully.
- **Repository backups must exclude secrets** (`.env`, credentials).
- **Mock results must be clearly labeled** and can never be mistaken for production results.

---

## 18. Testing Strategy — [0.3 builds this out]

- **unit tests** for normalization, rules, validation, and category mapping;
- **fixture-based tests** that run without any API call;
- **mock-client integration tests** exercising the full pipeline offline;
- **small, controlled real-API pilots** with hard lead limits;
- **regression tests using reviewed gold sets;**
- **no full production run before calibration;**
- **benchmark quality must be verified before measuring model accuracy** (never measure against the
  polluted legacy benchmark).

---

## 19. Architecture Decisions Already Accepted

- AI recommends, humans approve;
- the Human Review Gate is mandatory;
- ICP-specific rules override generic scoring;
- missing information is not evidence;
- Python owns deterministic logic;
- the LLM owns semantic judgment;
- historical manual benchmarks are not ground truth;
- explicit paths over fuzzy discovery;
- the current model is `claude-haiku-4-5-20251001`;
- no automatic outreach;
- vendor-agnostic core;
- local-first MVP.

---

## 20. Release Boundaries

**Release 0.3 includes:**

- normalized ICP profile;
- evidence model;
- governed dealbreakers;
- corrected buyer-persona logic;
- confidence and coverage;
- human-review-ready result schema;
- tests;
- updated export columns.

**Out of scope for 0.3:**

- Google Sheets API;
- Vayne API automation;
- Linked Helper API;
- database;
- production multi-user review UI;
- authentication;
- cloud deployment;
- automatic learning;
- new ICP creation;
- CRM integrations.

---

## 21. Future Direction

- **Release 0.4:** enrichment and/or Google Sheets integration; scoring recalibration based on the
  evidence actually available (closing the "A+ unreachable" gap honestly).
- **Release 0.5:** a Human Review Workspace; structured reviewer feedback capture.
- **Release 1.0:** a stable Lead Intelligence Platform workflow; vendor adapters; reviewed
  benchmarks; operational reliability.

Kept bounded and realistic — no enterprise build-out is implied.

---

## 22. Decisions

### 22.1 Resolved (Product-Owner approved — see `docs/DECISIONS.md`)

The six previously-open decisions are now **closed and accepted**:

1. **Embedded finance vs core payment rails** — evidence-based; embedded finance = uses financial
   infrastructure to deliver a business outcome (in scope); core rails = provides the underlying
   processor/network/acquiring/core-banking layer (excluded); ambiguous = `suspected`, not
   disqualified. → **ADR-001**
2. **Universal vs ICP-specific exclusions** — universal exclusions are minimal and validity-only
   (duplicate, invalid/missing identifier, no current employment, corrupt data); commercial
   exclusions are ICP-specific and never applied globally. → **ADR-002**
3. **A+ and enrichment** — A+ requires strong core-dimension fit, no confirmed dealbreaker, and
   **Data Coverage ≥ 80%**; otherwise `A+ Candidate — Enrichment Required`; never invent data. →
   **ADR-003**
4. **Incomplete data** — do **not** auto-recalibrate ICP thresholds; every result reports Raw ICP
   Score, Evidence-Adjusted Fit, Data Coverage, and Provisional Priority; unknowns may lower
   confidence and restrict A+ but never confirm a dealbreaker. → **ADR-004**
5. **Human-review export fields** — a fixed field contract (Priority … Reviewer Comment); reviewer
   fields blank until a human acts. → **ADR-005**
6. **Standard rejection reasons** — a controlled list. → **ADR-006**

### 22.2 Still open (refinements — not blocking, require later confirmation)

These were **not** settled by the six ADRs and remain genuinely open:

- The exact **formulas** for **Evidence-Adjusted Fit** and **Data Coverage** (how coverage is
  weighted across dimensions).
- The precise definition of **"strong fit on the ICP's core dimensions"** used in the A+ gate.
- Whether **`A+ Candidate — Enrichment Required`** is a distinct Priority band or a status modifier,
  and whether **Provisional Priority** bands off Raw ICP Score or Evidence-Adjusted Fit.
- The numeric **confidence thresholds and penalty magnitudes** (Confidence Layer).
- The **enrichment mechanism** itself (deferred to Release 0.4).

---

*Architecture only. No implementation, pseudocode, database, or service design is specified here.
This document is the design reference the MVP and Release 0.3 must remain consistent with.*
