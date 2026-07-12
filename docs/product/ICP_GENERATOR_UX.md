# ICP Generator — UX & User Journey (Sprint 4.0, design only)

**Status:** UX architecture. No mockups, no UI code. Describes the journey, flows, and states.
Vendor-neutral. Companion: `docs/product/ICP_GENERATOR_PRD.md`, `docs/iqs/IQS_v1.0.md`,
`docs/iqs/ICP_PROFILE_SCHEMA.md`.

The Generator is an **auxiliary area** of the existing local app; it ends by handing an approved ICP
to the unchanged **Lead Qualification** flow.

---

## 1. Navigation

Two top-level areas coexist:

- **ICP Generator** — create/refine/validate ICPs (this document).
- **Lead Qualification** — the existing engine (upload ICP + leads → score → review → export).

Within the Generator:

- **ICP list** (home) — all ICPs for the organization, each with name, scope tag (industry / segment
  / region), status (Draft · Needs Info · Ready for Review · Approved), IQS state, and last updated.
- **ICP builder** — the wizard for one ICP.
- **ICP detail / review** — read, validate, approve, download, or send to Lead Qualification.

Because many ICPs exist per organization, the **ICP list is the hub**; everything else is entered
from a specific ICP (new or existing).

**Entry-point selector.** Starting a new ICP first asks the user to choose one of **two entry
points**:

- **Standardize Existing ICP** — "I already have an ICP document."
- **Create New ICP** — "Build one from my company materials."

Both selections open the same builder shell and, after their path-specific early steps, **converge on
the identical Review & Validate → Approve → Use flow**. Nothing downstream (including the
Qualification Engine) depends on which entry point was chosen.

## 2. Screen flow (states of one ICP)

```
ICP list
  └─(New ICP / Clone / Import)→ Builder wizard
        └→ Review & Validate ──(fails IQS)→ back into the wizard at the failing step
                             └─(passes IQS)→ Approve → ICP detail (Approved)
                                                   ├→ Download
                                                   └→ Use in Lead Qualification
```

An ICP moves through statuses: **Draft → Needs Info → Ready for Review → Approved**. It can return to
an earlier status at any time (editing a field, a failed validation, or a reviewer sending it back).

## 3. Wizard steps

The builder is a linear-but-revisitable wizard whose early steps depend on the chosen entry point.
Both entry points **converge at "Generate → Review & Validate"** and share every step from there on.

### 3.A Entry Point 2 — Create New ICP

1. **Name & scope.** Give the ICP a name and scope tags (industry, segment, region). This makes it a
   distinct strategy from the start (e.g. "Healthcare Germany" vs "Healthcare SMB").
2. **Add materials.** Drag in any supported sources (PDF/DOCX/PPTX/TXT/MD; decks, case studies,
   service catalogue, proposals, existing ICP, discovery/meeting notes, best/lost customer lists).
   Zero materials is allowed (the interview covers more).
3. **Knowledge extraction (system).** The system extracts a **Business Knowledge** view from the
   materials, with per-fact source attribution and confidence. The user sees a progress state and
   then a **read-only knowledge summary** (what was found, from where, how confident) — not an ICP
   yet.
4. **Gap detection (system).** The system compares the extracted knowledge against what the ICP model
   requires and lists **what is missing, conflicting, or low-confidence**.
5. **AI interview.** The system asks **only targeted questions** to close the gaps from step 4 — never
   a long form. Questions are short, prioritized (blocking gaps first), and each explains why it is
   asked. The user can answer, skip (leaving the field unknown), or mark "not applicable".
6. **Generate → Review & Validate (shared).** See §3.C.

### 3.B Entry Point 1 — Standardize Existing ICP

1. **Name & scope.** Same as above.
2. **Upload existing ICP.** Provide the current ICP document (PDF, DOCX, Markdown, TXT; JSON later).
   The **original file is never modified** — it is read-only input; the system will produce a new
   standardized version alongside it.
