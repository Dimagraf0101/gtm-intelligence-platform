# ICP Profile Schema (Sprint 4.0, design only)

**Status:** Model specification. **No JSON, no code.** Describes the internal ICP model the Generator
produces and the Qualification Engine consumes. Vendor-neutral and industry-agnostic. Companion:
`docs/iqs/IQS_v1.0.md`, `docs/product/ICP_GENERATOR_PRD.md`.

Each section below is documented as: **Purpose · Required · Optional · Description · Validation ·
Examples.** "Required" means the section (or field) must be present for an ICP to be IQS-valid; an
empty-but-deliberate declaration (e.g. "no hard exclusions") still satisfies presence where noted.

**Mapping note:** where a section maps to something the current engine already understands, it is
called out (the engine's Knowledge Layer already models dimensions + weights, category thresholds,
target geographies, employee ranges, excluded company types, hard exclusions, and enrichment fields).

---

## 1. Metadata

- **Purpose:** identify this ICP as one independent strategy among many.
- **Required:** name; scope tags (industry and/or segment and/or region); status; created/updated
  timestamps.
- **Optional:** owner/author label; short description; source-set summary; **entry-point origin**
  (Standardize Existing ICP vs Create New ICP) recorded for audit only — the Qualification Engine
  does not read or branch on it.
- **Description:** the ICP's identity. Scope tags let an organization keep parallel ICPs distinct
  (e.g. "Healthcare Enterprise" vs "Healthcare SMB" vs "Healthcare Germany"). Status is one of
  Draft · Needs Info · Ready for Review · Approved.
- **Validation:** name non-empty and unique within the organization; status from the allowed set;
  timestamps present.
- **Examples:** name "FinTech — DACH, mid-market"; scope tags {industry: FinTech, region: DACH,
  segment: mid-market}; status Draft.

## 2. Business Context

- **Purpose:** capture who the organization is and what it offers, at a level sufficient to justify
  the targeting.
- **Required:** a concise statement of the offering and the outcome it delivers.
- **Optional:** value proposition, differentiators, typical engagement model.
- **Description:** grounds the ICP in the seller's reality; used by the model to interpret fit
  semantically. Not a marketing essay.
- **Validation:** present and non-trivial; claims should be evidence-attributed (see §9).
- **Examples:** "Provides embedded analytics that shorten reporting cycles for B2B SaaS platforms."

## 3. Products

- **Purpose:** enumerate concrete product offerings.
- **Required:** at least one Product **or** at least one Service (§4) must exist.
- **Optional:** per-product description, category, maturity.
- **Description:** the "what we sell" that a good-fit buyer would buy.
- **Validation:** if present, each product has a name; the combined Products+Services set is
  non-empty.
- **Examples:** "Embedded dashboard SDK"; "Managed data pipeline".

## 4. Services

- **Purpose:** enumerate concrete service offerings.
- **Required:** see §3 (at least one of Products/Services).
- **Optional:** delivery model, typical scope, packaging.
- **Description:** for service-led organizations (agencies, consultancies), this is the primary
  offering section.
- **Validation:** if present, each service has a name.
- **Examples:** "Team augmentation (2–4 engineers)"; "Fractional RevOps".

## 5. Target Companies

- **Purpose:** describe the **preferred** company profile — as *preferences*, not rejections.
- **Required:** target industries and/or subsegments; target company types; a size **preference**; a
  geography **preference**.
- **Optional:** company maturity/stage preference, technographic hints, specialities.
- **Description:** the attributes that make a company a strong fit. **These are target criteria:**
  being outside them lowers fit but does not disqualify. Size and geography here are *ranges/lists of
  preference*, explicitly separate from Hard Exclusions (§9). Maps to the engine's target
  geographies, employee ranges (target), and target industries/company types.
- **Validation:** size preference is a well-formed range or set; geography is a clean list;
  industries/types are non-empty lists; **nothing here may be phrased as a rejection** (that belongs
  in §9).
