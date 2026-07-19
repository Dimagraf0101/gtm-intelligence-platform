# Roadmap — GTM Intelligence Platform

Current, code-grounded roadmap (replaces the previous empty placeholder). Reflects the state at
Sprint 5.1 and the finalized architecture: **Business Knowledge is the single Source of Truth; the
ICP is a derived artifact; the AI Interview operates on Business Knowledge.** See
`docs/PROJECT_STATE.md` for the verified current status.

Two tracks are distinguished throughout: **planned features** (new capability) and **technical
debt** (cleanup that reduces risk but adds no user capability).

---

## Where we are

- **Integrated & runnable:** Lead Qualification + Workbook Export (legacy ICP-PDF path in `app.py`),
  the **ICP Workspace wizard** (Sprint 5.2), the **Run Campaign pipeline** (Sprint 5.3: ICP library,
  Vayne scraping, campaign scoring/exports), **durable storage + cloud deployment** (Sprint 5.5),
  and — as of **Sprint 2A** — the **IQS-gated approval act** and the **Generated-ICP → Engine
  bridge** (an Approved generated ICP qualifies leads through its structured profile).
- **Not built:** AI Interview, Strategy Review, Business-Knowledge persistence (curation is
  in-session only).

## Release 0.5 — "Business Knowledge is the Source of Truth"

The goal of 0.5 is a **complete, human-in-the-loop ICP creation loop** that ends in an ICP an
operator can actually qualify leads with. Sequenced so each sprint de-risks the next.

### Sprint 2A — Generator→Engine Bridge + Approval ✅ *(done)*
- **Shipped:** the `GeneratedICP → ICPProfile → scoring` bridge (`scoring.score_leads(profile=…)`
  additive entrypoint + `campaign.load_icp_for_scoring`, context from `GeneratedICP.to_markdown()`),
  the IQS-gated Draft → Approved gate (`pipeline/icp_approval.py`, human act recorded in history,
  warnings must be acknowledged), `GeneratedICP.from_dict`/`from_json`, and library
  approval/readiness APIs. Run Campaign refuses unapproved generated ICPs.
- **Backward compatibility:** the legacy ICP-PDF path stays intact (ADR-012).

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
- **Model deserializers** (`from_dict`) — `GeneratedICP` ✅ (Sprint 2A); `BusinessKnowledge` still
  missing (the prerequisite for persisting curation across sessions).
- **Tests for the Sprint 5.2–5.5 surface:** `vayne`, `storage`, `search_criteria`, and both wizard
  pages have no automated coverage (`campaign`/`icp_library` gained partial coverage in Sprint 2A).

## Release 1.0 and beyond — planned features (not scheduled)

- **Persistence + ICP Library:** many named ICPs per organization over one shared Business Knowledge
  and Company Assets repository (the architecture is designed for this; deserializers are the
  prerequisite).
- **Standardize-Existing entry point at scale**, multi-industry / multi-hypothesis ICPs.
- **Feedback & learning layer** (structured human corrections sharpen future scoring).
- **Landed early (were "out of scope until prioritized"):** Vayne scraping (Run Campaign,
  credit-gated), shared-password auth + hosting (Caddy/Docker/OCI — `docs/DEPLOYMENT.md`).
- **Out of scope until explicitly prioritized:** Google Sheets API, CRM integrations.

## Sequencing rule

Do **integration and approval (2A) before interview (2B) before orchestrator (2C) before UI (2D).**
Technical-debt items are fixed *during* the sprint that first depends on them (e.g. `temporal_context`
in 2B), except the LLM-client abstraction, which is a standalone hardening task once ≥2 AI stages are
stable.
