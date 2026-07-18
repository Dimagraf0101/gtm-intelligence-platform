"""Knowledge Review — Streamlit page (Sprint 5.1, re-scoped in Sprint 5.2).

VIEW ONLY. All workflow logic lives in pipeline/knowledge_review.py (KnowledgeReviewWorkspace) and
pipeline/icp_project.py (ICPPortfolio / ICPProject). This page renders read-models and forwards user
actions to the workspace; it contains no business logic and never edits a Draft ICP.

Scope model: one **Company Knowledge** base (reusable company facts) + many isolated **ICP Projects**,
each with its own **ICP Knowledge** (hypothesis-only facts) and draft versions. Selecting a project
shows the Composed view (Company Knowledge + ICP Knowledge) that gap detection and draft generation
consume. Hypotheses never contaminate one another.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import business_knowledge as bk          # noqa: E402
import source_package as sp              # noqa: E402
import source_documents as sd            # noqa: E402
import knowledge_extractor as ke         # noqa: E402
import knowledge_review as kr            # noqa: E402
import icp_project as ip                 # noqa: E402

st.title("Business Knowledge")
st.caption("The mandatory human-review stage. **Company Knowledge** is the reusable Source of Truth; "
           "each **ICP Project** holds hypothesis-only **ICP Knowledge**. Draft ICPs are always "
           "regenerated from Company Knowledge + the selected project's ICP Knowledge.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"


def _portfolio() -> ip.ICPPortfolio | None:
    return st.session_state.get(PORT_KEY)


def _selected_project(port: ip.ICPPortfolio):
    pid = st.session_state.get(SEL_KEY)
    if not pid:
        return None
    try:
        return port.get_project(pid)
    except KeyError:
        return None


# A portfolio (one Company Knowledge + many isolated ICP Projects) is the working set. Start empty.
if _portfolio() is None:
    st.session_state[PORT_KEY] = ip.ICPPortfolio()
port = _portfolio()

# --- Stage 1: ICP Projects (isolated hypotheses) -----------------------------
st.subheader("1 · ICP Projects")
pc = st.columns([2, 3, 1])
new_name = pc[0].text_input("New project name", key="np_name", placeholder="e.g. FinTech — DACH")
new_hypo = pc[1].text_input("Hypothesis", key="np_hypo", placeholder="Who this ICP targets")
if pc[2].button("Create", disabled=not new_name.strip()):
    p = port.create_project(new_name.strip(), new_hypo.strip())
    st.session_state[SEL_KEY] = p.project_id
    st.rerun()

options = {"— Company Knowledge only —": None}
for p in port.projects:
    options[f"{p.name}  ({len(p.project_knowledge.knowledge_items)} ICP items, "
            f"{len(p.draft_versions)} draft(s))"] = p.project_id
labels = list(options)
current_pid = st.session_state.get(SEL_KEY)
current_label = next((l for l, v in options.items() if v == current_pid), labels[0])
chosen = st.selectbox("Active project", labels, index=labels.index(current_label))
st.session_state[SEL_KEY] = options[chosen]
project = _selected_project(port)

# --- Stage 2: extract company assets into an EXPLICITLY CHOSEN scope ----------
st.subheader("2 · Extract company assets")
cat = st.selectbox("Source category for the uploaded materials",
                   sd.SOURCE_CATEGORIES, index=len(sd.SOURCE_CATEGORIES) - 1)
files = st.file_uploader("Upload company materials (PDF / DOCX / PPTX / TXT / MD)",
                         type=["pdf", "docx", "pptx", "txt", "md", "markdown"],
                         accept_multiple_files=True)
project_dest_label = f"Selected ICP Project — {project.name}" if project is not None \
    else "Selected ICP Project (select one above first)"
dest_choice = st.radio(
    "Extract into", ["Company Knowledge", project_dest_label],
    help="Company-wide materials → Company Knowledge. Hypothesis-specific materials — existing ICPs, "
         "campaign notes, segment playbooks — → the selected ICP Project. No automatic promotion; no "
         "silent fallback.")
extract_scope = ip.SCOPE_COMPANY if dest_choice == "Company Knowledge" else ip.SCOPE_PROJECT
need_project = extract_scope == ip.SCOPE_PROJECT and project is None
if need_project:
    st.warning("Select or create an ICP Project above to extract hypothesis-specific materials into it.")
_, is_live = ke.get_client()
if not is_live:
    st.warning("**Offline mode** — `ANTHROPIC_API_KEY` not set; extraction uses the deterministic "
               "MockKnowledgeClient (placeholder knowledge, clearly marked). Add the key and restart "
               "to extract with the real model.")
if st.button("Extract", type="primary", disabled=(not files or need_project)):
    with st.spinner("Extracting…"):
        pkg = sp.build_package_from_files([(f.name, f.getvalue(), cat) for f in files])
        client, _ = ke.get_client()
        result = ke.extract_business_knowledge(pkg, client=client)
        ip.route_extraction(port.company, result, scope=extract_scope, project=project)
    where = "Company Knowledge" if extract_scope == ip.SCOPE_COMPANY else f"ICP Project '{project.name}'"
    st.success(f"Extracted {len(result.business_knowledge.knowledge_items)} item(s) into {where} "
               f"from {result.source_count} source(s). is_mock={result.is_mock}.")
    st.rerun()

ws = kr.KnowledgeReviewWorkspace.for_project(port.company, project) if project is not None \
    else kr.KnowledgeReviewWorkspace(port.company)
if project is not None:
    st.caption(f"Selected **{project.name}** — ICP Knowledge is isolated from every other project. "
               "Status below reflects the Composed view (Company + ICP Knowledge).")

# --- Summary -----------------------------------------------------------------
st.subheader("3 · Status")
s = ws.summary()
c = st.columns(5)
c[0].metric("Completeness", f"{s['completeness']}/100")
c[1].metric("Active items", s["active_items"])
c[2].metric("Open conflicts", len(s["open_conflicts"]))
c[3].metric("Unknown fields", len(s["unknown_fields"]))
c[4].metric("Ready for ICP?", "Yes" if s["is_ready_for_icp_generation"] else "No")
st.write(f"**By status:** {s['by_status']}")
with st.expander(f"Blocking gaps ({len(s['blocking_gaps'])})"):
    for g in s["blocking_gaps"]:
        st.write(f"- **{g['field']}** — {g['reason']}  \n  _{g['question']}_")
with st.expander(f"Important gaps ({len(s['important_gaps'])})"):
    for g in s["important_gaps"]:
        st.write(f"- **{g['field']}** — {g['reason']}")
if s["unknown_fields"]:
    st.caption("Unknown fields: " + ", ".join(s["unknown_fields"]))


def _act_on(rows, ws, key_prefix, *, allow_promote=False, allow_move=False):
    """Compact per-scope action block. Actions forward to the scope-aware workspace only."""
    if not rows:
        st.caption("No items in this scope.")
        return
    st.dataframe(
        [{"id": r["knowledge_id"][:10], "category": r["category"], "attribute": r["attribute"],
          "value": r["value"], "status": r["status"], "temporal": r.get("temporal_context", ""),
          "conf": r["confidence"], "origin": r["origin"],
          "sources": "; ".join(r["sources"]), "evidence": r["evidence_excerpt"][:50]} for r in rows],
        use_container_width=True, hide_index=True)
    id_map = {f"{r['category']}/{r['attribute']}: {str(r['value'])[:40]}  [{r['knowledge_id'][:8]}]":
              r["knowledge_id"] for r in rows}
    pick = st.selectbox("Item", list(id_map), key=f"{key_prefix}_pick")
    kid = id_map[pick]
    acts = ["Confirm", "Reject", "Edit value"]
    if allow_promote:
        acts.append("Promote to Company Knowledge")
    if allow_move:
        acts += ["Move to ICP Project", "Copy to ICP Project (override)"]
    action = st.radio("Action", acts, horizontal=True, key=f"{key_prefix}_act")
    new_value = st.text_input("New / override value", "", key=f"{key_prefix}_val") \
        if action in ("Edit value", "Copy to ICP Project (override)") else ""
    note = st.text_input("Note (optional)", "", key=f"{key_prefix}_note")
    if st.button("Apply", key=f"{key_prefix}_apply"):
        try:
            if action == "Confirm":
                ws.confirm(kid, note=note)
            elif action == "Reject":
                ws.reject(kid, note=note)
            elif action == "Edit value" and new_value.strip():
                ws.edit(kid, value=new_value.strip(), note=note or "manual edit")
            elif action == "Promote to Company Knowledge":
                ws.promote_to_company(kid, note=note)
            elif action == "Move to ICP Project":
                ws.move_to_project(kid, note=note)
            elif action == "Copy to ICP Project (override)":
                ws.copy_to_project(kid, value=new_value.strip() or None, note=note)
            st.rerun()
        except (ValueError, KeyError) as e:
            st.error(str(e))


# --- Review & curate ---------------------------------------------------------
st.subheader("4 · Review & curate knowledge")
if project is None:
    _act_on(ws.company_items(), ws, "solo")
else:
    t_company, t_project, t_composed = st.tabs(
        ["Company Knowledge", "ICP Knowledge", "Composed (Company + ICP)"])
    with t_company:
        st.caption("Reusable company facts. Move hypothesis-specific facts down into the ICP Project.")
        _act_on(ws.company_items(), ws, "co", allow_move=True)
    with t_project:
        st.caption("Hypothesis-only facts for this project. Promote reusable facts up to Company.")
        _act_on(ws.project_items(), ws, "pr", allow_promote=True)
    with t_composed:
        st.caption("Read-only view used for gap detection and draft generation. Each row is labelled "
                   "by the scope that owns it.")
        rows = ws.composed_items()
        st.dataframe(
            [{"scope": r.get("scope", ""), "category": r["category"], "attribute": r["attribute"],
              "value": r["value"], "status": r["status"],
              "temporal": r.get("temporal_context", "")} for r in rows],
            use_container_width=True, hide_index=True)

# --- Add new knowledge -------------------------------------------------------
with st.expander("Add knowledge", icon=":material/add:"):
    a = st.columns(3)
    ncat = a[0].selectbox("Category", bk.CATEGORIES, key="add_cat")
    nattr = a[1].text_input("Attribute", key="add_attr")
    nval = a[2].text_input("Value", key="add_val")
    b = st.columns(2)
    ntemporal = b[0].selectbox("Temporal context", bk.TEMPORAL_CONTEXTS, key="add_temporal")
    scope_choices = ["Company Knowledge"] + (["ICP Knowledge"] if project is not None else [])
    nscope = b[1].selectbox("Add to", scope_choices, key="add_scope")
    nev = st.text_input("Evidence excerpt (optional)", key="add_ev")
    if st.button("Add knowledge", disabled=not (nattr and nval)):
        scope = kr.SCOPE_PROJECT if nscope == "ICP Knowledge" else kr.SCOPE_COMPANY
        ws.add(ncat, nattr, nval, scope=scope, evidence_excerpt=nev, temporal_context=ntemporal)
        st.rerun()

# --- Resolve conflicts (Company scope) ---------------------------------------
open_conf = ws.conflicts(scope=kr.SCOPE_COMPANY, unresolved_only=True)
if open_conf:
    with st.expander(f"Resolve conflicts ({len(open_conf)})", icon=":material/warning:", expanded=True):
        for cf in open_conf:
            st.write(f"**{cf['category']}/{cf['attribute']}** — {cf['values']}")
            choices = {}
            for iid in cf["item_ids"]:
                try:
                    it = port.company._get(iid)
                    choices[f"{it.value}  [{iid[:8]}]"] = iid
                except KeyError:
                    continue
            if choices:
                pref = st.selectbox("Preferred value", list(choices), key=f"conf_{cf['conflict_id']}")
                if st.button("Resolve", key=f"btn_{cf['conflict_id']}"):
                    ws.resolve_conflict(cf["conflict_id"], choices[pref])
                    st.rerun()

# --- Generate Draft ICP ------------------------------------------------------
st.subheader("5 · Generate Draft ICP")
if project is None:
    st.info("Select an ICP Project above to generate and version a Draft ICP for that hypothesis.")
else:
    st.caption(f"Generates a brand-new Draft ICP for **{project.name}** from Company Knowledge + this "
               "project's ICP Knowledge. Every generation is a new version; previous drafts are never "
               "modified. Status is always Draft (no approval here).")
    if st.button("Generate Draft ICP", type="primary", icon=":material/auto_awesome:"):
        client, _ = __import__("icp_draft_generator").get_draft_client()
        ws.generate_draft(client=client, icp_name=project.name)
        st.rerun()

    if project.draft_versions:
        st.write(f"**{len(project.draft_versions)} draft version(s)** for this project:")
        vlabels = {f"v{i+1} · {d.metadata.name} ({d.metadata.status})": i
                   for i, d in enumerate(project.draft_versions)}
        vpick = st.selectbox("View version", list(vlabels), index=len(vlabels) - 1)
        icp = project.draft_versions[vlabels[vpick]]
        with st.expander("View Draft ICP (read-only)", expanded=True):
            st.markdown(icp.to_markdown())
        st.download_button("Draft ICP (Markdown)", icon=":material/download:", data=icp.to_markdown(),
                           file_name=f"{icp.metadata.name}-draft.md", mime="text/markdown")
        st.download_button("Draft ICP (JSON)", icon=":material/download:", data=icp.to_json(),
                           file_name=f"{icp.metadata.name}-draft.json", mime="application/json")
