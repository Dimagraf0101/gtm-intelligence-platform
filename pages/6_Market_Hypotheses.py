"""Market Hypotheses — Streamlit page (Sprint 8).

VIEW ONLY. Create / edit / delete Market Hypotheses, and generate an Adapted ICP for the selected one
(derived from the company's General ICP + the hypothesis's knowledge). All logic lives in
CompanyWorkspace, the adapted_icp service, and the existing interview/strategy/approval flows — this
page only forwards and renders. Selecting a hypothesis here drives pages 2–4 (they read
``selected_project_id``).

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import icp_project as ip                 # noqa: E402
import icp_draft_generator as dg         # noqa: E402
import icp_identity as idy               # noqa: E402
import adapted_icp as aicp              # noqa: E402

st.title("Market Hypotheses")
st.caption("Each Market Hypothesis is one independent GTM experiment. It carries its own knowledge and "
           "adapted-ICP lineage; deleting one never affects another. Company Knowledge and the General "
           "ICP are read-only here.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

if st.session_state.get(PORT_KEY) is None:
    st.session_state[PORT_KEY] = ip.CompanyWorkspace()
ws = st.session_state[PORT_KEY]

general = ws.latest_general_icp()
if general is None:
    st.warning("No **General ICP** yet. Generate it on the **General ICP** page first — it is the "
               "baseline every hypothesis adapts.")
else:
    st.caption(f"General ICP baseline: **{general.metadata.name}** v{general.metadata.version} "
               f"· identity `{idy.artifact_identity_str(general)[:28]}…`")

# --- create ------------------------------------------------------------------
st.subheader("1 · Create a hypothesis")
cc = st.columns([2, 3, 1])
new_name = cc[0].text_input("Name", key="mh_name", placeholder="e.g. Healthcare")
new_desc = cc[1].text_input("Description / GTM thesis", key="mh_desc",
                            placeholder="Who this experiment targets")
if cc[2].button("Create", disabled=not new_name.strip()):
    h = ws.create_hypothesis(new_name.strip(), new_desc.strip())
    st.session_state[SEL_KEY] = h.project_id
    st.rerun()

# --- list --------------------------------------------------------------------
st.subheader("2 · Hypotheses")
if not ws.hypotheses:
    st.info("No hypotheses yet. Create one above.")
    st.stop()

st.dataframe(
    [{"name": h.name, "status": h.status, "thesis": h.hypothesis,
      "adapted drafts": len(h.draft_versions), "approved": len(h.approved_versions),
      "latest derived from": (h.draft_versions[-1].metadata.derived_from_general_icp[:24] + "…")
      if h.draft_versions and h.draft_versions[-1].metadata.derived_from_general_icp else "—"}
     for h in ws.hypotheses],
    use_container_width=True, hide_index=True)

labels = {f"{h.name} ({h.status})": h.project_id for h in ws.hypotheses}
sel = st.session_state.get(SEL_KEY)
cur = next((l for l, v in labels.items() if v == sel), list(labels)[0])
chosen = st.selectbox("Selected hypothesis (also drives Interview / Strategy / Approval pages)",
                      list(labels), index=list(labels).index(cur))
st.session_state[SEL_KEY] = labels[chosen]
hyp = ws.get_hypothesis(labels[chosen])

# --- edit / delete -----------------------------------------------------------
with st.expander("Edit / delete this hypothesis"):
    e = st.columns([2, 3, 2])
    en = e[0].text_input("Name", value=hyp.name, key=f"edit_name_{hyp.project_id}")
    ed = e[1].text_input("Description", value=hyp.hypothesis, key=f"edit_desc_{hyp.project_id}")
    es = e[2].selectbox("Status", [ip.STATUS_ACTIVE, ip.STATUS_ARCHIVED],
                        index=0 if hyp.status == ip.STATUS_ACTIVE else 1, key=f"edit_st_{hyp.project_id}")
    if st.button("Save changes"):
        hyp.name, hyp.hypothesis, hyp.status = en.strip(), ed.strip(), es
        hyp.touch()
        st.rerun()
    st.divider()
    confirm = st.checkbox("Yes, permanently delete this hypothesis and its ICP lineage",
                          key=f"del_confirm_{hyp.project_id}")
    if st.button("Delete hypothesis", disabled=not confirm, icon=":material/delete:"):
        ws.delete_hypothesis(hyp.project_id)
        st.session_state[SEL_KEY] = None
        st.success("Hypothesis deleted. Other hypotheses are unaffected.")
        st.rerun()

# --- generate adapted ICP ----------------------------------------------------
st.subheader("3 · Generate Adapted ICP")
st.caption("Adapts the General ICP for this hypothesis using Company + Hypothesis knowledge. Curate "
           "hypothesis knowledge on **Knowledge Review**, refine gaps on **Knowledge Interview**, "
           "then review weights on **Strategy Review** and approve on **Approval**.")
_, is_live = dg.get_draft_client()
if not is_live:
    st.warning("**Offline mode** — no `ANTHROPIC_API_KEY`; adapted ICP uses the deterministic mock.")
if st.button("Generate Adapted ICP", type="primary", disabled=general is None, icon=":material/auto_awesome:"):
    with st.spinner("Adapting…"):
        client, _ = dg.get_draft_client()
        res = aicp.generate_adapted_icp(ws, hyp, client=client)
    st.session_state[f"adapted_result_{hyp.project_id}"] = res.summary()
    if res.ok:
        st.success(f"Generated Adapted ICP v{hyp.draft_versions[-1].metadata.version}"
                   f"{' (MOCK/OFFLINE)' if res.is_mock else ''} — derived from "
                   f"`{res.derived_from_general_icp[:28]}…`.")
    else:
        st.error(res.refusal_reason)
    st.rerun()

last = st.session_state.get(f"adapted_result_{hyp.project_id}")
if last and not last["ok"]:
    st.error(last["refusal_reason"])

# --- adapted ICP version history + view --------------------------------------
if hyp.draft_versions:
    st.subheader("4 · Adapted ICP versions")
    st.dataframe(
        [{"version": d.metadata.version, "name": d.metadata.name, "status": d.metadata.status,
          "scope": d.metadata.icp_scope,
          "derived from": d.metadata.derived_from_general_icp[:24] + "…"
          if d.metadata.derived_from_general_icp else "—",
          "identity": idy.artifact_identity_str(d)[:24] + "…"} for d in hyp.draft_versions],
        use_container_width=True, hide_index=True)
    latest = hyp.draft_versions[-1]
    if last and last.get("ok"):
        st.markdown(f"**IQS:** {'Valid' if last['is_valid'] else 'Has issues'}")
        if last.get("warnings"):
            with st.expander(f"Warnings ({len(last['warnings'])})"):
                for w in last["warnings"]:
                    st.write(f"- {w}")
    with st.expander("View latest Adapted ICP (read-only)", expanded=False):
        st.markdown(latest.to_markdown())
    st.download_button("Adapted ICP (Markdown)", icon=":material/download:", data=latest.to_markdown(),
                       file_name=f"{latest.metadata.name}-adapted.md", mime="text/markdown")
