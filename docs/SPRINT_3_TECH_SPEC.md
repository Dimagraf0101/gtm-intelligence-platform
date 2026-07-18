> **STATUS: ARCHIVED — NOT AUTHORITATIVE**
> Retained for history; do not treat as current architecture or status.
> REPLACED BY: (point-in-time Sprint 3 spec; no direct replacement)
> (Archived in the Sprint 7.3 documentation audit. See docs/README.md for the authority map.)

# Sprint 3 — Technical Specification (Release 0.3)

**Type:** Architecture and design specification. **No implementation.**
**Product:** Lead Intelligence Platform (MVP).
**Governing document:** `docs/PRODUCT_CONSTITUTION.md` — every design below must comply with it.

This document is the implementation specification for the next development phase. It defines
*what* Release 0.3 must do and *how it is structured*, not how to build it.

---

## 0. Approved product decisions (locked)

The Product Owner has approved six decisions (full records in `docs/DECISIONS.md`). They are
**binding** on everything below; where an earlier passage was tentative, these govern:

- **ADR-001 — Embedded finance vs core rails (FinTech-specific):** embedded finance (uses financial
  infrastructure to deliver a business outcome) is in scope; core rails (provides the underlying
  processor/network/acquiring/core-banking layer) are excluded; **ambiguous = `suspected`, never
  auto-disqualified**; classification is evidence-based.
- **ADR-002 — Universal vs ICP-specific exclusions:** universal exclusions are minimal and
  validity-only (duplicate, invalid/missing identifier, no current employment, corrupt data). All
  commercial exclusions (agency, bank, crypto, recruitment, size, geography, industry, subsegment)
  are ICP-specific and **never applied globally**.
- **ADR-003 — A+ requires Data Coverage ≥ 80%** (plus strong core-dimension fit and no confirmed
  dealbreaker); otherwise **`A+ Candidate — Enrichment Required`**; never invent data to reach A+.
- **ADR-004 — Incomplete data:** ICP thresholds are **not** auto-recalibrated; every result reports
  **Raw ICP Score, Evidence-Adjusted Fit, Data Coverage, Provisional Priority**; unknowns lower
  confidence and restrict A+ but never confirm a dealbreaker.
- **ADR-005 — Human-review export contract:** a fixed field set (see §7); reviewer fields stay blank
  until a human acts.
- **ADR-006 — Standard rejection reasons:** a controlled list (see §7).

---

## 1. Goal

**Make every qualification decision evidence-grounded, explainable, and trustworthy enough for a
human to approve or reject with confidence.**

Release 0.2 proved the pipeline runs end-to-end and can score real leads against a real ICP with a
real model. Release 0.3 turns that raw scoring into a **decision a reviewer can trust**: every
score is backed by cited evidence, every gap is explicitly marked unknown (never guessed),
dealbreakers are governed rather than improvised, and a confidence signal tells the reviewer how
much to rely on the result.

The business objective is not "higher scores" — it is **defensible scores**: outputs a
go-to-market team can act on because they can see *why*, and can see *what the system did not
know*. This directly operationalizes the constitution's core principles (AI recommends / humans
approve; explainability mandatory; every score includes evidence; missing information is never
evidence).

---

## 2. Problems discovered during Sprint 2

Symptoms were many; the root causes are few. This section separates them.

### 2.1 What we observed

- **MVP validation (Release 0.2):** the full workflow (upload → score → preview → export) worked,
  but scoring ran in offline placeholder mode and confidence was a flat, meaningless value.
- **FinTech Pilot (Sprint 2):** one batch of the highest-value leads was **lost to a JSON parse
  failure**; every scored lead was hard-disqualified; several dealbreakers fired on the **absence**
  of funding data ("no Series B/C signal"); the sample skewed to low-value leads; the chosen
  benchmark file did not even match the raw export.
- **Corrected FinTech Pilot (Sprint 2.1):** robust parsing and stratified sampling fixed the
  mechanics, but category agreement with the manual benchmark was **5%** and dealbreaker agreement
  **20%**. Manual review showed **74% of disagreements were the model correctly rejecting
  companies the ICP explicitly excludes** (crypto, HFT, payments-core-rails, consumer neobank,
  a public company, banks, a BPO, a healthcare EHR, a networking platform, consultancies).
