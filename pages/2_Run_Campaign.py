"""Run Campaign — guided 3-step wizard (Sprint 5.3).

Puts the pipeline together into one flow:

    ① Select an ICP (from the library)  →  ② Get leads (scrape with Vayne, or upload a CSV)
      →  ③ Score against the ICP, review, and export (report + threshold-filtered CSV)

VIEW ONLY. Logic lives in pipeline/{icp_library,vayne,campaign,scoring,export}.py. Human Review Gate
preserved: you review the ranked leads before downloading; nothing is ever auto-sent. Scraping is
data acquisition only and is gated behind an explicit credit confirmation.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import csv
import io
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import config                    # noqa: E402
import icp_library as lib        # noqa: E402
import iqs_validator as iqs      # noqa: E402
import vayne                     # noqa: E402
import campaign                  # noqa: E402
import export                    # noqa: E402
import search_criteria as sc     # noqa: E402

st.set_page_config(page_title="Run Campaign", page_icon="🚀", layout="wide")

STEP_KEY = "camp_step"
ICP_KEY = "camp_icp"             # {id, name, text, source, targets}
ROWS_KEY = "camp_rows"          # list[dict] of raw lead rows
SRC_KEY = "camp_rows_source"    # "vayne" | "vayne-mock" | "csv"
PAIRS_KEY = "camp_pairs"        # list[(Lead, ScoringResult)]
CHECK_KEY = "camp_url_check"    # url_checks result
CRIT_KEY = "camp_criteria"      # {icp_id, crit: SalesNavCriteria}

STEPS = [
    ("Select ICP", "Choose which ICP to qualify against"),
    ("Get leads", "Scrape with Vayne or upload a CSV"),
    ("Score & export", "Qualify, review, and download"),
]

_TARGET_LABELS = {
    "industries": "Industries", "subsegments": "Subsegments", "geographies": "Geographies",
    "company_types": "Company types", "preferred_sizes": "Preferred size",
    "acceptable_sizes": "Acceptable size", "primary_roles": "Primary buyer roles",
    "secondary_roles": "Secondary roles", "excluded_roles": "Excluded roles",
}


# --- helpers -----------------------------------------------------------------

def _step() -> int:
    return int(st.session_state.get(STEP_KEY, 1))


def _goto(step: int) -> None:
    st.session_state[STEP_KEY] = step
    st.rerun()


def _reset() -> None:
    for k in (ICP_KEY, ROWS_KEY, SRC_KEY, PAIRS_KEY, CHECK_KEY, CRIT_KEY):
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


def _render_criteria(crit) -> None:
    """Render the suggested Sales Navigator filters panel."""
    rows = [
        ("🌍 Geography", crit.geographies),
        ("🏭 Industry — pick the closest in Sales Nav", crit.industries),
        ("👥 Company headcount", crit.headcount_buckets),
        ("🎯 Seniority level", crit.seniority_levels),
        ("👤 Job titles", crit.title_keywords),
        ("🚫 Exclude titles", crit.excluded_titles),
    ]
    with st.container(border=True):
        shown = False
        for label, vals in rows:
            if vals:
                shown = True
                st.markdown(f"- **{label}:** {', '.join(vals)}")
        if not shown:
            st.caption("This ICP has no structured targets — build the search manually.")
        if crit.title_boolean:
            st.markdown("**Title keyword string** _(paste into Sales Nav Keywords / Title)_:")
            st.code(crit.title_boolean, language=None)
        if crit.keyword_boolean:
            st.markdown("**Extra keywords:**")
            st.code(crit.keyword_boolean, language=None)
        for n in crit.notes:
            st.caption("· " + n)
        if getattr(crit, "source", "") == "ai":
            st.caption("✨ Refined with AI.")


# --- Step 1: select ICP ------------------------------------------------------

def _approval_panel(entry) -> None:
    """IQS-gated human approval for a stored generated Draft ICP. The natural place to approve is
    the ICP Workspace right after generation; this panel covers ICPs saved as drafts."""
    st.warning(f"This generated ICP is **{entry.status or 'Draft'}** — human approval is required "
               "before an ICP may qualify leads (IQS v1.0 §10).")
    try:
        icp_obj = lib.load_generated(entry.id)
    except KeyError:
        st.error("The stored ICP JSON is missing — regenerate this ICP in the **ICP Workspace**.")
        return
    report = iqs.validate(icp_obj)
    if not report.is_valid:
        st.error("IQS blocking — cannot approve: " + "; ".join(report.blocking_errors))
        st.caption("Fix the underlying knowledge in the **ICP Workspace** and regenerate the draft.")
        return
    ack = True
    if report.warnings:
        with st.expander(f"IQS warnings to acknowledge ({len(report.warnings)})", expanded=True):
            for w in report.warnings:
                st.write("• " + w)
        ack = st.checkbox("I have read and acknowledge these warnings.", key=f"ack::{entry.id}")
    if st.button("✅ Approve this ICP for qualification", type="primary", disabled=not ack,
                 key=f"approve::{entry.id}"):
        lib.approve_entry(entry.id, acknowledge_warnings=True)
        st.session_state.pop(ICP_KEY, None)          # force reload with the new status
        st.rerun()


def step_select_icp() -> None:
    st.subheader("Step 1 · Select an ICP")
    st.caption("Pick an ICP from your library. Generate one in the **ICP Workspace** and click "
               "'Save to ICP library', or import an existing ICP PDF below.")

    ready = False
    entries = lib.list_entries()
    if entries:
        labels = {f"{e.name}  ·  {e.source}  ·  {e.status or '—'}  ·  {e.created_at[:10]}  "
                  f"[{e.id[:8]}]": e for e in entries}
        pick = st.selectbox("ICP library", list(labels))
        entry = labels[pick]

        sel = st.session_state.get(ICP_KEY)
        if sel is None or sel["id"] != entry.id:
            e2, text = lib.load_text(entry.id)
            st.session_state[ICP_KEY] = {"id": e2.id, "name": e2.name, "text": text,
                                         "source": e2.source, "status": e2.status,
                                         "targets": e2.targets}
            st.session_state.pop(CHECK_KEY, None)

        summary = entry.target_summary()
        with st.container(border=True):
            st.markdown(f"**{entry.name}** · _{entry.source}_ · {entry.status or '—'} · "
                        f"{entry.n_dimensions} dimension(s)")
            if summary:
                for key, label in _TARGET_LABELS.items():
                    if summary.get(key):
                        st.markdown(f"- **{label}:** {', '.join(summary[key])}")
            else:
                st.caption("No structured target attributes stored (imported PDF) — you'll build the "
                           "Vayne search manually in step 2.")

        ready = lib.is_ready_for_qualification(entry)
        if entry.source == lib.SOURCE_GENERATED:
            if ready:
                st.caption("✅ Approved — scored through the structured Generated-ICP → Engine "
                           "bridge (dimensions, thresholds, and exclusions from the ICP itself).")
            else:
                _approval_panel(entry)
        else:
            st.caption("Imported PDF — scored via the backward-compatible ICP-text path.")

        if st.button("🗑️ Delete this ICP from the library"):
            lib.delete(entry.id)
            if (st.session_state.get(ICP_KEY) or {}).get("id") == entry.id:
                st.session_state.pop(ICP_KEY, None)
            st.rerun()
    else:
        st.info("Your ICP library is empty. Generate an ICP in the **ICP Workspace** and save it, "
                "or import an ICP PDF below.")

    with st.expander("📄 Import an ICP PDF into the library"):
        up = st.file_uploader("ICP PDF", type=["pdf"], key="icp_pdf_up")
        nm = st.text_input("Name (optional — defaults to the file name)", key="icp_pdf_name")
        if st.button("Import PDF", disabled=up is None):
            try:
                e = lib.import_pdf(nm or up.name, up.getvalue())
                st.success(f"Imported '{e.name}' into the library.")
                st.rerun()
            except ValueError as ex:
                st.error(str(ex))

    st.divider()
    nav = st.columns([3, 5])
    if not ready and ICP_KEY in st.session_state and entries:
        nav[1].caption("Approval required above before this ICP can qualify leads.")
    if nav[0].button("Continue to leads  ▶", type="primary", disabled=not ready,
                     use_container_width=True):
        sel = st.session_state.get(ICP_KEY) or {}
        try:
            loaded = campaign.load_icp_for_scoring(sel["id"])
            sel.update(name=loaded["name"], text=loaded["text"], profile=loaded["profile"])
            st.session_state[ICP_KEY] = sel
            _goto(2)
        except campaign.CampaignError as exc:
            st.error(str(exc))


# --- Step 2: get leads -------------------------------------------------------

def _scrape_tab(icp: dict) -> None:
    client, is_live = vayne.get_vayne_client()
    if not is_live:
        st.warning("**Offline mode** — `VAYNE_API_TOKEN` not set. Scraping uses the deterministic "
                   "mock (placeholder leads, clearly marked). Add the token and restart to scrape "
                   "live.")

    st.markdown("#### Suggested Sales Navigator filters")
    st.caption("Derived from the selected ICP. Apply these in Sales Navigator, then paste the "
               "resulting search URL below.")
    crit_state = st.session_state.get(CRIT_KEY)
    if crit_state is None or crit_state.get("icp_id") != icp["id"]:
        crit_state = {"icp_id": icp["id"],
                      "crit": sc.suggest_from_targets(icp["targets"], icp["text"])}
        st.session_state[CRIT_KEY] = crit_state
    crit = crit_state["crit"]
    _render_criteria(crit)

    rc = st.columns([2, 5])
    if rc[0].button("✨ Refine with AI", use_container_width=True):
        with st.spinner("Refining with the model…"):
            cclient, _ = sc.get_criteria_client()
            crit_state["crit"] = sc.refine_with_ai(crit, icp["text"], client=cclient)
            st.session_state[CRIT_KEY] = crit_state
        st.rerun()
    if not os.getenv("ANTHROPIC_API_KEY"):
        rc[1].caption("Offline — showing the deterministic suggestion. Set `ANTHROPIC_API_KEY` to map "
                      "industries to Sales Navigator's taxonomy and expand title synonyms.")
    with st.expander("Copy / edit as text"):
        st.text_area("Search criteria", value=sc.to_markdown(crit), height=150,
                     key=f"crit_text_{icp['id']}")

    url = st.text_input("Sales Navigator search URL", key="camp_url",
                        placeholder="https://www.linkedin.com/sales/search/people?...")

    cols = st.columns([2, 3])
    if cols[0].button("Check URL", disabled=not url, use_container_width=True):
        try:
            with st.spinner("Checking…"):
                st.session_state[CHECK_KEY] = client.check_url(url)
        except Exception as exc:  # noqa: BLE001
            st.session_state.pop(CHECK_KEY, None)
            st.error(f"URL check failed: {exc}")
    check = st.session_state.get(CHECK_KEY)
    if check:
        cols[1].success(f"Found {check.get('total', '?')} {check.get('type', 'prospects')}"
                        + ("  ·  [MOCK]" if check.get("mock") else ""))

    available = int(check.get("total", 0)) if check else 0
    cap = min(vayne.MAX_LEADS_CAP, available) if available else vayne.MAX_LEADS_CAP
    default = min(vayne.DEFAULT_LIMIT, cap) if cap else vayne.DEFAULT_LIMIT
    limit = st.number_input("Leads to scrape", min_value=1, max_value=max(1, cap),
                            value=max(1, default), step=10)

    confirm = True
    if is_live:
        confirm = st.checkbox("I understand this will use Vayne credits.")
    if st.button(f"🔎 Scrape {int(limit)} lead(s)", type="primary",
                 disabled=not (url and confirm)):
        box = st.status("Scraping with Vayne…", expanded=True)
        try:
            rows = client.scrape(url, limit=int(limit), name=icp["name"],
                                 progress=lambda m: box.write(m))
            box.update(label=f"Scraped {len(rows)} lead(s)", state="complete")
            st.session_state[ROWS_KEY] = rows
            st.session_state[SRC_KEY] = "vayne-mock" if client.is_mock else "vayne"
            st.session_state.pop(PAIRS_KEY, None)
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            box.update(label="Scrape failed", state="error")
            st.error(f"Scrape failed: {exc}")


def _csv_tab() -> None:
    up = st.file_uploader("Lead CSV (Vayne export or compatible)", type=["csv"], key="camp_csv")
    if up is not None and st.button("Load CSV", type="primary"):
        text = up.getvalue().decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text)))
        rows = vayne.deduplicate(rows)
        if not rows:
            st.error("No rows found in that CSV.")
            return
        st.session_state[ROWS_KEY] = rows
        st.session_state[SRC_KEY] = "csv"
        st.session_state.pop(PAIRS_KEY, None)
        st.rerun()


def step_get_leads() -> None:
    icp = st.session_state[ICP_KEY]
    st.subheader("Step 2 · Get leads")
    st.caption(f"Qualifying against **{icp['name']}**.")

    mode = st.radio("Lead source", ["Scrape with Vayne", "Upload a CSV"], horizontal=True)
    if mode == "Scrape with Vayne":
        _scrape_tab(icp)
    else:
        _csv_tab()

    rows = st.session_state.get(ROWS_KEY)
    if rows:
        src = st.session_state.get(SRC_KEY, "")
        tag = {"vayne": "Vayne", "vayne-mock": "Vayne (MOCK)", "csv": "CSV upload"}.get(src, src)
        st.success(f"{len(rows)} lead(s) loaded from {tag}.")
        with st.expander("Preview first rows"):
            st.dataframe(pd.DataFrame(rows).head(8), use_container_width=True, hide_index=True)

    st.divider()
    nav = st.columns([2, 3, 3])
    if nav[0].button("◀ Back", use_container_width=True):
        _goto(1)
    if nav[1].button("Score leads  ▶", type="primary", disabled=not rows, use_container_width=True):
        _goto(3)
    if nav[2].button("↺ Start over", use_container_width=True):
        _reset()


# --- Step 3: score & export --------------------------------------------------

def step_score_export() -> None:
    icp = st.session_state[ICP_KEY]
    rows = st.session_state.get(ROWS_KEY) or []
    src = st.session_state.get(SRC_KEY, "")
    leads_mock = src == "vayne-mock"

    st.subheader("Step 3 · Score & export")
    st.caption(f"Scoring **{len(rows)}** lead(s) against **{icp['name']}**.")
    if icp.get("profile") is not None:
        st.caption("🔗 Structured bridge active — dimensions, thresholds, and exclusions come from "
                   "the approved Generated ICP's engine profile, not from text parsing.")

    if PAIRS_KEY not in st.session_state:
        if st.button(f"⚙️ Score {len(rows)} lead(s) against the ICP", type="primary", disabled=not rows):
            bar = st.progress(0.0, text="Scoring…")

            def prog(done, total):
                bar.progress(min(1.0, done / total) if total else 1.0, text=f"Scored {done}/{total}")

            with st.spinner("Scoring…"):
                pairs = campaign.score_rows(rows, icp["text"], icp["name"], progress_cb=prog,
                                            profile=icp.get("profile"))
            st.session_state[PAIRS_KEY] = pairs
            bar.empty()
            st.rerun()
        return

    pairs = st.session_state[PAIRS_KEY]
    scores_mock = any(getattr(r, "is_mock", False) for _, r in pairs)
    if leads_mock or scores_mock:
        bits = []
        if leads_mock:
            bits.append("leads are mock (Vayne offline)")
        if scores_mock:
            bits.append("scores are mock (`ANTHROPIC_API_KEY` not set)")
        st.warning("**MOCK/OFFLINE run** — " + "; ".join(bits) + ". Not production results.")

    threshold = st.slider("Qualifying threshold (Lead Score ≥)", 0, 100,
                          value=int(config.SCORE_THRESHOLD), step=5)
    qual = campaign.qualified(pairs, threshold)
    errors = sum(1 for _, r in pairs if r.error is not None)
    m = st.columns(4)
    m[0].metric("Scored", len(pairs))
    m[1].metric(f"Qualified (≥{threshold})", len(qual))
    m[2].metric("Below threshold", len(pairs) - len(qual))
    m[3].metric("Errors", errors)

    only_qual = st.checkbox("Show only qualifying leads", value=False)
    shown = qual if only_qual else pairs
    st.dataframe(export.build_main_dataframe(shown), use_container_width=True, hide_index=True)

    st.markdown("#### Export")
    src_label = {"vayne": "Vayne", "vayne-mock": "Vayne (mock)", "csv": "CSV"}.get(src, src)
    report_md = campaign.summary_report(pairs, icp["name"], threshold,
                                        is_mock=(leads_mock or scores_mock), source=src_label)
    d = st.columns(3)
    d[0].download_button("⬇️ Scoring workbook (XLSX)",
                         data=campaign.workbook_bytes(pairs, icp["name"]),
                         file_name=f"{icp['name']}-scored.xlsx",
                         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         use_container_width=True)
    d[1].download_button(f"⬇️ Qualified leads CSV (≥{threshold})",
                         data=campaign.qualifying_csv_bytes(pairs, threshold),
                         file_name=f"{icp['name']}-qualified-{threshold}.csv", mime="text/csv",
                         use_container_width=True)
    d[2].download_button("⬇️ Scoring report (Markdown)", data=report_md,
                         file_name=f"{icp['name']}-report.md", mime="text/markdown",
                         use_container_width=True)
    if st.button("💾 Save all exports to storage"):
        with st.spinner("Saving…"):
            saved = campaign.persist_exports(pairs, icp["name"], threshold,
                                             is_mock=(leads_mock or scores_mock), source=src_label)
        st.success(f"Saved {len(saved['files'])} artifact(s) to `{saved['location']}`.")
    with st.expander("View scoring report"):
        st.markdown(report_md)

    st.caption("Human Review Gate: review the ranked leads above before acting. This tool ranks and "
               "exports — it never contacts anyone. Approve and reach out to leads yourself, outside "
               "this app.")

    st.divider()
    nav = st.columns([2, 3, 3])
    if nav[0].button("◀ Back", use_container_width=True):
        _goto(2)
    if nav[1].button("Re-score", use_container_width=True):
        st.session_state.pop(PAIRS_KEY, None)
        st.rerun()
    if nav[2].button("↺ Start over", use_container_width=True):
        _reset()


# --- router ------------------------------------------------------------------

st.title("🚀 Run Campaign")
st.caption("Select an ICP, pull in leads, score them against it, and export the qualified list. "
           "The ICP Workspace defines *who's a good fit*; this page *finds and ranks* them.")

if _step() > 1 and ICP_KEY not in st.session_state:
    st.session_state[STEP_KEY] = 1

render_stepper(_step())

if _step() == 1:
    step_select_icp()
elif _step() == 2:
    step_get_leads()
else:
    step_score_export()
