"""General ICP — Streamlit page (Sprint 7).

VIEW ONLY. Generate the company-wide General ICP from company BusinessKnowledge, review it, browse
its version history, and save/reload the workspace. All logic lives in the general_icp service, the
CompanyWorkspace domain methods, and workspace_store — this page only forwards and renders.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import icp_project as ip                 # noqa: E402
import icp_draft_generator as dg         # noqa: E402
import icp_identity as idy               # noqa: E402
import general_icp as gicp              # noqa: E402
import workspace_store as store          # noqa: E402

st.header("General ICP")
st.caption("The company-wide capability ICP, generated from **Company Knowledge only** — the reusable "
           "baseline that Market Hypotheses adapt later. It is never adapted to a market here, and "
           "never approved here; it stays a reviewable Draft.")

PORT_KEY = "icp_portfolio"

# --- workspace (open / create) -----------------------------------------------
if st.session_state.get(PORT_KEY) is None:
    st.session_state[PORT_KEY] = ip.CompanyWorkspace()
ws = st.session_state[PORT_KEY]

st.subheader("1 · Company workspace")
new_name = st.text_input("Company / workspace name", value=ws.name or "", key="ws_name")
if new_name != ws.name:
    ws.name = new_name
    ws.touch()
cols = st.columns(3)
cols[0].metric("Company facts", len([it for it in ws.company.knowledge_items if it.is_active]))
cols[1].metric("Market hypotheses", len(ws.hypotheses))
cols[2].metric("General ICP versions", len(ws.list_general_icps()))
st.caption(f"Workspace id `{ws.workspace_id}`. Add and curate company materials on the "
           "**Knowledge** page; this page reads that knowledge, it never edits it.")

# --- company knowledge (read-only summary) -----------------------------------
with st.expander("Company Knowledge (read-only summary)"):
    s = ws.company.summary()
    st.write(f"**Active items:** {s['active_items']} · **Unknown fields:** "
             f"{', '.join(s['unknown_fields']) or '—'}")
    for name in ("company_overview", "products", "services", "capabilities", "technologies"):
        vals = ws.company.field(name)
        if vals:
            st.write(f"- **{name.replace('_', ' ')}:** {', '.join(str(v) for v in vals)}")

# --- generate ----------------------------------------------------------------
st.subheader("2 · Generate General ICP")
_, is_live = dg.get_draft_client()
if not is_live:
    st.warning("**Offline mode** — `ANTHROPIC_API_KEY` not set; the General ICP uses the deterministic "
               "MockICPDraftClient (placeholder content, clearly marked). Add the key and restart to "
               "generate with the real model.")
refusal = gicp.sufficiency_reason(ws.company)
if refusal:
    st.info(f"Cannot generate yet: {refusal}")
if st.button("Generate General ICP", type="primary", disabled=bool(refusal), icon=":material/auto_awesome:"):
    with st.spinner("Generating…"):
        client, _ = dg.get_draft_client()
        res = gicp.generate_and_append(ws, client=client)
    st.session_state["last_general_icp_result"] = res.summary()
    if res.ok:
        st.success(f"Generated General ICP v{ws.latest_general_icp().metadata.version}"
                   f"{' (MOCK/OFFLINE)' if res.is_mock else ''}.")
    else:
        st.error(res.refusal_reason)
    st.rerun()

last = st.session_state.get("last_general_icp_result")
if last and not last["ok"]:
    st.error(last["refusal_reason"])

# --- current General ICP -----------------------------------------------------
versions = ws.list_general_icps()
if not versions:
    st.info("No General ICP yet. Generate one above once company knowledge is sufficient.")
    st.stop()

st.subheader("3 · General ICP")
labels = {f"v{g.metadata.version} · {g.metadata.name}": i for i, g in enumerate(versions)}
picked = st.selectbox("Version", list(labels), index=len(labels) - 1)
icp = versions[labels[picked]]

meta_cols = st.columns(4)
meta_cols[0].metric("Scope", icp.metadata.icp_scope)
meta_cols[1].metric("Status", icp.metadata.status)
meta_cols[2].metric("Version", icp.metadata.version)
meta_cols[3].caption(f"Fingerprint\n`{idy.fingerprint_generated_icp(icp)[:16]}…`")

# IQS result for the newest generation (validation authority)
if last and last.get("ok"):
    st.markdown(f"**IQS:** {'Valid' if last['is_valid'] else 'Has issues'}")
    if last.get("blocking_gaps"):
        st.warning("Missing (blocking): " + ", ".join(last["blocking_gaps"]))
    if last.get("warnings"):
        with st.expander(f"Warnings ({len(last['warnings'])})"):
            for w in last["warnings"]:
                st.write(f"- {w}")
if icp.unknown_fields:
    st.caption("Unknown fields (declared, not invented): " + ", ".join(icp.unknown_fields))

with st.expander("View General ICP (structured, read-only)", expanded=True):
    st.markdown(icp.to_markdown())            # reuses the existing typed rendering; no invented fields
st.download_button("General ICP (Markdown)", icon=":material/download:", data=icp.to_markdown(),
                   file_name=f"{icp.metadata.name}-general.md", mime="text/markdown")

# --- version history ---------------------------------------------------------
st.subheader("4 · Version history")
st.dataframe(
    [{"version": g.metadata.version, "name": g.metadata.name, "status": g.metadata.status,
      "scope": g.metadata.icp_scope, "dimensions": len(g.dimensions),
      "fingerprint": idy.fingerprint_generated_icp(g)[:16]} for g in versions],
    use_container_width=True, hide_index=True)

# --- save / reload -----------------------------------------------------------
st.subheader("5 · Save / reload workspace")
default_path = st.session_state.get("ws_path", str(Path.home() / "gtm_workspace.json"))
path = st.text_input("Workspace file path", value=default_path, key="ws_path_input")
sc = st.columns(2)
if sc[0].button("Save workspace", icon=":material/save:"):
    try:
        saved = store.save_workspace(ws, path)
        st.session_state["ws_path"] = str(saved)
        st.success(f"Saved to {saved}.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {exc}")
if sc[1].button("Reload workspace", icon=":material/folder_open:"):
    try:
        loaded = store.load_workspace(path)
        st.session_state[PORT_KEY] = loaded
        st.session_state["ws_path"] = path
        st.session_state.pop("last_general_icp_result", None)
        st.success(f"Reloaded '{loaded.name}' — {len(loaded.list_general_icps())} General ICP "
                   "version(s) restored.")
        st.rerun()
    except store.WorkspacePersistenceError as exc:
        st.error(str(exc))
