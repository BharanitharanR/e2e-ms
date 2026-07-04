# frontend/pages/09_certification.py
"""Certification — certify an active SUT with a branded downloadable report."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme
from utils.html_report import build_certification_report, build_certification_pdf

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — Certification", page_icon="🏅", layout="wide")
inject_theme()

st.title("🏅 SUT Certification")

# ── Active environment banner ──────────────────────────────────────────────────
active_env = api_get("/environments/active") or {}
if "error" in active_env:
    st.warning("⚠️ No active environment. Configure one in **Sandbox Config** before certifying.")
    st.stop()

env_col1, env_col2 = st.columns([3, 1])
env_col1.info(
    f"🎯 **Active SUT:** {active_env.get('name', '—')}  |  "
    f"`{active_env.get('api_url', '—')}`  |  "
    f"JIT: `{active_env.get('customer_jit_url', '—')}`"
)

# ── Controls ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Certification Settings")
    threshold = st.slider(
        "Pass-rate threshold (%)",
        min_value=50, max_value=100, value=95, step=5,
        help="Minimum score required for a CERTIFIED verdict",
        key="cert_threshold",
    )
    reset_jit = st.checkbox("Reset JIT state before run", value=True, key="cert_reset")
    st.markdown("---")
    st.caption(
        "The certification suite runs the full RC matrix (RC 51/54/57/61/62/65/75/91/96) "
        "plus the lifecycle happy paths (auth approve/decline, advice, refund, reversal)."
    )

run_cert = env_col2.button("🏅 Certify this SUT", type="primary", key="cert_run_btn",
                            use_container_width=True)

if run_cert:
    with st.spinner("Running certification suite against the active SUT…"):
        cert_result = api_post("/certify", {"threshold": threshold, "reset": reset_jit})
    if cert_result and "error" not in cert_result:
        st.session_state["cert_result"] = cert_result
    else:
        st.error(f"Certification failed: {(cert_result or {}).get('error', 'unknown error')}")

# ── Display results ────────────────────────────────────────────────────────────
cert = st.session_state.get("cert_result")
if cert:
    coverage  = cert.get("coverage", {})
    certified = cert.get("certified", False)
    score     = coverage.get("score", 0)
    passed_n  = coverage.get("passed_scenarios", 0)
    total_n   = coverage.get("total_scenarios", 0)

    # Verdict banner
    if certified:
        st.success(f"## ✅ CERTIFIED — Score: {score}% ({passed_n}/{total_n} passed)")
    else:
        st.error(f"## ❌ NOT CERTIFIED — Score: {score}% ({passed_n}/{total_n} passed, "
                 f"need {cert.get('threshold')}%)")

    # Scorecard metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Score",   f"{score}%")
    m2.metric("Passed",  passed_n)
    m3.metric("Failed",  total_n - passed_n)
    m4.metric("Total",   total_n)
    m5.metric("Threshold", f"{cert.get('threshold')}%")

    # Coverage pills
    col_lc, col_rc = st.columns(2)
    with col_lc:
        st.markdown("**Lifecycle events covered:**")
        evts = coverage.get("lifecycle_events_covered", [])
        if evts:
            st.markdown(" ".join(
                f'<span style="background:#d4edda;color:#155724;padding:3px 10px;'
                f'border-radius:20px;font-size:.85em;font-weight:600">{e}</span>'
                for e in evts
            ), unsafe_allow_html=True)
        else:
            st.caption("None")

    with col_rc:
        st.markdown("**RC codes covered:**")
        rcs = coverage.get("rc_codes_covered", [])
        if rcs:
            st.markdown(" ".join(
                f'<span style="background:#cce5ff;color:#004085;padding:3px 10px;'
                f'border-radius:20px;font-size:.85em;font-weight:600">RC {r}</span>'
                for r in rcs
            ), unsafe_allow_html=True)
        else:
            st.caption("None")

    st.markdown("---")

    # Per-scenario results table
    st.subheader("📋 Per-Scenario Results")
    import pandas as pd
    rows = []
    for r in cert.get("results", []):
        rows.append({
            "":           "✅" if r.get("passed") else "❌",
            "Scenario":   r.get("name", r.get("scenario_id", "?")),
            "Event":      r.get("event_type", "?"),
            "Exp RC":     r.get("expected_rc", "?"),
            "Act RC":     r.get("actual_rc", "?"),
            "Exp Dec":    r.get("expected_decision", "?"),
            "Act Dec":    r.get("actual_decision", "?"),
            "ms":         f"{r.get('duration_ms') or 0:.0f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # Report download buttons
    st.markdown("---")
    st.subheader("⬇ Download Certification Report")
    dl1, dl2 = st.columns(2)

    html_bytes = build_certification_report(cert).encode("utf-8")
    ts = cert.get("timestamp", "")[:10]
    dl1.download_button(
        "📄 Download HTML Report",
        data=html_bytes,
        file_name=f"certification_{ts}.html",
        mime="text/html",
        key="cert_dl_html",
        use_container_width=True,
    )

    pdf_bytes, mime = build_certification_pdf(cert)
    ext = "pdf" if mime == "application/pdf" else "html"
    dl2.download_button(
        f"📑 Download {'PDF' if ext == 'pdf' else 'HTML (PDF N/A)'} Report",
        data=pdf_bytes,
        file_name=f"certification_{ts}.{ext}",
        mime=mime,
        key="cert_dl_pdf",
        use_container_width=True,
    )

    # ── T0.4 — Inline AI explanation on failed scenarios ───────────────────────
    failures = [r for r in cert.get("results", []) if not r.get("passed")]
    if failures:
        st.markdown("---")
        st.subheader("🤖 AI Failure Analysis")
        st.caption("Get Claude's root-cause analysis for each failing certification scenario:")

        for r in failures:
            with st.expander(f"❌ {r.get('name', r.get('scenario_id', '?'))}", expanded=False):
                c1, c2 = st.columns([3, 1])
                c1.markdown(
                    f"**Expected:** RC `{r.get('expected_rc','?')}` / `{r.get('expected_decision','?')}`  "
                    f"**Got:** RC `{r.get('actual_rc','?')}` / `{r.get('actual_decision','?')}`"
                )
                explain_btn = c2.button("🔍 Explain with AI",
                                        key=f"cert_explain_{r.get('scenario_id','?')}",
                                        use_container_width=True)
                if explain_btn:
                    with st.spinner("Analysing with Claude…"):
                        explanation = api_post("/ai/explain_failure", {
                            "scenario_id":       r.get("scenario_id"),
                            "scenario_name":     r.get("name"),
                            "expected_rc":       r.get("expected_rc"),
                            "actual_rc":         r.get("actual_rc"),
                            "expected_decision": r.get("expected_decision"),
                            "actual_decision":   r.get("actual_decision"),
                            "duration_ms":       r.get("duration_ms"),
                            "audit_trail":       r.get("audit_trail", []),
                        })
                    if explanation and "error" not in explanation:
                        expl = explanation.get("explanation") or json.dumps(explanation, indent=2)
                        st.markdown(expl)
                        # Show structured fields if present
                        for field, label in [
                            ("root_cause",          "🔍 Root Cause"),
                            ("likely_rule_triggered","⚙️ Rule Triggered"),
                            ("suggested_fix",        "🛠️ Suggested Fix"),
                        ]:
                            val = explanation.get(field)
                            if val:
                                st.markdown(f"**{label}:** {val}")
                        conf = explanation.get("confidence")
                        if conf:
                            st.caption(f"Confidence: {conf}")
                    else:
                        err = (explanation or {}).get("error", "AI service unavailable")
                        st.warning(f"⚠️ {err}")
