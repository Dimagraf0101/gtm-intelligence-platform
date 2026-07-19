"""GTM Intelligence Platform — Streamlit entrypoint: navigation + home landing page.

The app has two working areas (docs/product/ICP_WORKSPACE_UX.md §1):

    🧭 ICP Workspace — turn company materials into a reviewed, IQS-validated, human-approved ICP.
    🚀 Run Campaign  — select an ICP, get leads (Vayne scrape or CSV), score, review, export.

The former one-screen "upload ICP PDF + CSV → score" flow moved into Run Campaign (import the
PDF in step 1, upload the CSV in step 2). The legacy ICP-text scoring path itself is unchanged
(ADR-012) — only the separate screen is gone; this file now renders the landing page and defines
navigation (st.navigation), and holds no qualification logic.

Run:  ./.venv/bin/streamlit run app.py
"""
import os
import sys
from pathlib import Path

import streamlit as st

# The pipeline modules import each other by bare module name (from config import ...),
# so the pipeline/ directory must be importable.
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

st.set_page_config(page_title="GTM Intelligence Platform", page_icon="🎯", layout="wide")


def home() -> None:
    import config                # noqa: E402  (side effect: loads .env)
    import icp_library as lib    # noqa: E402
    import storage               # noqa: E402
    from generated_icp import STATUS_APPROVED  # noqa: E402

    st.title("🎯 GTM Intelligence Platform")
    st.caption("Company evidence in — ranked, explainable, human-approved leads out. The system "
               "qualifies and ranks; it never contacts anyone (Human Review Gate).")

    st.markdown(
        "**The workflow:** company materials → **Business Knowledge** (you approve every fact) → "
        "Draft ICP → IQS validation → **your approval** → select the ICP in a campaign → get leads "
        "(Vayne scrape or CSV upload) → score every lead → review the ranked list → export."
    )

    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.subheader("🧭 ICP Workspace")
        st.markdown("Define *who's a good fit*: upload company materials, review & approve the "
                    "extracted Business Knowledge, generate a Draft ICP, validate it against IQS, "
                    "and **approve** it for qualification.")
        st.page_link("pages/1_Business_Knowledge_Review.py", label="Open ICP Workspace", icon="🧭")
    with c2, st.container(border=True):
        st.subheader("🚀 Run Campaign")
        st.markdown("Find and rank them: select an approved ICP from the library, pull leads in "
                    "via Vayne or a CSV upload, score them against the ICP, review, and export "
                    "XLSX / CSV / report.")
        st.page_link("pages/2_Run_Campaign.py", label="Open Run Campaign", icon="🚀")
        st.caption("Have an existing ICP as a PDF? Import it in step 1 — that's the former "
                   "one-screen flow (same engine, same ICP-text path).")

    # --- ICP library at a glance (never fatal — storage may be unconfigured) --
    try:
        entries = lib.list_entries()
        gen = [e for e in entries if e.source == lib.SOURCE_GENERATED]
        approved = sum(1 for e in gen if e.status == STATUS_APPROVED)
        m = st.columns(4)
        m[0].metric("ICPs in library", len(entries))
        m[1].metric("Approved (generated)", approved)
        m[2].metric("Drafts awaiting approval", len(gen) - approved)
        m[3].metric("PDF imports", len(entries) - len(gen))
    except Exception as exc:  # noqa: BLE001 — the landing page must always render
        st.caption(f"💾 ICP library unavailable: {type(exc).__name__}: {exc}")

    # --- environment status ---------------------------------------------------
    st.divider()
    bits = [
        "🤖 Scoring/extraction: " + ("live (Anthropic)" if os.getenv("ANTHROPIC_API_KEY")
                                     else "offline mock — set `ANTHROPIC_API_KEY`"),
        "🔎 Vayne scraping: " + ("live" if os.getenv("VAYNE_API_TOKEN")
                                 else "offline mock — set `VAYNE_API_TOKEN`"),
    ]
    try:
        bits.append(f"💾 Storage: {(config.STORAGE_BACKEND or 'local')} · "
                    f"{storage.get_storage().describe('')}")
    except Exception as exc:  # noqa: BLE001
        bits.append(f"💾 Storage: unavailable ({type(exc).__name__})")
    st.caption("  ·  ".join(bits))
    st.caption("Offline mocks are deterministic placeholders and are always clearly labelled — "
               "they can never be mistaken for production results.")


nav = st.navigation([
    st.Page(home, title="Home", icon="🏠", default=True),
    st.Page("pages/1_Business_Knowledge_Review.py", title="ICP Workspace", icon="🧭"),
    st.Page("pages/2_Run_Campaign.py", title="Run Campaign", icon="🚀"),
])
nav.run()
