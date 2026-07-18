# Documentation Index — GTM Intelligence Platform

This index establishes which documents are authoritative, which are supporting, and which are
historical. Read it before trusting any other document. (Last reconciled: Sprint 7.3 audit.)

## Authoritative (comply with these)

| Document | Role |
|---|---|
| **`ARCHITECTURE_BASELINE_v1.0.md`** | **The frozen architecture constitution.** Every sprint must comply with it. Amend only via a versioned revision. |
| **`REPOSITORY_STATUS.md`** | **Current, code-grounded state** — what exists today (modules, pages, persistence, identity model, test count, next phase). |

If any other document conflicts with these two, these win.

## Supporting (current, non-authoritative reference)

| Document | Role |
|---|---|
| `PRODUCT_CONSTITUTION.md` | Product principles (Human Review Gate, honesty, determinism). Architecture authority is the Baseline. |
| `PROJECT_MANIFEST.md` | Product goal, input/output locations, standing rules. For *current status* see `REPOSITORY_STATUS.md`. |
| `DECISIONS.md` | Architecture Decision Records (ADR log). |
| `CHANGELOG.md` | Historical change log (append-only; may lag the current state). |
| `iqs/IQS_v1.0.md`, `iqs/ICP_PROFILE_SCHEMA.md` | The ICP Qualification Standard and profile schema. |
| `product/ICP_WORKSPACE_PRD.md`, `product/ICP_WORKSPACE_UX.md` | ICP Workspace product/UX reference. |

## Archived (NOT current architecture — do not rely on)

Each file below carries a `STATUS: ARCHIVED` header. They are retained for history and because other
documents still link to them; they must **not** be treated as current architecture or status.

| Archived document | Superseded by |
|---|---|
| `ARCHITECTURE.md` | `ARCHITECTURE_BASELINE_v1.0.md` |
| `PROJECT_STATE.md` | `REPOSITORY_STATUS.md` |
| `ROADMAP.md` | `ARCHITECTURE_BASELINE_v1.0.md` §11 + `REPOSITORY_STATUS.md` |
| `REPOSITORY_AUDIT.md` | `REPOSITORY_STATUS.md` (point-in-time audit) |
| `CLEANUP_REPORT.md` | — (point-in-time report) |
| `SPRINT_3_IMPLEMENTATION_PLAN.md` | — (point-in-time Sprint 3 plan) |
| `SPRINT_3_TECH_SPEC.md` | — (point-in-time Sprint 3 spec) |

## Note for future developers and AI agents

The product is centered on **Market Hypotheses**, not ICPs (the ICP is a supporting artifact). The
current domain roots are **CompanyWorkspace** and **MarketHypothesis** (aliases `ICPPortfolio` /
`ICPProject` remain for compatibility). Any document describing the platform as "an ICP generator,"
"two disconnected halves," or "not connected end-to-end" is archived and out of date — see
`REPOSITORY_STATUS.md`.
