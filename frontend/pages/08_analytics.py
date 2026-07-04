# frontend/pages/08_analytics.py
"""Analytics — RC coverage matrix, latency waterfall, daily trends."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get
from utils.session_state import init_session_state
from utils.theme import inject_theme

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — Analytics", page_icon="📊", layout="wide")
inject_theme()

st.title("📊 Analytics")

try:
    import pandas as pd
    import altair as alt
    _charting_ok = True
except ImportError:
    _charting_ok = False

# ── Summary KPIs ──────────────────────────────────────────────────────────────
summary = api_get("/analytics/summary") or {}
st.subheader("📈 All-Time Summary")
if summary:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Transactions",  summary.get("total_transactions", 0))
    c2.metric("Passed",              summary.get("total_passed", 0))
    c3.metric("Failed",              summary.get("total_failed", 0))
    c4.metric("Pass Rate",           f"{summary.get('pass_rate_pct', 0):.1f}%")
    c5.metric("Avg Latency",         f"{summary.get('avg_latency_ms', 0):.0f} ms")
    c6.metric("RC Codes Covered",    summary.get("rc_codes_covered", 0))
else:
    st.info("No data yet — run some scenarios to populate analytics.")

st.markdown("---")

# ── RC Coverage Matrix ────────────────────────────────────────────────────────
st.subheader("🎯 Response Code Coverage Matrix")
RC_DESCRIPTIONS = {
    "00": "Approved",
    "05": "Do Not Honor (generic decline)",
    "51": "Insufficient Funds",
    "54": "Expired Card",
    "57": "Transaction Not Permitted to Cardholder",
    "61": "Exceeds Withdrawal Amount Limit",
    "62": "Restricted Card",
    "65": "Exceeds Withdrawal Frequency Limit",
    "75": "Allowable PIN Tries Exceeded",
    "76": "Duplicate Transaction",
    "91": "Issuer Unavailable",
    "96": "System Error",
}
ALL_TARGET_RCS = list(RC_DESCRIPTIONS.keys())

rc_data = api_get("/analytics/rc_coverage") or []
if rc_data and _charting_ok:
    covered_map = {r["rc"]: r for r in rc_data}
    matrix_rows = []
    for rc in ALL_TARGET_RCS:
        info = covered_map.get(rc, {})
        tested = rc in covered_map
        matrix_rows.append({
            "RC":          rc,
            "Description": RC_DESCRIPTIONS.get(rc, "—"),
            "Tested":      "✅" if tested else "⬜",
            "Total":       info.get("total", 0),
            "Passed":      info.get("passed", 0),
            "Failed":      info.get("failed", 0),
            "Pass Rate":   f"{info.get('passed',0)/info.get('total',1)*100:.0f}%" if tested else "—",
        })
    df_matrix = pd.DataFrame(matrix_rows)
    st.dataframe(df_matrix, use_container_width=True, hide_index=True)

    covered_count = len(covered_map)
    total_target  = len(ALL_TARGET_RCS)
    pct = covered_count / total_target * 100
    color = "#28a745" if pct >= 80 else "#fd7e14" if pct >= 50 else "#dc3545"
    st.markdown(
        f'<div style="background:{color};color:#fff;padding:8px 16px;border-radius:6px;'
        f'display:inline-block;font-weight:bold">'
        f'RC Coverage: {covered_count}/{total_target} ({pct:.0f}%)</div>',
        unsafe_allow_html=True,
    )

    # Bar chart: pass/fail per RC
    if rc_data:
        chart_rows = []
        for r in rc_data:
            chart_rows.append({"RC": r["rc"], "Count": r.get("passed", 0), "Result": "Passed"})
            chart_rows.append({"RC": r["rc"], "Count": r.get("failed", 0), "Result": "Failed"})
        df_chart = pd.DataFrame(chart_rows)
        chart = alt.Chart(df_chart).mark_bar().encode(
            x=alt.X("RC:N", title="Response Code"),
            y=alt.Y("Count:Q", title="Count"),
            color=alt.Color("Result:N", scale=alt.Scale(
                domain=["Passed", "Failed"], range=["#28a745", "#dc3545"])),
            tooltip=["RC", "Result", "Count"],
        ).properties(title="Pass / Fail by RC", height=280)
        st.altair_chart(chart, use_container_width=True)
else:
    if not _charting_ok:
        st.warning("Install `pandas` and `altair` for charts: `pip install pandas altair`")
    else:
        st.info("No RC coverage data yet.")

st.markdown("---")

# ── Latency Chart ─────────────────────────────────────────────────────────────
st.subheader("⏱️ Latency Waterfall (recent transactions)")
lat_limit = st.slider("Show last N transactions", 10, 100, 50, 10, key="lat_slider")
latency_data = api_get("/analytics/latency", params={"limit": lat_limit}) or []
if latency_data and _charting_ok:
    df_lat = pd.DataFrame(latency_data)
    df_lat["Passed"] = df_lat["passed"].map({True: "Passed", False: "Failed"})
    df_lat["Index"]  = range(len(df_lat))
    chart_lat = alt.Chart(df_lat).mark_bar().encode(
        x=alt.X("Index:O", title="Transaction (newest first)", axis=alt.Axis(labels=False)),
        y=alt.Y("duration_ms:Q", title="Duration (ms)"),
        color=alt.Color("Passed:N", scale=alt.Scale(
            domain=["Passed", "Failed"], range=["#28a745", "#dc3545"])),
        tooltip=["scenario_name", "duration_ms", "actual_rc", "Passed", "timestamp"],
    ).properties(title="Transaction Latency (ms)", height=280)
    st.altair_chart(chart_lat, use_container_width=True)

    avg = df_lat["duration_ms"].mean()
    p95 = df_lat["duration_ms"].quantile(0.95)
    mx  = df_lat["duration_ms"].max()
    la1, la2, la3 = st.columns(3)
    la1.metric("Avg Latency",  f"{avg:.0f} ms")
    la2.metric("p95 Latency",  f"{p95:.0f} ms")
    la3.metric("Max Latency",  f"{mx:.0f} ms")
else:
    st.info("No latency data yet — run some scenarios.")

st.markdown("---")

# ── Daily Trends ──────────────────────────────────────────────────────────────
st.subheader("📅 Daily Pass / Fail Trends")
trend_days = st.selectbox("Lookback window", [7, 14, 30], index=0, key="trend_days")
trend_data = api_get("/analytics/trends", params={"days": trend_days}) or []
if trend_data and _charting_ok:
    df_trend = pd.DataFrame(trend_data)
    df_melt = df_trend.melt(id_vars="date", value_vars=["passed", "failed"],
                             var_name="Result", value_name="Count")
    df_melt["Result"] = df_melt["Result"].str.capitalize()
    chart_trend = alt.Chart(df_melt).mark_area(opacity=0.7).encode(
        x=alt.X("date:T", title="Date"),
        y=alt.Y("Count:Q", title="Transactions", stack=None),
        color=alt.Color("Result:N", scale=alt.Scale(
            domain=["Passed", "Failed"], range=["#28a745", "#dc3545"])),
        tooltip=["date", "Result", "Count"],
    ).properties(title=f"Daily Trends (last {trend_days} days)", height=280)
    st.altair_chart(chart_trend, use_container_width=True)
else:
    st.info("No trend data yet.")

st.markdown("---")

# ── Refresh button ────────────────────────────────────────────────────────────
if st.button("🔄 Refresh All Analytics", key="analytics_refresh"):
    st.rerun()
