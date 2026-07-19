"""Home — landing page (UX Sprint 2, polished in the UI Identity sprint).

The application's control center. Read-only orientation: it renders what already exists, routes the
user to the next action, and performs no work of its own.

Deliberate constraints:
  * It **never creates or mutates state** — not even an empty workspace. Viewing Home has no side effects.
  * Every number is read from a real artifact (`business_knowledge.summary`, `knowledge_gaps`,
    `list_general_icps`, `list_lead_batches`, `list_qualified_batches`, `lead_review.review_statistics`).
    Nothing is estimated, scored, or invented.
  * "Continue" is derived purely from **artifact existence**, never a stored completion flag — the app
    has no canonical definition of "completed" per stage, so no progress bar would be truthful.
  * Lead Acquisition offers **two alternatives** (Search Execution or Lead Import); Home offers both and
    never implies one must precede the other.

Run:  ./.venv/bin/streamlit run app.py   (this is the default page)
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import knowledge_gaps as kg               # noqa: E402
import icp_approval as ap                 # noqa: E402
import lead_review as lr                  # noqa: E402


PORT_KEY = "icp_portfolio"
SEL_KEY = "selected_project_id"

# --- identity ----------------------------------------------------------------
# Compact by design: the sidebar lockup already carries the brand, so repeating it as a full-height
# hero here would only push the first actionable content below the fold. Two lines instead of four
# blocks brings Workspace almost to the top of the viewport.
_CSS = (Path(__file__).resolve().parent.parent / "assets" / "home.css").read_text(encoding="utf-8")
st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def _tile(icon: str, tone: str, large: bool = False, cls: str = "") -> str:
    """A stage icon in a tinted rounded tile.

    The glyph is a Material Symbols ligature rendered directly, which is the same font Streamlit
    already loads for `:material/…:` — so the tiles use the product's real icon set rather than
    images, and stay crisp at any zoom.
    """
    size = " gtm-tile--lg" if large else ""
    return (f'<div class="gtm-tile gtm-tile--{tone}{size} {cls}">'
            f'<span class="gtm-ico">{icon}</span></div>')


# The hero is two lines of type and nothing else. No container, no border, no fill: a box — however
# faint — announces itself as a UI component, and the eye resolves the container before the words.
# Removing it is what makes the two lines read as one brand identity. Hierarchy is carried by size,
# weight and colour alone. Rendered as ONE markdown block because Streamlit's h2 carries
# `padding: 15px 0` and separate st.markdown calls add their own gap on top of it.
st.markdown(
    """
    <div class="gtm-hero">
      <div class="gtm-hero-title">GTM Intelligence Platform</div>
      <div class="gtm-hero-tagline">Build ICPs → Find Leads → Prioritize Accounts → Convert Faster</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- read-only state (Home never creates a workspace) ------------------------
port = st.session_state.get(PORT_KEY)
hypotheses = list(getattr(port, "hypotheses", []) or []) if port is not None else []
company = getattr(port, "company", None) if port is not None else None
summary = company.summary() if company is not None else {}
general_icps = port.list_general_icps() if port is not None else []
gaps = kg.detect_gaps(company) if company is not None else None

selected_id = st.session_state.get(SEL_KEY)
hyp = next((h for h in hypotheses if h.project_id == selected_id), None) or \
    (hypotheses[0] if hypotheses else None)


def _stat(h):
    """Real per-hypothesis artifact counts. Nothing is estimated."""
    if h is None:
        return {}
    qualified = h.list_qualified_batches()
    pending = 0
    for q in qualified:
        review = h.review_for_qualified_batch(q.batch_id)
        pending += lr.review_statistics(len(q.qualified), review)["pending"]
    return {
        "approved_icp": ap.get_active_approved_icp(h) is not None,
        "approved_strategy": h.latest_approved_search_strategy() is not None,
        "lead_batches": len(h.list_lead_batches()),
        "qualified_batches": len(qualified),
        "executions": len(h.list_search_executions()),
        "pending_review": pending,
    }


s = _stat(hyp)

# --- workspace ---------------------------------------------------------------
st.markdown("##### Workspace")
# One bordered panel holding all four metrics, so they read as a single dashboard rather than
# four unrelated numbers floating on the page.
with st.container(border=True):
    if port is None:
        st.markdown("**No workspace open**")
        st.caption("Add your company materials to begin — everything downstream is derived from them.")
    else:
        c = st.columns(4, gap="large")
        c[0].metric("Knowledge items", summary.get("active_items", 0))
        c[1].metric("ICP versions", len(general_icps))
        c[2].metric("Hypotheses", len(hypotheses))
        c[3].metric("Completeness", f"{gaps.completeness_score}%" if gaps is not None else "—")
        bits = []
        if hyp is not None:
            bits.append(f"Active hypothesis **{hyp.name}**")
        saved = st.session_state.get("ws_path")
        bits.append(f"Saved to `{saved}`" if saved
                    else ":material/warning: Not saved to a file yet")
        st.caption(" · ".join(bits))

# --- continue ----------------------------------------------------------------
# Derived only from which artifacts exist. No stored progress, no invented completion state.
st.markdown("##### Continue")