- **Examples:** preferred size 50–500 employees; preferred regions {US, UK, DACH}; target subsegments
  {embedded finance, wealth-tech}.

## 6. Target Buyers

- **Purpose:** define the personas / title families the ICP targets.
- **Required:** at least one primary buyer persona or title family.
- **Optional:** secondary/approver personas; seniority notes; buying-role notes.
- **Description:** who to reach; distinguishes primary buyer from approver. Title *families* (e.g.
  "VP Engineering / CTO / Head of Platform") rather than exhaustive title lists. Maps to the engine's
  buyer-persona dimension.
- **Validation:** at least one persona; personas are non-empty; excluded titles belong in §9, not
  here.
- **Examples:** primary {VP Engineering, CTO}; approver {Founder/CEO/COO}.

## 7. Qualification Dimensions

- **Purpose:** define the scored axes of fit and their weights.
- **Required:** a set of named dimensions, each with a weight.
- **Optional:** per-dimension guidance on how to evaluate it.
- **Description:** the rubric the engine scores against (e.g. subsegment fit, buyer persona, company
  size, geography, plus any enrichment-dependent dimensions). Weights express relative importance.
  Maps directly to the engine's dimensions + weights.
- **Validation:** no duplicate dimension names; weights numeric and in range; weight total internally
  consistent (per IQS §6); enrichment-dependent dimensions cross-referenced in §12.
- **Examples:** {Subsegment fit: 25, Buyer persona: 15, Company size: 10, Geography: 10, Stage &
  funding: 20 (enrichment), Hiring signal: 10 (enrichment), Reachability: 10 (enrichment)}.

## 8. Priority Thresholds

- **Purpose:** define the ICP's internal category score bands.
- **Required:** an ordered, non-overlapping set of bands with labels and score ranges.
- **Optional:** per-band action guidance.
- **Description:** the ICP's own category bands (its internal decision categories). The
  **operational priority mapping** applied at qualification time is owned by the engine and is not
  redefined per ICP. Maps to the engine's category thresholds.
- **Validation:** bands ordered, non-overlapping, min ≤ max, covering the intended range.
- **Examples:** {A+: 85–110, A: 70–84, B: 55–69, C: 40–54, Not Relevant: 0–39}.

## 9. Hard Exclusions

- **Purpose:** list the **explicit** rules that disqualify a lead outright when directly evidenced.
- **Required:** the section must exist and be deliberate; it may explicitly state "none declared".
- **Optional:** rationale per exclusion.
- **Description:** the only place rejections live. Each exclusion is an individual, explicit rule
  (e.g. excluded company types/industries, excluded titles, sanctioned geography, an explicit hard
  size rule such as "reject if fewer than 50 employees"). **A size or geography *preference* from §5
  is not a hard exclusion** — that separation is enforced. Maps to the engine's excluded company
  types and to explicit hard-exclusion rules (including the explicit hard employee-size exclusion the
  pre-qualification layer requires).
- **Validation:** each exclusion explicit and individually stated; **no value both targeted (§5/§6)
  and excluded**; a size/geography exclusion must be written as a rejection rule, not a preference;
  exclusions require the strongest evidence (IQS §7).
- **Examples:** "Reject staffing/recruitment firms"; "Reject if company size < 50 employees";
  "Reject sanctioned geographies".

## 10. Evidence Requirements

- **Purpose:** record what evidence each major claim rests on.
- **Required:** for every target attribute, hard exclusion, and example — a source reference,
  interview answer, or explicit unknown marker.
- **Optional:** confidence notes; conflicting-source notes.
- **Description:** the audit backbone. Mirrors the engine's Evidence Layer (confirmed / conflicting /
  unknown / not applicable), sourced and quotable. Conflicts are recorded, not resolved silently.
- **Validation:** no unattributed hard exclusion; unattributed non-critical claims warn; conflicting
  evidence flagged and barred from hard exclusions.
