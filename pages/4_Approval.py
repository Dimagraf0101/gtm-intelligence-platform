"""Approval — Streamlit page (Sprint 5.5).

VIEW ONLY. All rules live in pipeline/icp_approval.py (ApprovalWorkspace). This page shows a reviewed
Draft ICP's eligibility, lets a human acknowledge each IQS warning and approve with a named approver,
and shows the active Approved version and history. It never approves automatically, invents no
approver, and touches neither Lead Qualification nor scoring.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import icp_approval as ap                # noqa: E402
import workspace_revision as wr          # noqa: E402

st.title("ICP Approval")
st.caption("Approve one reviewed Draft ICP version. Approval requires a complete Strategy Review, no "
           "IQS blocking errors, every IQS warning acknowledged, and an explicit approver. The "
           "approved version becomes immutable; Lead Qualification is not affected here.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

port = st.session_state.get(PORT_KEY)
if port is None or not port.projects:
    st.info("Open **Knowledge Review** first to create an ICP Project.")
    st.stop()

labels = {p.name: p.project_id for p in port.projects}
current_pid = st.session_state.get(SEL_KEY)
current_label = next((l for l, v in labels.items() if v == current_pid), list(labels)[0])
chosen = st.selectbox("ICP Project", list(labels), index=list(labels).index(current_label))
st.session_state[SEL_KEY] = labels[chosen]
project = port.get_project(labels[chosen])

WS_KEY = f"approval_ws_{project.project_id}"
REV_KEY = WS_KEY + "_rev"
# Rebuild the workspace (and drop stale, fingerprint-scoped acknowledgements) when the strategy
# revision or the reviewed draft changes. Approving does not change these inputs, so it never
# self-invalidates.
current_rev = wr.approval_inputs_revision(project)
ws = st.session_state.get(WS_KEY)
if ws is None or ws.project is not project or st.session_state.get(REV_KEY) != current_rev:
    ws = ap.ApprovalWorkspace(project)
    st.session_state[WS_KEY] = ws
    st.session_state[REV_KEY] = current_rev

versions = ws.versions()
if not versions:
    st.info("No reviewed Draft versions yet. Produce one on the **Strategy Review** page.")
    st.stop()

# --- select a reviewed version -----------------------------------------------
opts = {f"v{d.metadata.version} · {d.metadata.name} ({d.metadata.status})": i
        for i, d in enumerate(versions)}
pick = st.selectbox("Reviewed Draft version", list(opts), index=len(opts) - 1)
draft = versions[opts[pick]]

check = ws.eligibility(draft)

# --- eligibility -------------------------------------------------------------
st.subheader("Eligibility")
c = st.columns(4)
c[0].metric("Strategy Review", "complete" if check.strategy_complete else "incomplete")
c[1].metric("IQS", "valid" if (check.iqs_result and check.iqs_result.is_valid) else "blocking")
c[2].metric("Stale strategy", "yes" if check.stale_strategy else "no")
c[3].metric("Derived status", ap.readiness_state(check))

if check.iqs_result and check.iqs_result.blocking_errors:
    st.error("IQS blocking errors:\n" + "\n".join(f"- {e}" for e in check.iqs_result.blocking_errors))

if check.blocking_reasons:
    st.warning("Blocking reasons:\n" + "\n".join(f"- {r}" for r in check.blocking_reasons))
else:
    st.success("All conditions pass. This version is ready to approve.")

# --- warnings: acknowledge individually --------------------------------------
st.subheader("IQS warnings")
warnings = ws.warnings(draft)
if not warnings:
    st.caption("No IQS warnings.")
for wid, message, acked in warnings:
    cols = st.columns([6, 1])
    cols[0].markdown(f"{':material/check_circle:' if acked else ':material/radio_button_unchecked:'} {message}")
    if acked:
        if cols[1].button("Unacknowledge", key=f"un_{wid}"):
            ws.unacknowledge_warning(draft, wid)
            st.rerun()
    else:
        if cols[1].button("Acknowledge", key=f"ack_{wid}"):
            ws.acknowledge_warning(draft, wid)
            st.rerun()
st.caption("Acknowledgement is recorded for this exact draft version only — a changed draft must be "
           "re-acknowledged.")

# --- approve -----------------------------------------------------------------
st.subheader("Approve")
approver = st.text_input("Approver name (required)", key=f"approver_{project.project_id}")
note = st.text_input("Approval note (optional)", key=f"note_{project.project_id}")
disabled = not (check.can_approve and approver.strip())
if st.button("Approve this version", type="primary", disabled=disabled, icon=":material/check:"):
    try:
        approved = ws.approve(draft, approved_by=approver.strip(), approval_note=note.strip())
        st.success(f"Approved **{approved.metadata.name}** v{approved.metadata.version}.")
        st.rerun()
    except ap.ApprovalError as e:
        st.error(str(e))
if not approver.strip():
    st.caption("Enter an approver name to enable approval — the system never invents one.")

# --- active approved version + history ---------------------------------------
st.subheader("Active Approved version")
active = ws.active_approved()
if active is None:
    st.caption("No Approved version yet.")
else:
    st.success(f"**{active.metadata.name}** v{active.metadata.version} — status "
               f"{active.metadata.status}, {len(active.dimensions)} dimension(s), "
               f"{len(active.hard_exclusions)} hard exclusion(s).")
    with st.expander("View active Approved ICP (read-only)"):
        st.markdown(active.to_markdown())

records = ws.history()
if records:
    st.subheader("Approval history")
    st.dataframe(
        [{"version": r.icp_version, "approved_by": r.approved_by, "approved_at": r.approved_at,
          "iqs": r.iqs_version, "strategy_rev": r.strategy_revision,
          "acknowledged": len(r.acknowledged_warning_ids), "note": r.approval_note}
         for r in records],
        use_container_width=True, hide_index=True)
