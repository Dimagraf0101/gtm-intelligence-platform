"""Search Execution — Streamlit page (Sprint 12).

VIEW ONLY. Turn an **Approved Search Strategy** into leads via the automatic Vayne route: the user
configures LinkedIn Sales Navigator manually (using the recommended filters shown here), pastes the
resulting search URL, and the platform submits it to Vayne. When the scrape finishes, the CSV flows
through the SAME `vayne_adapter` → `lead_import` gate as the manual upload, producing one immutable
Lead Batch. All orchestration lives in `search_execution_service`; this page only forwards and renders.

Async is user-driven: submit, then click **Refresh status** — no background workers. Secrets are never
shown here. The manual CSV upload on the **Lead Import** page remains a supported fallback.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import config                              # noqa: E402
import search_strategy as ss              # noqa: E402
import search_execution as sx             # noqa: E402
import search_execution_service as sxs    # noqa: E402
import lead_batch as lb                    # noqa: E402

st.title("Search Execution")
st.caption("Run an Approved Search Strategy through Vayne: configure Sales Navigator manually, paste "
           "the search URL, and the platform scrapes it into an immutable Lead Batch via the same "
           "importer the manual upload uses. Vayne is one replaceable external lead source.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

port = st.session_state.get(PORT_KEY)
if port is None or not getattr(port, "projects", None):
    st.info("No workspace / hypotheses yet. Create a Market Hypothesis on the **Market Hypotheses** page.")
    st.stop()

labels = {f"{h.name} ({h.status})": h.project_id for h in port.hypotheses}
sel = st.session_state.get(SEL_KEY)
cur = next((l for l, v in labels.items() if v == sel), list(labels)[0])
chosen = st.selectbox("Market Hypothesis", list(labels), index=list(labels).index(cur))
st.session_state[SEL_KEY] = labels[chosen]
hyp = port.get_hypothesis(labels[chosen])

# --- credentials signal (presence only — never the value) --------------------
if not config.VAYNE_API_TOKEN:
    st.warning("**Vayne credentials not configured.** Set `VAYNE_API_TOKEN` in your `.env` (see "
               "`.env.example`) to submit automatic searches. You can still use the manual CSV upload "
               "on the **Lead Import** page.")

# --- choose an Approved Search Strategy --------------------------------------
approved = [s for s in hyp.list_search_strategies() if s.status == ss.STRATEGY_APPROVED]
if not approved:
    st.warning("This hypothesis has no **Approved Search Strategy**. Approve one on the **Search "
               "Strategy** page first — it is the authority for the search filters. The pasted URL is "
               "only evidence of the manually configured search.")
    st.stop()

st.subheader("1 · Approved Search Strategy")
slabels = {f"v{s.version} · {ss.search_strategy_reference(s)[:40]}…": s.strategy_id for s in approved}
spick = st.selectbox("Search Strategy", list(slabels))
strategy = next(s for s in approved if s.strategy_id == slabels[spick])

with st.expander("Recommended Sales Navigator filters (configure these manually in Sales Navigator)",
                 expanded=True):
    f = strategy.sales_nav_filters
    rows = [(k.replace("_", " ").title(), ", ".join(v))
            for k, v in vars(f).items() if v]
    if rows:
        st.dataframe([{"filter": k, "values": v} for k, v in rows],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No structured filters recorded on this strategy.")
    if strategy.exclusions:
        st.caption("Exclusions: " + "; ".join(strategy.exclusions))
    st.caption("The platform does **not** build or interpret the LinkedIn URL — you configure the "
               "search in Sales Navigator, then paste its URL below as evidence.")

# --- submit to Vayne ---------------------------------------------------------
st.subheader("2 · Paste the Sales Navigator search URL")
url = st.text_input("Sales Navigator search URL", key=f"snav_url_{hyp.project_id}",
                    placeholder="https://www.linkedin.com/sales/search/people?...")
requested_by = st.text_input("Requested by", key="exec_requested_by", placeholder="Your name")

# --- lead retrieval (unlimited vs limited) -----------------------------------
st.markdown("**Lead Retrieval**")
mode = st.radio("Lead Retrieval", ["Scrape all available leads", "Scrape a specific number of leads"],
                label_visibility="collapsed", key="exec_retrieval_mode")

lead_limit = None            # provider-independent intent: None = unlimited
limit_error = ""
if mode == "Scrape a specific number of leads":
    presets = [10, 25, 50, 100, 250, 500]
    choice = st.selectbox("Lead count", [str(p) for p in presets] + ["Custom…"], index=3,
                          key="exec_limit_choice")
    if choice == "Custom…":
        custom = st.number_input("Custom lead count", min_value=1, step=1, value=100,
                                 key="exec_limit_custom")
        lead_limit = int(custom)
    else:
        lead_limit = int(choice)
    # deterministic validation from the domain (must be > 0, within a sane bound)
    issues = sx.validate_lead_limit(lead_limit)
    if issues:
        limit_error = "; ".join(issues)
        st.error(limit_error)
    else:
        st.caption(f"Requesting up to **{lead_limit}** leads. If the search yields fewer, that's not an "
                   "error — the imported count will simply be lower.")
else:
    st.caption("No limit — every lead the Sales Navigator search returns will be scraped.")

can_submit = bool(url.strip() and requested_by.strip() and config.VAYNE_API_TOKEN and not limit_error)
if st.button("Submit search", type="primary", disabled=not can_submit, icon=":material/travel_explore:"):
    res = sxs.create_and_submit(hyp, strategy.strategy_id, url.strip(),
                                requested_by=requested_by.strip(), lead_limit=lead_limit)
    if res.ok and res.resumed:
        st.info(f"An active execution for this exact search already exists — resumed "
                f"`{res.execution.execution_id[:12]}` (status **{res.execution.status}**). No duplicate "
                "Vayne order was created. Use **Refresh status** below to advance it.")
    elif res.ok:
        st.success(f"Submitted. Execution `{res.execution.execution_id[:12]}` — "
                   f"job `{res.execution.external_job_id}` — requested **{res.execution.requested_label}**. "
                   "Use **Refresh status** below.")
    else:
        # user-facing only; the service never puts secrets or stack traces in .error
        st.error(res.error + ("  (This looks transient — you can try again.)" if res.transient else ""))
    st.rerun()

# --- executions + refresh ----------------------------------------------------
executions = hyp.list_search_executions()
if not executions:
    st.info("No search executions yet. Submit one above, or use the manual CSV upload on the "
            "**Lead Import** page.")
    st.stop()

st.subheader("3 · Executions")
# actual imported count comes from the derived LeadBatch's stats (never from the requested amount)
_batch_by_id = {b.batch_id: b for b in hyp.list_lead_batches()}


def _imported_label(e):
    b = _batch_by_id.get(e.derived_lead_batch_id) if e.derived_lead_batch_id else None
    return str(b.stats.get("imported", len(b.leads))) if b is not None else "—"


st.dataframe(
    [{"name": e.name or e.execution_id[:12], "requested": e.requested_label,
      "imported": _imported_label(e), "status": e.status,
      "requested_by": e.requested_by, "requested_at": e.requested_at}
     for e in executions],
    use_container_width=True, hide_index=True)

elabels = {f"{e.execution_id[:12]} · {e.status} · {e.requested_at}": i
           for i, e in enumerate(executions)}
epick = st.selectbox("Execution", list(elabels), index=len(elabels) - 1)
execution = executions[elabels[epick]]

cols = st.columns([1, 3])
if cols[0].button("Refresh status", disabled=execution.is_terminal, icon=":material/refresh:"):
    res = sxs.refresh_execution(hyp, execution.execution_id)
    if res.ok and execution.status == sx.EXEC_COMPLETED:
        st.success(f"Completed — imported Lead Batch `{execution.derived_lead_batch_id[:12]}`.")
    elif res.ok:
        st.info(f"Status: **{execution.status}**." + (" Result not ready yet — refresh again."
                if res.transient else ""))
    else:
        st.error(res.error + ("  (Transient — you can refresh again.)" if res.transient else ""))
    st.rerun()
if execution.is_terminal:
    cols[1].caption(f"Terminal: **{execution.status}** (immutable).")

# --- per-execution detail ----------------------------------------------------
st.caption(f"Execution `{execution.execution_id}` · **{execution.name}** · status "
           f"**{execution.status}** · requested **{execution.requested_label}** · imported "
           f"**{_imported_label(execution)}** · derived from "
           f"`{execution.derived_from_search_strategy[:40]}…`")
if execution.failure_reason:
    st.error("Failure: " + execution.failure_reason)

with st.expander("Status history (append-only)"):
    st.dataframe([{"status": ev.get("status"), "at": ev.get("at"), "note": ev.get("note", "")}
                  for ev in execution.events], use_container_width=True, hide_index=True)

# --- on completion: show the resulting Lead Batch + link to Qualification -----
if execution.status == sx.EXEC_COMPLETED and execution.derived_lead_batch_id:
    batch = next((b for b in hyp.list_lead_batches()
                  if b.batch_id == execution.derived_lead_batch_id), None)
    if batch is not None:
        st.subheader("4 · Resulting Lead Batch")
        s = batch.stats
        c = st.columns(5)
        c[0].metric("Leads", s.get("imported", len(batch.leads)))
        c[1].metric("Unique companies", s.get("unique_companies", 0))
        c[2].metric("With LinkedIn", s.get("with_linkedin_url", 0))
        c[3].metric("Duplicates removed", s.get("duplicates_removed", 0))
        c[4].metric("Skipped (no company)", s.get("skipped_missing_company", 0))
        for w in lb.batch_warnings(batch):
            st.caption(":material/warning: " + w)
        st.caption("This batch keeps its authoritative Search Strategy provenance; the execution id is "
                   "recorded additively. Qualify it on the **Qualification** page.")
        try:
            st.page_link("pages/9_Qualification.py", label="Go to Qualification", icon=":material/analytics:")
        except Exception:  # noqa: BLE001 — older Streamlit without page_link
            st.info("Next: open the **Qualification** page to qualify this batch.")

st.divider()
st.caption("Manual fallback: the **Lead Import** page still accepts a Vayne CSV exported from a manual "
           "Sales Navigator search — both routes use the same importer and produce identical Lead "
           "Batches.")