- **Documentation cleanup:** legacy datasets, prompts, and outputs were indistinguishable from
  active ones, and active tooling was at risk of discovering the wrong file by ambiguous name.

### 2.2 Root causes (not symptoms)

1. **There is no evidence layer.** The engine conflates three distinct states — *a known fact*,
   *an affirmed absence*, and *unknown/not-present* — into one undifferentiated input. Because
   "unknown" is not a first-class concept, the model treated missing data as a signal and fired
   dealbreakers on absence. This is the single deepest root cause: **without evidence status,
   nothing downstream can reason honestly about what is or isn't known.**

2. **Scoring dimensions require data the input cannot supply.** The ICP rubric rewards funding
   stage, engineering hiring, and reachability — none of which exist in a raw Vayne export.
   With the anti-hallucination rule correctly forbidding invention, those dimensions are
   *structurally unearnable*, which (a) caps achievable scores (A+ is unreachable) and (b) creates
   constant pressure on the model to fabricate. The gap is an **enrichment gap**, not a scoring bug.

3. **Dealbreaker logic is monolithic and ungoverned.** All twelve filters live as prose in one
   prompt with no distinction between filters that can be *confirmed from data* (size, subsegment,
   business model) and filters that require information the data never contains (distress, stage,
   negative history, campaign touch). This produced both over-application (a CRO disqualified as
   "procurement") and absence-firing.

4. **Confidence is a bare model self-report.** It is not tied to how much evidence actually backed
   the decision, so it cannot guide human review.

5. **There is no clean ground truth.** The manual benchmarks are polluted with ICP-excluded
   companies. Measuring "accuracy" against them is meaningless; the project has been comparing a
   more-faithful system to a less-faithful reference.

6. **There is no human-review contract.** Nothing guarantees the reviewer sees the evidence,
   the unknowns, or the basis for a dealbreaker — so "humans approve" cannot yet be done well.

Release 0.3 addresses causes **1, 3, 4, 6** directly and **5** partially (a clean evaluation set);
cause **2** (enrichment) is explicitly deferred to 0.4 but must be *surfaced* in 0.3 so its impact
is visible rather than hidden.

---

## 3. Functional Requirements

Release 0.3 introduces four cooperating capabilities. They form a pipeline of responsibility, each
consuming the previous one's output:

```
Inputs → Evidence Engine → Decision Engine → Confidence Engine → Human Review preparation
```

| Capability | Purpose |
|---|---|
| **Evidence Engine** | Convert raw inputs into structured, status-tagged, sourced evidence — the honest substrate for everything else. |
| **Decision Engine** | Turn evidence + the ICP rubric into per-dimension assessments and a deterministic score, category, and governed dealbreaker verdict. |
| **Confidence Engine** | Quantify how much the decision can be trusted, based on evidence coverage and missing information, and recommend whether human review is required. |
| **Human Review preparation** | Assemble the guaranteed information contract a reviewer must see before approving or rejecting a lead. |

Each capability must be **explainable, reproducible, and auditable** on its own (per the
constitution's Definition of Done), and each must preserve the separation of *deterministic rules
in Python* from *judgment in the LLM*.

---

## 4. Evidence Engine

The Evidence Engine is the foundation. Its job is to answer, for every fact the scoring depends on:
**do we know this, and if so, from where and how certainly?**

### 4.1 Evidence model

Each relevant attribute of a lead becomes an **evidence item** with (conceptually) these
properties:

| Property | Meaning |
|---|---|
| Attribute | The fact under consideration (e.g. company size, subsegment, buyer role, geography, funding stage). |
| Value | What the data says the value is — or nothing, if unknown. |
| Status | One of **Confirmed**, **Absent**, or **Unknown** (defined below). |
| Source | Where the value came from (which input field or document region). |
| Evidence confidence | How direct the source is: a structured field is stronger than a phrase inferred from free text. |
| Provenance | A short, quotable justification from the source so the claim can be audited. |

### 4.2 Evidence status

- **Confirmed** — the fact is present in the data with an identifiable source.
- **Absent** — the data affirmatively establishes the fact is not true or not applicable (rare, and
  itself requires a source).
- **Unknown** — the fact is simply not present. **Unknown is never a negative signal and never
  triggers a dealbreaker.** This is the central rule the Evidence Engine exists to enforce.

### 4.3 Evidence source

Evidence must trace to a concrete origin: a specific normalized input field, a region of the ICP
document, or a clearly-labeled text inference. The engine must distinguish **current employment**
from **previous employment** and never let historical facts stand in for current ones.

### 4.4 Evidence confidence

Each item carries its own confidence, driven by source directness — a structured field (e.g. an
explicit employee count) is high; a value inferred from a bio sentence is medium; a loose
associative reading is low. Evidence confidence feeds the Confidence Engine; it is not the same as
overall lead confidence.

### 4.5 Unknown fields

The engine must produce an explicit, enumerated list of the attributes it could **not** confirm
that are relevant to the ICP's rubric — especially those the raw export can never contain (funding
stage, hiring signal, reachability). This list is a first-class output, not an afterthought, and
flows to both the Confidence Engine and Human Review.

