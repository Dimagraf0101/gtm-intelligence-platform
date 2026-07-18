"""GTM Intelligence Platform — Streamlit entry point.

This script is the **navigation router only**. It declares the application's pages with
``st.navigation`` / ``st.Page`` (the official Streamlit multipage API) and runs the selected one.

Navigation design (UX Sprint 1):
  * Home (UX Sprint 2) is the default landing page — a read-only command center, not a workflow step.
  * Sidebar labels are deliberately short (Design Sprint 2): the section header supplies the context, so
    the label only needs to disambiguate within its section. This is what keeps the sidebar readable as
    the product grows past a handful of pages. `url_path` is pinned on every page, so renaming a label
    can never change a URL.
  * The workflow pages are grouped into the four stages of the product using ``st.navigation``'s native
    section support, so the sidebar reads as a guided workflow rather than a flat module list.
  * Icons come from one family (Material Symbols, ``:material/...:``) for a consistent, monochrome look.
  * **Titles and URLs are unchanged**; ``url_path`` is pinned explicitly so existing bookmarks and deep
    links keep working regardless of how the sidebar is presented.
  * No step numbering, no checkmarks, no completion state — the app has no canonical definition of
    "completed" for every stage, and Search Execution / Lead Import are *alternative* acquisition paths,
    not sequential steps. The sidebar must not imply either.

Why a router instead of Streamlit's automatic ``pages/`` discovery (UI-001): with automatic discovery
the entry script itself is rendered as the first sidebar item, labelled from its filename — which is why
an "app" entry appeared. Declaring pages explicitly removes it without CSS, JavaScript, or injected HTML.

The legacy PDF + CSV qualification screen lives in ``legacy_qualification.py``. It is registered with
``visibility="hidden"``, so it is not listed in the sidebar but remains reachable at
``/legacy_qualification`` and via ``st.page_link`` / ``st.switch_page``.

Run:  ./.venv/bin/streamlit run app.py
"""
import streamlit as st

# --- shared page shell (owned here, never by individual pages) ---------------
# One set_page_config for the whole app: every page inherits the same favicon, the same wide layout
# and the same default tab title. Pages must not call st.set_page_config; the per-page browser title
# comes from each st.Page(title=...) below.
st.set_page_config(page_title="GTM Intelligence Platform",
                   page_icon="assets/favicon.png", layout="wide")

# --- product identity --------------------------------------------------------
# st.logo is the only slot that renders ABOVE the navigation menu; sidebar text written from this
# router appears *below* it, which is too late to orient a first-time user. The wordmark carries the
# product name and its one-line promise, in monochrome that reads on both light and dark themes.
st.logo("assets/wordmark.svg", size="large", link=None)

# --- navigation --------------------------------------------------------------
# Section order follows the product workflow. Order *within* each section, page titles, and page URLs
# are preserved. `url_path` is pinned so presentation changes can never break a bookmark.
NAVIGATION = {
    # Home is the landing page: read-only orientation, not a workflow step.
    "Start": [
        # The default page is always served at "/" — Streamlit ignores url_path for it, so none is
        # pinned here (pinning one would imply a "/Home" route that does not exist).
        st.Page("pages/0_Home.py", title="Home", icon=":material/home:", default=True),
    ],
    "Foundation": [
        st.Page("pages/1_Business_Knowledge_Review.py", title="Knowledge",
                icon=":material/database:", url_path="Business_Knowledge_Review"),
        st.Page("pages/2_Knowledge_Interview.py", title="Interview",
                icon=":material/forum:", url_path="Knowledge_Interview"),
    ],
    "ICP Strategy": [
        st.Page("pages/3_Strategy_Review.py", title="Strategy",
                icon=":material/tune:", url_path="Strategy_Review"),
        st.Page("pages/4_Approval.py", title="Approval",
                icon=":material/verified:", url_path="Approval"),
        st.Page("pages/5_General_ICP.py", title="General ICP",
                icon=":material/apartment:", url_path="General_ICP"),
        st.Page("pages/6_Market_Hypotheses.py", title="Hypotheses",
                icon=":material/explore:", url_path="Market_Hypotheses"),
        st.Page("pages/7_Search_Strategy.py", title="Search Strategy",
                icon=":material/manage_search:", url_path="Search_Strategy"),
    ],
    # Two ALTERNATIVE acquisition paths — not sequential steps. Automated first, manual fallback second.
    "Lead Acquisition": [
        st.Page("pages/10_Search_Execution.py", title="Run Search",
                icon=":material/travel_explore:", url_path="Search_Execution"),
        st.Page("pages/8_Lead_Import.py", title="Import",
                icon=":material/upload_file:", url_path="Lead_Import"),
    ],
    "Qualification": [
        st.Page("pages/9_Qualification.py", title="Qualification",
                icon=":material/analytics:", url_path="Qualification"),
        st.Page("pages/11_Human_Review.py", title="Review",
                icon=":material/how_to_reg:", url_path="Human_Review"),
        # Legacy PDF + CSV screen — hidden from the menu, still reachable by URL / page_link.
        st.Page("legacy_qualification.py", title="Lead Qualification (legacy)",
                icon=":material/history:", url_path="legacy_qualification", visibility="hidden"),
    ],
}

page = st.navigation(NAVIGATION)

# --- orientation -------------------------------------------------------------
# Stated in words rather than drawn as arrows: the acquisition paths are genuinely alternatives, and the
# app tracks no per-stage completion state, so no progress indicator would be truthful.
st.sidebar.caption("In **Lead Acquisition**, Run Search and Import are alternatives — use either one.")

# Appearance: Streamlit has no public API to set the active theme at runtime (st.context.theme is
# read-only), so the real control is the built-in System/Light/Dark selector restored by
# toolbarMode="viewer". This reports the active theme and points at that control rather than
# rendering a widget that could not actually apply a change.
try:
    _active = (getattr(st.context.theme, "type", None) or "system").capitalize()
except Exception:  # noqa: BLE001 - context is unavailable outside a script run
    _active = "System"
st.sidebar.caption(f":material/contrast: Appearance: **{_active}** — change it under "
                   ":material/more_vert: → Settings.")

page.run()
