"""Search Strategy — Streamlit page (Sprint 9).

VIEW ONLY. Generate / review / approve a Search Strategy for the selected Market Hypothesis, derived
from that hypothesis's approved Adapted ICP. All logic lives in the search_strategy service and the
MarketHypothesis domain — this page only forwards and renders. It shows structured Sales Navigator
filter *recommendations*; it does NOT scrape, qualify, or build a Sales Navigator URL.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import icp_project as ip                 # noqa: E402
import icp_approval as ap                # noqa: E402
import icp_identity as idy               # noqa: E402
import search_strategy as ss            # noqa: E402

st.title("Search Strategy")
st.caption("How to find leads for one Market Hypothesis — company/person criteria, geography, signals, "
           "exclusions, and **LinkedIn Sales Navigator filter recommendations** to configure manually. "
           "Derived from the hypothesis's approved Adapted ICP. It does not scrape or qualify leads.")

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

# --- source Adapted ICP ------------------------------------------------------
source = ap.get_active_approved_icp(hyp)
if source is None:
    st.warning("This hypothesis has **no approved Adapted ICP**. Generate one on **Market Hypotheses**, "
               "then approve it on **Strategy Review** / **Approval** — a Search Strategy is derived "
               "from an approved ICP.")
else:
    st.success(f"Source Adapted ICP: **{source.metadata.name}** v{source.metadata.version} · identity "
               f"`{idy.artifact_identity_str(source)[:28]}…`")

# --- generate ----------------------------------------------------------------
st.subheader("1 · Generate")
if st.button("Generate Search Strategy", type="primary", disabled=source is None, icon=":material/auto_awesome:"):
    res = ss.generate_search_strategy(hyp)
    st.session_state[f"ss_result_{hyp.project_id}"] = res.summary()
    if res.ok:
        st.success(f"Generated Search Strategy v{hyp.latest_search_strategy().version}.")
    else:
        st.error(res.refusal_reason)
        for i in res.issues:
            st.write(f"- {i}")
    st.rerun()

last = st.session_state.get(f"ss_result_{hyp.project_id}")
if last and not last["ok"]:
    st.error(last["refusal_reason"])

strategies = hyp.list_search_strategies()
if not strategies:
    st.info("No Search Strategy yet. Generate one above once an approved Adapted ICP exists.")
    st.stop()

# --- version history + select ------------------------------------------------
st.subheader("2 · Versions")
vlabels = {f"v{s.version} · {s.status}": i for i, s in enumerate(strategies)}
pick = st.selectbox("Version", list(vlabels), index=len(vlabels) - 1)
strat = strategies[vlabels[pick]]
st.dataframe(
    [{"version": s.version, "status": s.status, "confidence": s.confidence,
      "derived from": s.derived_from_adapted_icp[:24] + "…" if s.derived_from_adapted_icp else "—",
      "approved by": s.approved_by or "—"} for s in strategies],
    use_container_width=True, hide_index=True)

# --- structured review -------------------------------------------------------
st.subheader("3 · Review")
st.markdown(f"**Objective:** {strat.objective}")
c = st.columns(3)
c[0].metric("Confidence", strat.confidence)
c[1].metric("Status", strat.status)
c[2].metric("Version", strat.version)

cc, pc, geo, snf = strat.company_criteria, strat.person_criteria, strat.geography, strat.sales_nav_filters
col1, col2 = st.columns(2)
with col1:
    st.markdown("**Geography** — included: " + (", ".join(geo.included) or "—") +
                (" · excluded: " + ", ".join(geo.excluded) if geo.excluded else ""))
    st.markdown("**Industries:** " + (", ".join(cc.industries) or "—"))
    st.markdown("**Company sizes:** " + (", ".join(cc.company_sizes) or "—"))
    st.markdown("**Company types:** " + (", ".join(cc.company_types) or "—"))
    st.markdown("**Included keywords:** " + (", ".join(cc.included_keywords) or "—"))
with col2:
    st.markdown("**Job titles:** " + (", ".join(pc.job_titles) or "—"))
    st.markdown("**Decision makers:** " + (", ".join(pc.decision_maker_roles) or "—"))
    st.markdown("**Excluded titles:** " + (", ".join(pc.excluded_job_titles) or "—"))
    st.markdown("**Exclusions:** " + (", ".join(strat.exclusions) or "—"))
    st.markdown("**Buying signals:** " + (", ".join(strat.buying_signals) or "— (none evidenced)"))

with st.expander("Sales Navigator filter recommendations (configure manually — no URL is generated)"):
    for lbl, vals in (("Geography", snf.geography), ("Industry", snf.industry),
                      ("Company headcount", snf.company_headcount), ("Company type", snf.company_type),
                      ("Current job title", snf.current_job_title), ("Seniority", snf.seniority),
                      ("Function", snf.function), ("Years in current position",
                                                   snf.years_in_current_position),
                      ("Keywords", snf.keywords)):
        st.write(f"- **{lbl}:** {', '.join(vals) or '—'}")

with st.expander("Rationale · known facts · assumptions · unknowns"):
    st.markdown(f"**Rationale:** {strat.rationale}")
    st.write("**Known facts:** " + (", ".join(strat.known_facts) or "—"))
    st.write("**Assumptions:** " + (", ".join(strat.assumptions) or "—"))
    st.write("**Unknowns:** " + (", ".join(strat.unknowns) or "—"))

issues = ss.validate_search_strategy(strat)
if issues:
    st.warning("Validation issues:\n" + "\n".join(f"- {i}" for i in issues))

# --- lifecycle ---------------------------------------------------------------
st.subheader("4 · Lifecycle")
st.caption("Draft → Reviewed → Approved → Archived. Approved strategies are immutable.")
b = st.columns(4)
if b[0].button("Mark Reviewed", disabled=strat.status != ss.STRATEGY_DRAFT):
    ss.set_status(hyp, strat.strategy_id, ss.STRATEGY_REVIEWED)
    st.rerun()
approver = b[1].text_input("Approver", key=f"ss_appr_{strat.strategy_id}", label_visibility="collapsed",
                           placeholder="Approver name")
if b[2].button("Approve", disabled=(strat.status != ss.STRATEGY_REVIEWED or not approver.strip())):
    try:
        ss.set_status(hyp, strat.strategy_id, ss.STRATEGY_APPROVED, approved_by=approver.strip())
        st.success("Approved.")
        st.rerun()
    except ss.SearchStrategyError as e:
        st.error(str(e))
if b[3].button("Archive", disabled=strat.status == ss.STRATEGY_ARCHIVED):
    ss.set_status(hyp, strat.strategy_id, ss.STRATEGY_ARCHIVED)
    st.rerun()

st.caption("Next: manual Sales Navigator configuration and lead acquisition (Vayne) are **not yet "
           "implemented** — a later sprint.")
