# frontend/pages/01_home.py
"""Home Dashboard — live KPIs, service health, quick-run, recent history."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state

init_session_state()
st.set_page_config(page_title="e2MS — Home", page_icon="🏠", layout="wide")

st.title("🏠 e2MS — Marqeta E2E Simulator")
st.caption("Enterprise-grade end-to-end issuer processor test platform")

# ── Service Health ────────────────────────────────────────────────────────────
st.subheader("🟢 Service Health")
health = api_get("/health/all")
if health:
    cols = st.columns(len(health.get("services", {})))
    for col, (name, info) in zip(cols, health["services"].items()):
        icon = "✅" if info["status"] == "ok" else "⚠️" if info["status"] == "degraded" else "❌"
        col.metric(name.replace("_", " ").title(), f"{icon} {info['status'].upper()}")
else:
    st.warning("Could not reach backend — is docker-compose up?")

st.markdown("---")

# ── Analytics Summary ────────────────────────────────────────────────────────
st.subheader("📊 All-Time Stats")
summary = api_get("/analytics/summary")
if summary:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Transactions",  summary.get("total_transactions", 0))
    c2.metric("Passed",              summary.get("total_passed", 0))
    c3.metric("Failed",              summary.get("total_failed", 0))
    c4.metric("Pass Rate",           f"{summary.get('pass_rate_pct', 0):.1f}%")
    c5.metric("Avg Latency",         f"{summary.get('avg_latency_ms', 0):.0f} ms")
    c6.metric("RC Codes Covered",    summary.get("rc_codes_covered", 0))
else:
    st.info("No analytics yet — run some scenarios first.")

st.markdown("---")

# ── Quick-Run ────────────────────────────────────────────────────────────────
st.subheader("⚡ Quick-Run Scenario")
scenarios_resp = api_get("/scenarios", params={"limit": 200})
scenarios = (scenarios_resp or {}).get("items", [])
if scenarios:
    scenario_map = {s["id"]: f"{s['name']} ({s['event_type']})" for s in scenarios}
    col_a, col_b = st.columns([3, 1])
    sel = col_a.selectbox("Pick a scenario", list(scenario_map.keys()),
                          format_func=lambda k: scenario_map[k], key="home_quick_run")
    if col_b.button("▶ Run", type="primary", key="home_run_btn"):
        with st.spinner("Executing…"):
            trace = api_post(f"/execute/{sel}")
        if trace and "error" not in trace:
            st.session_state.last_trace = trace
            ok = trace.get("passed", False)
            rc = trace.get("actual_network_response_code", "?")
            dec = trace.get("actual_customer_decision", "?")
            if ok:
                st.success(f"✅ PASSED — RC: {rc} | Decision: {dec} | {trace.get('duration_ms')} ms")
            else:
                exp_rc = trace.get("expected_network_response_code", "?")
                st.error(f"❌ FAILED — Expected RC: {exp_rc} | Got RC: {rc} | Decision: {dec}")
        else:
            st.error(f"Execution error: {(trace or {}).get('error', 'unknown')}")
else:
    st.info("No scenarios found. Go to Scenario Lab to generate one.")

st.markdown("---")

# ── Recent History ────────────────────────────────────────────────────────────
st.subheader("📜 Recent History")
hist = api_get("/history", params={"page": 1, "limit": 10})
items = (hist or {}).get("items", [])
total = (hist or {}).get("total", 0)
if items:
    st.caption(f"Showing last 10 of {total} transactions")
    for row in items:
        ok = row.get("passed", False)
        icon = "✅" if ok else "❌"
        ts = (row.get("timestamp") or "")[:19].replace("T", " ")
        rc = row.get("actual_rc") or row.get("actual_network_response_code", "?")
        dec = row.get("actual_decision") or row.get("actual_customer_decision", "?")
        name = row.get("scenario_name") or row.get("scenario_id", "unknown")
        dur = row.get("duration_ms") or 0
        st.markdown(
            f"{icon} **{name}** &nbsp;|&nbsp; RC `{rc}` | {dec} | {dur:.0f} ms &nbsp;|&nbsp; _{ts}_"
        )
else:
    st.info("No history yet.")

if total > 10:
    st.caption(f"→ View full paginated history in the **Scenario Lab** page")