# Each branch also carries the icon for its tile — the glyph names the action, so it changes with the
# step rather than being decoration bolted onto a fixed card.
if port is None or summary.get("active_items", 0) == 0:
    step, why, link, label, icon = (
        "Add company materials",
        "Everything downstream is derived from them.",
        "pages/1_Business_Knowledge_Review.py", "Business Knowledge", "note_add")
elif gaps is not None and not gaps.is_ready_for_icp_generation:
    step, why, link, label, icon = (
        "Close knowledge gaps",
        f"{len(gaps.blocking_gaps)} blocking gap(s) remain.",
        "pages/2_Knowledge_Interview.py", "Knowledge Interview", "forum")
elif not general_icps:
    step, why, link, label, icon = (
        "Generate your General ICP",
        "The company-wide baseline every hypothesis adapts from.",
        "pages/5_General_ICP.py", "General ICP", "target")
elif not hypotheses:
    step, why, link, label, icon = (
        "Create a Market Hypothesis",
        "A specific market bet with its own adapted ICP.",
        "pages/6_Market_Hypotheses.py", "Market Hypotheses", "lightbulb")
elif not s.get("approved_icp"):
    step, why, link, label, icon = (
        "Approve the Adapted ICP",
        "Qualification always runs against an approved ICP.",
        "pages/4_Approval.py", "Approval", "task_alt")
elif not s.get("approved_strategy"):
    step, why, link, label, icon = (
        "Approve a Search Strategy",
        "Defines the Sales Navigator filters used to find leads.",
        "pages/7_Search_Strategy.py", "Search Strategy", "manage_search")
elif s.get("lead_batches", 0) == 0:
    step, why, link, label, icon = (
        "Acquire leads", "Choose either path — they are alternatives.", None, None, "travel_explore")
elif s.get("qualified_batches", 0) == 0:
    step, why, link, label, icon = (
        "Qualify your leads",
        "Scored against the exact ICP this strategy came from.",
        "pages/9_Qualification.py", "Qualification", "readiness_score")
elif s.get("pending_review", 0) > 0:
    step, why, link, label, icon = (
        f"Review {s['pending_review']} lead(s)",
        "Nothing is exported until you decide.",
        "pages/11_Human_Review.py", "Human Review", "how_to_reg")
else:
    step, why, link, label, icon = (
        "Export approved leads",
        "Download the workbook or publish to Google Sheets.",
        "pages/11_Human_Review.py", "Human Review", "ios_share")

# The primary call to action: tile on the left, the step and its reason stacked beside it. The
# horizontal composition is what keeps the card compact — the tile occupies the vertical space the
# three text lines already need, instead of adding a fourth row of its own.
with st.container(border=True, key="gtm-cta"):
    tile_col, body = st.columns([1, 11], gap="medium", vertical_alignment="center")
    with tile_col:
        st.markdown(_tile(icon, "blue", large=True), unsafe_allow_html=True)
    with body:
        st.markdown(f"#### {step}")
        st.caption(why)
        if link:
            st.page_link(link, label=f"Open {label}", icon=":material/arrow_forward:")
        else:
            a, b = st.columns(2, gap="medium")
            with a:
                st.page_link("pages/10_Search_Execution.py", label="Run Search — automated",
                             icon=":material/travel_explore:")
            with b:
                st.page_link("pages/8_Lead_Import.py", label="Import — upload a CSV",
                             icon=":material/upload_file:")

# --- workflow ----------------------------------------------------------------
st.markdown("##### Workflow")
# Four equal cards read as connected stages of one pipeline, not four paragraphs.
# Each card is an entry point, not a caption: the user can start any stage from here. That is what
# makes Home a command center rather than a description of the product.
# Each stage carries its own tint, so the four cards are distinguishable at a glance while the page
# stays quiet — the tints are decorative identity only and never encode status.
STAGES = [
    ("database", "blue", "Foundation", "Curate company knowledge.",
     "pages/1_Business_Knowledge_Review.py", "Knowledge"),
    ("tune", "violet", "ICP Strategy", "Approve an ICP and a search strategy.",
     "pages/5_General_ICP.py", "General ICP"),
    ("travel_explore", "teal", "Lead Acquisition", "Run a search *or* upload a CSV.",
     "pages/10_Search_Execution.py", "Run Search"),
    ("how_to_reg", "amber", "Qualification", "Score, review, export.",
     "pages/9_Qualification.py", "Qualification"),
]
g = st.columns(4, gap="large", border=True)
for col, (ico, tone, name, desc, path, lbl) in zip(g, STAGES):
    with col:
        # Tile, name, detail, action — the same four-beat rhythm in every card, so the row reads as
        # one row of stages rather than four independent blocks.
        st.markdown(_tile(ico, tone, cls="gtm-wf-tile"), unsafe_allow_html=True)
        st.markdown(f"**{name}**")
        st.caption(desc)
        st.page_link(path, label=lbl, icon=":material/arrow_forward:")

# --- pipeline (only when there is something real to show) --------------------
if hyp is not None and (s.get("lead_batches") or s.get("qualified_batches") or s.get("executions")):
    st.markdown("##### Pipeline")
    with st.container(border=True):
        p = st.columns(4, gap="large")
        p[0].metric("Searches", s["executions"])
        p[1].metric("Lead batches", s["lead_batches"])
        p[2].metric("Qualified", s["qualified_batches"])
        p[3].metric("Pending review", s["pending_review"])
        st.caption(f"{hyp.name} · counts read directly from stored artifacts.")
