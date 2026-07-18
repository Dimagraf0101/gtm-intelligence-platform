"""Strategy Review — Streamlit page (Sprint 5.4).

VIEW ONLY. All rules live in pipeline/strategy_review.py (StrategyReviewWorkspace) and the modules it
reuses. This page shows the proposed Draft ICP for the selected project, lets a human choose weights
and activate/decline the draft's exclusion candidates, and appends a reviewed Draft version. It never
approves, never edits an ICPProfile, never touches Lead Qualification, and uses no LLM wording.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import icp_draft_generator as dg         # noqa: E402
import iqs_validator as iqs              # noqa: E402
import strategy_review as sr             # noqa: E402
import workspace_revision as wr          # noqa: E402

st.set_page_config(page_title="Strategy Review", page_icon="🎯", layout="wide")
st.title("🎯 Strategy Review")
st.caption("Choose qualification dimension weights and decide which of the draft's exclusion "
           "candidates to activate. This produces a new reviewed Draft ICP version — it never "
           "approves it and never changes Lead Qualification. Facts stay in Knowledge; the ICP stays "
           "a Draft.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

port = st.session_state.get(PORT_KEY)
if port is None or not port.projects:
    st.info("Open **Knowledge Review** first to create an ICP Project and add company materials.")
    st.stop()

labels = {p.name: p.project_id for p in port.projects}
current_pid = st.session_state.get(SEL_KEY)
current_label = next((l for l, v in labels.items() if v == current_pid), list(labels)[0])
chosen = st.selectbox("ICP Project", list(labels), index=list(labels).index(current_label))
st.session_state[SEL_KEY] = labels[chosen]
project = port.get_project(labels[chosen])

WS_KEY = f"strategy_ws_{project.project_id}"
REV_KEY = WS_KEY + "_rev"
# Rebuild the workspace (fresh proposed base draft) when the underlying knowledge has changed on
# another page. Strategy edits don't change knowledge, so they never trigger a spurious rebuild.
current_rev = wr.knowledge_revision(port.company, project)
ws = st.session_state.get(WS_KEY)
if ws is None or ws.project is not project or st.session_state.get(REV_KEY) != current_rev:
    client, _ = dg.get_draft_client()
    ws = sr.StrategyReviewWorkspace(port.company, project, draft_client=client)
    st.session_state[WS_KEY] = ws
    st.session_state[REV_KEY] = current_rev

cols = st.columns(2)
if cols[0].button("Start / resume review", type="primary"):
    ws.start_review()
    st.rerun()
if cols[1].button("Regenerate proposed draft from current knowledge"):
    ws.regenerate_base()
    st.rerun()

if project.strategy is None:
    st.info("Start the review to load the freshly proposed Draft ICP for this project.")
    st.stop()

# --- dimensions --------------------------------------------------------------
st.subheader("Qualification dimensions")
st.caption("Each proposed weight is AI-suggested until you review it. Weights are never "
           "auto-normalized — IQS requires the included weights to total 100.")
for row in ws.proposed_dimensions():
    c = st.columns([3, 1, 2, 1])
    default_flag = " · _suggested_" if row["proposed_is_default"] else ""
    c[0].markdown(f"**{row['name']}**{default_flag}")
    c[1].caption(f"proposed {row['proposed_weight']}")
    reviewed = row["reviewed_weight"] if row["reviewed_weight"] is not None else row["proposed_weight"]
    new_w = c[2].number_input("weight", 0, 100, int(reviewed), key=f"w_{row['name']}",
                              label_visibility="collapsed", disabled=not row["included"])
    if c[2].button("Set weight", key=f"setw_{row['name']}", disabled=not row["included"]):
        ws.set_weight(row["name"], new_w)
        st.rerun()
    if row["included"]:
        if c[3].button("Exclude", key=f"exc_{row['name']}"):
            ws.exclude_dimension(row["name"])
            st.rerun()
    else:
        c[3].caption("excluded")
        if c[3].button("Include", key=f"inc_{row['name']}"):
            ws.include_dimension(row["name"])
            st.rerun()

total = ws.weight_total()
st.metric("Included weight total", f"{total} / 100",
          delta=("balanced" if total == 100 else f"{total - 100:+d}"))
if total != 100:
    st.caption("IQS will block until included weights total 100. Strategy Review never rebalances "
               "for you.")

# --- exclusion candidates ----------------------------------------------------
st.subheader("Hard-exclusion candidates")
cands = ws.exclusion_candidates()
if not cands:
    st.caption("This draft has no exclusion candidates. (Candidates come only from declared "
               "hard-exclusion knowledge — Strategy Review never invents rejection rules.)")
for cand in cands:
    c = st.columns([4, 2, 2])
    decided = "✅ active" if cand["activated"] else ("🚫 declined" if cand["decided"] else "— undecided")
    c[0].markdown(f"**{cand['rule']}**  \n<small>evidence: {cand['evidence_required'] or '—'} · "
                  f"{cand['evaluation_mode']} · {cand['scope']}</small>", unsafe_allow_html=True)
    c[1].caption(decided)
    if c[2].button("Activate", key=f"act_{cand['rule']}"):
        ws.activate_exclusion(cand["rule"])
        st.rerun()
    if c[2].button("Decline", key=f"dec_{cand['rule']}"):
        ws.decline_exclusion(cand["rule"])
        st.rerun()

# --- stale decisions ---------------------------------------------------------
stale = ws.stale()
if stale["dimensions"] or stale["exclusions"]:
    st.subheader("⚠️ Stale decisions")
    st.caption("These decisions reference something no longer in the proposed draft (knowledge "
               "changed). They are kept for audit and block completion until you discard them.")
    for name in stale["dimensions"]:
        if st.button(f"Discard stale dimension: {name}", key=f"ds_{name}"):
            ws.discard_stale_decision(dimension=name)
            st.rerun()
    for rule in stale["exclusions"]:
        if st.button(f"Discard stale exclusion: {rule}", key=f"es_{rule}"):
            ws.discard_stale_decision(exclusion=rule)
            st.rerun()

# --- decision-layer status ---------------------------------------------------
st.subheader("Review status")
issues = ws.validate()
complete = ws.is_complete()
st.write(f"Strategy Review complete: {'✅ yes' if complete else '❌ not yet'}")
if issues:
    for i in issues:
        st.write(f"- {i}")
if not complete and not issues:
    st.caption("Set a weight for every included dimension and decide every exclusion candidate.")

# --- apply -------------------------------------------------------------------
st.subheader("Reviewed Draft ICP")
if st.button("🧩 Generate reviewed Draft ICP version", type="primary", disabled=not complete):
    reviewed, report = ws.generate_reviewed_draft()
    st.session_state["last_reviewed"] = (reviewed, report)
    st.rerun()

last = st.session_state.get("last_reviewed")
if last is not None:
    reviewed, report = last
    st.success(f"Reviewed Draft **{reviewed.metadata.name}** v{reviewed.metadata.version} — status "
               f"{reviewed.metadata.status}, {len(reviewed.dimensions)} dimension(s), "
               f"{len(reviewed.hard_exclusions)} active exclusion(s).")
    st.markdown(f"**IQS:** {'valid ✅' if report.is_valid else 'blocking issues ❌'} "
                f"(completeness {report.completeness_score}/100)")
    if report.blocking_errors:
        st.warning("IQS blocking: " + "; ".join(report.blocking_errors))
    if report.warnings:
        with st.expander(f"IQS warnings ({len(report.warnings)})"):
            for w in report.warnings:
                st.write(f"- {w}")
    with st.expander("View reviewed Draft ICP (read-only)"):
        st.markdown(reviewed.to_markdown())
