# Architecture Decision Records

Formal record of product/architecture decisions. Each is **Accepted** and governs Release 0.3 and
beyond. These records are authoritative; where a spec conflicts with an accepted ADR, the ADR wins
and the spec must be corrected. Amend only by a new dated ADR, never silently.

Governed by `docs/PRODUCT_CONSTITUTION.md`. Related: `docs/ARCHITECTURE.md`,
`docs/SPRINT_3_TECH_SPEC.md`, `docs/SPRINT_3_IMPLEMENTATION_PLAN.md`.

---

## ADR-001 — Embedded finance vs core payment rails

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** the FinTech ICP (and any ICP that adopts the same distinction). ICP-specific.

**Context.** The Sprint 2.1 pilot showed the "embedded finance / BaaS / lending" target subsegment
overlaps with the "payments-core-rails" exclusion; the engine sometimes disqualified in-scope
platforms as core rails and vice-versa. This was the only genuinely *ambiguous* class of exclusion.

**Decision.**
- **In scope (embedded finance):** a company that provides a **B2B platform, application, or
  vertical product that *uses* financial infrastructure to deliver a business outcome.**
- **Excluded (core rails):** a company that **itself primarily provides the underlying processor,
  payment network, acquiring infrastructure, core-banking infrastructure, or foundational
  transaction layer.**
- The classification must be **evidence-based**. **Ambiguous cases are `suspected`, not
  automatically disqualified** — they lower confidence and flag review; they do not confirm a
  dealbreaker.

**Consequences.** The Decision Engine must classify from cited evidence (company description,
specialities, product language), distinguish "uses rails" from "provides rails," and route
uncertainty to `suspected` rather than `confirmed`. This is a FinTech-ICP rule and must **not** be
generalized to other ICPs.

---

## ADR-002 — Universal vs ICP-specific exclusions

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** all ICPs (defines the boundary between global and per-ICP rules).

**Context.** Sprint 2 fired commercial exclusions (agency, bank, crypto, size, geography) as if they
were universal truths, and the manual benchmark encoded ad-hoc exclusions inconsistently. Root
cause: no boundary between *data/process validity* and *commercial fit*.

**Decision.**
- **Universal exclusions** (apply to every ICP) are **minimal and concern data/process validity
  only:**
  - duplicate record;
  - invalid or missing profile identifier;
  - no identifiable current employment;
  - unusable or corrupt source data.
- **Commercial exclusions** — agency, consultancy, bank, crypto company, recruitment firm, company
  size, geography, industry, subsegment — are **ICP-specific** and live with the ICP's knowledge
  profile.
- **Never apply one ICP's commercial exclusions globally.**

**Consequences.** The engine separates a universal validity gate (small, global) from ICP-scoped
commercial dealbreakers (loaded from the ICP profile). FinTech-specific rules (ADR-001, ADR-006's
title rules) are scoped to FinTech.

---

## ADR-003 — A+ classification and Data Coverage

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** all ICPs.

**Context.** The anti-hallucination rule makes some rubric dimensions (funding, hiring, reachability)
unearnable from the raw export, so a genuinely strong lead can look A+ while the system actually
knows very little about it. Awarding A+ on thin evidence would mislead reviewers.

**Decision.** A final **A+** requires **all** of:
1. **strong fit on the ICP's core dimensions**;
2. **no confirmed dealbreaker**;
3. **Data Coverage ≥ 80%**.

When fit appears A+ but **Data Coverage < 80%**, the result is classified as
**`A+ Candidate — Enrichment Required`** instead of A+. **Missing data must never be invented to
reach A+.**

**Consequences.** The Confidence/Decision layers must compute Data Coverage and gate the top
category on it. A new priority value/status, `A+ Candidate — Enrichment Required`, enters the result
schema. (Enrichment itself is Release 0.4; 0.3 only *surfaces* the gap.)

---

## ADR-004 — Handling incomplete data (no auto-recalibration)

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** all ICPs.

**Context.** When coverage is low, one tempting "fix" is to move the ICP's score thresholds so more
leads clear the bar. That silently redefines the ICP and destroys comparability.

**Decision.**
- **Do not automatically recalibrate or replace the ICP's original thresholds.**
- Every result must support four reported values:
  - **Raw ICP Score** — the score against the ICP's own rubric/thresholds;
  - **Evidence-Adjusted Fit** — fit relative to the evidence actually available;
  - **Data Coverage** — how much of the rubric was backed by confirmed evidence;
  - **Provisional Priority** — the system's pre-review priority.
- **Missing information is unknown, not negative evidence.** Unknowns may **lower confidence and
  restrict final A+ eligibility** (per ADR-003) but must **not independently confirm a dealbreaker.**

**Consequences.** Thresholds stay fixed and ICP-owned. The result carries both the raw score and the
evidence-adjusted view so a human can see the difference rather than the system hiding it behind a
moved threshold.

---

## ADR-005 — Human-review export contract

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** the human-review-ready result/export (Release 0.3 produces the data; UI is later).

**Context.** "Humans approve" requires the reviewer to see a fixed, complete set of facts. Sprint 2
outputs did not guarantee this.

**Decision.** The human-review-ready result must include exactly these fields:

Priority · Raw ICP Score · Evidence-Adjusted Fit · Data Coverage · Confidence · Review Status ·
First Name · Last Name · Job Title · Company · LinkedIn URL · Company LinkedIn URL · Company Website ·
Location · Employee Count · Industry · Qualification Reason · Evidence Summary ·
Confirmed Dealbreakers · Suspected Dealbreakers · Unknown Fields · Human Decision · Rejection Reason ·
Reviewer Comment.

