# frontend/pages/01_home.py
"""Paycon e2ePS — Mission-Control Home.

Live service health · AI provider status · cert readiness gauge ·
all-time stats · one-click demo presets · recent run history.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post, get_api_url
from utils.session_state import init_session_state
from utils.theme import inject_theme, provider_badge_html, chip

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — Home", page_icon="🏠", layout="wide")
inject_theme()

# ── Brand header ───────────────────────────────────────────────────────────────
st.markdown("""
<div class="pc-brand-bar">
  <span style="font-size:1.6em">🏠</span>
  <div>
    <div class="pc-brand-name">Pay<span>con</span> · Mission Control</div>
    <div style="font-size:0.76em;color:#7a9cc0">Live health · stats · one-click demo</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# ROW 1 — Service health + AI provider status
# ─────────────────────────────────────────────────────────────────────────────
col_health, col_ai = st.columns([3, 1])

with col_health:
    st.subheader("🟢 Service Health")
    api_url = get_api_url()
    with st.spinner("Checking services…"):
        health = api_get("/health/all")
    if health and health.get("services"):
        svc_cols = st.columns(len(health["services"]))
        for col, (name, info) in zip(svc_cols, health["services"].items()):
            status = info.get("status", "unknown")
            if status == "ok":
                icon = "✅"
                colour = "#2ecc71"
            elif status == "degraded":
                icon = "⚠️"
                colour = "#f39c12"
            else:
                icon = "❌"
                colour = "#e74c3c"
            tried_url = info.get("url", "")
            tooltip = f"URL: {tried_url}" if tried_url else ""
            col.markdown(
                f'<div class="pc-card" title="{tooltip}">'
                f'<div class="pc-card-title">{name.replace("_"," ").title()}</div>'
                f'<div class="pc-card-value" style="font-size:1.05em;color:{colour}">'
                f'{icon} {status.upper()}</div>'
                + (
                    f'<div style="font-size:0.7em;color:#e74c3c;margin-top:4px;word-break:break-all">'
                    f'UNREACHABLE<br><code>{tried_url}</code></div>'
                    if status == "unreachable" else ""
                )
                + f'</div>',
                unsafe_allow_html=True,
            )
        # Show CTA if any service is unreachable
        unreachable = [n for n, i in health["services"].items() if i.get("status") == "unreachable"]
        if unreachable:
            with st.expander("🛠 Troubleshooting — some services unreachable", expanded=False):
                st.markdown(
                    f"**Backend URL in use:** `{api_url}`\n\n"
                    "**Options:**\n"
                    "- Local (no Docker): `make demo-local`\n"
                    "- Docker Compose: `docker-compose up --build`\n"
                    "- Change URL: go to **⚙️ Sandbox Config** → set API URL"
                )
                st.caption(f"Unreachable: {', '.join(unreachable)}")
    else:
        st.warning(
            f"⚠️ Cannot reach backend at `{api_url}`. "
            "Start: `make demo-local` (no Docker) or `docker-compose up --build`."
        )
        st.caption(
            "Wrong URL? Go to **⚙️ Sandbox Config** and update the API URL, "
            "or set the `API_URL` environment variable."
        )

with col_ai:
    st.subheader("🤖 AI Provider")
    providers_resp = api_get("/ai/providers") or {}
    primary   = providers_resp.get("primary", "claude")
    prov_list = providers_resp.get("providers", [])
    pmap      = {p["provider"]: p for p in prov_list}
    detected  = (pmap.get(primary) or {}).get("key_status") == "detected"
    st.markdown(provider_badge_html(primary, detected), unsafe_allow_html=True)
    if detected:
        st.caption(f"Model: `{(pmap.get(primary) or {}).get('model','—')}`")
    else:
        st.caption("Set a key in **🔑 AI Settings** to enable the Copilot.")

    # P3 T3.2 — jPOS vs pyiso8583 health badge
    st.markdown("---")
    st.markdown("**🔧 ISO Engine**")
    iso_health = api_get("/iso-engine/health")
    if iso_health and iso_health.get("available"):
        resp_data = iso_health.get("response", {})
        version   = resp_data.get("version", "")
        st.markdown(
            f'<span style="background:#1fb7ac;color:#fff;padding:3px 10px;'
            f'border-radius:12px;font-size:0.8em;font-weight:600">'
            f'☕ jPOS (Java){" v" + version if version else ""}</span>',
            unsafe_allow_html=True,
        )
        st.caption("Byte-authentic ISO 8583 packing active.")
    else:
        st.markdown(
            '<span style="background:#7a9cc0;color:#fff;padding:3px 10px;'
            'border-radius:12px;font-size:0.8em;font-weight:600">'
            '🐍 pyiso8583 (fallback)</span>',
            unsafe_allow_html=True,
        )
        st.caption("Run `make iso-engine` to start jPOS sidecar.")

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# ROW 2 — All-time stats + Certification readiness gauge
# ─────────────────────────────────────────────────────────────────────────────
col_stats, col_cert = st.columns([3, 1])

