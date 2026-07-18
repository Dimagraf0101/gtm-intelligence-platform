> **STATUS: ARCHIVED — NOT AUTHORITATIVE**
> Retained for history; do not treat as current architecture or status.
> REPLACED BY: (point-in-time Sprint 3 plan; no direct replacement)
> (Archived in the Sprint 7.3 documentation audit. See docs/README.md for the authority map.)

# Sprint 3 — Implementation Plan (Release 0.3)

**Type:** Planning only. No production code, no pseudocode, no API calls, no file moves in this
document. This plan defines *how* Release 0.3 will be implemented later, consistent with
`docs/DECISIONS.md` (ADR-001…006), `docs/SPRINT_3_TECH_SPEC.md`, `docs/ARCHITECTURE.md`, and
`docs/PRODUCT_CONSTITUTION.md`.

**Constraints (do not introduce):** databases, microservices, FastAPI, React, Docker, cloud
infrastructure, authentication, Google Sheets API, Vayne API automation, Linked Helper integration.
The system stays a single local Streamlit app with plain Python modules.

---

## 1. Exact active files to modify

| File | Change (responsibility after change) |
|---|---|
| `prompts/scoring_system.md` | New model-output contract: per-dimension `{points, state, evidence[]}`, evidence items with provenance, dealbreaker **candidates** with `suspected`/`confirmed-proposal` + cited evidence, unknown fields; embedded-finance-vs-core-rails guidance (FinTech); explicit "never emit final score/category/coverage/priority". |
| `pipeline/scoring.py` | Becomes the **orchestrator**: normalize → build evidence → LLM judgment → validation/decision → confidence → result. Extend the result type (see §4). Keep the deterministic score/category math. |
| `pipeline/export.py` | Produce the **three-sheet workbook** (§6): Sheet 1 = ADR-005 human-review contract + internal columns, Sheet 2 = approved-only compact, Sheet 3 = summary metrics; reviewer fields emitted empty; XLSX + CSV fallbacks. |
| `app.py` | Preview surfaces the new fields and `Review Status`; offline banner + explicit mock labeling retained/strengthened. No new UI framework. |
| `pipeline/config.py` | Add named constants/paths only (e.g. `DATA_COVERAGE_A_PLUS_MIN = 80`, ICP-profile location). No logic change. |
| `requirements.txt` | Expected unchanged (no new runtime deps anticipated). Confirm during CP5. |

`pipeline/icp_pdf.py` is **not** expected to change (extraction stays as-is; interpretation moves to
the new Knowledge module).

## 2. Exact new modules (kept minimal)

Three small modules, each a *responsibility boundary* (not a service). They may be collapsed into
`scoring.py` if that proves simpler during implementation; the split is for testability.

| New module | Responsibility |
|---|---|
| `pipeline/icp_profile.py` | **Knowledge Layer.** Turn extracted ICP text into a normalized ICP profile: target attributes, dimensions, weights, thresholds, **ICP-specific commercial exclusions**, ambiguous definitions, and unavailable-enrichment requirements. FinTech-specific rules (ADR-001 embedded-finance/rails; ADR-006-adjacent title rules) live here **as ICP data**, never in the generic engine. |
| `pipeline/evidence.py` | **Evidence Layer.** Define the evidence item, assign status (`confirmed` / `conflicting` / `unknown` / `not_applicable`), keep current-vs-previous employment separate, and compute **Data Coverage**. |
| `pipeline/decision.py` | **Validation & Decision + Confidence.** The **universal validity gate** (ADR-002), governed dealbreakers, deterministic score/category, the **A+ / coverage gate** (ADR-003), evidence-grounded confidence, Evidence-Adjusted Fit, and Provisional Priority / Review Status. |

If `decision.py` grows awkward, its confidence portion may split into `pipeline/confidence.py`;
decide during CP4.

## 3. Responsibility of each module (summary)

- `icp_profile.py` — *what "good" means* for this ICP. No lead scoring.
- `evidence.py` — *what is known and how certainly*. No fit judgment.
- `scoring.py` — orchestration + the LLM judgment step (proposals only) + assembling the result.
- `decision.py` — *the verdict*: the only place `Disqualified` and the final category are applied.
- `export.py` — the human-review export contract.
- `app.py` — upload/run/preview/download; mock labeling.

## 4. Data models that must change

**New (conceptual — no schema/ORM):**

