# ICP Workspace — UX & User Journey

**Status:** UX architecture. Reconciled with the codebase at Sprint 5.1. Describes the journey,
flows, and states. Sections are tagged **[implemented]** or **[planned]** so the design is not
mistaken for shipped behavior. Companions: `docs/product/ICP_WORKSPACE_PRD.md`,
`docs/iqs/IQS_v1.0.md`, `docs/iqs/ICP_PROFILE_SCHEMA.md`, `docs/PROJECT_STATE.md`.

The ICP Workspace is an **auxiliary area** of the local app; it ends by handing an approved ICP to
the unchanged **Lead Qualification** flow.

---

## 1. Navigation

Two top-level areas:

- **Lead Qualification** — the existing engine (upload ICP + leads → score → review → export).
  **[implemented, `app.py`]**
- **ICP Workspace** — create/curate/validate ICPs from company evidence. Today this is the
  **Business Knowledge Review** page **[implemented, `pages/1_Business_Knowledge_Review.py`]**; the
  full multi-step journey below is **[planned]**.

**Entry-point selector [planned]:** starting a new ICP first asks the user to choose **Create New
ICP** or **Standardize Existing ICP**. Both open the same workspace and converge on the same
Review → (Interview) → Draft → Approve → Use flow.

## 2. Core principle — Business Knowledge is the Source of Truth

Everything the user reviews or answers updates **Business Knowledge**, not the ICP. The **Draft ICP
is a derived, read-only projection** regenerated from Business Knowledge on demand. This is what
lets many ICPs share one curated knowledge base.

## 3. The journey (states of one ICP)

```
Company Assets
  → Business Knowledge Extraction
  → BUSINESS KNOWLEDGE REVIEW        ← human curation of FACTS      [implemented]
      → AI Interview                 ← resolve gaps/conflicts on BK [planned]
  → Draft ICP (generated, read-only)                               [implemented]
      → Strategy Review              ← ICP weights/thresholds only  [planned]
      → IQS Validation                                             [implemented]
      → Approve                       ← human act                   [planned]
  → Use in Lead Qualification                                      [engine ready; bridge planned]
```

## 4. Business Knowledge Review — the mandatory human stage [implemented]

The page lets the user:

- **Extract** Business Knowledge from uploaded company materials (real model, or a clearly-marked
  offline mock when no API key is present).
- **Browse & group** all knowledge by category; **filter** by category and status; **search**.
- For each item, see **value, status** (confirmed / proposed / conflicting / unknown),
  **confidence**, **origin**, **source attribution**, and **evidence excerpt**.
- **Curate:** confirm, reject, edit a value, manually add new knowledge, and merge duplicates.
- **Resolve conflicts** (choose the preferred value; contrary evidence is retained for audit).
- See a **status panel:** completeness, active-item counts by status, open conflicts, unknown fields,
  and blocking / important / optional gaps.
- Press **Generate Draft ICP** — the one deterministic action — to produce a brand-new Draft ICP from
  the current Business Knowledge, shown **read-only** with its IQS result and offered as a
  Markdown/JSON download. The draft is never edited here.

**Behavioral rules (enforced):** every action updates Business Knowledge only; human edits are marked
`origin = user_input`; provenance is preserved; AI knowledge never silently overwrites confirmed
human knowledge.

## 5. AI Interview [planned]

Operates on **Business Knowledge**, not the ICP. Driven by the gap report, it asks **only targeted
questions** for blocking/important gaps and open conflicts, explains why each is asked, and lets the
user answer, skip (leave unknown), or mark not-applicable. Answers are written back through the same
review primitives (`add_item(origin=user_input)`, `confirm_item`, `resolve_conflict`); the draft is
then regenerated. Never a long form; never invents answers.

## 6. Draft review & Strategy Review [Draft view implemented read-only; Strategy Review planned]

- **Draft review [implemented]:** the generated ICP is shown read-only (Markdown) with its IQS
  report. It is not edited in this sprint.
- **Strategy Review [planned]:** a separate, thin step for **ICP-strategy only** — dimension weights,
  priority-threshold nuance, which candidate exclusion applies to this hypothesis. It never edits
  facts (those belong in Business Knowledge).

## 7. Approval [planned]

Approval is a **human act**, allowed only when IQS passes with no blocking errors. Not yet
implemented — the generator always produces a `Draft`, and the (tested) engine adapter refuses any
non-Approved / IQS-invalid ICP.

## 8. Transition into Lead Qualification [engine implemented; bridge planned]

The existing **ICP-PDF upload path remains available and unchanged** (backward compatible). Once the
Generated-ICP → Engine bridge is built, an **Approved** ICP will become an alternative input to the
same qualification flow (upload leads → score → review → export). The workspace never scores leads or
launches outreach; human approval remains required before any outreach, exactly as today.