### 4.6 Explainability

Every evidence item must be human-readable on its own: a reviewer should be able to read the
attribute, its status, its value, and its provenance and understand it without seeing the raw data.
No score may later depend on an attribute that has no evidence item.

*(No implementation, schema code, or storage design is specified here — only the model.)*

---

## 5. Decision Engine

The Decision Engine consumes evidence and the ICP rubric and produces the verdict. It is where the
constitution's "deterministic rules in Python, judgment in the LLM" boundary is enforced.

### 5.1 Per-dimension assessment states

For each rubric dimension, the assessment is classified as:

- **Confirmed** — the dimension is supported by Confirmed evidence; full scoring applies.
- **Suspected** — only indirect or text-inferred evidence supports it; scored cautiously and
  flagged as uncertain.
- **None** — no evidence supports it; the dimension scores at its floor, is recorded as Unknown,
  and contributes to lower confidence. **None is never a penalty and never a dealbreaker.**

This replaces the current implicit behavior where a missing dimension silently became a low or
zero score with no indication of *why*.

### 5.2 Dealbreakers

Dealbreakers are split into two governed classes, and this governance is the core correction from
Sprint 2:

- **Evidence-derivable dealbreakers** — those a data source can confirm (wrong size, excluded
  subsegment, wrong business model / not a target company type, disqualifying current title,
  sanctioned geography). These may fire **only** on **Confirmed** evidence, with the specific
  evidence item cited.
- **Inference-only dealbreakers** — those that depend on information the input cannot contain
  (company distress, funding stage, negative history, prior-campaign contact, technology stack).
  These may **never** fire from absence. Without Confirmed evidence they resolve to *not fired* and
  the related attribute is recorded as Unknown.

A dealbreaker is a hard override: when fired, the lead is not relevant regardless of other points —
but the firing must always be traceable to a cited, Confirmed evidence item.

### 5.3 How deterministic rules and LLM judgement cooperate

- **The LLM provides judgment:** it interprets the evidence, classifies each dimension as
  Confirmed / Suspected / None, and *proposes* dealbreaker candidates — each with a cited evidence
  item and short reasoning. It never emits a final score, a final category, or a final dealbreaker
  decision.
- **Python provides the verdict:** it validates that every proposed dimension score and dealbreaker
  is backed by an evidence item of the required status; enforces the governance in §5.2; clamps and
  sums points; applies caps and overrides; and assigns the final score and category deterministically.
- **Conflicts are resolved by rule, not by the model:** where input fields disagree (e.g. an
  employee count that contradicts a size range), Python prefers the more reliable source and lowers
  confidence rather than letting a conflict fire a dealbreaker.

The result: identical inputs always yield the same verdict, and every number is defensible by an
evidence trail.

---

## 6. Confidence Engine

The Confidence Engine answers: **how much should a human trust this result, and must they review
it?** It converts evidence completeness into a signal, so low-information leads are never silently
presented as if they were well-understood.

### 6.1 Overall confidence

A single, explainable confidence level for the lead, derived from the evidence backing the
decision — not a model self-report. It must be reproducible and must move in the expected
direction (more Confirmed evidence on high-weight dimensions → higher confidence).

### 6.2 Evidence coverage

The proportion of scoring-relevant rubric dimensions backed by **Confirmed** evidence, versus
**Suspected**, versus **None**. Coverage is reported alongside the score so a reviewer can see how
complete the picture was.

### 6.3 Missing information

The enumerated Unknown attributes (from §4.5) that materially affect the score — in particular the
structurally-unearnable dimensions (funding, hiring, reachability). This makes the enrichment gap
(root cause §2.2.2) **visible** in 0.3 even though enrichment itself is deferred.