| Model | Key fields (conceptual) |
|---|---|
| `ICPProfile` | dimensions + weights, category thresholds, target attributes, ICP-specific exclusions, ambiguous definitions, unavailable-enrichment requirements. |
| `EvidenceItem` | attribute, observed value, source field, status (`confirmed`/`conflicting`/`unknown`/`not_applicable`), confidence, explanation, provenance, `employment_scope` (current/previous/none). |
| `DimensionAssessment` | dimension, points, max, state (`Confirmed`/`Suspected`/`None`), evidence refs. |
| `DealbreakerAssessment` | filter id, class (universal-validity / ICP-commercial), state (`confirmed`/`suspected`/`none`), cited evidence. |

**Changed — the current `ScoringResult` → `QualificationResult` (superset, additive):**

Retains `score`, `category`, `dimensions`, `reason`, `unknowns`, `model`, `error`. Adds:
`raw_icp_score`, `evidence_adjusted_fit`, `data_coverage`, `provisional_priority`, `review_status`
(incl. `A+ Candidate — Enrichment Required`), grounded `confidence`, `evidence` (list of
EvidenceItems), `dimension_assessments`, `confirmed_dealbreakers`, `suspected_dealbreakers`, and the
**empty** reviewer placeholders `human_decision`, `rejection_reason`, `reviewer_comment`.

`Lead` gains explicit current-vs-previous employment structure and the fields needed by the
universal validity gate (identifier presence, duplicate marker, current-employment presence).

## 5. Prompt-schema changes (`prompts/scoring_system.md`)

- The model returns, per lead: per-dimension `{points, state, evidence[]}`; a list of evidence items
  with provenance; **dealbreaker candidates** each as `suspected` or a `confirmed-proposal` with
  cited evidence; the enumerated unknown fields; and a short reason.
- The model **must not** emit: final total, final category, Data Coverage, confidence number,
  Provisional Priority, or a final dealbreaker verdict. Those are Python's.
- FinTech guidance added: embedded-finance-vs-core-rails (ADR-001), and that ambiguous → `suspected`.
- Explicit restatement: unknown ≠ negative; previous employment is not evidence about the current
  company.
- Output remains strict JSON with tolerant-parse recovery (per §16 of the spec).

## 6. Export-schema changes (`pipeline/export.py`)

### 6.1 Reference workbook inspection (`data/reference/fintech_scored_output_example.xlsx`)

Output-format reference only — **not** ground truth (ADR-007). Contents:

- **Sheets:** `Fintech Leads Scored` (1,075 data rows × 20 cols) and `Summary`.
- **Data columns:** `#`, `Lead Score`, `Priority Status`, `First Name`, `Last Name`, `Job Title`,
  `Job Started`, `LinkedIn URL`, `Number of Connections`, `Location`, `Company`,
  `Company LinkedIn URL`, `Company Website`, `LinkedIn Employees`, `LinkedIn Founded Year`,
  `LinkedIn Industry`, `LinkedIn Specialities`, `ICP Signals`, `Score Reason`, `Score Breakdown`.