- **Examples:** target size ← "Service catalogue, p.3: 'we serve 50–500-employee firms'"; exclusion
  ← interview answer 2026-07-12.

## 11. Unknown Fields

- **Purpose:** declare, honestly, what the ICP cannot assess from available data.
- **Required:** present (may be empty); each entry names the unknown attribute.
- **Optional:** why unknown; whether an interview attempt was made.
- **Description:** makes missing information explicit so downstream coverage/confidence stay honest.
  Unknown ≠ negative.
- **Validation:** entries are real attribute names; no field is simultaneously "known with a value"
  and "unknown".
- **Examples:** {funding stage: unknown}, {engineering headcount: unknown}.

## 12. Enrichment Fields

- **Purpose:** declare attributes that require data a standard lead export cannot supply.
- **Required:** present (may be empty); each entry names the enrichment-dependent attribute and the
  dimension(s) it feeds.
- **Optional:** suggested enrichment source category (non-vendor-specific).
- **Description:** e.g. funding, hiring signal, engineering headcount, revenue, traffic, technology
  stack, recent activity, reachability. Keeps the engine's coverage/confidence honest and marks which
  dimensions can only be earned with enrichment. Maps to the engine's enrichment-required fields.
- **Validation:** each enrichment field references an existing dimension; not both "scorable from
  standard data" and "enrichment-only".
- **Examples:** {Stage & funding fit → funding data}, {Hiring signal → open-roles data}.

## 13. Examples

- **Purpose:** provide concrete illustrative good-fit and poor-fit companies/buyers.
- **Required:** optional (recommended by IQS, not blocking).
- **Optional:** annotations explaining why each example fits or not.
- **Description:** helps reviewers and the model calibrate; not ground truth.
- **Validation:** examples, if present, are evidence-attributed (or clearly marked hypothetical).
- **Examples:** good-fit: "150-person embedded-finance platform, US"; poor-fit: "3-person crypto
  startup".

## 14. Warnings

- **Purpose:** carry non-blocking issues surfaced during generation/validation.
- **Required:** present (may be empty).
- **Optional:** severity notes.
- **Description:** e.g. low evidence coverage, a resolved-but-noted conflict, a preference that nearly
  became an exclusion. Mirrors the engine's validation-warnings behavior — surfaced, never
  auto-fixed.
- **Validation:** informational; do not block approval but must be acknowledged (IQS §10).
- **Examples:** "Geography preference derived from a single source"; "Two sources disagreed on target
  size; used the interview answer."

## 15. Version

- **Purpose:** identify this exact ICP version and the IQS version it was validated against.
- **Required:** ICP version identifier; IQS version reference; status.
- **Optional:** approval marker (who/when) once approved.
- **Description:** any change to dimensions, weights, thresholds, targets, or hard exclusions creates
  a new version (IQS §9).
- **Validation:** version present; IQS version referenced; approved status only with a passing IQS
  report.
- **Examples:** ICP v3, validated against IQS v1.0, status Approved.

## 16. History

- **Purpose:** preserve the lineage of the ICP across versions.
- **Required:** optional (recommended).
- **Optional:** per-version change summary; superseded-by/supersedes links; reviewer notes.
- **Description:** append-only record; superseding an approved ICP never deletes prior versions.
- **Validation:** entries are ordered and reference real versions.
- **Examples:** "v2 → v3: raised Company size weight 10→12; added hard exclusion 'recruitment firms'."

---

*This schema is the contract between the ICP Generator and the Qualification Engine. Because it emits
the shape the engine's Knowledge Layer already understands — with target criteria and hard exclusions
kept strictly separate — no engine redesign is required to consume a generated ICP.*

*Both entry points — **Standardize Existing ICP** and **Create New ICP** — produce this identical
model and are validated by the same IQS. The engine consumes an approved profile and is agnostic to
the entry point; any origin marker lives only in Metadata/History for audit.*
