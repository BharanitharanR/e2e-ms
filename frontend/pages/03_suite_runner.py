# frontend/pages/03_suite_runner.py
"""Suite Runner — run test suites, view history, download reports, AI explain failures."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
from utils.api_client import api_get, api_post, api_get_raw
from utils.session_state import init_session_state
from utils.theme import inject_theme
from utils.html_report import build_html_report, build_junit_xml

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — Suite Runner", page_icon="🧪", layout="wide")
inject_theme()

st.title("🧪 Suite Runner")

# ── Suite catalogue ───────────────────────────────────────────────────────────
suites_data = api_get("/suites") or []
suite_opts  = {s["key"]: f"{s['name']} ({s['scenario_count']} tests)" for s in suites_data}

col_s, col_rst, col_r = st.columns([3, 1, 1])
sel_suite = col_s.selectbox(
    "Test Suite",
    options=list(suite_opts.keys()),
    format_func=lambda k: suite_opts.get(k, k),
    key="suite_picker",
)
reset_before = col_rst.checkbox("Reset JIT before run", value=True, key="suite_reset")
run_suite = col_r.button("▶ Run Suite", type="primary", key="run_suite_btn")

if run_suite:
    with st.spinner("Running suite…"):
        result = api_post("/execute_suite", {"suite_name": sel_suite, "reset_before": reset_before})
    if result:
        st.session_state.suite_result = result
    else:
        st.error("Suite execution failed.")

sr = st.session_state.suite_result
if sr:
    total  = sr.get("total", 0)
    passed = sr.get("passed", 0)
    failed = sr.get("failed", 0)
    dur    = sr.get("duration_ms", 0)
    all_ok = passed == total

    badge_color = "#28a745" if all_ok else "#dc3545"
    badge_text  = "ALL PASS" if all_ok else "FAILURES"
    st.markdown(
        f'### {sr.get("suite_name", "Suite")} '
        f'<span style="background:{badge_color};color:#fff;padding:4px 14px;'
        f'border-radius:6px;font-size:0.9em">{badge_text}</span>',
        unsafe_allow_html=True,
    )
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total",    total)
    k2.metric("Passed",   passed)
    k3.metric("Failed",   failed)
    k4.metric("Duration", f"{dur:.0f} ms")

    # Results table
    df_rows = []
    for r in sr.get("results", []):
        df_rows.append({
            "":           "✅" if r.get("passed") else "❌",
            "Scenario":   r.get("name", r.get("scenario_id", "?")),
            "Exp RC":     r.get("expected_network_response_code", "?"),
            "Act RC":     r.get("actual_network_response_code", "?"),
            "Exp Dec":    r.get("expected_customer_decision", "?"),
            "Act Dec":    r.get("actual_customer_decision", "?"),
            "ms":         f"{r.get('duration_ms') or 0:.0f}",
        })
    st.dataframe(pd.DataFrame(df_rows), use_container_width=True)

    # Download buttons
    dl1, dl2 = st.columns(2)
    html_report = build_html_report(sr)
    dl1.download_button(
        "⬇ HTML Report", data=html_report,
        file_name=f"suite_{sr.get('run_at','')[:10]}.html",
        mime="text/html", key="dl_html",
    )
    junit_xml = build_junit_xml(sr)
    dl2.download_button(
        "⬇ JUnit XML", data=junit_xml,
        file_name=f"suite_{sr.get('run_at','')[:10]}.xml",
        mime="application/xml", key="dl_junit",
    )

    # AI explanations for failures
    failures = [r for r in sr.get("results", []) if not r.get("passed")]
    if failures:
        st.markdown("---")
        st.subheader("🤖 AI Failure Explanations")
        ai_suite_btn = st.button("✨ Get AI Suite Insights", key="ai_suite_btn")
        if ai_suite_btn:
            with st.spinner("Asking Claude…"):
                insights = api_post("/ai/suite_insights", {"suite_result": sr})
            if insights and "error" not in insights:
                st.markdown(insights.get("insights", ""))
            else:
                st.warning(insights.get("error", "AI unavailable") if insights else "AI unavailable")

        st.caption("Click a failing scenario to get Claude's root-cause analysis:")
        for r in failures:
            with st.expander(f"❌ {r.get('name', r.get('scenario_id','?'))}", expanded=False):
                exp_btn = st.button("🔍 Explain Failure", key=f"explain_{r.get('scenario_id','?')}")
                if exp_btn:
                    with st.spinner("Analysing…"):
                        explanation = api_post("/ai/explain_failure", {
                            "scenario_id":   r.get("scenario_id"),
                            "scenario_name": r.get("name"),
                            "expected_rc":   r.get("expected_network_response_code"),
                            "actual_rc":     r.get("actual_network_response_code"),
                            "expected_decision": r.get("expected_customer_decision"),
                            "actual_decision":   r.get("actual_customer_decision"),
                            "audit_trail":   r.get("audit_trail", []),
                        })
                    if explanation and "error" not in explanation:
                        st.markdown(explanation.get("explanation", ""))
                    else:
                        err = (explanation or {}).get("error", "AI service unavailable")
                        st.warning(err)

st.markdown("---")

# ── Suite Run History ─────────────────────────────────────────────────────────
st.subheader("📜 Suite Run History")
sr_page = st.session_state.get("suite_runs_page", 1)
sr_resp  = api_get("/suite_runs", params={"page": sr_page, "limit": 10})
sr_items = (sr_resp or {}).get("items", [])
sr_total = (sr_resp or {}).get("total", 0)
sr_pages = max(1, (sr_total + 9) // 10)

if sr_items:
    sr_rows = []
    for run in sr_items:
        all_p = run.get("passed", 0) == run.get("total", 0)
        sr_rows.append({
            "":          "✅" if all_p else "❌",
            "Suite":     run.get("suite_name", run.get("suite_key", "?")),
            "Total":     run.get("total", 0),
            "Passed":    run.get("passed", 0),
            "Failed":    run.get("failed", 0),
            "Duration":  f"{run.get('duration_ms') or 0:.0f} ms",
            "Run At":    (run.get("run_at") or "")[:19].replace("T", " "),
        })
    st.dataframe(pd.DataFrame(sr_rows), use_container_width=True)
    pc1, pc2, pc3 = st.columns([1, 3, 1])
    if pc1.button("◀ Prev", disabled=(sr_page <= 1), key="sr_prev"):
        st.session_state.suite_runs_page = max(1, sr_page - 1)
        st.rerun()
    pc2.caption(f"Page {sr_page} of {sr_pages} ({sr_total} total runs)")
    if pc3.button("Next ▶", disabled=(sr_page >= sr_pages), key="sr_next"):
        st.session_state.suite_runs_page = sr_page + 1
        st.rerun()
else:
    st.info("No suite runs yet — click ▶ Run Suite above.")
