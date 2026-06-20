# frontend/pages/06_sandbox_config.py
"""Sandbox & Environment Configuration — manage environments, JIT config, health checks."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post, get_api_url
from utils.session_state import init_session_state

init_session_state()
st.set_page_config(page_title="e2MS — Sandbox Config", page_icon="⚙️", layout="wide")

st.title("⚙️ Sandbox & Environment Configuration")

# ── Active API indicator ──────────────────────────────────────────────────────
active_url = get_api_url()
st.info(f"🌐 Currently connected to: **{active_url}**")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_env, tab_health, tab_jit, tab_reset = st.tabs([
    "🌍 Environments",
    "💓 Service Health",
    "🔧 JIT Configuration",
    "🔄 Reset / Flush",
])

# ── Tab 1: Environments ───────────────────────────────────────────────────────
with tab_env:
    st.subheader("Environment Registry")
    st.caption("Switch between local Docker, Marqeta sandbox, staging, or any custom endpoint.")

    envs = api_get("/environments") or []
    if envs:
        for env in envs:
            is_active = env.get("is_active", 0)
            badge = "🟢 ACTIVE" if is_active else "⚪ inactive"
            with st.expander(f"{badge} — {env.get('name','?')}", expanded=is_active):
                c1, c2 = st.columns(2)
                c1.markdown(f"**API URL:** `{env.get('api_url','?')}`")
                c2.markdown(f"**Customer JIT URL:** `{env.get('customer_jit_url') or '—'}`")
                st.markdown(f"**Notes:** {env.get('notes') or '—'}")
                st.caption(f"Created: {(env.get('created_at') or '')[:19].replace('T',' ')} UTC")
                if not is_active:
                    if st.button(f"✅ Activate", key=f"act_{env['id']}"):
                        result = api_post(f"/environments/{env['id']}/activate", {})
                        if result and result.get("activated"):
                            activated_env = result.get("environment", {})
                            st.session_state.active_api_url  = activated_env.get("api_url")
                            st.session_state.active_env_name = activated_env.get("name")
                            st.session_state.active_env_id   = activated_env.get("id")
                            st.success(f"Switched to: **{activated_env.get('name')}**")
                            st.rerun()
                else:
                    st.success("This is the active environment.")

    st.markdown("---")
    with st.expander("➕ Add New Environment", expanded=False):
        n1, n2 = st.columns(2)
        new_name = n1.text_input("Name",      placeholder="My Sandbox", key="new_env_name")
        new_api  = n2.text_input("API URL",   placeholder="http://...:8000", key="new_env_api")
        new_jit  = n1.text_input("JIT URL",   placeholder="http://...:8001", key="new_env_jit")
        new_note = n2.text_input("Notes",     placeholder="optional", key="new_env_note")
        if st.button("💾 Create Environment", key="create_env_btn"):
            if new_name and new_api:
                result = api_post("/environments", {
                    "name": new_name, "api_url": new_api,
                    "customer_jit_url": new_jit or None, "notes": new_note or None,
                })
                if result and result.get("created"):
                    st.success(f"Environment created (ID: {result.get('id')})")
                    st.rerun()
            else:
                st.warning("Name and API URL are required.")

    # Direct URL override (session-only)
    st.markdown("---")
    st.subheader("🔗 Direct URL Override (session-only)")
    st.caption("Temporarily override the API URL for this browser session without saving to the registry.")
    override_url = st.text_input(
        "Backend API URL", value=st.session_state.get("active_api_url") or "",
        placeholder="http://backend:8000", key="override_url_input"
    )
    co1, co2 = st.columns(2)
    if co1.button("Apply Override", key="apply_override"):
        st.session_state.active_api_url = override_url.strip() or None
        st.success("URL override applied for this session.")
    if co2.button("Clear Override", key="clear_override"):
        st.session_state.active_api_url = None
        st.success("Override cleared — using default from env var.")

# ── Tab 2: Service Health ─────────────────────────────────────────────────────
with tab_health:
    st.subheader("Service Health Dashboard")
    if st.button("🔄 Refresh Health", key="health_refresh"):
        st.session_state.health_cache = {}

    health = api_get("/health/all")
    if health:
        overall = health.get("overall", "unknown")
        overall_color = "#28a745" if overall == "ok" else "#dc3545"
        st.markdown(
            f'<div style="background:{overall_color};color:#fff;padding:8px 16px;'
            f'border-radius:6px;font-size:1.1em;margin-bottom:16px">'
            f'Overall: <b>{overall.upper()}</b></div>',
            unsafe_allow_html=True,
        )
        for name, info in health.get("services", {}).items():
            status = info.get("status", "unknown")
            icon   = "✅" if status == "ok" else "⚠️" if status == "degraded" else "❌"
            col_n, col_s, col_u = st.columns([2, 1, 3])
            col_n.markdown(f"**{name.replace('_',' ').title()}**")
            col_s.markdown(f"{icon} `{status}`")
            col_u.code(info.get("url", "—"), language=None)
            if info.get("error"):
                st.caption(f"   Error: {info['error']}")
    else:
        st.error("Cannot reach backend. Ensure docker-compose is running.")

# ── Tab 3: JIT Configuration ──────────────────────────────────────────────────
with tab_jit:
    st.subheader("Customer JIT Service Configuration")
    st.caption("Displays the current runtime configuration of the Customer JIT (System Under Test).")
    if st.button("🔄 Fetch JIT Config", key="jit_config_fetch"):
        # Call JIT config directly via orchestrator health-all (it has the JIT URL)
        # We'll proxy via a dedicated path
        pass

    # Try to get JIT config by calling the backend (which knows the JIT URL).
    # We use the /environments/active endpoint to find the jit url, then call it.
    active_env = api_get("/environments/active") or {}
    jit_base = active_env.get("customer_jit_url") or "http://customer_jit:8001"

    import requests as _req
    try:
        jit_cfg_resp = _req.get(f"{jit_base}/config", timeout=3)
        jit_cfg = jit_cfg_resp.json()
    except Exception:
        jit_cfg = None

    if jit_cfg:
        c1, c2 = st.columns(2)
        c1.metric("Approval Limit",
                  jit_cfg.get("approval_limit_display", f"${jit_cfg.get('approval_limit_cents',0)/100:.2f}"))
        c2.metric("Seen Transactions", jit_cfg.get("seen_transactions", 0))
        blocked = jit_cfg.get("blocked_mccs", [])
        daily   = jit_cfg.get("daily_limit_cents", 0)
        vel     = jit_cfg.get("velocity_max_txn", 0)
        c3, c4 = st.columns(2)
        c3.markdown(f"**Blocked MCCs:** {', '.join(blocked) if blocked else '(none)'}")
        c3.markdown(f"**Daily Limit:** {'Disabled' if daily == 0 else f'${daily/100:.2f}'}")
        c4.markdown(f"**Velocity Max Txn:** {'Disabled' if vel == 0 else str(vel)}")

        st.markdown("---")
        st.caption(
            "ℹ️ To change these limits, set environment variables on the `customer_jit` service "
            "in `docker-compose.yml` (`APPROVAL_LIMIT_CENTS`, `BLOCKED_MCCS`, "
            "`DAILY_LIMIT_CENTS`, `VELOCITY_MAX_TXN`) and restart the container."
        )
    else:
        st.warning(
            f"Could not reach JIT service at `{jit_base}/config`. "
            "Is the customer_jit container running?"
        )

# ── Tab 4: Reset / Flush ──────────────────────────────────────────────────────
with tab_reset:
    st.subheader("Reset & Flush")
    st.caption(
        "Reset the Customer JIT in-memory state (seen transactions, daily spend, velocity counters). "
        "Use this before running a fresh test suite."
    )
    col_r1, col_r2 = st.columns(2)
    if col_r1.button("🔄 Reset Customer JIT", type="primary", key="reset_jit_btn"):
        result = api_post("/reset", {})
        if result and result.get("status") == "ok":
            st.success("Customer JIT state cleared.")
        else:
            st.error(f"Reset failed: {(result or {}).get('detail','unknown error')}")

    st.markdown("---")
    st.warning(
        "⚠️ **Note:** This resets only the in-memory state of the Customer JIT service "
        "(deduplication set, velocity counters, daily spend). "
        "SQLite transaction history is preserved."
    )
