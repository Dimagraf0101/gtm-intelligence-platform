# ICP Workspace — Product Requirements

**Status:** Product/PRD. Reconciled with the codebase at Sprint 5.1. The existing **Lead
Qualification Engine is stable and unchanged**; the **ICP Workspace** is the subsystem that turns
company evidence into a usable ICP. (Renamed from "ICP Generator" — the workspace is broader than
one-shot generation: it is where knowledge is reviewed, interviewed, and projected into ICPs.)

Governed by `docs/PRODUCT_CONSTITUTION.md`. Companions: `docs/product/ICP_WORKSPACE_UX.md`,
`docs/iqs/IQS_v1.0.md`, `docs/iqs/ICP_PROFILE_SCHEMA.md`, `docs/PROJECT_STATE.md` (what is actually
built).

**Vendor-neutral and industry-agnostic:** no hardcoded commercial rules; all commercial content is
ICP-specific and evidence-attributed.

---

## 1. Architectural principle (finalized)

- **Business Knowledge is the single Source of Truth.** It is the evidence base — categorized,
  status-tracked (confirmed / proposed / conflicting / unknown), confidence-scored, and
  source-attributed.
- **A Generated ICP is a derived projection** of Business Knowledge, produced on demand and never
  edited as a source of facts.
- **Knowledge Review and the (planned) AI Interview operate on Business Knowledge**, so any curation
  benefits every ICP derived from it — this is what makes many ICPs over one shared knowledge base
  tractable.
- **Strategy Review is separate from Knowledge Review:** company *facts* live in Business Knowledge;
  ICP-*strategy* choices (dimension weights, thresholds, which candidate exclusion applies to a given
  hypothesis) are a thin, per-ICP concern that never writes facts back.
- **Human approval is mandatory** before an ICP is used for qualification.

## 2. Goals

- Let any B2B organization turn existing materials into a **standardized, machine-usable ICP** without
  hand-writing a long document.
- Derive as much as possible from materials, then ask only for what is genuinely missing.
- Keep the ICP **immediately consumable by the existing engine** (same profile shape — dimensions,
  weights, priority thresholds, target attributes, hard exclusions, evidence/enrichment fields).
- Enforce the **ICP Qualification Standard (IQS)** so every ICP is complete, evidence-grounded, and
  internally consistent before approval.
- Support **many ICPs per organization**, each an independent projection of shared knowledge.

**Non-goals:** the workspace does not score leads or launch outreach; it *creates the input* the
Qualification Engine consumes.

## 3. Target workflow

```
Company Assets → Document Extraction → Source Package → Business Knowledge Extraction
  → Business Knowledge  ← (Source of Truth)
      ├─ Knowledge Review        (human curation of facts)        [implemented, Sprint 5.1]
      └─ AI Interview            (resolve gaps/conflicts on BK)   [planned]
  → Draft ICP Generation (derived)                                [implemented]
      → Strategy Review          (ICP weights/thresholds only)    [planned]
      → IQS Validation                                            [implemented]
      → Human Approval                                            [planned]
  → (bridge) → Lead Qualification Engine → Workbook Export        [engine integrated; bridge planned]
```

**Implemented today (Sprint 5.1):** extraction → Business Knowledge → gap detection →
**Business Knowledge Review Workspace** → deterministic Draft ICP generation (read-only) → IQS.
**Not yet implemented:** AI Interview, Strategy Review, Approval, and the Generated-ICP → Engine
bridge (see `docs/PROJECT_STATE.md`).

## 4. Two entry points (one output)

Both converge on the **same internal ICP Profile**; the Qualification Engine is agnostic to which
path produced it.

- **Create New ICP** — upload business materials → extract Business Knowledge → review → (interview)
  → generate → validate → approve.
- **Standardize Existing ICP** — upload an existing ICP document *as a source material* → extract its
  structure into Business Knowledge → review/gap-fill → generate a new standardized IQS ICP. **The
  original document is never modified.**

Entry point is audit metadata only; it never changes downstream behavior. (Refine/clone are edits of
an already-standardized ICP, re-entering at review — not a third entry point.)

## 5. Supported inputs

Document formats: PDF, DOCX, PPTX, TXT, Markdown. Artifacts: pitch/sales decks, case studies, service
catalogue, proposals, existing ICP, discovery/meeting notes; customer lists (best/lost). Each source
is treated as **evidence with attribution**. No OCR, websites, spreadsheets, email, or CRM.

## 6. Outputs

- **Business Knowledge** — the normalized, evidence-attributed knowledge base (the Source of Truth).
- **A derived ICP Profile** matching `ICP_PROFILE_SCHEMA.md`, ready for the engine.
- **An IQS validation report** — pass/fail with specific blocking errors and warnings.
- **A human-review summary** — what was extracted, from where, what remains unknown, and confidence.

## 7. Business Knowledge Review (implemented — the mandatory human stage)

A user can: browse all extracted knowledge; group by category; see confidence, source attribution,
and evidence excerpt; see status (confirmed/proposed/conflicting/unknown); confirm, reject, edit,
manually add, and merge duplicate items; resolve conflicts; filter by category/status; and search.
It shows completeness, open conflicts, unknown fields, and blocking/important/optional gaps, and
offers one deterministic action: **Generate Draft ICP** (a brand-new draft from current knowledge).

**Rules enforced:** every modification updates Business Knowledge only; human edits are marked
`origin = user_input`; provenance is preserved; AI-generated knowledge never silently overwrites
confirmed human knowledge (a differing AI value becomes a conflict, not an overwrite).

## 8. Validation & approval

Every ICP is checked against **IQS v1.0** before it may be approved. Validation is evidence-aware,
returns warnings (never silently fixes), and a profile that fails IQS cannot be marked "Approved".
**Approval is a human act and is not yet implemented in code** — the current draft is always `Draft`.

## 9. Qualification Engine integration

- The engine **continues to accept ICP PDFs** exactly as today (backward-compatible path).
- A derived, IQS-approved ICP is intended to become an **alternative input** via the engine adapter
  (`GeneratedICP → ICPProfile`). The adapter exists and is tested; the **bridge into `scoring` is not
  yet implemented** (`scoring.score_leads` currently consumes ICP *text*). Integration is additive:
  the PDF path remains available until the bridge lands.

## 10. Locked product decisions

1. Users are never required to hand-write a long ICP; the default is materials → extract → review →
   (interview) → generate → validate → approve.
2. Business Knowledge is the single Source of Truth; the ICP is derived.
3. Knowledge Review and Strategy Review are separate concerns.
4. Target criteria and hard exclusions are distinct everywhere in the model, UX, and validation.
5. The IQS gate is mandatory before approval; approval is a human act.
6. Everything is evidence-attributed or explicitly unknown; the workspace never invents facts.
7. Two entry points, one output; the engine never knows which path was used.
