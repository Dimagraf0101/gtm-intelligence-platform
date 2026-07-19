"""ICP Workspace — guided 3-step wizard (Sprint 5.2).

A cohesive, linear experience over the ICP Workspace backend:

    ① Upload company materials  →  ② Review & approve candidates  →  ③ Generate a Draft ICP

VIEW ONLY. All workflow logic lives in pipeline/knowledge_review.py (KnowledgeReviewWorkspace) and
the modules it wraps (source_documents, source_package, knowledge_extractor, icp_draft_generator).
This page renders read-models and forwards user actions to the workspace; it holds no business logic
and never edits a Draft ICP. Business Knowledge is the single Source of Truth — every curation action
here mutates Business Knowledge only, and the Draft ICP is always regenerated from it.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import business_knowledge as bk          # noqa: E402
import source_documents as sd            # noqa: E402
import source_package as sp              # noqa: E402
import knowledge_extractor as ke         # noqa: E402
import knowledge_review as kr            # noqa: E402
import icp_draft_generator as dg         # noqa: E402
import icp_approval                      # noqa: E402
import icp_library as lib                # noqa: E402

st.set_page_config(page_title="ICP Workspace", page_icon="🧭", layout="wide")

# --- session-state keys -------------------------------------------------------
WS_KEY = "bk_workspace"        # KnowledgeReviewWorkspace (the Source of Truth wrapper)
STEP_KEY = "wizard_step"       # 1 | 2 | 3
DRAFT_KEY = "last_draft"       # ICPDraftGenerationResult
META_KEY = "extract_meta"      # small dict describing the last extraction

STEPS = [
    ("Upload", "Add your company materials"),
    ("Review & approve", "Curate the extracted candidates"),
    ("Generate ICP", "Produce a Draft ICP"),
]

_STATUS_BADGE = {
    bk.CONFIRMED: "✅ approved",
    bk.PROPOSED: "🟡 pending",
    bk.CONFLICTING: "⚠️ conflict",
    bk.UNKNOWN: "❔ unknown",
    bk.REJECTED: "🗑️ rejected",
}
_APPROVE_CONF_FLOOR = 0.6      # bulk "approve all pending" only touches confident candidates


# --- small helpers ------------------------------------------------------------

def _ws() -> kr.KnowledgeReviewWorkspace | None:
    return st.session_state.get(WS_KEY)


def _step() -> int:
    return int(st.session_state.get(STEP_KEY, 1))


def _goto(step: int) -> None:
    st.session_state[STEP_KEY] = step
    st.rerun()


def _reset() -> None:
    for k in (WS_KEY, DRAFT_KEY, META_KEY):
        st.session_state.pop(k, None)
    _goto(1)


def render_stepper(current: int) -> None:
    cols = st.columns(len(STEPS))
    for i, (label, desc) in enumerate(STEPS, start=1):
        icon = "✅" if i < current else ("🔵" if i == current else "⚪")
        with cols[i - 1]:
            if i == current:
                st.markdown(f"### {icon} {i}. {label}")
                st.caption(desc)
            else:
                st.markdown(f"{icon} **{i}. {label}**")
    st.divider()


# --- Step 1: upload -----------------------------------------------------------

def step_upload() -> None:
    st.subheader("Step 1 · Upload company materials")
    st.caption("PDF / DOCX / PPTX / TXT / MD — pitch or sales decks, case studies, a service "
               "catalogue, proposals, an existing ICP, or discovery/meeting notes. Each file is "
               "treated as evidence with source attribution.")

    _, is_live = ke.get_client()
    if not is_live:
        st.warning("**Offline mode** — `ANTHROPIC_API_KEY` is not set. Extraction will use the "
                   "deterministic **MockKnowledgeClient**, which returns clearly-marked placeholder "
                   "candidates rather than real knowledge. Add the key and restart to extract with "
                   "the real model.")

    files = st.file_uploader(
        "Choose files", type=["pdf", "docx", "pptx", "txt", "md", "markdown"],
        accept_multiple_files=True)

    specs: list[tuple[str, bytes, str]] = []
    if files:
        st.markdown("**Tag each file** so extraction knows what it is:")
        default_idx = sd.SOURCE_CATEGORIES.index(sd.DEFAULT_CATEGORY)
        for i, f in enumerate(files):
            data = f.getvalue()
            c = st.columns([5, 3])
            c[0].markdown(f"📄 `{f.name}` · {len(data):,} bytes")
            cat = c[1].selectbox("Category", sd.SOURCE_CATEGORIES, index=default_idx,
                                 key=f"cat::{f.name}::{i}", label_visibility="collapsed")
            specs.append((f.name, data, cat))

    nav = st.columns([2, 2, 4])
    if nav[0].button("Extract knowledge  ▶", type="primary", disabled=not specs,
                     use_container_width=True):
        with st.spinner("Reading documents and extracting business knowledge…"):
            pkg = sp.build_package_from_files(specs)
            client, _ = ke.get_client()
            result = ke.extract_business_knowledge(pkg, client=client)

        if result.source_count == 0:
            st.error("No readable text could be extracted from those files. They may be scanned or "
                     "image-only PDFs (OCR is not supported). Try a text-based PDF, or DOCX / PPTX / "
                     "TXT / MD.")
            for w in pkg.package_warnings[:5]:
                st.caption("• " + w)
            return

        st.session_state[WS_KEY] = kr.KnowledgeReviewWorkspace.from_extraction_result(result)
        st.session_state[DRAFT_KEY] = None
        st.session_state[META_KEY] = {
            "source_count": result.source_count,
            "items": len(result.business_knowledge.knowledge_items),
            "is_mock": result.is_mock,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "warnings": list(result.warnings),
        }
        _goto(2)

    # Returning to Step 1 after an extraction: let the user continue without re-extracting.
    if _ws() is not None and nav[1].button("Continue to review  ▶", use_container_width=True):
        _goto(2)
    if _ws() is not None:
        st.info(f"An extraction with {len(_ws().knowledge.knowledge_items)} candidate(s) is already "
                "loaded. Extracting again starts a fresh set and discards the current review.")


# --- Step 2: review & approve -------------------------------------------------

def _candidate_card(ws, r: dict) -> None:
    kid = r["knowledge_id"]
    with st.container(border=True):
        top = st.columns([6, 2, 1.5, 1.5])
        top[0].markdown(f"**{r['category']} / {r['attribute']}**  \n{r['value'] or '_(no value)_'}")
        top[1].markdown(f"{_STATUS_BADGE.get(r['status'], r['status'])}  \n"
                        f"conf {r['confidence']:.2f} · {r['origin']}")
        if r["status"] == bk.CONFIRMED:
            top[2].markdown("**✓ approved**")
        elif top[2].button("Approve", key=f"appr::{kid}", type="primary",
                           use_container_width=True):
            ws.confirm(kid)
            st.rerun()
        if top[3].button("Reject", key=f"rej::{kid}", use_container_width=True):
            ws.reject(kid)
            st.rerun()

        with st.expander("Evidence · edit"):
            if r["sources"]:
                st.caption("Sources: " + "; ".join(r["sources"]))
            if r["evidence_excerpt"]:
                st.markdown(f"> {r['evidence_excerpt']}")
            else:
                st.caption("No verbatim evidence excerpt (unverified — kept as a proposal).")
            new_value = st.text_input("Edit value", value=r["value"], key=f"editval::{kid}")
            if st.button("Save edit", key=f"save::{kid}"):
                if new_value.strip() and new_value != r["value"]:
                    ws.edit(kid, value=new_value.strip(), note="manual edit")
                    st.rerun()


def step_review() -> None:
    ws = _ws()
    st.subheader("Step 2 · Review & approve candidates")
    st.caption("Approve the facts you trust, reject the noise, fix values, and resolve conflicts. "
               "Everything you approve becomes protected Business Knowledge the ICP is built from.")

    meta = st.session_state.get(META_KEY, {})
    if meta.get("is_mock"):
        st.warning("These candidates were produced **offline by the mock extractor** — placeholders, "
                   "not real knowledge. Set `ANTHROPIC_API_KEY` and re-extract for real results.")

    s = ws.summary()
    by = s["by_status"]
    m = st.columns(5)
    m[0].metric("Candidates", s["active_items"])
    m[1].metric("Approved", by.get(bk.CONFIRMED, 0))
    m[2].metric("Pending", by.get(bk.PROPOSED, 0))
    m[3].metric("Conflicts", len(s["open_conflicts"]))
    m[4].metric("Completeness", f"{s['completeness']}/100")

    # --- conflicts first (they block clean approval) ---
    open_conf = ws.conflicts(unresolved_only=True)
    if open_conf:
        st.markdown("#### ⚠️ Resolve conflicts")
        st.caption("Two sources disagree on a single-valued fact. Pick the value to keep.")
        for cf in open_conf:
            with st.container(border=True):
                st.write(f"**{cf['category']} / {cf['attribute']}** — {cf['values']}")
                choices = {}
                for iid in cf["item_ids"]:
                    try:
                        it = ws.knowledge._get(iid)
                        choices[f"{it.value}  ·  {iid[:8]}"] = iid
                    except KeyError:
                        continue
                if choices:
                    pref = st.selectbox("Keep", list(choices), key=f"cf::{cf['conflict_id']}")
                    if st.button("Resolve", key=f"cfb::{cf['conflict_id']}"):
                        ws.resolve_conflict(cf["conflict_id"], choices[pref])
                        st.rerun()

    # --- filters ---
    st.markdown("#### Candidates")
    fc = st.columns([3, 3, 4])
    cat_filter = fc[0].selectbox("Category", ["(all)"] + ws.categories())
    status_filter = fc[1].selectbox("Status", ["(all)"] + list(kr.STATUSES))
    search = fc[2].text_input("Search", "")
    rows = ws.items(
        category=None if cat_filter == "(all)" else cat_filter,
        status=None if status_filter == "(all)" else status_filter,
        search=search or None)

    # --- bulk actions ---
    bc = st.columns([3, 3, 4])
    if bc[0].button(f"✅ Approve all pending (conf ≥ {_APPROVE_CONF_FLOOR:g})",
                    use_container_width=True):
        for it in ws.items(status=bk.PROPOSED):
            if it["confidence"] >= _APPROVE_CONF_FLOOR:
                ws.confirm(it["knowledge_id"])
        st.rerun()
    if bc[1].button("Approve all in view", use_container_width=True, disabled=not rows):
        for it in rows:
            if it["status"] != bk.CONFIRMED:
                ws.confirm(it["knowledge_id"])
        st.rerun()
    bc[2].caption(f"{len(rows)} shown")

    if not rows:
        st.info("No candidates match the current filters.")
    for r in rows:
        _candidate_card(ws, r)

    # --- advanced: add / merge (kept out of the main flow) ---
    with st.expander("➕ Add a fact · 🔗 merge duplicates (advanced)"):
        st.markdown("**Add a fact** (recorded as `origin=user_input`, confirmed)")
        a = st.columns(3)
        ncat = a[0].selectbox("Category", bk.CATEGORIES, key="add_cat")
        nattr = a[1].text_input("Attribute", key="add_attr")
        nval = a[2].text_input("Value", key="add_val")
        nev = st.text_input("Evidence excerpt (optional)", key="add_ev")
        if st.button("Add fact", disabled=not (nattr and nval)):
            ws.add(ncat, nattr, nval, evidence_excerpt=nev)
            st.rerun()

        st.markdown("**Merge duplicates** (same category + attribute + value)")
        ids = {f"{r['category']}/{r['attribute']}: {str(r['value'])[:30]} [{r['knowledge_id'][:8]}]":
               r["knowledge_id"] for r in ws.items()}
        if len(ids) >= 2:
            keep = st.selectbox("Keep", list(ids), key="merge_keep")
            other = st.selectbox("Merge in and remove", list(ids), key="merge_other")
            if st.button("Merge"):
                try:
                    ws.merge_duplicates(ids[keep], ids[other])
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        else:
            st.caption("Need at least two items to merge.")

    # --- navigation ---
    st.divider()
    if by.get(bk.PROPOSED, 0):
        st.caption(f"{by[bk.PROPOSED]} candidate(s) still pending — you can still generate; pending "
                   "items are included but flagged for review.")
    nav = st.columns([2, 3, 3])
    if nav[0].button("◀ Back", use_container_width=True):
        _goto(1)
    if nav[1].button("Generate Draft ICP  ▶", type="primary", use_container_width=True):
        _goto(3)
    if nav[2].button("↺ Start over", use_container_width=True):
        _reset()


# --- Step 3: generate ---------------------------------------------------------

def _render_draft(res) -> None:
    icp = res.generated_icp
    val = res.validation_result
    if res.is_mock:
        st.warning("**MOCK / OFFLINE draft** — placeholder content, not production-ready.")
    st.success(
        f"Draft **{icp.metadata.name}** — {len(icp.dimensions)} dimension(s) · "
        f"status {icp.metadata.status} · "
        f"IQS {'valid ✅' if val.is_valid else 'blocking issues ❌'} "
        f"(completeness {val.completeness_score})")
    if val.blocking_errors:
        st.error("IQS blocking: " + "; ".join(val.blocking_errors))
    if val.warnings:
        with st.expander(f"IQS warnings ({len(val.warnings)})"):
            for w in val.warnings:
                st.write("• " + w)
    with st.expander("View Draft ICP (Markdown)"):
        st.markdown(icp.to_markdown())
    d = st.columns(2)
    d[0].download_button("⬇️ Draft ICP (Markdown)", data=icp.to_markdown(),
                         file_name=f"{icp.metadata.name}-draft.md", mime="text/markdown",
                         use_container_width=True)
    d[1].download_button("⬇️ Draft ICP (JSON)", data=icp.to_json(),
                         file_name=f"{icp.metadata.name}-draft.json", mime="application/json",
                         use_container_width=True)

    st.markdown("**Save this ICP so you can qualify leads with it**")
    st.caption("Approval is a human act (IQS v1.0 §10): an ICP may only qualify leads once you "
               "approve it. You can also save it as a Draft and approve later in Run Campaign.")

    ack = True
    if val.is_valid and val.warnings:
        ack = st.checkbox(f"I have read and acknowledge the {len(val.warnings)} IQS warning(s) "
                          "above.", key="approve_ack")
    c = st.columns(2)
    if c[0].button("✅ Approve & save to library", type="primary",
                   disabled=not (val.is_valid and ack)):
        icp_approval.approve(icp, acknowledge_warnings=True)
        entry = lib.save_generated(icp)
        st.success(f"Approved and saved '{entry.name}'. It can now qualify leads through the "
                   "structured engine bridge.")
        st.page_link("pages/2_Run_Campaign.py", label="🚀 Use it in Run Campaign", icon="➡️")
    if c[1].button("💾 Save as Draft (approve later)"):
        entry = lib.save_generated(icp)
        st.success(f"Saved '{entry.name}' as a Draft — approve it in **🚀 Run Campaign** before "
                   "qualifying leads with it.")
    if not val.is_valid:
        st.caption("Approval is blocked while IQS has blocking errors — curate the knowledge in "
                   "Step 2 and regenerate. You can still save a Draft.")


def step_generate() -> None:
    ws = _ws()
    st.subheader("Step 3 · Generate Draft ICP")
    st.caption("Builds a brand-new Draft ICP from the CURRENT Business Knowledge. The draft is a "
               "derived, read-only artifact — regenerate any time after curating more.")

    s = ws.summary()
    r = st.columns(4)
    r[0].metric("Completeness", f"{s['completeness']}/100")
    r[1].metric("Approved facts", s["by_status"].get(bk.CONFIRMED, 0))
    r[2].metric("Unresolved conflicts", len(s["open_conflicts"]))
    r[3].metric("Blocking gaps", len(s["blocking_gaps"]))

    if s["open_conflicts"]:
        st.warning("You still have unresolved conflicts — resolve them in Step 2 for a cleaner ICP.")
    if not s["is_ready_for_icp_generation"]:
        st.info("The gap report considers the knowledge base thin. You can still generate a Draft, "
                "but expect IQS warnings and unknown fields.")

    name = st.text_input("ICP name (optional)")
    notes = st.text_area("Notes for the generator (optional)", height=80)
    if st.button("🧩 Generate Draft ICP", type="primary"):
        with st.spinner("Generating…"):
            client, _ = dg.get_draft_client()
            st.session_state[DRAFT_KEY] = ws.generate_draft(
                client=client, icp_name=name or None, user_notes=notes or None)
        st.rerun()

    res = st.session_state.get(DRAFT_KEY)
    if res is not None:
        _render_draft(res)

    st.divider()
    nav = st.columns([2, 3, 3])
    if nav[0].button("◀ Back", use_container_width=True):
        _goto(2)
    if nav[2].button("↺ Start over", use_container_width=True):
        _reset()


# --- router -------------------------------------------------------------------

st.title("🧭 ICP Workspace")
st.caption("Turn company materials into a reviewed Draft ICP. Business Knowledge is the Source of "
           "Truth — you approve every fact before an ICP is generated from it.")

# A step past Upload is meaningless without a workspace (e.g. after a refresh) — bounce back.
if _step() > 1 and _ws() is None:
    st.session_state[STEP_KEY] = 1

render_stepper(_step())

if _step() == 1:
    step_upload()
elif _step() == 2:
    step_review()
else:
    step_generate()