**`Human Decision`, `Rejection Reason`, and `Reviewer Comment` remain empty until a human acts.**

**Consequences.** The export schema is defined by this contract (a superset of today's columns). The
system fills everything except the three reviewer fields, which are left blank for the human.

---

## ADR-006 — Standard human rejection reasons

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** the Review Decision (reviewer input; captured as data in 0.3, acted on later).

**Context.** Free-text rejection reasons cannot be aggregated for benchmark re-baselining or ICP
refinement.

**Decision.** Rejection reasons use this controlled list only:

Wrong company type · Wrong industry/subsegment · Wrong geography · Wrong company size ·
Wrong buyer/title · Wrong current employment · Duplicate · Already contacted · Data incorrect ·
Insufficient evidence · Other.

**Consequences.** `Rejection Reason` is constrained to these values (with `Other` + a free-text
`Reviewer Comment` for anything outside the list). This standardization feeds the future feedback
loop; it does not, by itself, change any rule (constitution: feedback never auto-rewrites rules).

---

## ADR-007 — Existing scored workbook is an output-format reference, not a scoring benchmark

- **Status:** Accepted (2026-07-11), Product Owner approved.
- **Applies to:** output structure / export design.

**Context.** The team added `data/reference/fintech_scored_output_example.xlsx`, the current
semi-manual document the lead-generation team works from. Its structure is valuable UX guidance, but
its scores/categories are manually produced and — like all historical manual outputs (ADR-002,
ADR-004, and the Sprint 2.1 findings) — are **not** validated labels.

**Decision.**
- The workbook is an **output-format and UX reference only.** Its sheet structure, column layout, and
  summary style inform the target export. Its **scores, categories, and qualification decisions are
  not validated ground truth** and must never be used as labels or as an accuracy target (same rule
  as `data/benchmarks/legacy/`).
- The **primary future operational document** is a **Google Sheets workbook with three sheets**:
  **Sheet 1 — Qualified Leads**, **Sheet 2 — Approved for Outreach**, **Sheet 3 — Summary**.
- **CSV/XLSX remain fallback and backup exports.** **Linked Helper import remains manual.**
- The Google Sheets **API integration is out of scope for Release 0.3** (Release 0.4+). Release 0.3
  produces the equivalent **multi-sheet XLSX** shaped to the target, plus CSV fallbacks.

**Consequences.** The Export Layer evolves from a single flat table into the three-sheet structure
described in `docs/ARCHITECTURE.md` §10.1 and `docs/SPRINT_3_IMPLEMENTATION_PLAN.md` §6. The
reference file stays under `data/reference/`, labeled reference-only, and is never read by production
scoring.

---

*ADR-001…007 are Accepted. The corresponding items in `docs/ARCHITECTURE.md` §22 are now closed;
remaining refinements are tracked there under "Still open."*

---

## ADR-008 — Business Knowledge is the single Source of Truth

- **Status:** Accepted (Sprint 4.2E / 5.1).
- **Applies to:** the ICP Workspace subsystem.

**Context.** The ICP Workspace must support many ICPs over one shared knowledge base, with intact
evidence attribution.

**Decision.** **Business Knowledge** (`pipeline/business_knowledge.py`) is the single source of truth
for ICP creation: the evidence-attributed, status-tracked knowledge base from which everything else
is derived. All curation and the (planned) interview operate here.

**Consequences.** Facts live in one place with provenance; ICPs are cheap projections; knowledge
compounds across ICPs instead of being re-elicited per ICP.

## ADR-009 — A Generated ICP is a derived projection

- **Status:** Accepted (Sprint 4.2E / 5.1).

**Decision.** A `GeneratedICP` is a **derived artifact** regenerated from Business Knowledge on
demand; it is **never edited as a source of facts**. Knowledge-resolving edits go to Business
Knowledge; the ICP is then regenerated.

**Consequences.** Regeneration is always safe and reflects current knowledge; the ICP never becomes a
competing store of facts (which would fracture the source of truth).

## ADR-010 — The AI Interview operates on Business Knowledge

- **Status:** Accepted (Sprint 4.2E). **Implementation: planned.**

**Context.** Evaluated interviewing the Draft ICP (Option B) vs Business Knowledge (Option A).

**Decision.** The interview operates on **Business Knowledge** (Option A): answers are written back
as knowledge (`origin=user_input`, confirm, resolve conflict), then the draft is regenerated.

**Consequences.** Interview effort is amortized across all ICPs; ICPs can be regenerated without
repeating interviews; no second knowledge-state machine is duplicated on `GeneratedICP`.

## ADR-011 — Knowledge Review and Strategy Review are separate

- **Status:** Accepted (Sprint 5.1). **Strategy Review: planned.**

**Decision.** **Knowledge Review** curates company *facts* in Business Knowledge (implemented,
Sprint 5.1). **Strategy Review** is a separate, thin, per-ICP step for ICP-*strategy* only (dimension
weights, thresholds, which candidate exclusion applies) and **never writes facts**.

**Consequences.** Two clearly-scoped review surfaces; strategy tuning does not pollute the shared
knowledge base.

## ADR-012 — Backward-compatible ICP-PDF qualification remains available until the bridge lands

- **Status:** Accepted (Sprint 5.1).

**Decision.** Lead Qualification's legacy path (upload ICP PDF → `scoring.score_leads(icp.text, …)`)
**remains the supported input** until the Generated-ICP → Engine **bridge** is implemented.
`icp_adapter.to_engine_profile` exists and is tested but is not yet consumed by `scoring`.

**Consequences.** Qualification is never blocked on the ICP Workspace; the bridge (Sprint 2A) is
additive and does not remove the PDF path.
