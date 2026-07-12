# ICP Generator — Product Requirements (Sprint 4.0, design only)

**Status:** Architecture/PRD. No implementation. The existing **Lead Qualification Engine is stable
and unchanged**; the ICP Generator is a new **auxiliary module** that produces standardized ICPs the
engine can consume.

**Vendor-neutral:** this product references no specific agency, company, or brand. It must serve
agencies, internal sales teams, consulting firms, software companies, and any B2B organization.

Governed by `docs/PRODUCT_CONSTITUTION.md`. Companion documents: `docs/product/ICP_GENERATOR_UX.md`,
`docs/iqs/IQS_v1.0.md`, `docs/iqs/ICP_PROFILE_SCHEMA.md`.

---

## 1. Goals

- Let any B2B organization create a **standardized, machine-usable ICP** without hand-writing a long
  document.
- Derive as much of the ICP as possible from **existing business materials**, then ask only for what
  is genuinely missing.
- Produce ICPs that **immediately qualify against the existing engine** — same profile shape the
  engine already understands (dimensions, weights, category thresholds, target attributes, hard
  exclusions, evidence/enrichment requirements).
- Enforce a **quality bar** (the ICP Qualification Standard, IQS) so every generated ICP is complete,
  evidence-grounded, and internally consistent before it is used.
- Support **many ICPs per organization**, each an independent qualification strategy.

**Non-goals of the module:** it does not score leads, launch outreach, or change the engine. It
*creates the input* the engine consumes.

## 2. Product vision (target workflow)

```
Business Materials → Knowledge Extraction → Business Knowledge → Gap Detection
     → AI Interview → ICP Generation → IQS Validation → Approved ICP → Lead Qualification
```

The Qualification Engine sits at the end unchanged. Everything to its left is the ICP Generator.

## 3. User types

| User | Needs |
|---|---|
| **Sales / GTM operator** (primary) | Turn their materials into a usable ICP fast; answer a few questions; get a validated profile. |
| **Sales / RevOps lead** | Maintain several ICPs (segments, regions); review and approve; keep them consistent. |
| **Consultant / agency strategist** | Build ICPs on behalf of clients from client materials; repeatable, standardized output. |
| **Reviewer / approver** | Read the generated ICP, see evidence and gaps, approve or send back. |
| **Engine (system consumer)** | Receive an approved ICP in the shape it already understands. |

No authentication/roles are designed in this sprint (out of scope); "reviewer" is a workflow role,
not an access-control concept yet.

## 4. Supported workflows — two entry points

The module has **exactly two entry points**. Both converge on the **same internal ICP Profile**, and
the Qualification Engine **cannot tell which path produced it** (see §4.3).

### 4.1 Entry Point 1 — Standardize Existing ICP

- **Purpose:** let a user who already has an ICP document convert it into the internal IQS standard.
- **Workflow:** Upload Existing ICP → Extract Structure → IQS Validation → Detect Missing Sections →
  Generate Standardized IQS ICP → Human Review → Use for Qualification.
- **Supported formats:** PDF, DOCX, Markdown, TXT (JSON later).
- **Immutability rule:** the **original ICP is never modified.** The system reads it and **generates a
  new, standardized IQS version** as a separate artifact; the source document is preserved untouched.
- **Gap-filling:** where the existing ICP is missing required IQS sections, the same **targeted
  interview** (Entry Point 2, step 4) is used to fill only the gaps — never a rewrite of the whole ICP.

### 4.2 Entry Point 2 — Create New ICP

- **Purpose:** generate a completely new ICP from company knowledge.
- **Workflow:** Upload Business Materials → Business Knowledge Extraction → Gap Detection →
  AI Interview → Generate IQS ICP → IQS Validation → Human Review → Use for Qualification.
- **Sub-modes** (same entry point): *materials-first* (rich materials, minimal interview) and
  *interview-first* (thin/no materials, the interview covers more).

### 4.3 Convergence guarantee

- Both entry points **produce exactly the same final internal ICP Profile** (`ICP_PROFILE_SCHEMA.md`).
- Both are gated by the **same IQS validation** and the **same human approval**.
- The Qualification Engine receives an approved ICP and is **agnostic to the entry point** — there is
  no path marker in what the engine consumes. (The profile's own metadata/history may record its
  origin for audit, but that is not something the engine reads or branches on.)

