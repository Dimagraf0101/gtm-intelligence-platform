> **STATUS: ARCHIVED — NOT AUTHORITATIVE**
> Retained for history; do not treat as current architecture or status.
> REPLACED BY: docs/ARCHITECTURE_BASELINE_v1.0.md (§11) + docs/REPOSITORY_STATUS.md
> (Archived in the Sprint 7.3 documentation audit. See docs/README.md for the authority map.)

# Roadmap — GTM Intelligence Platform

Current, code-grounded roadmap (replaces the previous empty placeholder). Reflects the state at
Sprint 5.1 and the finalized architecture: **Business Knowledge is the single Source of Truth; the
ICP is a derived artifact; the AI Interview operates on Business Knowledge.** See
`docs/PROJECT_STATE.md` for the verified current status.

Two tracks are distinguished throughout: **planned features** (new capability) and **technical
debt** (cleanup that reduces risk but adds no user capability).

---

## Where we are

- **Integrated & runnable:** Lead Qualification + Workbook Export (legacy ICP-PDF path).
- **Built, tested, partially surfaced:** the ICP Workspace backend (extraction → Business Knowledge
  → gaps → Draft ICP → IQS → adapter) plus the **Business Knowledge Review Workspace** page
  (Sprint 5.1).
- **Not built:** AI Interview, Approval, Generated-ICP → Engine bridge, Strategy Review, persistence.

## Release 0.5 — "Business Knowledge is the Source of Truth"

The goal of 0.5 is a **complete, human-in-the-loop ICP creation loop** that ends in an ICP an
operator can actually qualify leads with. Sequenced so each sprint de-risks the next.

### Sprint 2A — Generator→Engine Bridge + Approval *(highest value; do first)*
- **Feature:** a `GeneratedICP → ICPProfile → scoring` bridge (additive scoring entrypoint that
  accepts a prebuilt `ICPProfile` + a context string synthesized from `GeneratedICP.to_markdown()`),
  and a deterministic Draft → Approved gate (IQS-gated, human act recorded in history).
- **Why first:** it closes both audit FAILs (no approval, no integration) with the least code and
  makes "an approved ICP can qualify leads" true — demonstrable immediately with the existing
  Sprint-4.1E.1 draft. Everything downstream feeds this bridge.
- **Backward compatibility:** the legacy ICP-PDF path stays intact throughout.

### Sprint 2B — AI Interview (operates on Business Knowledge)
- **Feature:** a headless interview engine driven by the gap report — asks only targeted questions
  for blocking/important gaps and open conflicts, writes answers back through the existing workspace
  primitives (`add_item(origin=user_input)`, `confirm_item`, `resolve_conflict`), then regenerates
  the draft. Real/mock client pattern; Python owns validation.
- **Bundled debt fix:** **persist `temporal_context`** on `KnowledgeItem` so current-vs-historical is
  faithful (stop re-guessing in the generator).
- **Why after 2A:** interview answers only matter if the resolved ICP can reach the engine.

### Sprint 2C — Workflow Orchestrator (headless, in-session)
- **Feature:** one in-memory state machine chaining assets → extract → gaps → interview → draft →
  approve → qualify, fully mock-testable offline.
- **Why after 2B:** the orchestrator wires the finished stages; building it earlier would wrap
  incomplete logic. It keeps flow logic out of Streamlit.

### Sprint 2D — Minimal end-to-end UI (no polish)
- **Feature:** thin Streamlit journey over the orchestrator: materials → **Knowledge Review**
  (exists) → **Interview** → **Draft review** → **Strategy Review** (weights/thresholds only) →
  **Approve** → **Use in Lead Qualification**.
- **Why last:** lowest risk; a view over tested logic.

## Phase 2 hardening (parallel to / after 0.5) — mostly technical debt

- **Shared LLM-client abstraction:** collapse the triplicated client/cache/parse scaffolding and the
  3× `MODEL` constant; remove the `icp_draft_generator → knowledge_extractor` private-helper import.
- **Real-API regression harness:** put the three AI stages under a recorded/replay or gated live
  test so prompt/parse regressions are caught.
- **Model deserializers** (`from_dict`) on `BusinessKnowledge` / `GeneratedICP` — unblocks
  persistence and removes the manual reconstruction the pilots needed.
- **Cleanup:** remove dead Vayne config from `config.py`; drop the unused `requests` dependency.

## Release 1.0 and beyond — planned features (not scheduled)

- **Persistence + ICP Library:** many named ICPs per organization over one shared Business Knowledge
  and Company Assets repository (the architecture is designed for this; deserializers are the
  prerequisite).
- **Standardize-Existing entry point at scale**, multi-industry / multi-hypothesis ICPs.
- **Feedback & learning layer** (structured human corrections sharpen future scoring).
- **Out of scope until explicitly prioritized:** Vayne API automation, Google Sheets API, CRM
  integrations, authentication, hosting/deployment.

## Sequencing rule

Do **integration and approval (2A) before interview (2B) before orchestrator (2C) before UI (2D).**
Technical-debt items are fixed *during* the sprint that first depends on them (e.g. `temporal_context`
in 2B), except the LLM-client abstraction, which is a standalone hardening task once ≥2 AI stages are
stable.