### 6.4 Confidence penalties

Confidence is reduced, by explicit and documented rules, when: high-weight dimensions are None or
Suspected; the score sits near a category boundary; a dealbreaker was fired on anything weaker than
Confirmed evidence (which should not happen, but must be caught); or input fields conflict.

### 6.5 Human review recommendations

The engine emits a clear recommendation — for example **review required** vs **review optional** —
driven by confidence, coverage, boundary proximity, and dealbreaker certainty. Because the Human
Review Gate is mandatory (constitution), this signal governs *prioritization and emphasis* of
review, never whether review happens at all.

---

## 7. Human Review

Release 0.3 defines the **information contract** for review — the set of facts a reviewer must
always see before approving or rejecting a lead. **(This section specifies information, not UI.)**

Before a lead can be approved, the reviewer must always be shown:

1. The **final score and category**, and that they were computed deterministically.
2. The **per-dimension breakdown**, each with its assessment state (Confirmed / Suspected / None)
   and its **cited evidence**.
3. **Every dealbreaker that fired**, with the exact Confirmed evidence that triggered it.
4. The **Unknown / missing-information list**, explicitly including any structurally-unearnable
   dimensions (so the reviewer knows what the system could not assess).
5. The **overall confidence and evidence coverage**, and the **review recommendation**.
6. Any **data conflicts** the Decision Engine resolved, and how.
7. A clear statement that **no outreach has been or will be taken** by the system — approval is a
   human act (constitution: AI recommends, humans approve).

### 7.1 Human-review export contract (ADR-005)