### 4.4 Additional flows (variants, not new entry points)

- **Refine an existing standardized ICP** and **clone & specialize** (derive a regional/segment
  variant) are edits/derivations of an already-standardized ICP; they re-enter at Review & Validate,
  not as a third entry point.

Every path ends in the same place: an **IQS-validated, approved ICP** available to the engine.

### Multiple ICPs per organization
The system must treat ICPs as a **set of independent qualification strategies**, e.g. *Healthcare*,
*Healthcare Enterprise*, *Healthcare SMB*, *Healthcare Germany*, *Healthcare UK*, *FinTech*,
*Manufacturing*, *Legal*, *AI*, *Cybersecurity*. Never assume a single ICP. Each has its own name,
scope, dimensions, thresholds, and exclusions, and is validated and used independently.

## 5. Supported inputs (knowledge sources)

Design must anticipate these source types (implementation later; extraction quality varies by type
and is surfaced, never faked):

- **Document formats:** PDF, DOCX, PPTX, TXT, Markdown.
- **Business artifacts:** portfolio, pitch deck, sales deck, case studies, service catalogue,
  commercial proposal, existing ICP, discovery notes, meeting notes.
- **Customer signals:** customer lists, best-customer lists, lost-customer lists.

Each source is treated as **evidence with attribution** (see §11 Business Knowledge Model). Sources
are optional individually; the system works with whatever is provided and asks for the rest.

## 6. Outputs

1. **The ICP Profile** — a normalized internal representation matching `ICP_PROFILE_SCHEMA.md`, ready
   for the engine (metadata, business context, products/services, target companies, target buyers,
   qualification dimensions + weights, priority thresholds, **hard exclusions distinct from target
   preferences**, evidence requirements, unknown fields, enrichment fields, examples, warnings,
   version, history).
- **An IQS validation report** — pass/fail against the standard, with the specific issues and
  warnings (see IQS §Validation Rules).
- **A human-review summary** — what was extracted, from which sources, what was asked, what remains
  unknown, and the confidence of each major field.
- **An exportable ICP artifact** — a portable representation the engine can ingest, plus a
  human-readable rendering for the approver.

## 7. Validation

- Every generated ICP is checked against **IQS v1.0** before it can be approved.
- Validation is **evidence-aware**: required sections present, weights valid, thresholds ordered and
  non-overlapping, target vs. hard-exclusion separation respected, exclusions non-contradictory,
  enrichment/unknown fields declared honestly.
- Validation **returns warnings, never silently fixes** (consistent with the engine's Knowledge
  Layer behavior).
- A profile that fails IQS cannot be marked "Approved"; it can be saved as a draft.

## 8. Non-functional requirements

- **Local-first**, consistent with the current MVP: no cloud dependency for the core flow, no
  database in this foundation.
- **Vendor-neutral and industry-agnostic**: no hardcoded commercial rules; all commercial content is
  ICP-specific.
- **Explainable & auditable**: every field traces to a source or to an interview answer or is marked
  unknown; nothing is invented.
- **Deterministic where it matters / LLM where judgment is needed** (constitution): structure,
  validation, and thresholds are deterministic; extraction, summarization, and question generation
  are LLM tasks.
- **Reproducible**: the same materials + answers produce the same structured profile shape.
- **Privacy**: business materials and customer lists are sensitive; handled locally, never logged in
  full, never sent anywhere the constitution forbids.
- **Human-in-the-loop**: no ICP is used for qualification until a human approves it.

## 9. Out of scope (this module / foundation)

CRM integrations, Google Sheets, Linked Helper, analytics, campaign tracking, market learning, A/B
testing, a shared ICP library implementation, database schema, and authentication. Also out of
scope: changing the Qualification Engine, building Streamlit pages, writing prompts, or defining JSON
models. This sprint is architecture only.

## 10. Success metrics

- **Time-to-ICP:** median minutes from first upload to an approved ICP.
- **Materials leverage:** share of ICP fields populated from materials vs. asked in the interview.
- **Interview economy:** median number of clarification questions asked (lower is better, given
  completeness).
