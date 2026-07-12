# ICP Qualification Standard (IQS) v1.0 (Sprint 4.0, design only)

**Status:** Standard specification. No implementation. Defines what makes an ICP **valid, complete,
evidence-grounded, and usable by the Qualification Engine.** Vendor-neutral and **industry-agnostic**
— it contains no industry-specific rules. Companion: `docs/iqs/ICP_PROFILE_SCHEMA.md`.

Governed by `docs/PRODUCT_CONSTITUTION.md`.

---

## 1. Purpose

The IQS is the quality bar every generated ICP must clear before it is approved and used. It ensures
an ICP is **complete** (all required sections present), **consistent** (weights, thresholds, and
exclusions do not contradict), **honest** (missing information is declared, not invented), and
**engine-ready** (shaped so the Qualification Engine can consume it without redesign). The IQS is a
*standard*, not a tool: a set of rules a validator (built later) enforces.

The IQS applies **identically to both module entry points** — *Standardize Existing ICP* (convert an
existing ICP document into the standard, without modifying the original) and *Create New ICP*
(generate from company knowledge). Both converge on the same internal ICP Profile and clear the same
IQS gate before human approval.

## 2. Definitions

- **ICP** — a single, independent qualification strategy for one target (e.g. an industry, segment,
  or region). An organization may have many.
- **ICP Profile** — the normalized internal representation of an ICP (see `ICP_PROFILE_SCHEMA.md`).
- **Target criterion** — a *preferred* attribute (e.g. preferred company size or geography). Being
  outside a target criterion lowers fit; it does **not**, by itself, disqualify.
- **Hard exclusion** — an *explicit* rule that disqualifies a lead outright when directly evidenced
  (e.g. "reject companies with fewer than 50 employees", "reject staffing agencies").
- **Qualification dimension** — a scored axis of fit (e.g. buyer persona, subsegment, size,
  geography), each with a weight.
- **Priority thresholds** — the score bands the ICP defines for its internal categories.
- **Evidence** — a sourced fact backing a claim in the ICP.
- **Unknown field** — an attribute the ICP acknowledges it cannot assess from available data.
- **Enrichment field** — an attribute that requires data a standard lead export cannot supply and
  that would need a future enrichment step.

## 3. Core principles

1. **Target ≠ exclusion.** Preferences and hard rejections are distinct and never conflated.
2. **Missing information is unknown, not negative.** The ICP must declare unknowns; it must never
   invent facts, customers, funding, hiring, or exclusions to appear complete.
3. **Every material claim is evidence-attributed** to a source or an interview answer, or explicitly
   marked unknown.
4. **Determinism where it matters:** weights, thresholds, and exclusion structure are explicit and
   checkable. **Judgment stays with the model** at qualification time, not baked into rigid prose.
5. **Human approval is mandatory** before an ICP is used.
6. **Vendor- and industry-neutral:** the standard defines *shape and rules*, never specific
   commercial content.

## 4. Required sections

An ICP is **valid only if all required sections are present and well-formed** (details in
`ICP_PROFILE_SCHEMA.md`):

- **Metadata** (name, scope, version, status).
- **Business Context** (who the organization is / what it sells, at a high level).
- **Products** and/or **Services** (at least one).
- **Target Companies** (target attributes — industries/subsegments, size preference, geography
  preference — as *preferences*).
- **Target Buyers** (personas / title families being targeted).
- **Qualification Dimensions** (with weights).
- **Priority Thresholds**.
- **Hard Exclusions** (may be an explicit "none declared", but the section must exist and be
  deliberate).
- **Evidence Requirements** (what evidence each major claim rests on).
- **Unknown Fields** and **Enrichment Fields** (declared honestly).

Optional but recommended sections (Examples, Warnings, History) improve quality but do not block
validity.

## 5. Writing rules

- **Plain, specific, and testable.** Prefer concrete attributes over marketing prose.
- **Separate preferences from rejections** in wording (a target company section may say "prefer 50–500
  employees"; a hard exclusion must say "reject if …").
- **One idea per rule.** Hard exclusions are individually stated and individually auditable.
- **No inferred commercial exclusions.** An exclusion may be written only if the organization
  explicitly intends it.
- **Name unknowns explicitly** rather than omitting them.
- **No chain-of-thought or raw model output** is stored in the ICP; only conclusions with evidence.

## 6. Validation rules

An ICP fails IQS (blocking) when any of these hold; it warns (non-blocking, must be acknowledged)
for the softer cases noted:

- **Missing required section** → fail.
- **Duplicated qualification dimension** → fail.
- **Invalid weights** — non-numeric, negative, out of range, or a weight sum outside the accepted
  tolerance → fail.
- **Priority thresholds** overlapping, mis-ordered, or with min > max → fail.
- **Contradictory exclusions** — a value both targeted and hard-excluded → fail.
- **Malformed employee/size range** or geography entry → fail.
- **Hard exclusion without an explicit rule** (e.g. a size *preference* mislabeled as a hard
  exclusion) → fail (this protects the target-vs-exclusion boundary).
- **Unattributed material claim** (a target, exclusion, or example with no evidence and not marked
  unknown) → warn (or fail if it underpins a hard exclusion).
- **Low overall evidence coverage** → warn.
- **Enrichment-dependent dimension not marked as such** → warn.

Validation **returns the issues; it never edits the ICP**.

## 7. Evidence rules

- Every **target attribute, hard exclusion, and example** must trace to (a) a source with a
  quotable snippet, (b) an interview answer, or (c) an explicit "unknown/assumed by reviewer" marker.
- **Conflicting evidence is recorded, not resolved silently**; a field backed by conflicting sources
  cannot be used as a hard exclusion and is flagged for review.
- **Hard exclusions require the strongest evidence:** they may be marked "confirmed" only when the
  organization explicitly states the rule; otherwise they remain a preference or a suspected item for
  human confirmation.
- **Previous/historical facts are not treated as current facts.**
- **Confidence** accompanies major fields, derived from source directness and cross-source agreement.

## 8. Scoring rules (ICP-side)

The IQS governs how an ICP *defines* scoring, not how the engine executes it:

- Qualification dimensions carry **explicit weights**; the weight set must be internally consistent
  (valid range, consistent total).
- **Priority thresholds** must be a clean, ordered, non-overlapping set of bands.
- The ICP must distinguish **dimensions scorable from typical lead data** from **enrichment-dependent
  dimensions**; the latter are declared so downstream coverage/confidence stay honest.
- The ICP defines its own **internal category bands**; the operational priority mapping applied at
  qualification time is owned by the engine and is not re-specified per ICP.
- **Hard exclusions override scoring** by definition and are validated as explicit, evidenced rules.

## 9. Versioning

- Every ICP carries a **version** and a **status** (Draft · Needs Info · Ready for Review · Approved).
- The IQS itself is versioned (this is **IQS v1.0**); an ICP records which IQS version it was
  validated against.
- **Any change to dimensions, weights, thresholds, targets, or hard exclusions creates a new ICP
  version**; approval is per version. Cosmetic edits may be minor versions.
- Superseding an approved ICP does not delete its history.

## 10. Review rules

- **Approval is a human act** and is only possible when IQS validation passes with no blocking errors
  and all warnings acknowledged.
- The reviewer must be able to see, for every field, its **evidence, confidence, and unknown status**.
- **Feedback never auto-rewrites the ICP**; a reviewer's edits are explicit and re-validated.
- An approved ICP that is later edited **returns to Draft/Needs Info** and must be re-validated and
  re-approved before use.
- The standard is **advisory to humans, not autonomous**: it flags and blocks, but people decide.
