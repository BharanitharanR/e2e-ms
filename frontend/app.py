# e2e-marqeta-simulator/frontend/app.py
"""Streamlit UI for the End-to-End Marqeta Transaction Simulator."""
import os
import json
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://backend:8000")

st.set_page_config(page_title="Marqeta E2E Simulator", page_icon="💳", layout="wide")
st.title("End-to-End Marqeta Transaction Simulator")
st.caption("Cardholder → Terminal → Acquirer → Visa → Marqeta → Customer JIT → Response")


def api_get(path):
    try:
        return requests.get(f"{API_URL}{path}", timeout=20).json()
    except Exception as e:  # noqa: BLE001
        st.error(f"GET {path} failed: {e}")
        return None


def api_post(path, body=None):
    try:
        return requests.post(f"{API_URL}{path}", json=body or {}, timeout=30).json()
    except Exception as e:  # noqa: BLE001
        st.error(f"POST {path} failed: {e}")
        return None


# --------------------------------------------------------------------------- #
# Sidebar: scenario selection
# --------------------------------------------------------------------------- #
scenarios = api_get("/scenarios") or []
id_to_scn = {s["id"]: s for s in scenarios}

st.sidebar.header("Scenario")
if scenarios:
    selected_id = st.sidebar.selectbox(
        "Select a scenario",
        options=[s["id"] for s in scenarios],
        format_func=lambda i: id_to_scn[i]["name"],
    )
else:
    selected_id = None
    st.sidebar.warning("No scenarios found. Is the backend running?")

unique_run = st.sidebar.checkbox(
    "Unique transaction id per run", value=True,
    help="Keeps repeated runs green. Uncheck to replay and trigger the "
         "customer's DUPLICATE (idempotency) handling.",
)

if st.sidebar.button("Reset customer state"):
    res = api_post("/reset")
    st.sidebar.success(f"Reset: {res}")

# --------------------------------------------------------------------------- #
# Scenario detail + run
# --------------------------------------------------------------------------- #
if selected_id:
    scn = id_to_scn[selected_id]
    st.subheader(scn["name"])
    st.write(scn.get("description", ""))
    st.caption(f"Event type: **{scn.get('event_type', 'authorization')}**")

    run = st.button("Run End-to-End Transaction", type="primary")

    if run:
        trace = api_post(f"/execute/{selected_id}?unique={'true' if unique_run else 'false'}")
        if trace and "error" not in trace:
            passed = trace.get("passed")
            if passed:
                st.success("PASS  ✅")
            else:
                st.error("FAIL  ❌")

            steps = [
                "Cardholder Tap",
                "Terminal (simulated)",
                "Acquirer",
                "Visa Network",
                "Marqeta Issuer Processor",
                "Customer JIT (System Under Test)",
                "Response back through network",
            ]
            st.markdown("#### Transaction flow")
            for s in steps:
                st.markdown(f"&nbsp;&nbsp;⬇️&nbsp;&nbsp;**{s}**", unsafe_allow_html=True)

            col1, col2, col3 = st.columns(3)
            col1.metric("Network response code",
                        trace.get("actual_network_response_code"),
                        help=f"expected {trace.get('expected_network_response_code')}")
            col2.metric("Customer decision",
                        trace.get("actual_customer_decision"),
                        help=f"expected {trace.get('expected_customer_decision')}")
            col3.metric("Latency (ms)", trace.get("duration_ms"))

            with st.expander("Request sent (to acquirer)"):
                st.json(trace.get("request_sent"))
            with st.expander("Response received (full chain)"):
                st.json(trace.get("response_received"))
        elif trace:
            st.error(trace.get("error"))

# --------------------------------------------------------------------------- #
# Generate scenario
# --------------------------------------------------------------------------- #
with st.expander("➕ Generate a new scenario"):
    g1, g2, g3 = st.columns(3)
    new_id = g1.text_input("Scenario id", value="my_scenario_01")
    event_type = g2.selectbox("Event type",
                              ["authorization", "advice", "refund", "reversal"])
    amount = g3.number_input("Amount (cents)", min_value=0, value=2500, step=100)

    g4, g5, g6 = st.columns(3)
    exp_rc = g4.text_input("Expected network response code", value="00")
    exp_dec = g5.selectbox("Expected customer decision", ["APPROVED", "DECLINED"])
    orig_txn = g6.text_input("Original transaction id (advice/refund/reversal)",
                             value="TXN_AUTH_001")

    if st.button("Generate"):
        payload = {
            "scenario_id": new_id,
            "event_type": event_type,
            "amount": int(amount),
            "expected_response_code": exp_rc,
            "expected_customer_decision": exp_dec,
            "original_transaction_id": orig_txn,
        }
        res = api_post("/generate", payload)
        if res:
            st.success(f"Created {res.get('created')}")
            st.json(res.get("scenario"))
            st.info("Re-open the sidebar dropdown to pick it up.")

# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
st.markdown("---")
st.subheader("Recent runs")
hist = api_get("/history") or []
if hist:
    rows = [
        {
            "passed": "✅" if h.get("passed") else "❌",
            "scenario": h.get("scenario_name"),
            "event": h.get("event_type"),
            "rc": h.get("actual_network_response_code"),
            "decision": h.get("actual_customer_decision"),
            "ms": h.get("duration_ms"),
            "timestamp": h.get("timestamp"),
        }
        for h in hist
    ]
    st.dataframe(rows, use_container_width=True)
else:
    st.caption("No runs yet.")
