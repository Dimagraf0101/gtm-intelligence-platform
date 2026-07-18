"""Lead Import — Streamlit page (Sprint 10).

VIEW ONLY. Upload a Vayne CSV export (produced from a manual Sales Navigator search) and import it as
an immutable Lead Batch for the selected Market Hypothesis. All parsing/validation/mapping lives in
``vayne_adapter`` (the anti-corruption layer) and the ``lead_batch`` domain — this page only forwards
and renders. It does NOT score, qualify, rank, or contact leads.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import lead_batch as lb                  # noqa: E402
import vayne_adapter as va              # noqa: E402
import search_strategy as ss            # noqa: E402
import lead_import as li                 # noqa: E402

st.set_page_config(page_title="Lead Import", page_icon="📥", layout="wide")
st.title("📥 Lead Import")
st.caption("Import a Vayne CSV export (from a manual Sales Navigator search) into an immutable Lead "
           "Batch for one Market Hypothesis. This is lead **acquisition** only — no scoring, "
           "qualification, or outreach happens here. Vayne is one replaceable source adapter.")

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

approved_strategy = hyp.latest_approved_search_strategy()
if approved_strategy is None:
    st.warning("This hypothesis has no **approved Search Strategy** yet. A persisted Lead Batch must "
               "derive from one — approve a Search Strategy on the **Search Strategy** page first, run "
               "that search manually in Sales Navigator, then export via Vayne. (Preview is allowed "
               "below; import is blocked until a Search Strategy is approved.)")
else:
    st.success(f"Approved Search Strategy v{approved_strategy.version} available — leads will be "
               f"provenanced to `{ss.search_strategy_reference(approved_strategy)[:40]}…`.")

# --- upload + preview --------------------------------------------------------
st.subheader("1 · Upload Vayne CSV")
csv_file = st.file_uploader("Vayne CSV export", type=["csv"], label_visibility="collapsed")
imported_by = st.text_input("Imported by", key="imported_by", placeholder="Your name")

if csv_file is not None:
    try:
        preview = va.preview_vayne_csv(csv_file.getvalue(), limit=10)
        st.caption(f"Preview (first {len(preview)} mapped row(s)):")
        st.dataframe(
            [{"company": l.company_name, "person": l.person_name, "title": l.current_title,
              "geography": l.geography, "industry": l.industry,
              "linkedin": l.linkedin_url[:40]} for l in preview],
            use_container_width=True, hide_index=True)
    except va.VayneImportError as exc:
        st.error(str(exc))

# --- import ------------------------------------------------------------------
st.subheader("2 · Import")
if approved_strategy is None:
    st.info("Import is disabled: approve a Search Strategy for this hypothesis first.")
can_import = bool(csv_file and imported_by.strip() and approved_strategy is not None)
if st.button("📥 Import as Lead Batch", type="primary", disabled=not can_import):
    res = li.import_leads_from_strategy(hyp, approved_strategy.strategy_id, csv_file.getvalue(),
                                        imported_by=imported_by.strip())
    st.session_state[f"lead_import_{hyp.project_id}"] = res.summary()
    if res.ok:
        st.success(f"Imported {res.batch.stats['imported']} lead(s) into a new batch, derived from "
                   f"the approved Search Strategy.")
    else:
        st.error(res.error)
    st.rerun()

last = st.session_state.get(f"lead_import_{hyp.project_id}")
if last and not last["ok"]:
    st.error(last["error"])

# --- batches -----------------------------------------------------------------
batches = hyp.list_lead_batches()
if not batches:
    st.info("No lead batches yet. Upload a Vayne CSV above.")
    st.stop()

st.subheader("3 · Lead batches")
st.dataframe(
    [{"batch": b.batch_id[:12], "imported_at": b.imported_at, "by": b.imported_by,
      "source": b.source.kind, "leads": b.stats.get("imported", len(b.leads)),
      "duplicates_removed": b.stats.get("duplicates_removed", 0),
      "skipped": b.stats.get("skipped_missing_company", 0)} for b in batches],
    use_container_width=True, hide_index=True)

blabels = {f"{b.batch_id[:12]} · {b.imported_at} ({b.stats.get('imported', len(b.leads))} leads)": i
           for i, b in enumerate(batches)}
pick = st.selectbox("Batch", list(blabels), index=len(blabels) - 1)
batch = batches[blabels[pick]]

s = batch.stats
c = st.columns(5)
c[0].metric("Leads", s.get("imported", len(batch.leads)))
c[1].metric("Unique companies", s.get("unique_companies", 0))
c[2].metric("With LinkedIn", s.get("with_linkedin_url", 0))
c[3].metric("Duplicates removed", s.get("duplicates_removed", 0))
c[4].metric("Skipped (no company)", s.get("skipped_missing_company", 0))

for w in lb.batch_warnings(batch):
    st.caption("⚠️ " + w)

with st.expander("View leads (read-only)", expanded=True):
    st.dataframe(
        [{"company": l.company_name, "person": l.person_name, "title": l.current_title,
          "size": l.company_size, "geography": l.geography, "industry": l.industry,
          "linkedin_url": l.linkedin_url} for l in batch.leads],
        use_container_width=True, hide_index=True)

st.caption("Next: lead qualification against the hypothesis's Approved ICP is a **separate, later** "
           "step — this page only acquires leads.")
