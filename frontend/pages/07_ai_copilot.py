# frontend/pages/07_ai_copilot.py
"""AI Copilot — Claude-powered scenario generation, anomaly explanation, coverage advisory."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state

init_session_state()
st.set_page_config(page_title="e2MS — AI Copilot", page_icon="🤖", layout="wide")

st.title("🤖 AI Copilot")
st.caption(
    "Powered by Claude · Generate test scenarios in plain English, explain failures, "
    "get coverage gap analysis, and receive executive suite summaries."
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_gen, tab_explain, tab_coverage, tab_insights = st.tabs([
    "✨ Generate Scenario",
    "🔍 Explain Failure",
    "📊 Coverage Advisor",
    "📋 Suite Insights",
])

# ── Tab 1: Generate Scenario ──────────────────────────────────────────────────
with tab_gen:
    st.subheader("✨ Generate Scenario from Natural Language")
    st.caption(
        "Describe what you want to test in plain English. "
        "Claude will generate a valid JSON scenario and save it."
    )

    examples = [
        "A $75 contactless purchase at a gas station that should be declined due to exceeding the funding limit",
        "A chip+PIN $25 ATM cash withdrawal that is approved",
        "A $0 account verification for a new card",
        "A EUR 50 online purchase from France using a Visa card",
        "A refund for a previously approved $30 grocery purchase",
    ]
    st.caption("**Example prompts:**")
    for ex in examples:
        st.markdown(f"- *{ex}*")

    user_prompt = st.text_area(
        "Describe the test scenario", height=100, key="ai_gen_prompt",
        placeholder="e.g. A $150 purchase at a casino (MCC 7995) that should be declined as the MCC is blocked",
    )
    col_g1 = st.container() 
    
    if col_g1.button("✨ Generate with Claude", type="primary", key="ai_gen_btn"):
        if not user_prompt.strip():
            st.warning("Please enter a scenario description.")
        else:
            with st.spinner("Asking Claude to generate your scenario…"):
                result = api_post(
    "/ai/run_test",
    {
        "description": user_prompt
    }
)
            if result and "error" not in result:
                st.session_state.ai_last_run = result
                st.success(
    "Test completed successfully."
)
            else:
                err = (result or {}).get("error", "AI service unavailable — check ANTHROPIC_API_KEY")
                st.error(err)

    run = st.session_state.get(
        "ai_last_run"
    )

    if run and "error" not in run:

        st.subheader(
            "Generated Scenario"
        )

        st.json(
            run["scenario"]
        )

        st.subheader(
            "Execution Result"
        )

        execution = run[
            "execution_result"
        ]

        st.json(
            execution
        )

        passed = execution.get(
            "passed",
            False
        )

        if passed:

            st.success(
                "✅ Scenario Passed"
            )

        else:

            st.error(
                "❌ Scenario Failed"
            )

        st.subheader(
            "AI Analysis Report"
        )

        st.json(
            run["analysis"]
        )
   
# ── Tab 2: Explain Failure ────────────────────────────────────────────────────
with tab_explain:
    st.subheader("🔍 Explain a Test Failure")
    st.caption(
        "Paste or load a failed scenario trace. "
        "Claude analyses the audit trail and explains the root cause."
    )

    trace = st.session_state.last_trace
    if trace and not trace.get("passed", True):
        st.info("🔎 Last failed trace auto-loaded from session.")
        pre_fill_exp_rc  = trace.get("expected_network_response_code", "")
        pre_fill_act_rc  = trace.get("actual_network_response_code", "")
        pre_fill_exp_dec = trace.get("expected_customer_decision", "")
        pre_fill_act_dec = trace.get("actual_customer_decision", "")
        pre_fill_audit   = trace.get("audit_trail", [])
        pre_fill_name    = trace.get("scenario_name", "")
    else:
        pre_fill_exp_rc = pre_fill_act_rc = ""
        pre_fill_exp_dec = pre_fill_act_dec = ""
        pre_fill_audit = []
        pre_fill_name = ""

    import json
    e1, e2 = st.columns(2)
    exp_rc  = e1.text_input("Expected RC",  value=pre_fill_exp_rc,  key="ex_exp_rc")
    act_rc  = e2.text_input("Actual RC",    value=pre_fill_act_rc,  key="ex_act_rc")
    exp_dec = e1.text_input("Expected Decision", value=pre_fill_exp_dec, key="ex_exp_dec")
    act_dec = e2.text_input("Actual Decision",   value=pre_fill_act_dec, key="ex_act_dec")
    audit_raw = st.text_area(
        "Audit Trail (JSON array)",
        value=json.dumps(pre_fill_audit, indent=2) if pre_fill_audit else "[]",
        height=180, key="ex_audit"
    )
    sc_name = st.text_input("Scenario Name (optional)", value=pre_fill_name, key="ex_sc_name")

    if st.button("🔍 Analyse with Claude", type="primary", key="ai_explain_btn"):
        try:
            audit_parsed = json.loads(audit_raw)
        except Exception:
            audit_parsed = []
        with st.spinner("Analysing failure…"):
            result = api_post("/ai/explain_failure", {
                "scenario_name":     sc_name,
                "expected_rc":       exp_rc,
                "actual_rc":         act_rc,
                "expected_decision": exp_dec,
                "actual_decision":   act_dec,
                "audit_trail":       audit_parsed,
            })
        if result and "error" not in result:
            st.session_state.ai_last_explanation = result.get("explanation", "")
            st.success("Analysis complete.")
        else:
            st.error((result or {}).get("error", "AI service unavailable"))

    if st.session_state.ai_last_explanation:
        with st.expander("📝 Claude's Analysis", expanded=True):
            st.markdown(st.session_state.ai_last_explanation)

# ── Tab 3: Coverage Advisor ───────────────────────────────────────────────────
with tab_coverage:
    st.subheader("📊 RC Coverage Gap Advisor")
    st.caption(
        "Claude analyses your current RC coverage and recommends new test scenarios "
        "to fill the gaps against the ISO 8583 standard."
    )

    rc_data = api_get("/analytics/rc_coverage") or []
    if rc_data:
        covered_rcs = [r["rc"] for r in rc_data]
        import pandas as pd
        df_rc = pd.DataFrame(rc_data)
        st.dataframe(df_rc, use_container_width=True, hide_index=True)
    else:
        covered_rcs = []
        st.info("No RC coverage data yet — run some scenarios first.")

    if st.button("🤖 Get Coverage Recommendations", type="primary", key="ai_cov_btn"):
        with st.spinner("Analysing coverage gaps…"):
            result = api_post("/ai/coverage_advisor", {"covered_rcs": covered_rcs})
        if result and "error" not in result:
            st.markdown("---")
            st.markdown("### 🎯 Claude's Recommendations")
            st.markdown(result.get("recommendations", ""))
        else:
            st.error((result or {}).get("error", "AI service unavailable"))

# ── Tab 4: Suite Insights ─────────────────────────────────────────────────────
with tab_insights:
    st.subheader("📋 Suite Insights — Executive Summary")
    st.caption(
        "Paste a suite result or load the last run. "
        "Claude provides a concise executive summary with risk assessment."
    )

    sr = st.session_state.suite_result
    if sr:
        st.info("Last suite result auto-loaded from session.")
        if st.button("✨ Summarise with Claude", type="primary", key="ai_insights_btn"):
            with st.spinner("Generating insights…"):
                result = api_post("/ai/suite_insights", {"suite_result": sr})
            if result and "error" not in result:
                st.markdown("---")
                st.markdown(result.get("insights", ""))
            else:
                st.error((result or {}).get("error", "AI service unavailable"))
    else:
        st.info("No suite result in session. Run a suite in **Suite Runner** first.")

    st.markdown("---")
    st.caption(
        "💡 **Tip:** AI features require the `ANTHROPIC_API_KEY` environment variable "
        "to be set on the backend service. Without it, these features return graceful errors."
    )
