# frontend/pages/02_scenario_lab.py
"""Scenario Lab — browse, search, run scenarios; audit trail; ISO simulator; demo mode."""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme
from utils.demo_mode import render_node_diagram, render_playback_step, _STEP_NODE_MAP

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — Scenario Lab", page_icon="🧬", layout="wide")
inject_theme()

st.title("🧬 Scenario Lab")

# ── Sidebar controls ──────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Filters")
    search_q = st.text_input("Search", placeholder="keyword…", key="lab_search")
    evt_filter = st.selectbox("Event type", ["(all)", "authorization", "advice", "refund", "reversal"],
                              key="lab_evt")
    st.markdown("---")
    st.subheader("Network")
    _NETWORK_OPTIONS = {
        "(auto — BIN routing)": None,
        "🔵 Visa":         "visa",
        "🔴 Mastercard":   "mastercard",
        "🟢 Amex":         "amex",
        "🟠 Discover":     "discover",
    }
    selected_network_label = st.selectbox(
        "Force network dialect",
        list(_NETWORK_OPTIONS.keys()),
        key="lab_network",
        help="Override BIN routing and force a specific ISO 8583 network dialect.",
    )
    selected_network = _NETWORK_OPTIONS[selected_network_label]
    st.markdown("---")
    st.subheader("Simulator Mode")
    st.session_state.iso_mode = st.toggle(
        "ISO Simulator Mode", value=st.session_state.iso_mode,
        help="Show ISO 8583 DE ↔ JPF mapping panel")
    st.session_state.demo_mode = st.toggle(
        "Demo Mode", value=st.session_state.demo_mode,
        help="Animated step-by-step transaction replay")
    if st.session_state.demo_mode:
        st.slider("Step delay (s)", 0.5, 3.0, 1.5, 0.25, key="demo_speed")

# ── Scenario browser ──────────────────────────────────────────────────────────
params = {"limit": 200}
if search_q:
    params["search"] = search_q
if evt_filter != "(all)":
    params["event_type"] = evt_filter

scenarios_resp = api_get("/scenarios", params=params)
scenarios = (scenarios_resp or {}).get("items", [])
total_sc = (scenarios_resp or {}).get("total", 0)

st.subheader(f"📋 Scenarios ({total_sc})")
if not scenarios:
    st.info("No scenarios match your filter. Try generating one below.")
else:
    scenario_map = {s["id"]: f"[{s['event_type'].upper()[:3]}] {s['name']}" for s in scenarios}
    col_sel, col_run = st.columns([4, 1])
    selected_id = col_sel.selectbox(
        "Select scenario", list(scenario_map.keys()),
        format_func=lambda k: scenario_map[k], key="lab_scenario")

    sel_scenario = next((s for s in scenarios if s["id"] == selected_id), None)
    if sel_scenario:
        with st.expander("Scenario details", expanded=False):
            st.markdown(f"**Description:** {sel_scenario.get('description', '—')}")
            st.markdown(f"**Event type:** `{sel_scenario.get('event_type','authorization')}`")
            st.markdown(
                f"**Expected:** RC `{sel_scenario.get('expected_network_response_code','?')}` "
                f"| Decision `{sel_scenario.get('expected_customer_decision','?')}`"
            )
            tags = sel_scenario.get("tags", [])
            if tags:
                st.markdown(f"**Tags:** {' '.join(f'`{t}`' for t in tags)}")

    run_clicked = col_run.button("▶ Run", type="primary", key="lab_run_btn")
    if run_clicked and selected_id:
        _exec_url = f"/execute/{selected_id}"
        if selected_network:
            _exec_url += f"?network={selected_network}"
        with st.spinner("Executing…"):
            trace = api_post(_exec_url)
        if trace and "error" not in trace:
            st.session_state.last_trace = trace
        else:
            st.error(f"Error: {(trace or {}).get('error', 'unknown')}")

