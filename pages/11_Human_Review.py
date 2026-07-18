"""Human Review — Streamlit page (Sprint 13b).

The primary review workbench: inspect the AI's proposal for each qualified lead and record the human
verdict. VIEW + DECISION ONLY — all projection/filtering/statistics live in `review_view`, all decision
recording in `lead_review` (append-only), all serialization in `review_export`. This page forwards and
renders; it never assembles domain data itself, never edits AI or business fields, and never re-runs
qualification or touches priority.

AI proposes → Python validates → Human approves.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import lead_review as lr                   # noqa: E402
import review_view as rv                   # noqa: E402
import review_export as rx                 # noqa: E402
from integrations import google_sheets_publisher as gs   # noqa: E402

st.set_page_config(page_title="Human Review", page_icon="✅", layout="wide")
st.title("✅ Human Review")
st.caption("Review the AI's proposal for each qualified lead and record your decision. The AI score, "
           "priority and evidence are read-only — your verdict is stored separately and never edits "
           "the lead or the qualification result.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

port = st.session_state.get(PORT_KEY)
if port is None or not getattr(port, "projects", None):
    st.info("No workspace / hypotheses yet. Create a Market Hypothesis first.")
    st.stop()

# --- 1 · context / lineage ---------------------------------------------------
labels = {f"{h.name} ({h.status})": h.project_id for h in port.hypotheses}
sel = st.session_state.get(SEL_KEY)
cur = next((l for l, v in labels.items() if v == sel), list(labels)[0])
chosen = st.selectbox("Market Hypothesis", list(labels), index=list(labels).index(cur))
st.session_state[SEL_KEY] = labels[chosen]
hyp = port.get_hypothesis(labels[chosen])

batches = hyp.list_qualified_batches()
if not batches:
    st.info("No Qualified Lead Batches yet. Qualify a Lead Batch on the **Qualification** page first.")
    st.stop()

blabels = {f"{q.batch_id[:12]} · {q.qualified_at} ({q.stats.get('total', 0)} leads)": i
           for i, q in enumerate(batches)}
pick = st.selectbox("Qualified Lead Batch", list(blabels), index=len(blabels) - 1)
qbatch = batches[blabels[pick]]
st.caption(f"Lineage: LeadBatch `{qbatch.derived_from_lead_batch[:12]}` · ICP "
           f"`{qbatch.derived_from_adapted_icp[:26]}…` · qualified by **{qbatch.qualified_by}**")

rows = rv.build_review_rows(hyp, qbatch)
if not rows:
    st.warning("This qualified batch has no leads to review.")
    st.stop()

reviewer = st.text_input("Reviewer", key="review_reviewer", placeholder="Your name")

# --- 2 · summary metrics -----------------------------------------------------
review = hyp.review_for_qualified_batch(qbatch.batch_id)
stats = lr.review_statistics(len(rows), review)
m = st.columns(6)
m[0].metric("Total", stats["total"])
m[1].metric("Pending", stats["pending"])
m[2].metric("Approved", stats["approved"])
m[3].metric("Rejected", stats["rejected"])
m[4].metric("Skipped", stats["skipped"])
m[5].metric("Reviewed", f"{stats['progress_pct']}%")
st.progress(stats["progress_pct"] / 100)
if stats["remaining"] == 0:
    st.success("🎉 Every lead in this batch has been reviewed — export the approved ones below.")
elif stats["reviewed"] == 0:
    st.info("No decisions yet. Approve, reject, or skip leads below — the AI proposal is read-only.")
dist = rv.priority_distribution(rows)
st.caption("Priority distribution — " + " · ".join(f"**{k}**: {v}" for k, v in dist.items() if v))

# --- 3 · search + filters ----------------------------------------------------
with st.expander("Search & filters", expanded=True):
    f1 = st.columns(4)
    search = f1[0].text_input("Search (company / contact)", key="review_search")
    statuses = f1[1].multiselect("Review status", list(lr.REVIEW_STATUSES), default=[])
    priorities = f1[2].multiselect("Priority", rv.PRIORITY_ORDER, default=[])
    score_range = f1[3].slider("Score range", 0, 100, (0, 100))
    f2 = st.columns(3)
    industries = f2[0].multiselect("Industry", sorted({r.industry for r in rows if r.industry}))
    sizes = f2[1].multiselect("Company size", sorted({r.company_size for r in rows if r.company_size}))
    geos = f2[2].multiselect("Location", sorted({r.location for r in rows if r.location}))

visible = rv.sort_rows(rv.filter_rows(
    rows, statuses=statuses or None, priorities=priorities or None,
    score_min=score_range[0], score_max=score_range[1],
    industries=industries or None, company_sizes=sizes or None,
    geographies=geos or None, search=search))

st.caption(f"Showing **{len(visible)}** of {len(rows)} leads "
           "(sorted by priority, then score descending).")
if not visible:
    st.info("No leads match the current filters.")
    st.stop()

# --- 4 · review table --------------------------------------------------------
table = [{
    "Review Status": r.review_status, "Company": r.company, "Contact": r.contact,
    "Title": r.title, "Priority": r.priority, "Lead Score": r.score,
    "Industry": r.industry, "Location": r.location, "Company Size": r.company_size,
    "Person LinkedIn": r.linkedin_url, "Company Website": r.company_website,
} for r in visible]

event = st.dataframe(
    table, use_container_width=True, hide_index=True,
    on_select="rerun", selection_mode="multi-row",
    column_config={
        "Person LinkedIn": st.column_config.LinkColumn("Person LinkedIn", display_text="profile"),
        "Company Website": st.column_config.LinkColumn("Company Website", display_text="site"),
        "Lead Score": st.column_config.NumberColumn("Lead Score", format="%d"),
    })
selected_idx = list(getattr(getattr(event, "selection", None), "rows", []) or [])
selected = [visible[i] for i in selected_idx if i < len(visible)]

# --- 5 · bulk actions --------------------------------------------------------
st.subheader("Bulk actions")
scope_label = (f"{len(selected)} selected row(s)" if selected
               else f"all {len(visible)} filtered row(s)")
st.caption(f"Bulk actions apply to **{scope_label}**. Only these leads are affected.")
targets = selected if selected else visible
confirm = True
if not selected:
    confirm = st.checkbox(f"Confirm: apply to all {len(visible)} filtered leads", key="review_bulk_confirm")

bulk_reason = st.text_input("Shared rejection reason (used by Bulk Reject)", key="review_bulk_reason")
bc = st.columns(3)
can_bulk = bool(reviewer.strip() and confirm and targets)


def _apply_bulk(status, reason=""):
    n = lr.record_bulk(hyp, qbatch.batch_id, [r.lead_id for r in targets], status,
                       rejection_reason=reason, decided_by=reviewer.strip())
    st.success(f"{status} {n} lead(s).")
    st.rerun()


if bc[0].button(f"✅ Bulk Approve ({len(targets)})", disabled=not can_bulk, type="primary"):
    _apply_bulk(lr.REVIEW_APPROVED)
if bc[1].button(f"⛔ Bulk Reject ({len(targets)})", disabled=not can_bulk):
    _apply_bulk(lr.REVIEW_REJECTED, bulk_reason.strip())
if bc[2].button(f"⏭️ Bulk Skip ({len(targets)})", disabled=not can_bulk):
    _apply_bulk(lr.REVIEW_SKIPPED)
if not reviewer.strip():
    st.caption("Enter a **Reviewer** name to record decisions.")

# --- 6 · lead details + individual decision ---------------------------------
st.subheader("Lead details")
# the row ordinal keeps every label unique — two leads with the same company/contact/priority/status
# must both stay selectable (otherwise a lead becomes unreachable and cannot be reviewed).
dlabels = {f"{i + 1}. {r.company or '—'} · {r.contact or '—'} · {r.priority} ({r.review_status})": i
           for i, r in enumerate(visible)}
dpick = st.selectbox("Lead", list(dlabels), index=(selected_idx[0] if selected_idx else 0))
row = visible[dlabels[dpick]]

d1, d2 = st.columns(2)
with d1:
    st.markdown("**Business identity**")
    st.write({"Company": row.company, "Contact": row.contact, "Title": row.title,
              "Location": row.location})
    links = []
    if row.linkedin_url:
        links.append(f"[Person LinkedIn]({row.linkedin_url})")
    if row.company_linkedin_url:
        links.append(f"[Company LinkedIn]({row.company_linkedin_url})")
    if row.company_website:
        links.append(f"[Website]({row.company_website})")
    if links:
        st.markdown(" · ".join(links))
    st.markdown("**Business attributes**")
    st.write({"Connections": row.connections or "—", "Job started": row.job_started or "—",
              "Employees": row.employee_count or "—", "Founded": row.founded_year or "—",
              "Specialities": row.specialities or "—", "Company size": row.company_size or "—"})
with d2:
    st.markdown("**AI proposal** (read-only)")
    st.write({"Priority": row.priority, "Lead Score": row.score, "Confidence": row.confidence})
    st.caption("Score reason: " + (row.reason or "— none recorded by the engine"))
    st.caption("ICP signals: " + ("; ".join(row.signals) if row.signals
                                  else "— no evidence recorded (unknown stays unknown)"))
    if row.score_breakdown:
        st.caption("Score breakdown: " + row.score_breakdown)
    for w in row.warnings:
        st.caption("⚠️ " + w)
    if row.is_mock:
        st.caption("⚠️ Mock/offline result — set `ANTHROPIC_API_KEY` for real scoring.")
    with st.expander("Audit details"):
        st.write({"Internal category (audit only)": row.internal_category or "—",
                  "Raw ICP score": row.raw_icp_score or "—",
                  "Operational lead score": row.operational_lead_score or "—",
                  "Data coverage": row.evidence_coverage or "—",
                  "Decision confidence": row.decision_confidence or "—",
                  "Dealbreaker state": row.dealbreaker_state or "—",
                  "Source qualified batch": qbatch.batch_id})

st.markdown("**Your decision**")
dc = st.columns([2, 2, 1, 1, 1])
# the widget key includes the lead's last decision timestamp, so after a new decision (or a workspace
# reload) the inputs re-seed from the PERSISTED values instead of showing stale typed text.
_wkey = f"{row.lead_id}_{row.decided_at}"
reason = dc[0].text_input("Rejection reason", key=f"reason_{_wkey}",
                          value=row.rejection_reason)
comment = dc[1].text_input("Reviewer comment", key=f"comment_{_wkey}",
                           value=row.reviewer_comment)
can_decide = bool(reviewer.strip())


def _decide(status):
    lr.record_decision(hyp, qbatch.batch_id, row.lead_id, status,
                       rejection_reason=reason.strip(), reviewer_comment=comment.strip(),
                       decided_by=reviewer.strip())
    st.rerun()


if dc[2].button("✅ Approve", disabled=not can_decide, key="d_appr"):
    _decide(lr.REVIEW_APPROVED)
if dc[3].button("⛔ Reject", disabled=not can_decide, key="d_rej"):
    _decide(lr.REVIEW_REJECTED)
if dc[4].button("⏭️ Skip", disabled=not can_decide, key="d_skip"):
    _decide(lr.REVIEW_SKIPPED)

if review is not None:
    history = review.history_for(row.lead_id)
    if history:
        with st.expander(f"Decision history ({len(history)} entry/entries — append-only)"):
            st.dataframe([{"status": h.review_status, "at": h.decided_at, "by": h.decided_by,
                           "reason": h.rejection_reason, "comment": h.reviewer_comment}
                          for h in history], use_container_width=True, hide_index=True)

# --- 7 · export --------------------------------------------------------------
st.divider()
st.subheader("Export")
scope_choice = st.radio(
    "Scope",
    [f"Approved only ({stats['approved']})", f"Selected ({len(selected)})", f"All ({len(rows)})"],
    horizontal=True, key="review_export_scope")
scope = (rx.SCOPE_APPROVED if scope_choice.startswith("Approved")
         else rx.SCOPE_SELECTED if scope_choice.startswith("Selected") else rx.SCOPE_ALL)
export_rows = rx.select_rows(rows, scope, selected_ids=[r.lead_id for r in selected])
if scope == rx.SCOPE_SELECTED and not selected:
    st.warning("No rows are selected. Tick rows in the review table above, or choose another scope. "
               "(Selection resets when you change a filter.)")
st.caption(f"**{len(export_rows)}** lead(s) will be exported using the canonical schema "
           "(unchanged columns and order).")

if export_rows:
    e1, e2 = st.columns(2)
    e1.download_button(
        "⬇️ Download XLSX", data=rx.to_workbook_bytes(export_rows, icp_name=hyp.name or "ICP"),
        file_name=f"reviewed_leads_{qbatch.batch_id[:8]}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    e2.download_button(
        "⬇️ Download CSV", data=rx.to_main_csv_bytes(export_rows),
        file_name=f"reviewed_leads_{qbatch.batch_id[:8]}.csv", mime="text/csv")
else:
    st.info("Nothing to export for this scope yet.")

# --- 7b · publish to Google Sheets (external — always explicitly confirmed) --------------------
st.markdown("**Publish to Google Sheets**")
creds_ok = gs.credentials_available()
if not creds_ok:
    st.caption("⚠️ Google Sheets credentials are not configured. Set "
               "`GOOGLE_SHEETS_CREDENTIALS_FILE` or `GOOGLE_SHEETS_CREDENTIALS_JSON` (see "
               "`.env.example`) to enable publishing.")

pmode_label = st.radio("Publish mode", ["Create new spreadsheet", "Update existing spreadsheet"],
                       horizontal=True, key="gs_mode")
if pmode_label.startswith("Create"):
    gs_title = st.text_input("New spreadsheet name", key="gs_title",
                             value=f"Reviewed leads — {hyp.name or 'GTM'}")
    target = gs.PublishTarget(mode=gs.MODE_CREATE, title=gs_title)
else:
    gs_target = st.text_input("Existing spreadsheet ID or URL", key="gs_target",
                              placeholder="https://docs.google.com/spreadsheets/d/…")
    target = gs.PublishTarget(mode=gs.MODE_UPDATE, spreadsheet_id=gs_target)
    if gs_target.strip() and not gs.extract_spreadsheet_id(gs_target):
        st.error("That is not a recognizable Google Sheets ID or URL.")

gs_main = rx.build_main_rows(export_rows)
gs_ai = rx.build_ai_rows(export_rows)
gs_summary = rx.build_summary_rows(export_rows, hyp.name or "GTM", hyp.name or "ICP",
                                   qbatch.qualified_at)
st.caption(f"Will publish **{len(gs_main)}** lead(s) to *Leads*, *AI Details* and *Summary* "
           "(managed worksheets are fully replaced; other worksheets are left untouched).")

confirmed = st.checkbox("I confirm publishing this data to Google Sheets", key="gs_confirm")
blocking = gs.validate_publish(gs_main, gs_ai, target, confirmed=confirmed,
                               credentials_available=creds_ok)
if st.button("📤 Publish to Google Sheets", disabled=bool(blocking), type="primary"):
    try:
        result = gs.GoogleSheetsPublisher().publish_workbook(
            gs_main, gs_ai, gs_summary, target, confirmed=confirmed)
        st.success(f"Published {result.row_counts.get('Leads', 0)} lead(s) "
                   f"({result.publish_mode}). [Open the spreadsheet]({result.spreadsheet_url})")
        st.caption(f"Worksheets updated: {', '.join(result.worksheets_updated)} · "
                   f"published at {result.published_at}")
        for w in result.warnings:
            st.caption("⚠️ " + w)
    except gs.GoogleSheetsError as exc:
        # user-safe message only — never a secret or a raw stack trace
        st.error(str(exc) + ("  (Transient — you can retry.)" if exc.transient else ""))
elif blocking:
    st.caption("Blocked: " + "; ".join(blocking))

# --- 8 · persist review decisions (explicit, deterministic — no hidden autosave) ---------------
st.divider()
st.subheader("Save review decisions")
if stats["reviewed"]:
    st.warning(f"**{stats['reviewed']} decision(s) are held in this session only.** Save the workspace "
               "to keep them — closing the browser or restarting the app discards unsaved review work.")
else:
    st.caption("No decisions recorded yet in this session.")
default_path = st.session_state.get("ws_path", str(Path.home() / "gtm_workspace.json"))
ws_path = st.text_input("Workspace file path", value=default_path, key="review_ws_path")
if st.button("💾 Save workspace", type="primary", disabled=not ws_path.strip()):
    try:
        import workspace_store as store           # local import: page-level persistence only
        saved = store.save_workspace(port, ws_path.strip())
        st.session_state["ws_path"] = str(saved)
        st.success(f"Saved {stats['reviewed']} review decision(s) and the full workspace to {saved}.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Save failed: {exc}")
st.caption("Reload is available on the **General ICP** page; reloading restores every review decision, "
           "comment, timestamp and the append-only decision history.")
