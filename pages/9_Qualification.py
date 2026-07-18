"""Qualification — Streamlit page (Sprint 11).

VIEW ONLY. Qualify an imported Lead Batch against the selected Market Hypothesis's Approved Adapted
ICP, by reusing the frozen qualification engine through the `qualification_run` service. This page
only forwards and renders — no scoring, mapping, or validation logic lives here. No export.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import icp_approval as ap                # noqa: E402
import icp_identity as idy               # noqa: E402
import scoring as sc                     # noqa: E402
import qualification_run as qr          # noqa: E402

st.title("Qualification")
st.caption("Qualify an imported Lead Batch against this hypothesis's Approved Adapted ICP, using the "
           "existing Qualification Engine. Produces an immutable Qualified Lead Batch — no source "
           "artifact is edited, and nothing is exported here.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

port = st.session_state.get(PORT_KEY)
if port is None or not getattr(port, "projects", None):
    st.info("No workspace / hypotheses yet. Create a Market Hypothesis first.")
    st.stop()

labels = {f"{h.name} ({h.status})": h.project_id for h in port.hypotheses}
sel = st.session_state.get(SEL_KEY)
cur = next((l for l, v in labels.items() if v == sel), list(labels)[0])
chosen = st.selectbox("Market Hypothesis", list(labels), index=list(labels).index(cur))
st.session_state[SEL_KEY] = labels[chosen]
hyp = port.get_hypothesis(labels[chosen])

approved_icp = ap.get_active_approved_icp(hyp)
if approved_icp is None:
    st.warning("This hypothesis has no **Approved Adapted ICP** — qualification cannot run. Approve one "
               "on the **Strategy Review** / **Approval** pages first.")
else:
    st.success(f"Approved ICP: **{approved_icp.metadata.name}** v{approved_icp.metadata.version} · "
               f"`{idy.artifact_identity_str(approved_icp)[:28]}…`")

batches = hyp.list_lead_batches()
if not batches:
    st.info("No Lead Batches yet. Import leads on the **Lead Import** page.")
    st.stop()

# --- choose + preview a lead batch ------------------------------------------
st.subheader("1 · Choose a Lead Batch")
blabels = {f"{b.batch_id[:12]} · {b.imported_at} ({b.stats.get('imported', len(b.leads))} leads)": i
           for i, b in enumerate(batches)}
pick = st.selectbox("Lead Batch", list(blabels), index=len(blabels) - 1)
batch = batches[blabels[pick]]
with st.expander(f"Preview batch ({len(batch.leads)} leads)"):
    st.dataframe(
        [{"company": l.company_name, "person": l.person_name, "title": l.current_title,
          "geography": l.geography, "industry": l.industry} for l in batch.leads],
        use_container_width=True, hide_index=True)

# --- run ---------------------------------------------------------------------
st.subheader("2 · Run qualification")
_, is_live = sc.get_client()
if not is_live:
    st.warning("**Offline mode** — no `ANTHROPIC_API_KEY`; the engine uses the deterministic mock scorer.")
qualified_by = st.text_input("Qualified by", key="qualified_by", placeholder="Your name")
can_run = bool(approved_icp is not None and qualified_by.strip())
if st.button("Qualify this batch", type="primary", disabled=not can_run, icon=":material/play_arrow:"):
    client, _ = sc.get_client()
    res = qr.qualify_lead_batch(hyp, batch.batch_id, qualified_by=qualified_by.strip(), client=client)
    st.session_state[f"qual_result_{hyp.project_id}"] = res.summary()
    if res.ok:
        st.success(f"Qualified {res.batch.stats['total']} lead(s).")
    else:
        st.error(res.error)
    st.rerun()

last = st.session_state.get(f"qual_result_{hyp.project_id}")
if last and not last["ok"]:
    st.error(last["error"])

# --- results -----------------------------------------------------------------
runs = hyp.list_qualified_batches()
if not runs:
    st.stop()

st.subheader("3 · Qualified results")
rlabels = {f"{q.batch_id[:12]} · {q.qualified_at} ({q.stats.get('total', 0)} leads)": i
           for i, q in enumerate(runs)}
rpick = st.selectbox("Qualified batch", list(rlabels), index=len(rlabels) - 1)
run = runs[rlabels[rpick]]

s = run.stats
c = st.columns(4)
c[0].metric("Leads", s.get("total", 0))
c[1].metric("Disqualified", s.get("disqualified", 0))
c[2].metric("Errors", s.get("errors", 0))
c[3].metric("Avg score", s.get("average_score", 0))
st.caption(f"By decision: {s.get('by_decision', {})} · derived from LeadBatch "
           f"`{run.derived_from_lead_batch[:12]}` and ICP `{run.derived_from_adapted_icp[:24]}…`")

# pair each qualified lead back to its source lead for display
lead_by_id = {l.lead_id: l for l in batch.leads}
rows = []
for q in sorted(run.qualified, key=lambda x: x.score, reverse=True):
    src = lead_by_id.get(q.lead_id)
    rows.append({
        "company": getattr(src, "company_name", "") if src else "",
        "person": getattr(src, "person_name", "") if src else "",
        "title": getattr(src, "current_title", "") if src else "",
        "score": q.score, "decision": q.decision, "confidence": q.confidence,
        "evidence": "; ".join(q.evidence)[:80], "warnings": "; ".join(q.warnings)[:60],
    })
st.dataframe(rows, use_container_width=True, hide_index=True)

with st.expander("Per-lead detail (score · decision · evidence · warnings · reason)"):
    for q in sorted(run.qualified, key=lambda x: x.score, reverse=True):
        src = lead_by_id.get(q.lead_id)
        who = f"{getattr(src, 'person_name', '') or '—'} @ {getattr(src, 'company_name', '') or '—'}"
        st.markdown(f"**{who}** — score **{q.score}**, decision **{q.decision}** "
                    f"(confidence {q.confidence})")
        if q.reason:
            st.caption(q.reason)
        if q.evidence:
            st.caption("Evidence: " + "; ".join(q.evidence))
        if q.warnings:
            st.caption(":material/warning: " + "; ".join(q.warnings))