- **Summary metrics:** campaign + ICP title; a Category→Count table (A+ / A / B / C with the
  workbook's own thresholds ≥85 / ≥70 / ≥55 / ≥30), `Total qualified` (1075) and `Excluded` (1211).
  (Thresholds and counts are reference styling only, not validated.)

### 6.2 Column disposition for the target output

| From reference workbook | Target disposition |
|---|---|
| `Lead Score` → **Raw ICP Score**; `Priority Status` → **Priority** | Keep (main view) |
| First/Last Name, Job Title, Company, LinkedIn URL, Company LinkedIn URL, Company Website, Location | Keep (main view) |
| `LinkedIn Employees` → **Employee Count**; `LinkedIn Industry` → **Industry**; `Score Reason` → **Qualification Reason** | Keep (main view) |
| `Number of Connections`, `Job Started`, `LinkedIn Founded Year`, `LinkedIn Specialities`, `ICP Signals`, `Score Breakdown` | **Internal/secondary** — retained but grouped/collapsed after the review fields; useful for audit/personalization, kept out of the main working view |

**New Human Review / engine fields to add** (ADR-005 + engine): `Evidence-Adjusted Fit`,
`Data Coverage`, `Confidence`, `Review Status`, `Evidence Summary`, `Confirmed Dealbreakers`,
`Suspected Dealbreakers`, `Unknown Fields`, and the empty reviewer fields `Human Decision`,
`Rejection Reason`, `Reviewer Comment`.

### 6.3 Target output structure (ADR-007 — multi-sheet XLSX in 0.3; Google Sheets API is 0.4+)

- **Sheet 1 — Qualified Leads:** the §7.1 human-review contract (ADR-005) as the primary columns,
  plus the §6.2 internal/secondary columns grouped/collapsed. Reviewer fields emitted empty.
- **Sheet 2 — Approved for Outreach:** rows where `Human Decision = Approve` only; compact columns
  for manual Linked Helper import (First/Last Name, Job Title, Company, LinkedIn URL, Location,
  Priority, short Qualification Reason).
- **Sheet 3 — Summary:** qualification distribution (incl. `A+ Candidate — Enrichment Required` and
  Excluded), review progress (reviewed/approved/rejected/skipped/pending), and data-quality &
  dealbreaker metrics (avg Data Coverage, share ≥80%, data-conflict count, confirmed/suspected
  dealbreaker counts, top rejection reasons).

Release 0.3 produces this as a **multi-sheet XLSX** (+ CSV fallbacks per sheet). The live Google
Sheets API is **out of scope** for 0.3. In 0.3, Sheet 2 will typically be empty (no approvals until
the review UI exists) but its structure is produced.

## 7. Backward-compatibility risks

- **Result shape change** breaks `export.build_dataframe`, the `app.py` preview, and any tooling that
  reads `ScoringResult`. Mitigation: additive evolution + update export and app in the same
  checkpoint; keep `score`/`category` stable.
- **Export change** — the flat 22-column CSV becomes a **three-sheet workbook** (Sheet 1 = 25-field
  contract + internal columns; Sheet 2 approved-only; Sheet 3 summary). Anything consuming the old
  single-table CSV must move to Sheet 1 (or its per-sheet CSV fallback). Mitigation: the new workbook
  is the single supported export; the Sprint-2 pilot comparison tooling has its own schema and is out
  of the MVP path (updated separately, not a blocker).
- **MockClient** must produce the new richer output or offline mode breaks. Mitigation: update the
  mock in the same checkpoint as the prompt schema (CP5) and keep it clearly labeled non-production.
- **New Priority value** `A+ Candidate — Enrichment Required` must be handled by any category-count
  or sort logic. Mitigation: centralize category/priority definitions.
- **The v2 pilot harness** (`scripts/pilot_fintech_v2.py`) has its own local schema; engine changes
  do not break it, and migrating it onto the new engine is explicitly out of 0.3 scope.

## 8. Tests to create (`tests/`)

- **Unit — normalization:** current-vs-previous employment separated; universal-validity fields
  populated.
- **Unit — universal validity gate:** only duplicate / invalid-or-missing identifier / no current
  employment / corrupt data trip it; no commercial rule appears here.
- **Unit — dealbreaker governance:** `confirmed` requires direct evidence; `suspected` never changes
  the numeric result; `unknown` never confirms a dealbreaker.
- **Unit — A+ gate:** fit-A+ with coverage < 80% → `A+ Candidate — Enrichment Required`; ≥ 80% → A+.
- **Unit — coverage & evidence-adjusted fit:** computed deterministically; thresholds unchanged.
- **Unit — category mapping:** unchanged ICP thresholds applied to Raw ICP Score.
- **Unit — embedded-finance vs core-rails:** in-scope, excluded, and ambiguous(→`suspected`) cases.
- **Unit — export contract (multi-sheet):** Sheet 1 has all 25 contract fields in order (+ internal
  columns) with reviewer fields empty; Sheet 2 contains only approved rows with the compact field
  set; Sheet 3 carries the distribution / review-progress / data-quality / dealbreaker metrics.
- **Fixture-based (no API):** small sample leads drive the whole pipeline.
- **Mock-client integration:** full offline run produces a valid contract, labeled non-production.
- **Regression — gold set:** the Sprint 2.1 twenty leads, re-labeled against the ICP's real
  exclusions, used for agreement (never the legacy benchmark).

## 9. Migration strategy from the current `ScoringResult`

1. Introduce `QualificationResult` as an **additive superset** (keeps `score`, `category`,
   `dimensions`, `reason`, `unknowns`).
2. Provide a documented old→new field mapping and a temporary projection so `export.py`/`app.py` can
   be switched over in one checkpoint rather than piecemeal.
3. Validate parity offline (mock) first: same inputs → same `score`/`category` as today, plus the new
   fields — proving the change is additive, not a scoring change.
4. Only after the mock parity check and the export/app swap, run a small hard-limited real pilot.
5. Retire the old `ScoringResult` name once nothing references it. No data migration is needed (no
   database; outputs are files).

## 10. Implementation sequence — checkpoints + acceptance

Each checkpoint is small, independently testable, and offline unless noted. **No Anthropic API call
until CP7**, which is hard-limited.

- **CP0 — Baseline & scaffolding.** Create `tests/`, the gold set, and a frozen snapshot of current
  mock output.
  *Accept:* tests run; current behavior snapshot captured; nothing else changed.
- **CP1 — Knowledge Layer (`icp_profile.py`).** Normalized ICP profile incl. FinTech rules + the
  universal-validity list definition.
  *Accept:* profile exposes dimensions/weights/thresholds/exclusions/unknown-requirements; FinTech
  commercial rules are ICP-scoped; unit tests pass.
- **CP2 — Evidence Layer (`evidence.py`).** Evidence items, four statuses, current/previous scope,
  Data Coverage computation.
  *Accept:* unknown never negative; previous employment flagged non-current; coverage deterministic;
  unit tests pass.
- **CP3 — Decision Layer (`decision.py`).** Universal validity gate + governed dealbreakers;
  deterministic score/category unchanged; A+/coverage gate.
  *Accept:* confirmed needs evidence; suspected never overrides; unknown never confirms; A+ gate
  behaves per ADR-003; score/category parity with today on fixtures; unit tests pass.
- **CP4 — Confidence Layer.** Grounded confidence, Evidence-Adjusted Fit, Provisional Priority,
  Review Status.
  *Accept:* confidence derives from coverage/quality (not model self-report); values reproducible;
  unit tests pass.
- **CP5 — Prompt schema + MockClient.** New model-output contract + tolerant validation; mock updated
  and labeled.
  *Accept:* malformed output is rejected/recovered; offline full-pipeline integration test passes;
  mock results carry a non-production label.
- **CP6 — Export workbook + preview.** `export.py` emits the three-sheet workbook (§6): Sheet 1
  contract + internal columns, Sheet 2 approved-only, Sheet 3 summary; `app.py` surfaces Sheet 1.
  *Accept:* multi-sheet export test passes; reviewer fields empty; Sheet 2 approved-only; Sheet 3
  metrics correct; preview shows new fields; offline end-to-end works.
- **CP7 — Small controlled real pilot.** Hard-limited run on the gold set; capture tokens/cost;
  compare against the gold set (never the legacy benchmark).
  *Accept:* 0 lead losses on ≥100-lead robustness check (mock) and on the pilot; agreement reported
  vs gold set; cost captured; no secret printed.

## 11. Acceptance criteria for the release

The per-checkpoint criteria above roll up to the Release 0.3 acceptance criteria in
`docs/SPRINT_3_TECH_SPEC.md` §8 (now including criteria 10 A+ gate and 11 no-recalibration).

## 12. Rollback plan

- **Version control first:** before implementation begins, initialize git in the repo so each
  checkpoint is a revertible commit. (Not done in this planning task.)
- **Checkpoint isolation:** each CP is additive and independently revertible; the working app keeps
  running on the prior modules until CP6 swaps the export/preview.
- **File-level rollback:** because there is no database and outputs are files, rollback = restore the
  affected module(s) to their previous version (from git, or from the pre-cleanup backup ZIP noted in
  `docs/CLEANUP_REPORT.md`). No data migration to undo.
- **Fast disable:** if a real-API pilot (CP7) misbehaves, revert to the mock path — the offline
  pipeline remains fully functional and clearly labeled.

---

## 13. Explicit guardrails (must be enforced and tested)

Per the task's required protections, each is a rule **and** a test:

| Must be prevented | Where enforced | Test |
|---|---|---|
| Missing data becoming **negative** evidence | Evidence status `unknown` is inert; decision rules forbid negative scoring from absence | Unit: unknown attribute → floor points, no penalty, no dealbreaker |
| **Suspected** dealbreakers overriding score | Only Python-**confirmed** dealbreakers apply `Disqualified`; suspected only flags + lowers confidence | Unit: suspected dealbreaker leaves score/category unchanged |
| **Previous employment** treated as current-company evidence | Evidence carries `employment_scope`; decision uses current only | Unit: a past fintech role does not make the current employer in-scope |
| **ICP-specific exclusions** becoming universal | Commercial exclusions live in `icp_profile`; universal gate is a separate minimal list | Unit: universal gate contains no commercial rule; FinTech rule absent from other ICPs |
| **Model self-reported confidence** as the sole signal | Confidence computed in Python from coverage/quality; model confidence is at most an input | Unit: confidence changes with coverage even when model confidence is fixed |
| **Mock** results confused with production | Mock output + result + export + UI all carry a clear non-production label | Unit/integration: label present in offline results; absent when live |

---

*Planning only. Implementation is not started. This plan may be refined at each checkpoint but must
remain consistent with the accepted ADRs and the constitution.*
