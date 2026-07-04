# frontend/app.py
"""Paycon e2ePS — End-to-End Payment Simulator.

Multi-page Streamlit application shell.
Sets global page config, explicit st.navigation() with grouped icon'd sections,
injects the shared Paycon theme, and shows a branded landing screen with a live
health check.
"""
import sys
import os

# Make shared utilities importable from pages/ sub-modules.
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from utils.session_state import init_session_state
from utils.api_client import get_api_url
from utils.theme import inject_theme

init_session_state()

st.set_page_config(
    page_title="Paycon e2ePS — End-to-End Payment Simulator",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_theme()

# ── Explicit multi-page navigation (F2 fix) ─────────────────────────────────
# st.navigation() with titled, icon'd sections prevents Streamlit from showing
# raw lowercase filenames ("app", "01_home", etc.) in the sidebar.
_pages_dir = os.path.join(os.path.dirname(__file__), "pages")

pg = st.navigation(
    {
        "🏠 Run": [
            st.Page(os.path.join(_pages_dir, "01_home.py"),
                    title="Home",             icon="🏠"),
            st.Page(os.path.join(_pages_dir, "10_transaction_builder.py"),
                    title="Transaction Builder", icon="💳"),
            st.Page(os.path.join(_pages_dir, "03_suite_runner.py"),
                    title="Suite Runner",     icon="🧪"),
            st.Page(os.path.join(_pages_dir, "09_certification.py"),
                    title="Certification",   icon="🏅"),
        ],
        "🔭 Inspect": [
            st.Page(os.path.join(_pages_dir, "02_scenario_lab.py"),
                    title="Scenario Lab",    icon="🧬"),
            st.Page(os.path.join(_pages_dir, "04_iso_mapper.py"),
                    title="ISO Mapper",      icon="🔄"),
            st.Page(os.path.join(_pages_dir, "05_terminal_emulator.py"),
                    title="Terminal Emulator", icon="📱"),
            st.Page(os.path.join(_pages_dir, "08_analytics.py"),
                    title="Analytics",       icon="📊"),
            st.Page(os.path.join(_pages_dir, "12_enrichment_trace.py"),
                    title="Enrichment Trace", icon="🔍"),
        ],
        "⚙️ Configure": [
            st.Page(os.path.join(_pages_dir, "06_sandbox_config.py"),
                    title="Sandbox Config",  icon="⚙️"),
        ],
        "🤖 AI": [
            st.Page(os.path.join(_pages_dir, "07_ai_copilot.py"),
                    title="AI Copilot",      icon="🤖"),
            st.Page(os.path.join(_pages_dir, "11_ai_settings.py"),
                    title="AI Settings",     icon="🔑"),
        ],
    },
    position="sidebar",
)

pg.run()
