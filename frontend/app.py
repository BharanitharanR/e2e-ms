# e2e-marqeta-simulator/frontend/app.py
"""Streamlit UI for the End-to-End Marqeta Transaction Simulator."""
import os
import json
import time
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://backend:8000")

st.set_page_config(page_title="Marqeta E2E Simulator", page_icon="💳", layout="wide")


# --------------------------------------------------------------------------- #
# Session state initialisation (all enhancements deposit defaults here)
# --------------------------------------------------------------------------- #
def _init_session_state():
    defaults = {
        "iso_mode":        False,
        "iso_jcf_mapping": None,   # populated lazily from iso_mapping.py
        "suite_result":    None,
        "apdu_log":        [],
        "card_state":      None,
        "demo_mode":       False,
        "demo_running":    False,
        "last_trace":      None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_session_state()

st.title("End-to-End Marqeta Transaction Simulator")
st.caption("Cardholder → Terminal → Acquirer → Visa → Marqeta → Customer JIT → Response")


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
def api_get(path):
    try:
        return requests.get(f"{API_URL}{path}", timeout=20).json()
    except Exception as e:  # noqa: BLE001
        st.error(f"GET {path} failed: {e}")
        return None


def api_post(path, body=None):
    try:
        return requests.post(f"{API_URL}{path}", json=body or {}, timeout=60).json()
    except Exception as e:  # noqa: BLE001
        st.error(f"POST {path} failed: {e}")
        return None


# --------------------------------------------------------------------------- #
# Helper: HTML test report builder (Enhancement 2)
# --------------------------------------------------------------------------- #
def _build_html_report(suite_result: dict) -> str:
    from jinja2 import Environment
    template_str = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{{ suite_name }} - Test Report</title>
<style>
  body{font-family:Arial,sans-serif;margin:32px}
  h1{color:#333}
  .pass{background:#28a745;color:#fff;padding:3px 10px;border-radius:4px;font-weight:bold}
  .fail{background:#dc3545;color:#fff;padding:3px 10px;border-radius:4px;font-weight:bold}
  table{border-collapse:collapse;width:100%;margin-top:24px}
  th{background:#f5f5f5;padding:8px;text-align:left;border-bottom:2px solid #ddd}
  td{padding:8px;border-bottom:1px solid #eee}
  tr.pass-row td:first-child{color:#28a745;font-weight:bold}
  tr.fail-row td:first-child{color:#dc3545;font-weight:bold}
</style></head><body>
<h1>{{ suite_name }}</h1>
<p>Run: {{ run_at }} &nbsp;|&nbsp; {{ passed }}/{{ total }} passed &nbsp;|&nbsp;
{{ duration_ms }} ms &nbsp;
<span class="{{ 'pass' if passed == total else 'fail' }}">
  {{ 'PASS' if passed == total else 'FAIL' }}
</span></p>
<table>
<tr><th>Result</th><th>Scenario</th><th>Exp RC</th><th>Act RC</th>
    <th>Exp Decision</th><th>Act Decision</th><th>ms</th></tr>
{% for r in results %}
<tr class="{{ 'pass-row' if r.passed else 'fail-row' }}">
  <td>{{ 'PASS' if r.passed else 'FAIL' }}</td>
  <td>{{ r.name }}</td>
  <td>{{ r.expected_network_response_code }}</td>
  <td>{{ r.actual_network_response_code }}</td>
  <td>{{ r.expected_customer_decision }}</td>
  <td>{{ r.actual_customer_decision }}</td>
  <td>{{ r.duration_ms }}</td>
</tr>
{% endfor %}
</table></body></html>"""
    return Environment().from_string(template_str).render(**suite_result)


# --------------------------------------------------------------------------- #
# Helper: Demo Mode node diagram renderer (Enhancement 4)
# --------------------------------------------------------------------------- #
_DEMO_NODES = [
    ("💳", "Wallet"),
    ("🏪", "Terminal"),
    ("🏦", "Acquirer"),
    ("🌐", "Visa"),
    ("⚙️", "Marqeta"),
    ("✅", "JIT"),
]
# Maps audit step index (0-based) → which node to highlight
_STEP_NODE_MAP = [0, 1, 2, 3, 4, 5, 5, 4, 3, 2, 1]


def _render_node_diagram(active: int) -> str:
    parts = []
    for i, (emoji, label) in enumerate(_DEMO_NODES):
        if i == active:
            s = ("background:#1f77b4;color:white;padding:5px 10px;"
                 "border-radius:8px;font-weight:bold;white-space:nowrap")
        else:
            s = ("background:#f0f0f0;color:#555;padding:5px 10px;"
                 "border-radius:8px;white-space:nowrap")
        parts.append(f'<span style="{s}">{emoji} {label}</span>')
    return (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        'padding:8px 0">' + ' &rarr; '.join(parts) + '</div>'
    )


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

# ---- Simulator Mode toggles (Enhancements 1 & 4) ----
st.sidebar.markdown("---")
st.sidebar.subheader("Simulator Mode")
st.session_state.iso_mode = st.sidebar.toggle(
    "ISO Simulator Mode",
    value=st.session_state.iso_mode,
    help="Display ISO 8583 DE ↔ Marqeta JCF field mapping panel",
)
st.session_state.demo_mode = st.sidebar.toggle(
    "Demo Mode",
    value=st.session_state.demo_mode,
    help="Animated step-by-step replay of the full transaction flow",
)
if st.session_state.demo_mode:
    st.sidebar.slider(
        "Step delay (seconds)", 0.5, 3.0, 1.5, 0.25, key="demo_speed"
    )

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
            # Store for ISO Simulator translation panel
            st.session_state.last_trace = trace

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

            # ----------------------------------------------------------------- #
            # Audit trail: full payload chain from cardholder to merchant terminal
            # ----------------------------------------------------------------- #
            audit_trail = trace.get("audit_trail", [])
            if audit_trail:
                st.markdown("---")
                st.markdown("#### Audit Trail")
                st.caption(
                    "Full payload flow: Cardholder \u2192 Terminal \u2192 Acquirer "
                    "\u2192 Visa \u2192 Marqeta \u2192 Customer JIT \u2192 back to Merchant Terminal"
                )
                show_payloads = st.checkbox("Show full payloads", value=False,
                                            key="audit_show_payloads")

                for entry in audit_trail:
                    step = entry.get("step", "")
                    actor = entry.get("actor", "")
                    direction = entry.get("direction", "")
                    label = entry.get("label", "")
                    payload = entry.get("payload")
                    timestamp = entry.get("timestamp", "")

                    # Direction colour: outbound = blue arrow, inbound = green arrow
                    if direction == "\u2192":
                        arrow_html = '<span style="color:#1f77b4;font-weight:bold">\u2192</span>'
                    else:
                        arrow_html = '<span style="color:#2ca02c;font-weight:bold">\u2190</span>'

                    st.markdown(
                        f'**Step {step}** &nbsp;{arrow_html}&nbsp; **{actor}**'
                        f'&nbsp;&nbsp;<span style="color:#888;font-size:0.85em">{timestamp}</span>',
                        unsafe_allow_html=True,
                    )
                    st.caption(label)

                    if show_payloads and payload:
                        with st.expander(f"Payload \u25bc  (Step {step} \u2013 {actor})"):
                            st.json(payload)

                    st.markdown('<hr style="margin:6px 0;border-color:#e0e0e0">', unsafe_allow_html=True)

        elif trace:
            st.error(trace.get("error"))

# --------------------------------------------------------------------------- #
# Enhancement 1: ISO Simulator Mode
# --------------------------------------------------------------------------- #
if st.session_state.iso_mode:
    from iso_mapping import DEFAULT_ISO_JCF_MAPPING, extract_iso_jcf_values
    import pandas as pd

    if st.session_state.iso_jcf_mapping is None:
        st.session_state.iso_jcf_mapping = list(DEFAULT_ISO_JCF_MAPPING)

    st.markdown("---")
    with st.expander("🔄 ISO 8583 \u2194 JCF Mapping Table (editable)", expanded=True):
        st.caption(
            "Edit DE \u2194 JCF field mappings below. Add rows, change DE numbers or "
            "JCF field names. Changes are session-scoped and reset on browser refresh."
        )
        edited = st.data_editor(
            pd.DataFrame(st.session_state.iso_jcf_mapping),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "de":          st.column_config.TextColumn("DE #",        width=80),
                "iso_name":    st.column_config.TextColumn("ISO Name",    width=200),
                "jcf_field":   st.column_config.TextColumn("JCF Field",   width=150),
                "description": st.column_config.TextColumn("Description", width=240),
                "transform":   st.column_config.SelectboxColumn(
                    "Transform", width=160,
                    options=["passthrough", "tokenize", "format_iso8601",
                             "extract_time", "truncate_25", "numeric_to_alpha"],
                ),
            },
            key="iso_tbl",
        )
        st.session_state.iso_jcf_mapping = edited.to_dict("records")
        if st.button("Reset to defaults", key="iso_reset"):
            st.session_state.iso_jcf_mapping = list(DEFAULT_ISO_JCF_MAPPING)
            st.rerun()

    if st.session_state.last_trace:
        tr = st.session_state.last_trace
        with st.expander("ISO \u2194 JCF Translation \u2014 last transaction", expanded=True):
            rows = extract_iso_jcf_values(
                st.session_state.iso_jcf_mapping,
                tr.get("request_sent", {}),
                tr.get("response_received", {}),
            )
            df_tr = pd.DataFrame(rows)[
                ["de", "iso_name", "iso_value", "jcf_field", "jcf_value", "transform"]
            ]
            st.dataframe(df_tr, use_container_width=True)
            st.caption(
                "Fields with a non-passthrough transform (e.g. tokenize, truncate_25) "
                "indicate data element conversion performed by the issuer processor (JPBOS/JCF)."
            )

# --------------------------------------------------------------------------- #
# Enhancement 4: Demo Mode — animated end-to-end flow
# --------------------------------------------------------------------------- #
if st.session_state.demo_mode:
    st.markdown("---")
    st.markdown("### \U0001f3ac Demo Mode \u2014 Animated Transaction Flow")
    st.caption(
        "Executes a standard Purchase-Approve scenario, then replays each "
        "hop with live payload reveal and an animated node diagram."
    )
    d1, d2 = st.columns([2, 1])
    run_demo  = d1.button("\u25b6 Run Demo", type="primary", key="demo_run")
    stop_demo = d2.button("\u23f9 Stop Demo", key="demo_stop")
    if stop_demo:
        st.session_state.demo_running = False

    if run_demo:
        st.session_state.demo_running = True
        demo_trace = api_post("/execute/auth_approve_01?unique=true")
        if demo_trace and "error" not in demo_trace:
            speed = st.session_state.get("demo_speed", 1.5)
            audit = demo_trace.get("audit_trail", [])
            n_ph  = st.empty()   # node diagram placeholder
            s_ph  = st.empty()   # step status placeholder
            p_ph  = st.empty()   # payload placeholder

            for entry in audit:
                if not st.session_state.demo_running:
                    st.warning("Demo stopped.")
                    break

                step_idx  = entry.get("step", 1) - 1
                node_idx  = _STEP_NODE_MAP[min(step_idx, len(_STEP_NODE_MAP) - 1)]
                direction = entry.get("direction", "\u2192")
                dir_color = "#1f77b4" if direction == "\u2192" else "#2ca02c"
                dir_label = "OUTBOUND" if direction == "\u2192" else "INBOUND"

                n_ph.markdown(_render_node_diagram(node_idx), unsafe_allow_html=True)
                s_ph.markdown(
                    f'<div style="margin-top:8px">'
                    f'<b>Step {entry.get("step")}</b>: {entry.get("actor")} '
                    f'<span style="background:{dir_color};color:#fff;padding:2px 8px;'
                    f'border-radius:4px;font-size:0.8em">{dir_label}</span>'
                    f'<br><em>{entry.get("label","")}</em></div>',
                    unsafe_allow_html=True,
                )

                payload = entry.get("payload") or {}
                if payload:
                    lines = json.dumps(payload, indent=2).splitlines()
                    revealed = ""
                    per_line_delay = speed / max(len(lines), 1)
                    for line in lines:
                        revealed += line + "\n"
                        p_ph.code(revealed, language="json")
                        time.sleep(per_line_delay)
                else:
                    time.sleep(speed)

            if st.session_state.demo_running:
                st.session_state.demo_running = False
                decision = demo_trace.get("actual_customer_decision", "UNKNOWN")
                rc       = demo_trace.get("actual_network_response_code", "?")
                if decision == "APPROVED":
                    st.success(f"\u2705 Transaction Complete \u2014 APPROVED (RC: {rc})")
                else:
                    st.error(f"\u274c Transaction Complete \u2014 {decision} (RC: {rc})")
        else:
            st.error("Demo failed to run. Is the backend running and scenario 'auth_approve_01' available?")
            st.session_state.demo_running = False

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
            "scenario": h.get("scenario_name") or h.get("suite_name"),
            "event": h.get("event_type", "suite" if h.get("suite_run") else ""),
            "rc": h.get("actual_network_response_code", ""),
            "decision": h.get("actual_customer_decision", ""),
            "ms": h.get("duration_ms", ""),
            "timestamp": h.get("timestamp"),
        }
        for h in hist
    ]
    st.dataframe(rows, use_container_width=True)
else:
    st.caption("No runs yet.")

# --------------------------------------------------------------------------- #
# Enhancement 2: Test Suite Runner
# --------------------------------------------------------------------------- #
st.markdown("---")
with st.expander("\U0001f9ea Test Suite Runner", expanded=False):
    st.caption(
        "Run a curated suite of functional test cases (Purchase, ATM, PIN, Pre-Auth, "
        "OCT, Refund, Clearing, Reversal, Duplicate, Zero-Amount, Multi-Currency) "
        "and get a pass/fail summary with a downloadable HTML report."
    )
    suites_data = api_get("/suites") or []
    suite_opts  = {
        s["key"]: f"{s['name']} ({s['scenario_count']} tests)"
        for s in suites_data
    }

    if suite_opts:
        col_s, col_r = st.columns([3, 1])
        sel_suite = col_s.selectbox(
            "Suite",
            options=list(suite_opts.keys()),
            format_func=lambda k: suite_opts.get(k, k),
            key="suite_picker",
        )
        run_suite = col_r.button("\u25b6 Run Suite", type="primary", key="run_suite_btn")

        if run_suite:
            with st.spinner("Running test suite\u2026 this may take a few seconds"):
                st.session_state.suite_result = api_post(
                    "/execute_suite",
                    {"suite_name": sel_suite, "reset_before": True},
                )

        sr = st.session_state.suite_result
        if sr:
            total  = sr.get("total", 0)
            passed = sr.get("passed", 0)
            badge_color = "#28a745" if passed == total else "#dc3545"
            badge_text  = "PASS" if passed == total else "FAIL"
            st.markdown(
                f'**{sr.get("suite_name")}** &nbsp;'
                f'<span style="background:{badge_color};color:#fff;padding:3px 10px;'
                f'border-radius:4px;font-weight:bold">{badge_text}</span>'
                f'&nbsp;&nbsp;{passed}/{total} passed'
                f'&nbsp;|&nbsp;{sr.get("duration_ms")} ms',
                unsafe_allow_html=True,
            )

            import pandas as pd
            df_rows = [
                {
                    "": "\u2705" if r.get("passed") else "\u274c",
                    "Scenario":     r.get("name"),
                    "Exp RC":       r.get("expected_network_response_code"),
                    "Act RC":       r.get("actual_network_response_code"),
                    "Exp Decision": r.get("expected_customer_decision"),
                    "Act Decision": r.get("actual_customer_decision"),
                    "ms":           r.get("duration_ms"),
                }
                for r in sr.get("results", [])
            ]
            st.dataframe(pd.DataFrame(df_rows), use_container_width=True)

            html_report = _build_html_report(sr)
            st.download_button(
                "\u2b07 Download HTML Report",
                data=html_report,
                file_name=f"suite_report_{sr.get('run_at','')[:10]}.html",
                mime="text/html",
                key="dl_report",
            )
    else:
        st.warning("No suites available. Is the backend running?")

# --------------------------------------------------------------------------- #
# Enhancement 3: NFC / Chip Card Terminal Emulator
# --------------------------------------------------------------------------- #
st.markdown("---")
with st.expander("\U0001f4f1 NFC / Chip Card Terminal Emulator", expanded=False):
    st.markdown("#### Software-Only EMV Chip Card Simulator")
    st.caption(
        "APDU (Application Protocol Data Unit) command emulation — no physical "
        "hardware required. Simulates SELECT, GET DATA, VERIFY PIN, READ RECORD, "
        "PUT DATA, GENERATE AC against a virtual personalized card."
    )

    # Card state panel
    if st.button("\U0001f504 Refresh Card State", key="chip_refresh"):
        resp = api_post("/chip/command", {"command": "GET_STATE"})
        if resp:
            st.session_state.card_state = resp.get("card_state", {})

    cs = st.session_state.card_state or {}
    if cs:
        c1, c2, c3 = st.columns(3)
        c1.metric("AID",       cs.get("aid", "-"))
        c2.metric("PAN",       cs.get("pan_masked", "-"))
        c3.metric("Status",    cs.get("card_status", "-"))
        c4, c5, c6 = st.columns(3)
        c4.metric("Expiry (YYMM)", cs.get("expiry", "-"))
        c5.metric("Service Code",  cs.get("service_code", "-"))
        c6.metric("PIN Tries Left",cs.get("pin_tries_remaining", "-"))
    else:
        st.info("Click 'Refresh Card State' to load personalized card data.")

    st.markdown("---")
    tabs = st.tabs(["SELECT", "GET DATA", "VERIFY PIN", "READ RECORD", "PUT DATA", "GENERATE AC"])

    # Tab 0 — SELECT
    with tabs[0]:
        st.caption("SELECT Application by AID — initiates the contactless session.")
        aid_opts = {
            "A0000000031010": "Visa Credit",
            "A0000000032010": "Visa Debit",
            "A0000000041010": "Mastercard Credit",
            "A000000065":     "JCB",
        }
        sel_aid = st.selectbox(
            "AID", list(aid_opts.keys()),
            format_func=lambda k: f"{k}  ({aid_opts[k]})",
            key="chip_sel_aid",
        )
        if st.button("Send SELECT", key="chip_btn_sel"):
            r = api_post("/chip/command", {"command": "SELECT", "aid": sel_aid})
            if r:
                st.session_state.apdu_log.insert(0, r)
                st.session_state.card_state = r.get("card_state", {})

    # Tab 1 — GET DATA
    with tabs[1]:
        st.caption("GET DATA — read a specific EMV data object by tag number.")
        tag_opts = {
            "5A":   "PAN (Primary Account Number)",
            "5F24": "Expiry Date",
            "5F30": "Service Code",
            "9F36": "ATC (Application Transaction Counter)",
            "4F":   "AID (Application Identifier)",
        }
        sel_tag = st.selectbox(
            "Tag", list(tag_opts.keys()),
            format_func=lambda k: f"{k}  = {tag_opts[k]}",
            key="chip_get_tag",
        )
        if st.button("Send GET DATA", key="chip_btn_gd"):
            r = api_post("/chip/command", {"command": "GET_DATA", "tag": sel_tag})
            if r:
                st.session_state.apdu_log.insert(0, r)

    # Tab 2 — VERIFY PIN
    with tabs[2]:
        st.caption("VERIFY — submit offline PIN. Default PIN is 1234. Three wrong attempts block the card.")
        pin_in = st.text_input("PIN", value="1234", type="password", key="chip_pin")
        if st.button("Send VERIFY PIN", key="chip_btn_vfy"):
            r = api_post("/chip/command", {"command": "VERIFY", "pin": pin_in})
            if r:
                st.session_state.apdu_log.insert(0, r)
                st.session_state.card_state = r.get("card_state", {})

    # Tab 3 — READ RECORD
    with tabs[3]:
        st.caption("READ RECORD — read EMV application records. SFI=1, Record=1 returns PAN + expiry + service code.")
        sfi_v = st.number_input("SFI (Short File Identifier)", min_value=1, max_value=31, value=1, key="chip_sfi")
        rec_v = st.number_input("Record Number", min_value=1, max_value=255, value=1, key="chip_rec")
        if st.button("Send READ RECORD", key="chip_btn_rr"):
            r = api_post("/chip/command", {"command": "READ_RECORD", "sfi": sfi_v, "record_num": rec_v})
            if r:
                st.session_state.apdu_log.insert(0, r)

    # Tab 4 — PUT DATA (issuer script / personalisation commands)
    with tabs[4]:
        st.caption("PUT DATA — issuer script command. Update card data objects or change PIN.")
        put_tag = st.selectbox(
            "Tag to write",
            ["5F24", "5F20", "PINCHG"],
            format_func=lambda t: {
                "5F24":  "5F24  = Expiry Date (YYMM hex)",
                "5F20":  "5F20  = Cardholder Name (ASCII)",
                "PINCHG":"PINCHG = Change PIN (plaintext)",
            }[t],
            key="chip_put_tag",
        )
        put_val = st.text_input(
            "Value  (hex for 5F24/5F20, plaintext for PINCHG)",
            key="chip_put_val",
        )
        if st.button("Send PUT DATA", key="chip_btn_put"):
            val_hex = put_val.encode().hex() if put_tag == "PINCHG" else put_val
            r = api_post("/chip/command", {"command": "PUT_DATA", "tag": put_tag, "value": val_hex})
            if r:
                st.session_state.apdu_log.insert(0, r)

    # Tab 5 — GENERATE AC
    with tabs[5]:
        st.caption("GENERATE AC — request Application Cryptogram (ARQC) for online authorization.")
        cdol_in = st.text_input("CDOL Data (hex, optional)", value="", key="chip_cdol")
        if st.button("Send GENERATE AC", key="chip_btn_genac"):
            r = api_post("/chip/command", {"command": "GENERATE_AC", "cdol_data": cdol_in})
            if r:
                st.session_state.apdu_log.insert(0, r)
                st.session_state.card_state = r.get("card_state", {})

    # APDU Log
    st.markdown("---")
    lc1, lc2, lc3 = st.columns([3, 1, 1])
    lc1.markdown("**APDU Log** (most recent first)")
    if lc2.button("Clear Log", key="chip_clear"):
        st.session_state.apdu_log = []
    if lc3.button("Reset Card", key="chip_reset"):
        r = api_post("/chip/command", {"command": "RESET_CARD"})
        if r:
            st.session_state.apdu_log = []
            st.session_state.card_state = r.get("card_state", {})
        st.rerun()

    if st.session_state.apdu_log:
        for entry in st.session_state.apdu_log[:20]:
            sw     = entry.get("sw", "????")
            color  = "#28a745" if sw == "9000" else "#dc3545"
            data   = entry.get("data", "")
            status = entry.get("status", "")
            data_part = f"<br><span style='color:#555'>DATA: {data}</span>" if data else ""
            st.markdown(
                f'<div style="font-family:monospace;font-size:0.85em;'
                f'border-left:3px solid {color};padding:4px 8px;margin-bottom:4px">'
                f'<b>{entry.get("command", "?")}</b> '
                f'\u2192 SW: <b style="color:{color}">{sw}</b> ({status})'
                f'{data_part}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No APDU commands sent yet. Use the tabs above to interact with the virtual card.")
