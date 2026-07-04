# frontend/pages/07_ai_copilot.py
"""AI Copilot — Claude-powered scenario generation, anomaly explanation, coverage advisory."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme, provider_badge_html

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — AI Copilot", page_icon="🤖", layout="wide")
inject_theme()
# ── AI provider status badge ─────────────────────────────────────────────────
_providers_resp = (lambda r: r if r else {})((__import__('utils.api_client', fromlist=['api_get']).api_get('/ai/providers')))
_primary = _providers_resp.get('primary', 'claude')
_pmap = {p['provider']: p for p in _providers_resp.get('providers', [])}
_detected = (_pmap.get(_primary) or {}).get('key_status') == 'detected'
st.sidebar.markdown(provider_badge_html(_primary, _detected), unsafe_allow_html=True)

st.title("🤖 AI Copilot")
st.caption(
    "Powered by Claude · Generate test scenarios in plain English, explain failures, "
    "get coverage gap analysis, and receive executive suite summaries."
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_gen, tab_explain, tab_coverage, tab_insights, tab_mandate = st.tabs([
    "✨ Generate Scenario",
    "🔍 Explain Failure",
    "📊 Coverage Advisor",
    "📋 Suite Insights",
    "📋 Mandate → Impl",
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
        "to be set on the backend service. Without it, these features return graceful errors. "
        "You can also set the key via **AI Settings** (page 11)."
    )

# ── Tab 5: Mandate → Implementation ──────────────────────────────────────────
with tab_mandate:
    st.subheader("📋 Mandate → Implementation")
    st.caption(
        "Paste a network mandate excerpt (e.g. Visa bulletin text). "
        "Claude will propose **ISO-mapper additions**, **JPF/DB fields**, and **test scenarios**. "
        "You review the diff — then click **Apply** to write the spec change and save the scenarios. "
        "**No silent auto-apply** — every change requires explicit confirmation."
    )

    m_col1, m_col2 = st.columns([2, 1])

    with m_col1:
        mandate_text = st.text_area(
            "Paste mandate excerpt here",
            height=200,
            key="mandate_text",
            placeholder=(
                "Example:\n"
                "Visa mandate VBS-2024-07: Effective April 2025, acquirers processing "
                "contactless mobile wallet transactions must populate DE 104 "
                "(Transaction Category Code) with value '01' for Apple Pay, '02' for "
                "Google Pay, and '03' for Samsung Pay. The field is alphanumeric AN-2."
            ),
        )
        mandate_network = st.selectbox(
            "Target network",
            ["visa", "mastercard", "amex", "discover"],
            key="mandate_network",
        )

        if st.button("🤖 Analyze Mandate with Claude", type="primary", key="mandate_analyze_btn"):
            if not mandate_text.strip():
                st.warning("Please paste a mandate excerpt.")
            else:
                with st.spinner("Analyzing mandate…"):
                    result = api_post("/ai/mandate", {
                        "mandate_text": mandate_text,
                        "network":      mandate_network,
                    })
                if result and "error" not in result:
                    st.session_state["mandate_proposal"] = result
                    st.session_state["mandate_network"]  = mandate_network
                    st.success("✅ Analysis complete — review the proposal below.")
                else:
                    st.error((result or {}).get("error", "AI service unavailable"))

    with m_col2:
        st.markdown("**Example mandates to try:**")
        st.markdown("""