- **First-pass IQS rate:** share of generated ICPs that pass IQS without manual rework.
- **Engine acceptance:** share of approved ICPs the engine ingests and qualifies with without error.
- **Reviewer confidence:** approver-reported trust; low edit-after-generation rate.
- **Coverage honesty:** share of ICPs where unknown/enrichment fields are correctly declared (audited
  against materials).

## 11. Future Business Knowledge Model (documentation only)

The **Business Knowledge layer** is the intermediate representation between raw materials and the ICP.
It is *not* the ICP; it is the evidence base the ICP is generated from.

- **What is extracted:** the organization's offering (products, services, packaging), value
  proposition and outcomes, typical/ideal customers, named example customers (best/lost), target
  industries and subsegments, company-size and geography patterns, buyer roles and personas, pains
  the offering addresses, disqualifiers/anti-patterns, and any explicit rejection rules stated in
  the materials.
- **How conflicting information is handled:** when two sources disagree (e.g. a deck says "SMB focus"
  but the best-customer list is all enterprise), the conflict is **recorded, not resolved silently**.
  Both values and their sources are kept; the conflict is surfaced in Gap Detection and becomes an
  interview question. Conflicts lower confidence and never auto-populate a hard rule.
- **How missing information is tracked:** each expected knowledge attribute has a state —
  *confirmed*, *conflicting*, *unknown*, or *not applicable*. Unknown attributes drive Gap Detection
  and the interview; they are never filled by inference.
- **How confidence is represented:** each extracted attribute carries a confidence derived from the
  directness of its source (an explicit statement in a service catalogue is high; a phrase inferred
  from prose is medium; a single ambiguous mention is low) and from cross-source agreement.
- **How source attribution works:** every extracted fact links to its origin — the document, the
  section/region, and a short quotable snippet — so a reviewer can verify it and so the generated ICP
  can cite evidence. Interview answers are also attributed (to the interview, with timestamp).

This mirrors the engine's Evidence Layer philosophy: confirmed / conflicting / unknown, sourced and
auditable, missing ≠ negative, nothing invented.

## 12. Qualification Engine integration

- The engine **continues to accept ICP PDFs** exactly as today — that path is unchanged.
- A generated, **IQS-approved ICP becomes an alternative input source** to the engine: instead of (or
  in addition to) extracting a profile from an uploaded PDF, the engine can receive the already-
  normalized ICP profile the Generator produced.
- Because the Generator emits the **same profile shape** the engine's Knowledge Layer already
  produces (dimensions + weights, category thresholds, target attributes, **hard exclusions kept
  distinct from target preferences**, enrichment/unknown fields), **no engine redesign is required**.
  The Generator effectively pre-fills what the engine would otherwise parse from a PDF, at higher
  fidelity and with validation already done.
- The **target-vs-hard-exclusion distinction** (established for the pre-qualification layer) is a
  first-class part of the generated ICP, so the engine's deterministic pre-qualification only ever
  fires on explicitly declared hard exclusions — never on preferred ranges.
- Integration is **additive and optional**: a user can still ignore the Generator and upload a PDF.
  Multiple approved ICPs can be selected per qualification run, one at a time, as today.

## 13. Important product decisions (locked for design)

1. Users must **never** be required to hand-write a long ICP document. The default is:
   upload materials → extract → detect gaps → ask only targeted questions → generate → validate →
   use.
2. **Target criteria and hard exclusions are separate** everywhere in the model, UX, and validation.
3. The **IQS gate is mandatory** before approval; approval is a human act.
4. Everything is **evidence-attributed or explicitly unknown**; the Generator never invents business
   facts, customers, or exclusions.
5. **Many ICPs per organization**, each independent and independently validated.
6. **Two entry points, one output.** "Standardize Existing ICP" and "Create New ICP" are the only two
   entry points; both produce the **identical internal ICP Profile** and pass through the same IQS
   gate and human approval. The Qualification Engine never knows which path was used. When
   standardizing an existing ICP, the **original document is never modified** — a new standardized
   IQS version is generated alongside it.