# ── Result display ────────────────────────────────────────────────────────────
trace = st.session_state.last_trace
if trace and "error" not in trace:
    passed = trace.get("passed", False)
    rc = trace.get("actual_network_response_code", "?")
    exp_rc = trace.get("expected_network_response_code", "?")
    dec = trace.get("actual_customer_decision", "?")
    dur = trace.get("duration_ms", 0)

    st.markdown("---")
    if passed:
        st.success(f"✅ **PASSED** — RC: `{rc}` | Decision: `{dec}` | {dur:.0f} ms")
    else:
        st.error(f"❌ **FAILED** — Expected RC: `{exp_rc}` | Got: `{rc}` | Decision: `{dec}`")

    # Audit trail
    with st.expander("🔍 Audit Trail", expanded=True):
        for step in trace.get("audit_trail", []):
            dir_sym = step.get("direction", "→")
            color = "#1f77b4" if dir_sym == "→" else "#2ca02c"
            st.markdown(
                f'<div style="border-left:3px solid {color};padding:4px 12px;margin-bottom:6px">'
                f'<b>Step {step["step"]}</b>: {step["actor"]} '
                f'<span style="color:{color}">{dir_sym}</span> '
                f'<em>{step.get("label","")}</em>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if step.get("payload"):
                with st.expander(f"  Payload (step {step['step']})", expanded=False):
                    st.json(step["payload"])

    # ── Network ISO ↔ JPF contrast panel (T7) ─────────────────────────────
    iso_msg = trace.get("iso_message", {})
    jpf_data = trace.get("jpf", {})
    iso_warnings = trace.get("iso_warnings", [])

    if iso_msg and "error" not in iso_msg:
        st.markdown("---")
        _net_badge_colors = {
            "visa": "#1a1f71", "mastercard": "#eb001b",
            "amex": "#007ec1", "discover": "#f76f20",
        }
        _net = iso_msg.get("network", "visa")
        _badge_color = _net_badge_colors.get(_net, "#555")
        st.markdown(
            f'<h4>🌐 ISO 8583 ↔ JPF — Network: '
            f'<span style="background:{_badge_color};color:#fff;padding:2px 10px;'
            f'border-radius:4px;font-size:0.9em">{_net.upper()}</span>'
            f'&nbsp; MTI: <code>{iso_msg.get("mti","?")}</code>'
            f'&nbsp; STAN: <code>{iso_msg.get("stan","?")}</code>'
            f'&nbsp; RRN: <code>{iso_msg.get("rrn","?")}</code>'
            f'</h4>',
            unsafe_allow_html=True,
        )

        if iso_warnings:
            for w in iso_warnings:
                st.warning(f"⚠️ {w}")

        iso_col, jpf_col = st.columns(2)

        with iso_col:
            st.markdown("**📦 ISO 8583 Fields**")
            private_des = set(str(d) for d in iso_msg.get("private_des", []))
            fields = iso_msg.get("fields", {})
            try:
                import pandas as pd
                rows_iso = []
                for de_key in sorted(fields.keys(), key=lambda x: int(x) if x.isdigit() else 999):
                    is_private = de_key in private_des
                    rows_iso.append({
                        "DE": f"DE{de_key}",
                        "Value": fields[de_key],
                        "Private": "★" if is_private else "",
                    })
                df_iso = pd.DataFrame(rows_iso)

                def _highlight_private(row):
                    if row["Private"] == "★":
                        return ["background-color: #fff3cd"] * len(row)
                    return [""] * len(row)

                st.dataframe(
                    df_iso.style.apply(_highlight_private, axis=1),
                    use_container_width=True,
                    height=420,
                )
                st.caption("★ = network-private DE (highlighted)")
            except ImportError:
                st.json(fields)

        with jpf_col:
            st.markdown("**📋 Canonical JPF (dialect-agnostic)**")
            st.json(jpf_data)
            if iso_msg.get("packed_hex"):
                with st.expander("Packed hex (ISO 8583 wire bytes)", expanded=False):
                    hex_str = iso_msg["packed_hex"]
                    st.code(hex_str, language="text")
                    st.caption(f"{len(hex_str) // 2} bytes")

# ── ISO Simulator Mode ────────────────────────────────────────────────────────
if st.session_state.iso_mode:
    try:
        import pandas as pd
        from iso_mapping import DEFAULT_ISO_JPF_MAPPING, extract_iso_jpf_values
        st.markdown("---")
        if st.session_state.iso_jpf_mapping is None:
            st.session_state.iso_jpf_mapping = list(DEFAULT_ISO_JPF_MAPPING)
        with st.expander("🔄 ISO 8583 ↔ JPF Mapping Table (editable)", expanded=True):
            st.caption("Edit rows, add/remove DE entries. Changes are session-scoped.")
            edited = st.data_editor(
                pd.DataFrame(st.session_state.iso_jpf_mapping),
                num_rows="dynamic", use_container_width=True,
                column_config={
                    "de":          st.column_config.TextColumn("DE #",        width=80),
                    "iso_name":    st.column_config.TextColumn("ISO Name",    width=200),
                    "jpf_field":   st.column_config.TextColumn("JPF Field",   width=150),
                    "description": st.column_config.TextColumn("Description", width=240),
                    "transform":   st.column_config.SelectboxColumn(
                        "Transform", width=150,
                        options=["passthrough","tokenize","format_iso8601",
                                 "extract_time","truncate_25","numeric_to_alpha"]),
                }, key="iso_tbl_lab")
            st.session_state.iso_jpf_mapping = edited.to_dict("records")
            if st.button("Reset to defaults", key="iso_reset_lab"):
                st.session_state.iso_jpf_mapping = list(DEFAULT_ISO_JPF_MAPPING)
                st.rerun()

        if trace:
            with st.expander("ISO ↔ JPF Translation — last transaction", expanded=True):
                rows = extract_iso_jpf_values(
                    st.session_state.iso_jpf_mapping,
                    trace.get("request_sent", {}), trace.get("response_received", {}))
                df_tr = pd.DataFrame(rows)[["de","iso_name","iso_value","jpf_field","jcf_value","transform"]]
                st.dataframe(df_tr, use_container_width=True)
    except ImportError:
        st.info("ISO mapping module not found — ensure iso_mapping.py is in the frontend directory.")

# ── Demo Mode ─────────────────────────────────────────────────────────────────
if st.session_state.demo_mode:
    st.markdown("---")
    st.markdown("### 🎬 Demo Mode — Animated Transaction Flow")
    d1, d2 = st.columns([2, 1])
    run_demo  = d1.button("▶ Run Demo", type="primary", key="demo_run_lab")
    stop_demo = d2.button("⏹ Stop", key="demo_stop_lab")
    if stop_demo:
        st.session_state.demo_running = False
    if run_demo:
        st.session_state.demo_running = True
        demo_scenario_id = selected_id if scenarios else "authorization_approve"
        demo_trace = api_post(f"/execute/{demo_scenario_id}?unique=true")
        if demo_trace and "error" not in demo_trace:
            speed   = st.session_state.get("demo_speed", 1.5)
            audit   = demo_trace.get("audit_trail", [])
            # T0.3 — extract resolved network for dynamic node label
            _demo_network = (
                demo_trace.get("iso_message", {}).get("network")
                or demo_trace.get("request_sent", {}).get("network")
                or "visa"
            )
            n_ph   = st.empty()
            s_ph   = st.empty()
            p_ph   = st.empty()
            for entry in audit:
                if not st.session_state.demo_running:
                    st.warning("Demo stopped.")
                    break
                idx      = (entry.get("step", 1) - 1)
                node_idx = _STEP_NODE_MAP.get(idx + 1, 0)
                n_ph.markdown(render_node_diagram(node_idx, network=_demo_network), unsafe_allow_html=True)
                render_playback_step(entry, entry.get("step"), len(audit), network=_demo_network)
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
                dec = demo_trace.get("actual_customer_decision", "UNKNOWN")
                rc2 = demo_trace.get("actual_network_response_code", "?")
                if dec == "APPROVED":
                    st.success(f"✅ Transaction Complete — APPROVED (RC: {rc2})")
                else:
                    st.error(f"❌ Transaction Complete — {dec} (RC: {rc2})")
        else:
            st.error("Demo failed. Is the backend running?")
            st.session_state.demo_running = False

st.markdown("---")

# ── Ad-hoc Scenario Generator ─────────────────────────────────────────────────
with st.expander("🛠️ Generate Ad-hoc Scenario", expanded=False):
    st.caption("Creates and immediately saves a new scenario JSON file.")
    g1, g2 = st.columns(2)
    gen_id    = g1.text_input("Scenario ID",    value=f"gen_{int(time.time())}", key="gen_id")
    gen_name  = g2.text_input("Name",           value="My Scenario",            key="gen_name")
    gen_evt   = g1.selectbox("Event Type",      ["authorization","advice","refund","reversal"],
                              key="gen_evt")
    gen_amt   = g2.number_input("Amount (cents)",value=2500, min_value=0, key="gen_amt")
    gen_mcc   = g1.text_input("MCC",            value="5411", key="gen_mcc")
    gen_mname = g2.text_input("Merchant Name",  value="Test Merchant", key="gen_mname")
    gen_exp_rc   = g1.text_input("Expected RC",       value="00", key="gen_exp_rc")
    gen_exp_dec  = g2.selectbox("Expected Decision",  ["APPROVED","DECLINED","DUPLICATE"],
                                 key="gen_exp_dec")
    if st.button("💾 Generate & Save", key="gen_save_btn"):
        body = {
            "scenario_id": gen_id, "name": gen_name,
            "event_type": gen_evt, "amount": int(gen_amt),
            "mcc": gen_mcc, "merchant_name": gen_mname,
            "expected_response_code": gen_exp_rc,
            "expected_customer_decision": gen_exp_dec,
        }
        result = api_post("/generate", body)
        if result:
            st.success(f"Created: `{result.get('created')}`")
            st.json(result.get("scenario", {}))

st.markdown("---")

# ── Paginated History ─────────────────────────────────────────────────────────
st.subheader("📜 Transaction History")
h_col1, h_col2, h_col3 = st.columns([2, 2, 1])
h_passed_filter = h_col1.selectbox("Result", ["(all)", "passed", "failed"], key="h_pass_f")
h_evt_filter    = h_col2.selectbox("Event",  ["(all)", "authorization", "advice", "refund", "reversal"],
                                   key="h_evt_f")
h_reset = h_col3.button("🔄 Refresh", key="h_refresh")
if h_reset:
    st.session_state.history_page = 1

h_params = {"page": st.session_state.history_page, "limit": 20}
if h_passed_filter == "passed":
    h_params["passed"] = "true"
elif h_passed_filter == "failed":
    h_params["passed"] = "false"
if h_evt_filter != "(all)":
    h_params["event_type"] = h_evt_filter

hist_resp = api_get("/history", params=h_params)
h_items = (hist_resp or {}).get("items", [])
h_total = (hist_resp or {}).get("total", 0)
h_limit = (hist_resp or {}).get("limit", 20)
h_page  = (hist_resp or {}).get("page", 1)
h_pages = (h_total + h_limit - 1) // h_limit if h_limit else 1

if h_items:
    import pandas as pd
    rows = []
    for row in h_items:
        rows.append({
            "": "✅" if row.get("passed") else "❌",
            "Scenario":    row.get("scenario_name") or row.get("scenario_id", "?"),
            "Event":       row.get("event_type", "?"),
            "RC":          row.get("actual_rc") or row.get("actual_network_response_code", "?"),
            "Decision":    row.get("actual_decision") or row.get("actual_customer_decision", "?"),
            "ms":          f"{row.get('duration_ms') or 0:.0f}",
            "Timestamp":   (row.get("timestamp") or "")[:19].replace("T", " "),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
    pc1, pc2, pc3 = st.columns([1, 3, 1])
    if pc1.button("◀ Prev", disabled=(h_page <= 1), key="h_prev"):
        st.session_state.history_page = max(1, h_page - 1)
        st.rerun()
    pc2.caption(f"Page {h_page} of {h_pages} ({h_total} total)")
    if pc3.button("Next ▶", disabled=(h_page >= h_pages), key="h_next"):
        st.session_state.history_page = h_page + 1
        st.rerun()
else:
    st.info("No history matches your filter.")
