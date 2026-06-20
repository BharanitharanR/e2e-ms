# frontend/app.py
"""e2MS — Marqeta E2E Simulator.

Multi-page Streamlit application shell.
The bulk of the UI lives in frontend/pages/ (01_home.py … 08_analytics.py).
This file only sets global page config and provides a landing redirect notice.
"""
import sys
import os

# Make shared utilities importable from pages/ sub-modules.
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from utils.session_state import init_session_state
from utils.api_client import get_api_url

init_session_state()

st.set_page_config(
    page_title="e2MS — Marqeta E2E Simulator",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("💳 e2MS — Marqeta E2E Simulator")
st.caption("Enterprise-grade end-to-end issuer processor test platform")

st.markdown("---")

st.markdown("""
### 🗂️ Navigation

Use the sidebar to navigate between pages:

| Page | Description |
|------|-------------|
| 🏠 **Home** | Live KPIs, service health, quick-run |
| 🧬 **Scenario Lab** | Browse, run, and generate scenarios; ISO mapper; demo mode |
| 🧪 **Suite Runner** | Run test suites, download HTML/JUnit reports, AI failure explanations |
| 🔄 **ISO Mapper** | Three-column ISO 8583 ↔ JPBOS ↔ JCF canonical workbench |
| 📱 **Terminal Emulator** | Software EMV chip/NFC APDU emulation |
| ⚙️ **Sandbox Config** | Environment registry, health checks, JIT config |
| 🤖 **AI Copilot** | Claude-powered scenario generation, anomaly explanation, coverage gap advisor |
| 📊 **Analytics** | RC coverage matrix, latency waterfall, daily trends |
""")

st.markdown("---")
api_url = get_api_url()
st.info(f"🌐 Connected to backend: **{api_url}**")

# Quick health check on landing
try:
    import requests
    r = requests.get(f"{api_url}/health", timeout=2)
    if r.status_code == 200:
        st.success("✅ Backend is reachable")
    else:
        st.warning(f"⚠️ Backend returned HTTP {r.status_code}")
except Exception as e:
    st.error(f"❌ Cannot reach backend at {api_url} — {e}")
    st.caption("Start the stack with: `docker-compose up --build`")