- *Wallet indicator DE104: Apple Pay=01, Google Pay=02*
- *Mastercard mandate: populate DE48.SE43 for recurring transactions*
- *Amex mandate: DE47 must carry ANS-2 service code for airline MCCs 4511/4512*
- *Discover: DE62 must include cashback amount when MCC=6011*
""")

    # ── Proposal display + review gate ────────────────────────────────────────
    proposal = st.session_state.get("mandate_proposal")
    if proposal:
        st.markdown("---")
        st.subheader("📝 AI Proposal — Review Required")

        # Validation status
        validation = proposal.get("_validation", {})
        val_errors = validation.get("errors", [])
        if val_errors:
            st.error(f"⚠️ **{len(val_errors)} validation error(s) — CANNOT APPLY until fixed:**")
            for err in val_errors:
                st.markdown(f"- ❌ {err}")
        else:
            st.success("✅ Proposal passed all validation guardrails.")

        # Design summary
        st.markdown(f"**Design summary:** {proposal.get('design_summary', '—')}")
        if proposal.get("validation_notes"):
            st.info(f"ℹ️ {proposal['validation_notes']}")

        # Tabs for each section
        p_tabs = st.tabs([
            "🗂️ ISO Mapper Additions",
            "📊 JPF Fields",
            "🗄️ DB Columns",
            "🧪 Test Scenarios",
        ])

        with p_tabs[0]:
            iso_adds = proposal.get("iso_mapping_additions", [])
            if iso_adds:
                import pandas as pd
                rows = [
                    {
                        "Canonical JPF path": m.get("canonical", ""),
                        "DE": m.get("source", {}).get("de", ""),
                        "Transform": m.get("source", {}).get("transform", "passthrough"),
                        "Network": m.get("network", "all"),
                        "Description": m.get("description", ""),
                    }
                    for m in iso_adds
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                # Show YAML diff
                yaml_preview = "\n".join([
                    f"  - canonical: {m.get('canonical','')}\n"
                    f"    source:\n"
                    f"      de: {m.get('source',{}).get('de','?')}\n"
                    f"      transform: {m.get('source',{}).get('transform','passthrough')}\n"
                    f"    description: \"{m.get('description','')}\""
                    for m in iso_adds
                ])
                with st.expander("YAML diff (will be appended to spec file)", expanded=True):
                    st.code(yaml_preview, language="yaml")
            else:
                st.caption("No ISO mapper additions proposed.")

        with p_tabs[1]:
            jpf_fields = proposal.get("jpf_fields", [])
            if jpf_fields:
                import pandas as pd
                st.dataframe(pd.DataFrame(jpf_fields), use_container_width=True, hide_index=True)
            else:
                st.caption("No new JPF fields proposed.")

        with p_tabs[2]:
            db_cols = proposal.get("db_columns", [])
            if db_cols:
                import pandas as pd
                st.dataframe(pd.DataFrame(db_cols), use_container_width=True, hide_index=True)
            else:
                st.caption("No new DB columns proposed.")

        with p_tabs[3]:
            scenarios = proposal.get("scenarios", [])
            if scenarios:
                for i, sc in enumerate(scenarios):
                    with st.expander(f"Scenario {i+1}: {sc.get('name', sc.get('id','?'))}", expanded=(i==0)):
                        st.json(sc)
            else:
                st.caption("No test scenarios proposed.")

        # ── Apply gate ────────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("✅ Apply Mandate Changes")

        if val_errors:
            st.error(
                "Cannot apply — proposal has validation errors. "
                "The AI proposal may have used an invalid DE number or non-test PAN. "
                "Edit the proposal or re-run the analysis with a clearer mandate excerpt."
            )
        else:
            st.warning(
                "⚠️ **Review carefully before applying.** This will append YAML entries to "
                f"`backend/mapping/specs/{st.session_state.get('mandate_network','visa')}.yaml` "
                "and save the test scenarios. This action cannot be automatically undone."
            )
            confirm = st.checkbox(
                "I have reviewed the proposal above and confirm it is correct",
                key="mandate_confirm",
            )
            if st.button("🚀 Apply Mandate Changes", type="primary", key="mandate_apply_btn",
                         disabled=not confirm):
                with st.spinner("Applying…"):
                    apply_result = api_post("/ai/mandate/apply", {
                        "proposal":  proposal,
                        "network":   st.session_state.get("mandate_network", "visa"),
                        "confirmed": True,
                    })
                if apply_result and apply_result.get("status") == "applied":
                    st.success(
                        f"✅ Applied! {apply_result.get('additions_count', 0)} spec additions written. "
                        f"Scenarios saved: {apply_result.get('scenarios_saved', [])}"
                    )
                    with st.expander("Applied YAML diff", expanded=True):
                        st.code(apply_result.get("diff", ""), language="yaml")
                    # Certify: run the mandate scenarios immediately
                    saved_ids = apply_result.get("scenarios_saved", [])
                    if saved_ids:
                        st.markdown("---")
                        st.subheader("🏅 Certify Mandate Scenarios")
                        if st.button("▶ Run mandate scenarios now", key="mandate_certify"):
                            cert_results = []
                            for sc_id in saved_ids:
                                with st.spinner(f"Running {sc_id}…"):
                                    tr = api_post(f"/execute/{sc_id}")
                                if tr:
                                    cert_results.append({
                                        "id":     sc_id,
                                        "passed": tr.get("passed"),
                                        "rc":     tr.get("actual_network_response_code"),
                                    })
                            if all(r.get("passed") for r in cert_results):
                                st.success("✅ All mandate scenarios certified GREEN.")
                            else:
                                st.error("❌ Some scenarios failed — review.")
                            import pandas as pd
                            st.dataframe(pd.DataFrame(cert_results), use_container_width=True)
                else:
                    err_msg = (apply_result or {}).get("reason") or (apply_result or {}).get("error", "Unknown error")
                    st.error(f"Apply failed: {err_msg}")
                    if (apply_result or {}).get("errors"):
                        for e in apply_result["errors"]:
                            st.markdown(f"- ❌ {e}")