The human-review-ready result must contain exactly these fields (a superset of today's columns):

`Priority` · `Raw ICP Score` · `Evidence-Adjusted Fit` · `Data Coverage` · `Confidence` ·
`Review Status` · `First Name` · `Last Name` · `Job Title` · `Company` · `LinkedIn URL` ·
`Company LinkedIn URL` · `Company Website` · `Location` · `Employee Count` · `Industry` ·
`Qualification Reason` · `Evidence Summary` · `Confirmed Dealbreakers` · `Suspected Dealbreakers` ·
`Unknown Fields` · `Human Decision` · `Rejection Reason` · `Reviewer Comment`.

The system fills all fields **except** `Human Decision`, `Rejection Reason`, and `Reviewer Comment`,
which **stay empty until a human acts**.

### 7.2 Standard rejection reasons (ADR-006)

`Rejection Reason` is constrained to this controlled list: Wrong company type · Wrong
industry/subsegment · Wrong geography · Wrong company size · Wrong buyer/title · Wrong current
employment · Duplicate · Already contacted · Data incorrect · Insufficient evidence · Other.

Nothing that influenced the outcome may be hidden from the reviewer, and nothing may be presented
as certain when it rests on Suspected or absent evidence.

---

## 8. Acceptance Criteria

Release 0.3 is complete only when all of the following are objectively demonstrable:

1. **Evidence completeness** — 100% of awarded (non-floor) dimension points are backed by an
   evidence item with a source and provenance. No points exist without evidence.
2. **Unknown honesty** — every rubric-relevant attribute not present in the data is enumerated as
   Unknown; 0 dimensions are penalized for being Unknown.
3. **Dealbreaker governance** — 0 evidence-derivable dealbreakers fire without a Confirmed cited
   evidence item; 0 inference-only dealbreakers fire from absence, across a representative run.
4. **Determinism** — identical inputs produce identical scores, categories, and dealbreaker
   verdicts on repeated runs.
5. **Robust processing** — 0 leads are lost to malformed model output across a run of at least 100
   leads (parse recovery is proven, not assumed).
6. **Confidence validity** — confidence and coverage are present for every lead and correlate with
   evidence completeness; low-coverage leads are flagged review-required by rule.
7. **Human-review contract** — for every scored lead, all §7 items and all §7.1 export fields are
   produced together; none is missing; the three reviewer fields are present and empty.
8. **Clean evaluation set** — a small, human-adjudicated **gold set** (e.g. the Sprint 2.1 twenty
   leads, re-labeled against the ICP's actual exclusions) exists and is used for calibration;
   agreement is reported against this gold set, **not** against the polluted legacy benchmark.
9. **Auditability** — for any lead, its inputs, evidence, per-dimension judgment, computed verdict,
   and confidence can be reconstructed after the fact.
10. **A+ gate (ADR-003)** — no lead is labeled final **A+** unless it has strong core-dimension fit,
    no confirmed dealbreaker, **and Data Coverage ≥ 80%**; leads meeting fit but not coverage are
    labeled **`A+ Candidate — Enrichment Required`**; 0 leads reach A+ via invented data.
11. **No threshold recalibration (ADR-004)** — the ICP's original thresholds are unchanged by the
    system; Raw ICP Score, Evidence-Adjusted Fit, Data Coverage, and Provisional Priority are all
    reported; 0 dealbreakers are confirmed by unknown/missing information.

Measurement uses the gold set (criterion 8), never `data/benchmarks/legacy/`.

---

## 9. Risks

**Technical**

- **Evidence fabrication** — the model may cite evidence that isn't really in the data. Mitigation:
  Python validates every citation against the actual input before it can score or fire a dealbreaker.
- **Parsing/robustness at scale** — richer, structured model output is larger and more failure-prone.
  Mitigation: proven parse-recovery is an explicit acceptance criterion (§8.5).
- **Cost and latency** — evidence-rich prompts increase tokens per lead. Mitigation: measure per-lead
  cost in the pilot and project it before scale-up (as Sprint 2 did).
- **Over-engineering the evidence model** — a schema more elaborate than the decision needs.
  Mitigation: the constitution's "architecture over prompt engineering" is not "architecture for its
  own sake"; keep the evidence model minimal and driven by what the Decision Engine consumes.

**Product**

- **The enrichment gap persists** — without funding/hiring data, A+ remains structurally
  unreachable and legitimate leads stay under-scored. Mitigation: 0.3 makes the gap *visible* via
  missing-information reporting; a decision on recalibrating thresholds vs adding enrichment (0.4)
  must be taken explicitly, not by default.
- **Gold-set labor** — building clean ground truth is manual and opinionated. Mitigation: start
  small (the 20 reviewed FinTech leads) and grow it deliberately.
- **Reviewer overload** — surfacing all evidence risks overwhelming the human. Mitigation: the
  Confidence Engine's recommendation prioritizes attention; §7 defines the minimum, not a maximum.
- **Calibration is ICP-specific** — rules tuned on FinTech may not transfer. Mitigation: keep the
  ICP as the source of truth (constitution) and validate per ICP before claiming generality.

---

## 10. Out of Scope

Release 0.3 explicitly does **not** include:

- **Google Sheets API** — exports remain CSV/XLSX files, as today.
- **Linked Helper** or any outreach-platform integration.
- **CRM integrations** of any kind.
- **New ICPs** — 0.3 calibrates against the existing FinTech ICP; onboarding new ICPs is later.
- **Automation** — no scheduling, no background runs, no auto-actions. The Human Review Gate stays
  mandatory and manual.
- **Live enrichment / Vayne API automation** — the funding/hiring enrichment that would close the
  gap in §2.2.2 is deferred to 0.4; 0.3 only *surfaces* the gap.
- **A production Human Review UI** — 0.3 defines the review *information contract* (§7), not its
  interface.
- **Multi-ICP or batch cross-ICP scoring**, and any model change beyond the current
  `claude-haiku-4-5-20251001` unless separately decided.

---

## 11. Future Releases

Brief roadmap; architecture-level intent only.

- **0.4 — Close the enrichment gap.** Introduce evidence *enrichment* so structurally-unearnable
  dimensions (funding stage, engineering hiring, reachability) can become Confirmed evidence, then
  recalibrate thresholds. This is what allows genuinely strong leads to reach the top categories
  without fabrication.
- **0.5 — Human Review experience & feedback loop.** Turn the §7 information contract into a real
  reviewer surface, and capture reviewer decisions as structured feedback that sharpens future
  scoring — the first step from "scoring" toward "learning."
- **1.0 — Lead Intelligence Platform.** Calibrated, evidence-grounded qualification across multiple
  ICPs; feedback-driven improvement of judgment; and controlled integrations — always bound by this
  project's constitution: more capable and more insightful, but an advisor to humans, never an
  autonomous actor.

---

*Architecture only. No code, pseudocode, or implementation is specified in this document. It is the
design contract Release 0.3 must satisfy.*
