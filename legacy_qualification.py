"""Sales Pipeline — local MVP.

Workflow: upload an ICP PDF, upload a Vayne CSV, click Start, review the qualified
leads, download the result (XLSX/CSV, ready to import into Google Sheets).

Run:  ./.venv/bin/streamlit run app.py
"""
import csv
import io
import logging
import sys
from pathlib import Path

import streamlit as st

# The pipeline modules import each other by bare module name (from config import ...),
# so the pipeline/ directory must be importable.
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

from icp_pdf import extract_icp_from_bytes            # noqa: E402
from scoring import normalize_lead, get_client        # noqa: E402
import export                                         # noqa: E402
import qualification_bridge as qb                      # noqa: E402
import icp_approval                                    # noqa: E402



# --- capture engine logs so the user can see progress / errors ---------------
@st.cache_resource
def _log_buffer():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    root = logging.getLogger("qualification")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return buf


log_buf = _log_buffer()


def read_csv_rows(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


# --- header ------------------------------------------------------------------
st.title("Lead Qualification")
st.caption("Upload an ICP and a Vayne CSV, qualify every lead, review, and export.")

_, is_live = get_client()
if not is_live:
    st.warning(
        "**Offline mode** — `ANTHROPIC_API_KEY` is not set, so leads are scored by a "
        "local placeholder engine (not Claude). Add the key to `.env` and restart to "
        "score with the real model. The workflow below is otherwise identical."
    )

# --- Step 1: ICP source ------------------------------------------------------
# Exactly one ICP source per run — the two modes are mutually exclusive and never merged.
st.subheader("Step 1 · ICP source")
source_mode = st.radio("ICP source", ["Upload ICP document", "Use Approved ICP"],
                       horizontal=True, label_visibility="collapsed")

icp_file = None
selected_project = None
approved_ready = False
if source_mode == "Upload ICP document":
    icp_file = st.file_uploader("ICP definition (PDF)", type=["pdf"], label_visibility="collapsed")
    icp_ready = icp_file is not None
else:
    port = st.session_state.get("icp_portfolio")
    if not port or not getattr(port, "projects", None):
        st.info("No ICP projects yet. Build and approve one in the ICP Workspace pages "
                "(Knowledge Review → Knowledge Interview → Strategy Review → Approval).")
        icp_ready = False
    else:
        labels = {p.name: p.project_id for p in port.projects}
        sel = st.session_state.get("selected_project_id")
        cur = next((l for l, v in labels.items() if v == sel), list(labels)[0])
        chosen = st.selectbox("ICP Project", list(labels), index=list(labels).index(cur))
        st.session_state["selected_project_id"] = labels[chosen]
        selected_project = port.get_project(labels[chosen])
        active = icp_approval.get_active_approved_icp(selected_project)
        if active is None:
            st.warning("This project has no active Approved ICP. Approve one on the **Approval** page.")
            icp_ready = False
        else:
            rec = next((r for r in reversed(selected_project.approval_records)
                        if r.icp_version == active.metadata.version), None)
            st.success(f"Active Approved ICP: **{active.metadata.name}** — version "
                       f"{active.metadata.version}")
            st.caption(f"Fingerprint `{icp_approval.fingerprint_generated_icp(active)[:16]}…` · "
                       f"approved {rec.approved_at if rec else '—'} by {rec.approved_by if rec else '—'}")
            approved_ready = True
            icp_ready = True

# --- Step 2: leads -----------------------------------------------------------
st.subheader("Step 2 · Upload leads (Vayne CSV)")
csv_file = st.file_uploader("Vayne CSV export", type=["csv"], label_visibility="collapsed")

max_leads = st.number_input(
    "Max leads to score (safety cap so you never process a whole file by accident)",
    min_value=1, max_value=5000, value=25, step=5,
)

# --- Step 3: run -------------------------------------------------------------
st.subheader("Step 3 · Qualify")
start = st.button("Start Qualification", type="primary", icon=":material/play_arrow:",
                  disabled=not (icp_ready and csv_file))
if not (icp_ready and csv_file):
    st.info("Provide one ICP source and a Vayne CSV to enable qualification.")

if start:
    log_buf.truncate(0)
    log_buf.seek(0)
    try:
        # Build exactly one ICP context for this run (the source of truth for the run).
        if source_mode == "Upload ICP document":
            icp = extract_icp_from_bytes(icp_file.getvalue(), Path(icp_file.name).stem)
            if icp.is_empty():
                st.error("Could not extract any text from that PDF (is it a scanned image?).")
                st.stop()
            st.success(f"ICP **{icp.name}** — {icp.n_pages} page(s), "
                       f"{len(icp.text):,} characters extracted.")
            ctx = qb.context_from_uploaded(icp.name, icp.text)
        else:
            ctx = qb.context_from_approved_project(selected_project)   # raises BridgeError if invalid
            st.success(f"Using Approved ICP **{ctx.name}** — version {ctx.source_version}.")

        rows = read_csv_rows(csv_file.getvalue())
        if not rows:
            st.error("The CSV has no data rows.")
            st.stop()
        rows = rows[: int(max_leads)]
        leads = [normalize_lead(r, i) for i, r in enumerate(rows)]
        st.write(f"Scoring **{len(leads)}** lead(s) against ICP **{ctx.name}**…")

        bar = st.progress(0.0, text="Starting…")

        def on_progress(done: int, total: int) -> None:
            bar.progress(done / total, text=f"Scored {done}/{total} leads")

        results = qb.score_with_context(leads, ctx, progress_cb=on_progress)
        bar.progress(1.0, text="Done")

        # Re-pair leads with results (results are sorted best-first).
        by_index = {lead.index: lead for lead in leads}
        pairs = [(by_index[r.lead_index], r) for r in results if r.lead_index in by_index]
        main_df = export.build_main_dataframe(pairs)

        st.session_state["main_df"] = main_df
        st.session_state["workbook_bytes"] = export.to_workbook_bytes(pairs, ctx.name)
        st.session_state["main_csv"] = export.to_main_csv_bytes(main_df)
        st.session_state["icp_name"] = ctx.name
        st.session_state["icp_source_label"] = ctx.source_label
        st.session_state["icp_source_version"] = ctx.source_version
        st.session_state["icp_source_fingerprint"] = ctx.source_fingerprint
        st.session_state["n_mock"] = sum(1 for _, r in pairs if getattr(r, "is_mock", False))
        n_err = sum(1 for _, r in pairs if r.error)
        if n_err:
            st.warning(f"{n_err} lead(s) could not be scored. See the run log.")
    except qb.BridgeError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Qualification failed: {type(exc).__name__}: {exc}")

    with st.expander("Run log"):
        st.code(log_buf.getvalue() or "(no log output)", language="text")

# --- Results -----------------------------------------------------------------
if "main_df" in st.session_state:
    main_df = st.session_state["main_df"]
    icp_name = st.session_state.get("icp_name", "icp")
    st.subheader("Qualified leads")

    src_label = st.session_state.get("icp_source_label")
    if src_label:
        src_version = st.session_state.get("icp_source_version")
        src_fp = st.session_state.get("icp_source_fingerprint")
        line = f"**ICP source:** {src_label} — **{icp_name}**"
        if src_version:
            line += f" · version {src_version}"
        if src_fp:
            line += f" · fingerprint `{src_fp[:16]}…`"
        st.caption(line)

    if st.session_state.get("n_mock"):
        st.info(f"{st.session_state['n_mock']} row(s) are **MOCK/OFFLINE** placeholder results "
                "(see the 'Mock Result' column on the AI Details sheet) — not real model output. "
                "Human approval is required before any outreach.")

    counts = main_df["Priority"].value_counts()
    labels = list(counts.index)[:6]
    if labels:
        cols = st.columns(len(labels))
        for col, label in zip(cols, labels):
            col.metric(str(label), int(counts[label]))

    st.dataframe(main_df, use_container_width=True, hide_index=True)

    st.download_button(
        "Download workbook (XLSX)", icon=":material/download:",
        data=st.session_state["workbook_bytes"], file_name=f"{icp_name}-scored.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("Scored Leads CSV", icon=":material/download:", data=st.session_state["main_csv"],
                       file_name=f"{icp_name}-scored.csv", mime="text/csv")
    st.caption("To act on a lead: filter **Human Decision = Approved** on the main sheet and copy "
               "LinkedIn URLs into Linked Helper manually. Human approval is required before outreach.")
