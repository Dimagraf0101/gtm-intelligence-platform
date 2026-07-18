"""Knowledge Interview — Streamlit page (Sprint 5.3).

VIEW ONLY. Every rule lives in pipeline/knowledge_interview.py (KnowledgeInterview) and the modules it
reuses. This page renders the current question and forwards the user's action to the engine; it
decides nothing — not what to ask, not what an answer means, and not whether the interview is
complete.

Run:  ./.venv/bin/streamlit run app.py   (this page appears in the sidebar)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import business_knowledge as bk          # noqa: E402
import icp_project as ip                 # noqa: E402
import knowledge_interview as ki         # noqa: E402
import workspace_revision as wr          # noqa: E402

st.set_page_config(page_title="Knowledge Interview", page_icon="🎤", layout="wide")
st.title("🎤 Knowledge Interview")
st.caption("Targeted questions about what is still missing for the selected ICP Project. Your answers "
           "are saved to that project's **ICP Knowledge**. **Company Knowledge** is used to work out "
           "what to ask, and is only changed if you explicitly promote an answer to it.")

PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

port = st.session_state.get(PORT_KEY)
if port is None or not port.projects:
    st.info("Open **Knowledge Review** first to create an ICP Project and add company materials.")
    st.stop()

# --- selected ICP Project ----------------------------------------------------
labels = {f"{p.name}": p.project_id for p in port.projects}
current_pid = st.session_state.get(SEL_KEY)
current_label = next((l for l, v in labels.items() if v == current_pid), list(labels)[0])
chosen = st.selectbox("ICP Project", list(labels), index=list(labels).index(current_label))
st.session_state[SEL_KEY] = labels[chosen]
project = port.get_project(labels[chosen])
if project.hypothesis:
    st.caption(f"Hypothesis: _{project.hypothesis}_")

IV_KEY = f"interview_{project.project_id}"
include_optional = st.checkbox("Also ask optional questions", value=False,
                               help="Optional questions are never asked by default.")

REV_KEY = IV_KEY + "_rev"
# Rebuild the interview when the underlying knowledge changed on ANOTHER page. The interview's own
# answers re-stamp REV_KEY below, so answering never discards the in-progress session; only an
# external knowledge change does.
current_rev = wr.knowledge_revision(port.company, project)
interview = st.session_state.get(IV_KEY)
if interview is None or interview.project is not project or st.session_state.get(REV_KEY) != current_rev:
    interview = ki.KnowledgeInterview.for_project(port.company, project)
    st.session_state[IV_KEY] = interview
    st.session_state[REV_KEY] = current_rev
interview.include_optional = include_optional

if interview.session is None:
    summary = interview.gap_summary()
    st.write(f"**Missing Information:** {summary['blocking_gaps']} blocking · "
             f"{summary['important_gaps']} important · **Conflicts:** {summary['core_open_conflicts']}")
    if not interview.should_start():
        st.success("Nothing to ask — this ICP Project has no blocking gaps, important gaps, or "
                   "conflicts. Tick 'Also ask optional questions' to review optional details.")
    if st.button("Start Knowledge Interview", type="primary"):
        interview.start()
        st.rerun()
    st.stop()

session = interview.refresh()
s = session.current_gap_summary

# --- progress ----------------------------------------------------------------
st.subheader("Progress")
c = st.columns(6)
c[0].metric("Blocking", s["blocking_gaps"])
c[1].metric("Important", s["important_gaps"])
c[2].metric("Conflicts", s["core_open_conflicts"])
c[3].metric("Answered", session.answers_count)
c[4].metric("Skipped", session.skipped_count)
c[5].metric("Completeness", f"{s['completeness']}/100")
answered = sum(1 for q in session.questions if q.status != ki.PENDING)
st.progress(answered / len(session.questions) if session.questions else 1.0,
            text=f"{answered} of {len(session.questions)} question(s) handled · status: **{session.status}**")

if s["company_core_conflicts"]:
    st.warning(f"{s['company_core_conflicts']} conflict(s) in **Company Knowledge** also need a "
               "decision. Resolve those on the **Knowledge Review** page — the interview only "
               "changes this project's ICP Knowledge.")

for req in interview.strategy_requirements():
    st.info(f"**Handed to Strategy Review:** {req.title}. {req.reason}")

report = interview.effective_gap_report()
with st.expander(f"Missing Information — blocking ({len(report.blocking_gaps)})",
                 expanded=bool(report.blocking_gaps)):
    for g in report.blocking_gaps:
        st.write(f"- **{g.field}** — {g.reason}")
    if not report.blocking_gaps:
        st.caption("None.")
with st.expander(f"Missing Information — important ({len(report.important_gaps)})"):
    for g in report.important_gaps:
        st.write(f"- **{g.field}** — {g.reason}")
with st.expander(f"Conflicts ({len(report.unresolved_conflicts)})"):
    for cf in report.unresolved_conflicts:
        st.write(f"- **{cf['category']}/{cf['attribute']}** — {cf['conflicting_values']}")
    if not report.unresolved_conflicts:
        st.caption("None.")

# --- one question at a time --------------------------------------------------
st.subheader("Question")
q = interview.current_question()
if q is None:
    st.info("No further questions. Anything still open is shown above.")
else:
    st.markdown(f"**{q.question_text}**")
    meta = f"{q.source_type.replace('_', ' ')} · {q.category}"
    st.caption(f"{meta}{' · required' if q.required else ''}")
    if q.help_text:
        st.caption(q.help_text)

    key = f"ans_{q.question_id}"
    if q.answer_type == ki.LONG_TEXT:
        answer = st.text_area("Your answer", key=key)
    elif q.answer_type == ki.SINGLE_SELECT:
        answer = st.selectbox("Your answer", q.suggested_options, key=key)
    elif q.answer_type == ki.MULTI_SELECT:
        answer = st.multiselect("Your answer", q.suggested_options, key=key)
    elif q.answer_type == ki.YES_NO:
        answer = st.radio("Your answer", ["yes", "no"], horizontal=True, key=key)
    elif q.answer_type == ki.NUMERIC_RANGE:
        answer = st.text_input("Your answer", placeholder="e.g. 50-500", key=key)
    else:
        answer = st.text_input("Your answer", key=key)

    temporal = None
    if q.temporal_required:
        temporal = st.selectbox(
            "Does this describe something current or past?", bk.TEMPORAL_CONTEXTS,
            key=f"tmp_{q.question_id}",
            help="Past experience is recorded as experience — it never becomes a current ICP target.")
    note = st.text_input("Note (optional)", key=f"note_{q.question_id}")

    b = st.columns(3)
    if b[0].button("Submit answer", type="primary"):
        res = interview.submit_answer(q.question_id, answer, temporal_context=temporal, note=note)
        if res.ok:
            st.session_state["last_answered"] = res.question_id
            st.session_state[REV_KEY] = wr.knowledge_revision(port.company, project)  # own write
            st.rerun()
        else:
            st.error(res.error)
    if b[1].button("Skip"):
        interview.skip(q.question_id, note=note)
        st.rerun()
    na_allowed = not ki.na_rejection_reason(q)
    if b[2].button("Not applicable", disabled=not na_allowed,
                   help=ki.na_rejection_reason(q) or
                        "Records that this genuinely does not apply to this ICP Project."):
        res = interview.not_applicable(q.question_id, note=note)
        if res.ok:
            st.rerun()
        else:
            st.error(res.error)

# --- optional, explicit promotion of the last answer -------------------------
last = st.session_state.get("last_answered")
if last:
    try:
        lq = session.get(last)
    except KeyError:
        lq = None
    if lq is not None and lq.status == ki.ANSWERED and lq.written_item_ids:
        with st.expander("Promote your last answer to Company Knowledge", expanded=False):
            st.caption(f"Last answer: _{lq.question_text}_ — kept in this project's ICP Knowledge. "
                       "Promote a value only if it is true for the whole company, not just this ICP.")
            values = {}
            for kid in lq.written_item_ids:
                try:
                    values[project.project_knowledge._get(kid).value] = kid
                except KeyError:
                    continue
            picked = st.multiselect("Which value(s)?", list(values), default=list(values),
                                    key="promote_values")
            mode = st.radio("How", ["Copy to Company Knowledge (keep it here too)",
                                    "Move to Company Knowledge"], key="promote_mode")
            if st.button("Promote to Company Knowledge", disabled=not picked):
                for v in picked:
                    interview.promote_answer(last, knowledge_id=values[v],
                                             move=mode.startswith("Move"))
                st.success(f"Promoted to Company Knowledge: {', '.join(picked)}.")
                st.session_state["last_answered"] = None
                st.session_state[REV_KEY] = wr.knowledge_revision(port.company, project)  # own write
                st.rerun()

# --- updated draft -----------------------------------------------------------
st.subheader("Updated Draft ICP")
ready = session.status == ki.COMPLETED
if not ready:
    st.caption(f"Available once there are no blocking gaps and no open conflicts "
               f"(currently {s['blocking_gaps']} blocking, {s['core_open_conflicts']} conflict(s)). "
               "Important gaps may remain.")
if st.button("🧩 Generate Updated Draft ICP", type="primary", disabled=not ready):
    client, _ = __import__("icp_draft_generator").get_draft_client()
    res = interview.generate_updated_draft(client=client)
    st.session_state["last_interview_draft"] = res
    st.rerun()

res = st.session_state.get("last_interview_draft")
if res is not None and project.draft_versions:
    icp, val = res.generated_icp, res.validation_result
    st.success(f"Draft ICP **{icp.metadata.name}** v{len(project.draft_versions)} — status "
               f"{icp.metadata.status}, {len(icp.dimensions)} dimension(s), IQS "
               f"{'valid' if val.is_valid else 'blocking issues'} "
               f"(completeness {val.completeness_score}). is_mock={res.is_mock}")
    if val.blocking_errors:
        st.warning("IQS blocking: " + "; ".join(val.blocking_errors))
    with st.expander("View Updated Draft ICP (read-only)"):
        st.markdown(icp.to_markdown())
    st.download_button("⬇️ Updated Draft ICP (Markdown)", data=icp.to_markdown(),
                       file_name=f"{icp.metadata.name}-draft.md", mime="text/markdown")