3. **Extract structure (system).** The system maps the uploaded ICP onto the internal ICP model,
   with source attribution per field.
4. **IQS validation + detect missing sections (system).** The extracted structure is validated
   against IQS; **missing or non-conforming sections** are listed.
5. **Targeted interview for gaps only.** The same interview mechanism (§3.A step 5) asks **only** for
   the missing/non-conforming sections — never a full rewrite.
6. **Generate → Review & Validate (shared).** See §3.C.

### 3.C Shared final steps (both entry points converge here)

- **ICP generation (system).** The system assembles the **normalized ICP** from the extracted
  knowledge/structure + interview answers, keeping **target criteria and hard exclusions separate**
  and marking unknown/enrichment fields honestly. For the Standardize path this is a **new
  standardized IQS version**, produced without altering the source.
- **Review & validate.** The user reviews the generated ICP section by section, sees evidence and
  gaps inline, and runs **IQS validation**.
- **Approve → Use.** Identical for both paths (§4–§8 below).

The two paths produce the **exact same internal ICP Profile**; nothing after §3.C differs by entry
point. The wizard shows progress and lets the user jump back to any completed step; re-running
extraction or the interview updates downstream steps.

## 4. Validation flow

- Validation runs on demand and automatically before approval.
- It returns a **structured IQS report**: which required sections are present, which rules pass, and
  a list of **errors** (block approval) and **warnings** (do not block, must be acknowledged).
- Each issue links to the **exact section/field** and offers a jump-to-fix. A weight-sum error jumps
  to Qualification Dimensions; a threshold-overlap error jumps to Priority Thresholds; a
  target-vs-exclusion conflict jumps to the relevant section.
- Validation **never auto-corrects**; it explains and points. The user fixes, then re-validates.

## 5. Review flow

- The review screen presents the ICP in reader form: metadata, business context, products/services,
  target companies, target buyers, dimensions & weights, priority thresholds, **hard exclusions**
  (clearly separated from targets), evidence requirements, unknown fields, enrichment fields,
  examples, warnings.
- Every field shows its **provenance** (source snippet, interview answer, or "unknown") and
  confidence, so the approver can trust or challenge it.
- The reviewer can **edit** (returns the ICP to Draft/Needs Info and invalidates approval),
  **approve** (only when IQS passes), or **send back** with notes.

## 6. Error handling

- **Unreadable / corrupt / scanned document:** flagged per-file with a clear message; the file is
  skipped, extraction continues on the rest, and the gap it would have filled becomes an interview
  question.
- **Thin or empty materials:** allowed; the flow shifts weight to the interview and warns that
  confidence will be lower.
- **Conflicting sources:** surfaced explicitly in Gap Detection and as an interview question; never
  resolved silently.
- **Extraction/generation failure:** the step reports the failure, preserves prior work (draft is
  never lost), and offers retry; partial results remain usable.
- **IQS failure:** blocks approval, lists precise issues, routes the user to the failing step.
- **Nothing is ever silently invented** to recover from an error; missing stays unknown.

## 7. Download flow

- From an Approved (or Draft) ICP, the user can **download** two artifacts: an engine-ingestible ICP
  representation and a human-readable rendering.
- Draft downloads are watermarked/labeled "Draft — not IQS-approved" so they cannot be mistaken for an
  approved ICP.
- Download never triggers qualification or any outward action.

## 8. Transition into Lead Qualification

- From an **Approved** ICP, a primary action **"Use in Lead Qualification"** hands that ICP to the
  existing engine as its ICP input — replacing the "upload an ICP PDF" step for that run.
- The existing PDF-upload path remains available and unchanged; the generated-ICP path is an
  **alternative input source**.
- The user then continues in the unchanged Lead Qualification flow: upload leads → score → review →
  export. The Generator does not score leads or launch any outreach; human approval remains required
  before any outreach, exactly as today.
- Multiple approved ICPs can be picked from the ICP list; one ICP is used per qualification run.