with col_stats:
    st.subheader("📊 All-Time Stats")
    summary = api_get("/analytics/summary")
    if summary:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total Txns",   summary.get("total_transactions", 0))
        c2.metric("Passed",       summary.get("total_passed", 0))
        c3.metric("Failed",       summary.get("total_failed", 0))
        c4.metric("Pass Rate",    f"{summary.get('pass_rate_pct', 0):.1f}%")
        c5.metric("Avg Latency",  f"{summary.get('avg_latency_ms', 0):.0f} ms")
        c6.metric("RC Codes Seen", summary.get("rc_codes_covered", 0))
    else:
        st.markdown(
            '<div class="pc-card" style="text-align:center;padding:24px">'
            '<div style="font-size:2em">📊</div>'
            '<div style="color:#7a9cc0;margin-top:8px">No analytics yet</div>'
            '<div style="font-size:0.82em;color:#4a6a8a;margin-top:4px">'
            'Run a scenario below to start collecting stats.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

with col_cert:
    st.subheader("🏅 Cert Readiness")
    cert = st.session_state.get("cert_result")
    if cert:
        score     = cert.get("coverage", {}).get("score", 0)
        certified = cert.get("certified", False)
        bar_cls   = "" if certified else "fail"
        bar_w     = max(score, 3)
        colour    = "#2ecc71" if certified else "#e74c3c"
        label     = "CERTIFIED" if certified else "NOT CERTIFIED"
        st.markdown(
            f'<div class="pc-card">'
            f'<div class="pc-card-title">Last Cert Run</div>'
            f'<div class="pc-card-value" style="color:{colour}">{score}%</div>'
            f'<div class="pc-gauge-wrap">'
            f'<div class="pc-gauge-bar {bar_cls}" style="width:{bar_w}%"></div></div>'
            f'<div class="pc-card-sub">{label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="pc-card" style="text-align:center;padding:18px">'
            '<div style="font-size:1.6em">🏅</div>'
            '<div style="color:#7a9cc0;font-size:0.82em;margin-top:6px">'
            'No cert run yet.<br>Go to <b>Certification</b> to score your SUT.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# ROW 3 — Quick-Run: use-case presets + freeform scenario selector
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("⚡ Quick-Run")

_PRESETS = [
    ("💳 AUTH Approve",    "authorization_approve"),
    ("❌ AUTH Decline",    "authorization_decline_51"),
    ("🏧 ATM Withdrawal",  "atm_withdrawal_approve"),
    ("🏨 Pre-Auth",        "preauth_approve"),
    ("↩️ Reversal",        "reversal_approve"),
]

preset_cols = st.columns(len(_PRESETS))
for col, (label, sc_id) in zip(preset_cols, _PRESETS):
    if col.button(label, key=f"home_preset_{sc_id}", use_container_width=True):
        with st.spinner(f"Running {label}…"):
            trace = api_post(f"/execute/{sc_id}")
        if trace and "error" not in trace:
            st.session_state.last_trace = trace
            ok  = trace.get("passed", False)
            rc  = trace.get("actual_network_response_code", "?")
            dec = trace.get("actual_customer_decision", "?")
            dur = trace.get("duration_ms", 0)
            if ok:
                st.success(f"✅ **{label}** PASSED — RC `{rc}` | {dec} | {dur:.0f} ms")
            else:
                exp_rc = trace.get("expected_network_response_code", "?")
                st.error(f"❌ **{label}** FAILED — Expected `{exp_rc}` · Got `{rc}` | {dec}")
        else:
            st.error(f"Run error: {(trace or {}).get('error', 'scenario may not exist yet')}")

st.markdown("##### Or pick any scenario:")
with st.spinner("Loading scenarios…"):
    scenarios_resp = api_get("/scenarios", params={"limit": 200})
scenarios = (scenarios_resp or {}).get("items", [])
if scenarios:
    scenario_map = {s["id"]: f"{s['name']} ({s['event_type']})" for s in scenarios}
    col_a, col_b = st.columns([4, 1])
    sel = col_a.selectbox(
        "Scenario", list(scenario_map.keys()),
        format_func=lambda k: scenario_map[k], key="home_quick_run",
        label_visibility="collapsed",
    )
    if col_b.button("▶ Run", type="primary", key="home_run_btn", use_container_width=True):
        with st.spinner("Executing…"):
            trace = api_post(f"/execute/{sel}")
        if trace and "error" not in trace:
            st.session_state.last_trace = trace
            ok  = trace.get("passed", False)
            rc  = trace.get("actual_network_response_code", "?")
            dec = trace.get("actual_customer_decision", "?")
            dur = trace.get("duration_ms", 0)
            if ok:
                st.success(f"✅ PASSED — RC `{rc}` | {dec} | {dur:.0f} ms")
            else:
                exp_rc = trace.get("expected_network_response_code", "?")
                st.error(f"❌ FAILED — Expected `{exp_rc}` · Got `{rc}` | {dec}")
        else:
            st.error(f"Execution error: {(trace or {}).get('error','unknown')}")
else:
    st.markdown(
        '<div class="pc-card" style="text-align:center;padding:24px">'
        '<div style="font-size:2em">🧬</div>'
        '<div style="color:#7a9cc0;margin-top:8px">No scenarios found</div>'
        '<div style="font-size:0.82em;color:#4a6a8a;margin-top:4px">'
        'Go to <b>🧬 Scenario Lab → AI Copilot</b> to generate your first scenario.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# ROW 4 — Recent History
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("📜 Recent Runs")
with st.spinner("Loading history…"):
    hist  = api_get("/history", params={"page": 1, "limit": 10})
items = (hist or {}).get("items", [])
total = (hist or {}).get("total", 0)
if items:
    st.caption(f"Showing last {len(items)} of {total} transactions")
    for row in items:
        ok   = row.get("passed", False)
        icon = "✅" if ok else "❌"
        cls  = "" if ok else "fail"
        ts   = (row.get("timestamp") or "")[:19].replace("T", " ")
        rc   = row.get("actual_rc") or row.get("actual_network_response_code", "?")
        dec  = row.get("actual_decision") or row.get("actual_customer_decision", "?")
        name = row.get("scenario_name") or row.get("scenario_id", "unknown")
        dur  = row.get("duration_ms") or 0
        net  = row.get("network", "")
        net_chip = f"&nbsp;{chip(net, 'blue')}" if net else ""
        st.markdown(
            f'<div class="pc-hist-row {cls}">'
            f'<span style="font-size:1em">{icon}</span>'
            f'<span style="flex:1"><b>{name}</b>{net_chip}</span>'
            f'<span class="pc-mono">RC&nbsp;{rc}</span>'
            f'<span style="color:#7a9cc0;font-size:0.82em">{dec}</span>'
            f'<span style="color:#4a6a8a;font-size:0.78em">{dur:.0f}&nbsp;ms</span>'
            f'<span style="color:#4a6a8a;font-size:0.76em">{ts}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    if total > 10:
        st.caption("→ Full paginated history in **🧬 Scenario Lab**")
else:
    st.markdown(
        '<div class="pc-card" style="text-align:center;padding:24px">'
        '<div style="font-size:2em">📜</div>'
        '<div style="color:#7a9cc0;margin-top:8px">No run history yet</div>'
        '<div style="font-size:0.82em;color:#4a6a8a;margin-top:4px">'
        'Use the Quick-Run presets above to execute your first transaction.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
