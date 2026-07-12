"""Business Knowledge Review — Streamlit page (Sprint 5.1).

VIEW ONLY. All workflow logic lives in pipeline/knowledge_review.py (KnowledgeReviewWorkspace) and
the modules it wraps. This page renders read-models and forwards user actions to the workspace; it
contains no business logic and never edits a Draft ICP.

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

st.set_page_config(page_title="Business Knowledge Review", page_icon="🧠", layout="wide")
st.title("🧠 Business Knowledge Review")
st.caption("The mandatory human-review stage. Business Knowledge is the platform's Source of Truth — "
           "every edit here updates Business Knowledge only; the Draft ICP is always regenerated from it.")

WS_KEY = "bk_workspace"


def _ws() -> kr.KnowledgeReviewWorkspace | None:
    return st.session_state.get(WS_KEY)


# --- Stage 1: build Business Knowledge from company assets --------------------
st.subheader("1 · Company assets → Business Knowledge")
cat = st.selectbox("Source category for the uploaded materials",
                   sd.SOURCE_CATEGORIES, index=len(sd.SOURCE_CATEGORIES) - 1)
files = st.file_uploader("Upload company materials (PDF / DOCX / PPTX / TXT / MD)",
                         type=["pdf", "docx", "pptx", "txt", "md", "markdown"],
                         accept_multiple_files=True)
_, is_live = ke.get_client()
if not is_live:
    st.warning("**Offline mode** — `ANTHROPIC_API_KEY` not set; extraction uses the deterministic "
               "MockKnowledgeClient (placeholder knowledge, clearly marked). Add the key and restart "
               "to extract with the real model.")
if st.button("Extract Business Knowledge", type="primary", disabled=not files):
    with st.spinner("Extracting…"):
        pkg = sp.build_package_from_files([(f.name, f.getvalue(), cat) for f in files])
        client, _ = ke.get_client()
        result = ke.extract_business_knowledge(pkg, client=client)
        st.session_state[WS_KEY] = kr.KnowledgeReviewWorkspace.from_extraction_result(result)
    st.success(f"Extracted {len(st.session_state[WS_KEY].knowledge.knowledge_items)} knowledge item(s) "
               f"from {result.source_count} source(s). is_mock={result.is_mock}")
    st.rerun()

ws = _ws()
if ws is None:
    st.info("Upload materials and extract to begin the review.")
    st.stop()

# --- Summary -----------------------------------------------------------------
st.subheader("2 · Business Knowledge status")
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
with st.expander(f"Optional gaps ({len(s['optional_gaps'])})"):
    for g in s["optional_gaps"]:
        st.write(f"- **{g['field']}** — {g['reason']}")
if s["unknown_fields"]:
    st.caption("Unknown fields: " + ", ".join(s["unknown_fields"]))

# --- Browse / filter / search ------------------------------------------------
st.subheader("3 · Review & curate knowledge")
fc = st.columns(3)
cat_filter = fc[0].selectbox("Filter category", ["(all)"] + ws.categories())
status_filter = fc[1].selectbox("Filter status", ["(all)"] + list(kr.STATUSES))
search = fc[2].text_input("Search", "")
rows = ws.items(category=None if cat_filter == "(all)" else cat_filter,
                status=None if status_filter == "(all)" else status_filter,
                search=search or None)
st.caption(f"{len(rows)} item(s)")
st.dataframe(
    [{"id": r["knowledge_id"][:10], "category": r["category"], "attribute": r["attribute"],
      "value": r["value"], "status": r["status"], "conf": r["confidence"], "origin": r["origin"],
      "sources": "; ".join(r["sources"]), "evidence": r["evidence_excerpt"][:60]} for r in rows],
    use_container_width=True, hide_index=True)

# --- Act on an item ----------------------------------------------------------
st.markdown("**Act on an item**")
if rows:
    id_map = {f"{r['category']}/{r['attribute']}: {str(r['value'])[:40]}  [{r['knowledge_id'][:8]}]":
              r["knowledge_id"] for r in rows}
    pick = st.selectbox("Item", list(id_map))
    kid = id_map[pick]
    action = st.radio("Action", ["Confirm", "Reject", "Edit value"], horizontal=True)
    new_value = st.text_input("New value (for Edit)", "") if action == "Edit value" else ""
    note = st.text_input("Note (optional)", "")
    if st.button("Apply"):
        if action == "Confirm":
            ws.confirm(kid, note=note)
        elif action == "Reject":
            ws.reject(kid, note=note)
        elif action == "Edit value" and new_value.strip():
            ws.edit(kid, value=new_value.strip(), note=note or "manual edit")
        st.rerun()

# --- Add new knowledge -------------------------------------------------------
with st.expander("➕ Add new knowledge (marked origin=user_input)"):
    a = st.columns(3)
    ncat = a[0].selectbox("Category", bk.CATEGORIES, key="add_cat")
    nattr = a[1].text_input("Attribute", key="add_attr")
    nval = a[2].text_input("Value", key="add_val")
    nev = st.text_input("Evidence excerpt (optional)", key="add_ev")
    if st.button("Add knowledge", disabled=not (nattr and nval)):
        ws.add(ncat, nattr, nval, evidence_excerpt=nev)
        st.rerun()

# --- Merge duplicates --------------------------------------------------------
with st.expander("🔗 Merge duplicate items (same category + attribute + value)"):
    ids = {f"{r['category']}/{r['attribute']}: {str(r['value'])[:30]} [{r['knowledge_id'][:8]}]":
           r["knowledge_id"] for r in ws.items()}
    if len(ids) >= 2:
        keep = st.selectbox("Keep", list(ids), key="merge_keep")
        other = st.selectbox("Merge into 'Keep' and remove", list(ids), key="merge_other")
        if st.button("Merge"):
            try:
                ws.merge_duplicates(ids[keep], ids[other])
                st.rerun()
            except ValueError as e:
                st.error(str(e))

# --- Resolve conflicts -------------------------------------------------------
open_conf = ws.conflicts(unresolved_only=True)
if open_conf:
    with st.expander(f"⚠️ Resolve conflicts ({len(open_conf)})", expanded=True):
        for cf in open_conf:
            st.write(f"**{cf['category']}/{cf['attribute']}** — {cf['values']}")
            choices = {}
            for iid in cf["item_ids"]:
                try:
                    it = ws.knowledge._get(iid)
                    choices[f"{it.value}  [{iid[:8]}]"] = iid
                except KeyError:
                    continue
            if choices:
                pref = st.selectbox("Preferred value", list(choices), key=f"conf_{cf['conflict_id']}")
                if st.button("Resolve", key=f"btn_{cf['conflict_id']}"):
                    ws.resolve_conflict(cf["conflict_id"], choices[pref])
                    st.rerun()

# --- Generate Draft ICP (the one deterministic action) -----------------------
st.subheader("4 · Generate Draft ICP")
st.caption("Generates a brand-new Draft ICP from the CURRENT Business Knowledge. The draft is a "
           "derived, read-only artifact here — it is never edited in this workspace.")
if st.button("🧩 Generate Draft ICP", type="primary"):
    client, _ = __import__("icp_draft_generator").get_draft_client()
    res = ws.generate_draft(client=client)
    st.session_state["last_draft"] = res
    st.rerun()

res = st.session_state.get("last_draft")
if res is not None:
    icp = res.generated_icp
    val = res.validation_result
    st.success(f"Draft ICP **{icp.metadata.name}** — status {icp.metadata.status}, "
               f"{len(icp.dimensions)} dimension(s), IQS "
               f"{'valid' if val.is_valid else 'blocking issues'} "
               f"(completeness {val.completeness_score}). is_mock={res.is_mock}")
    if val.blocking_errors:
        st.warning("IQS blocking: " + "; ".join(val.blocking_errors))
    with st.expander("View Draft ICP (read-only)"):
        st.markdown(icp.to_markdown())
    st.download_button("⬇️ Draft ICP (Markdown)", data=icp.to_markdown(),
                       file_name=f"{icp.metadata.name}-draft.md", mime="text/markdown")
    st.download_button("⬇️ Draft ICP (JSON)", data=icp.to_json(),
                       file_name=f"{icp.metadata.name}-draft.json", mime="application/json")
