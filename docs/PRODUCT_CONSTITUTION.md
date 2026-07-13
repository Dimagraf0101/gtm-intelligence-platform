# Product Constitution

**Status:** Highest-level architectural document. **Authoritative and immutable in spirit.**

This is the constitution of the project. Every Sprint, every feature, and every design decision
must comply with it. Where any other document, prompt, or piece of code conflicts with this
constitution, **this document wins** and the conflicting artifact must be corrected.

Amendments are allowed only as deliberate, explicit, reviewed changes to this file — never by
side effect of implementing a feature.

Read alongside `docs/PROJECT_MANIFEST.md` (the operational definition) and
`docs/REPOSITORY_AUDIT.md` (the file map).

---

## 1. Product Mission

The **GTM Intelligence Platform** turns company evidence and a raw lead export into a
**prioritized, explainable list of qualified leads** — and into the **standardized ICPs** that drive
that qualification — that a human can trust and act on, while keeping a person firmly in control of
every decision to contact anyone.

## 2. Product Vision

The **GTM Intelligence Platform** begins as a local qualification tool plus an ICP Workspace (this
stage) and evolves into a system that consistently reasons about fit, surfaces evidence and
uncertainty, learns from human judgment, and makes go-to-market teams faster and sharper — always as
an advisor to people, never as an autonomous actor.

---

## 3. Core Principles

These are non-negotiable. Every Sprint must uphold all of them.

1. **AI recommends, humans approve.** The system proposes; a person decides. The AI never has the
   final word on who to contact.
2. **The Human Review Gate is mandatory.** There is always a human checkpoint between scoring and
   any outward action. It cannot be bypassed, defaulted-through, or automated away.
3. **Source of truth, by subsystem.** *In Lead Qualification,* the ICP governs scoring: when an ICP
   defines its own dimensions, weights, thresholds, or dealbreakers, those govern, overriding any
   generic scoring guide. *In the ICP Workspace,* **Business Knowledge is the single source of
   truth**, and a Generated ICP is a **derived projection** of it — curation and the interview
   operate on Business Knowledge, never on the ICP, and the ICP is regenerated from knowledge.
4. **Missing information is never evidence.** Absence of data is *unknown*, not a negative signal
   and not a disqualifier. The system may never invent funding, hiring, stage, revenue, traffic,
   headcount, technology, or activity to fill a gap.
5. **Explainability is mandatory.** Every score, category, and exclusion must be understandable by
   a human in plain terms. Opaque verdicts are not acceptable.
6. **Every score must include evidence.** A number without cited, data-grounded reasoning is
   invalid output.
7. **Historical benchmarks are reference material, not ground truth.** Prior manual scoring is
   context, never a validated label, and never a target to optimize toward without re-baselining.
8. **Deterministic rules belong in Python.** Totals, thresholds, category assignment, clamping,
   and dealbreaker enforcement are computed in code — reproducibly and testably.
9. **Judgment belongs in the LLM.** Qualitative interpretation (fit, persona, subsegment, signal
   reading) is the model's role. The model informs; it does not compute the final verdict.
10. **Every decision must be auditable.** For any lead, it must be possible to reconstruct what
    inputs were used, what the model judged, what Python computed, and why the outcome resulted.
11. **Safety is preferred over automation.** When in doubt, do less automatically and surface more
    to the human. A missed convenience is preferable to an unreviewed action.
12. **Architecture is preferred over prompt engineering.** Durable structure (clear layers,
    validation, explicit contracts) is favored over fragile prompt tricks. Prompts express intent;
    they are not the safety mechanism.
13. **Repository clarity is mandatory.** Active, reference, experimental, generated, and archived
    material stay clearly separated. Legacy and benchmark files must never masquerade as active
    production sources.
14. **Explicit paths are preferred over automatic discovery.** Code references known, configured
    locations. It never guesses inputs by fuzzy filename matching, and never auto-selects among
    multiple datasets or benchmarks.

---

## 4. Decision Hierarchy

Responsibilities are layered. No layer may absorb another's responsibility.

| Layer | Owns | Must never |
|---|---|---|
| **UI** | Collecting inputs, showing progress, presenting ranked results and evidence, offering exports. | Make scoring or eligibility decisions; hide reasoning or uncertainty. |
| **Rule Engine (Python)** | Deterministic math: validation, clamping, totals, thresholds, category assignment, dealbreaker enforcement. | Perform qualitative judgment or invent data. |
| **LLM** | Qualitative judgment: interpreting fit, personas, subsegments, and signals; returning per-dimension assessments, evidence, unknowns, and confidence. | Decide final scores or categories; fabricate missing facts. |
| **Validation** | Guarding the boundary between LLM output and the Rule Engine: enforcing the output contract, ranges, and structure; recording what could not be trusted. | Silently repair meaning; let malformed or invented output through unchecked. |
| **Human Review** | The final decision to accept, deprioritize, or act on any lead. | Be skipped, pre-approved, or replaced by the system. |
| **Export** | Producing faithful, portable artifacts of the reviewed results. | Trigger outreach, transmit to external systems, or alter the underlying judgments. |

Flow of authority: **UI → LLM (judgment) → Validation → Rule Engine (verdict) → UI → Human Review → Export.**
Action on the world happens only *after* the Human Review Gate, and only by human choice.

---

## 5. Definition of Done

A feature is complete **only if all five hold**:

- **Documented** — its purpose, inputs, outputs, and constraints are written down.
- **Testable** — its behavior can be exercised and checked without calling paid services.
- **Explainable** — a human can understand what it does and why, including its outputs.
- **Reproducible** — given the same inputs, it yields the same deterministic results.
- **Auditable** — its inputs, judgments, computations, and outcomes can be traced after the fact.

A feature that scores leads but cannot explain, reproduce, or audit its results is **not done**,
regardless of how well it appears to work.

---

## 6. Non-goals

The product intentionally does **not**:

- Launch, send, or schedule any outreach automatically.
- Act on the world without an explicit human decision.
- Treat historical or manual benchmarks as validated ground truth.
- Invent, infer, or assume information that is not present in the data.
- Optimize for automation, throughput, or "hands-off" operation at the expense of human control,
  explainability, or safety.
- Hide reasoning, uncertainty, or dealbreaker logic from the reviewer.
- Depend on cloud services, databases, background automation, or third-party orchestration for its
  core qualification function.
- Auto-discover its inputs or silently choose among competing datasets.

## 7. Future Direction

The intended evolution is toward a full **GTM Intelligence Platform** — a system that not only scores
leads but generates and standardizes the ICPs behind them, and explains, calibrates, and improves its
judgment over time: richer evidence and
uncertainty modeling, ICP-aware reasoning across many profiles, structured human feedback that
sharpens future scoring, and clearer prioritization for go-to-market teams.

Every step of that evolution remains bound by this constitution: the platform becomes more
capable and more insightful, but it stays an **advisor to humans, never an autonomous actor.**
Growth in intelligence never becomes growth in unreviewed authority.

---

*This constitution supersedes conflicting guidance elsewhere in the repository. Amend it
deliberately, or comply with it — do not quietly work around it.*
