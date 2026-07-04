# frontend/pages/10_transaction_builder.py
"""Transaction Builder — key in a card, pick a network, fire a custom transaction.

T0.1 + T0.3 (Phase 3):
- Free-entry PAN with per-network test-card presets (Visa/MC/Amex/Discover).
- Network selector (auto by BIN | visa | mastercard | amex | discover).
- PAN ↔ network consistency validation, Luhn check, Amex 15-digit handling.
- Amount, currency, MCC, POS entry mode, merchant details.
- Runs immediately via POST /execute_adhoc and renders the full trace +
  ISO 8583 ↔ JPF contrast panel.
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme
from utils.demo_mode import render_node_diagram, render_playback_step, _STEP_NODE_MAP

init_session_state()
st.set_page_config(
    page_title="Paycon e2ePS — Transaction Builder",
    page_icon="💳",
    layout="wide",
)
inject_theme()

st.title("💳 Transaction Builder")
st.caption(
    "Build and run a custom transaction end-to-end. "
    "Pick a test-card preset or enter a PAN manually."
)

# ── Per-network test card presets (T0.3) ─────────────────────────────────────
_PRESETS = {
    "🔵 Visa":        {
        "pan": "4111 1111 1111 1111", "network": "visa",
        "note": "16-digit Visa test PAN (Luhn-valid)",
    },
    "🔴 Mastercard":  {
        "pan": "5555 5555 5555 4444", "network": "mastercard",
        "note": "16-digit Mastercard test PAN (Luhn-valid)",
    },
    "🟢 Amex":        {
        "pan": "3782 822463 10005",   "network": "amex",
        "note": "15-digit Amex test PAN — 4-digit CID (Luhn-valid)",
    },
    "🟠 Discover":    {
        "pan": "6011 1111 1111 1117", "network": "discover",
        "note": "16-digit Discover test PAN (Luhn-valid)",
    },
    "✏️ Custom PAN":  {
        "pan": "", "network": "auto",
        "note": "Enter your own test PAN below.",
    },
}

_NETWORK_LABELS = {
    "(auto — BIN routing)": "auto",
    "🔵 Visa":              "visa",
    "🔴 Mastercard":        "mastercard",
    "🟢 Amex":              "amex",
    "🟠 Discover":          "discover",
}

_CURRENCY_OPTIONS = {
    "USD (840)": "840",
    "EUR (978)": "978",
    "GBP (826)": "826",
    "JPY (392)": "392",
    "CAD (124)": "124",
    "AUD (036)": "036",
}

_MCC_OPTIONS = {
    "5411 — Grocery Stores":         "5411",
    "5812 — Restaurants":            "5812",
    "5541 — Service Stations":       "5541",
    "4111 — Transportation":         "4111",
    "5912 — Drug Stores":            "5912",
    "5999 — Misc. Retail":           "5999",
    "4829 — Wire Transfers":         "4829",
    "6011 — ATM/Cash":               "6011",
    "7011 — Hotels":                 "7011",
    "4411 — Cruise Lines":           "4411",
}

_ENTRY_MODES = {
    "Contactless (chip/NFC) — 071":  "071",
    "Chip (contact EMV) — 051":      "051",
    "Magstripe — 011":               "011",
    "Manual / keyed — 010":          "010",
    "E-commerce — 810":              "810",
}

# ── Sidebar: preset picker ────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Test Card Preset")
    preset_label = st.selectbox(
        "Choose a preset",
        list(_PRESETS.keys()),
        key="tb_preset",
        help="Select a per-network test card or enter a custom PAN.",
    )
    preset = _PRESETS[preset_label]
    st.caption(preset["note"])

    st.markdown("---")
    st.subheader("Simulator Mode")
    st.session_state.demo_mode = st.toggle(
        "Demo Mode", value=st.session_state.demo_mode,
        help="Animated step-by-step transaction replay",
        key="tb_demo_toggle",
    )
    if st.session_state.demo_mode:
        st.slider("Step delay (s)", 0.5, 3.0, 1.5, 0.25, key="tb_demo_speed")

# ── Main form ─────────────────────────────────────────────────────────────────
with st.form("txn_builder_form", clear_on_submit=False):
    st.subheader("Card & Network")
    col_pan, col_net = st.columns([3, 2])

    # Pre-fill PAN from preset
    default_pan = preset["pan"] if preset["pan"] else st.session_state.get("tb_pan_val", "")

    pan_input = col_pan.text_input(
        "PAN (card number)",
        value=default_pan,
        max_chars=22,
        placeholder="e.g. 4111 1111 1111 1111",
        key="tb_pan",
        help="16 digits for Visa / Mastercard / Discover; 15 digits for Amex. "
             "Spaces and dashes are stripped automatically.",
    )

    # Network selector — if a non-custom preset is selected, default to its network
    preset_net = preset["network"]
    default_net_label = next(
        (lbl for lbl, val in _NETWORK_LABELS.items() if val == preset_net),
        "(auto — BIN routing)",
    )
    net_label = col_net.selectbox(
        "Network",
        list(_NETWORK_LABELS.keys()),
        index=list(_NETWORK_LABELS.keys()).index(default_net_label),
        key="tb_network",
        help="'auto' resolves the network from the PAN's BIN prefix.",
    )
    network_val = _NETWORK_LABELS[net_label]

    st.markdown("---")
    st.subheader("Transaction Details")
    c1, c2, c3 = st.columns(3)
    amount_cents = c1.number_input(
        "Amount (cents)", value=1000, min_value=1, max_value=9_999_999,
        key="tb_amount",
        help="Minor units — 1000 = $10.00 USD",
    )
    currency_label = c2.selectbox(
        "Currency", list(_CURRENCY_OPTIONS.keys()), key="tb_currency"
    )
    currency_val = _CURRENCY_OPTIONS[currency_label]
    mcc_label = c3.selectbox(
        "MCC (Merchant Category)", list(_MCC_OPTIONS.keys()), key="tb_mcc"
    )
    mcc_val = _MCC_OPTIONS[mcc_label]

    c4, c5 = st.columns(2)
    merchant_name = c4.text_input(
        "Merchant Name", value="Builder Test Merchant", key="tb_mname"
    )
    entry_label = c5.selectbox(
        "POS Entry Mode", list(_ENTRY_MODES.keys()), key="tb_entry"
    )
    entry_val = _ENTRY_MODES[entry_label]

    st.markdown("---")
    st.subheader("Expected Outcome")
    e1, e2 = st.columns(2)
    exp_rc  = e1.text_input("Expected RC",       value="00",       key="tb_exp_rc")
    exp_dec = e2.selectbox("Expected Decision", ["APPROVED", "DECLINED"], key="tb_exp_dec")

    run_btn = st.form_submit_button("▶ Run Transaction", type="primary")

# ── Execute ───────────────────────────────────────────────────────────────────
if run_btn:
    pan_clean = pan_input.replace(" ", "").replace("-", "")

    if not pan_clean or not pan_clean.isdigit():
        st.error("PAN must contain digits only (spaces and dashes are stripped automatically).")
        st.stop()

    payload = {
        "pan":              pan_clean,
        "network":          network_val,
        "amount":           int(amount_cents),
        "currency":         currency_val,
        "mcc":              mcc_val,
        "merchant_name":    merchant_name,
        "pos_entry_mode":   entry_val,
        "expected_rc":      exp_rc,
        "expected_decision": exp_dec,
        "name":             f"Builder — {merchant_name}",
    }

    with st.spinner("Executing…"):
        trace = api_post("/execute_adhoc", payload)

    if trace and "error" not in trace:
        st.session_state["tb_last_trace"] = trace
    else:
        st.error(f"Execution error: {(trace or {}).get('error', 'unknown')}")

# ── Results ───────────────────────────────────────────────────────────────────
trace = st.session_state.get("tb_last_trace")
if trace and "error" not in trace:

    # Validation warnings from /execute_adhoc (T0.3)
    for w in trace.get("adhoc_warnings", []):
        st.warning(f"⚠️ {w}")

    passed = trace.get("passed", False)
    rc     = trace.get("actual_network_response_code", "?")
    exp_rc_r = trace.get("expected_network_response_code", "?")
    dec    = trace.get("actual_customer_decision", "?")
    dur    = trace.get("duration_ms", 0)
    net    = (trace.get("iso_message") or {}).get("network") or trace.get("detected_network", "?")

    _net_colors = {
        "visa": "#1a1f71", "mastercard": "#eb001b",
        "amex": "#007ec1", "discover": "#f76f20",
    }
    _nc = _net_colors.get(net, "#555")

    st.markdown("---")

    # Result banner
    if passed:
        st.success(
            f"✅ **PASSED** — RC: `{rc}` | Decision: `{dec}` | "
            f"Network: `{net.upper()}` | {dur:.0f} ms"
        )
    else:
        st.error(
            f"❌ **FAILED** — Expected RC: `{exp_rc_r}` | Got: `{rc}` | "
            f"Decision: `{dec}` | Network: `{net.upper()}`"
        )

    # ── ISO 8583 ↔ JPF contrast panel ────────────────────────────────────────
    iso_msg      = trace.get("iso_message", {})
    jpf_data     = trace.get("jpf", {})
    iso_warnings = trace.get("iso_warnings", [])

    if iso_msg and "error" not in iso_msg:
        st.markdown(
            f'<h4>🌐 ISO 8583 ↔ JPF — Network: '
            f'<span style="background:{_nc};color:#fff;padding:2px 10px;'
            f'border-radius:4px;font-size:0.9em">{net.upper()}</span>'
            f'&nbsp; MTI: <code>{iso_msg.get("mti","?")}</code>'
            f'&nbsp; STAN: <code>{iso_msg.get("stan","?")}</code>'
            f'&nbsp; RRN: <code>{iso_msg.get("rrn","?")}</code>'
            f'</h4>',
            unsafe_allow_html=True,
        )
        for w in iso_warnings:
            st.warning(f"⚠️ ISO: {w}")

        iso_col, jpf_col = st.columns(2)

        with iso_col:
            st.markdown("**📦 ISO 8583 Fields**")
            private_des = set(str(d) for d in iso_msg.get("private_des", []))
            fields = iso_msg.get("fields", {})
            try:
                import pandas as pd
                rows_iso = []
                for de_key in sorted(fields.keys(), key=lambda x: int(x) if x.isdigit() else 999):
                    is_priv = de_key in private_des
                    rows_iso.append({
                        "DE":      f"DE{de_key}",
                        "Value":   fields[de_key],
                        "Private": "★" if is_priv else "",
                    })
                df_iso = pd.DataFrame(rows_iso)

                def _hl(row):
                    return (
                        ["background-color: #fff3cd"] * len(row)
                        if row["Private"] == "★" else
                        [""] * len(row)
                    )

                st.dataframe(
                    df_iso.style.apply(_hl, axis=1),
                    use_container_width=True, height=420,
                )
                st.caption("★ = network-private DE")
            except ImportError:
                st.json(fields)

        with jpf_col:
            st.markdown("**📋 Canonical JPF**")
            st.json(jpf_data)
            if iso_msg.get("packed_hex"):
                with st.expander("Packed hex (wire bytes)", expanded=False):
                    st.code(iso_msg["packed_hex"], language="text")
                    st.caption(f"{len(iso_msg['packed_hex']) // 2} bytes")

    # ── Full audit trail ──────────────────────────────────────────────────────
    with st.expander("🔍 Audit Trail", expanded=False):
        for step in trace.get("audit_trail", []):
            dir_sym = step.get("direction", "→")
            color   = "#1f77b4" if dir_sym == "→" else "#2ca02c"
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

    # ── Grouped audit trail + per-category playback ───────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Transaction Flow (Grouped)")

    audit = trace.get("audit_trail", [])

    # Resolve network for dynamic node labels (fix: was always "visa")
    _tb_network = (
        (trace.get("iso_message") or {}).get("network")
        or trace.get("detected_network")
        or network_val
        or "visa"
    )

    # Step grouping — maps audit step numbers to phase category
    _STEP_GROUPS = {
        "1 — Card Origination": {
            "steps": [1, 2],
            "icon":  "💳",
            "desc":  "Cardholder tap / swipe — terminal captures PAN & entry mode",
        },
        "2 — Acquirer Outbound": {
            "steps": [3],
            "icon":  "🏦",
            "desc":  "Acquirer builds and forwards ISO 8583 authorization request",
        },
        "3 — Network Transit": {
            "steps": [4, 5],
            "icon":  "🌐",
            "desc":  "Network routes message; Marqeta dispatches JIT webhook",
        },
        "4 — JIT Authorization": {
            "steps": [6],
            "icon":  "✅",
            "desc":  "Customer JIT webhook makes approve / decline decision",
        },
        "5 — Response Path": {
            "steps": [7, 8, 9],
            "icon":  "↩️",
            "desc":  "Network → Acquirer → Terminal: response code propagates back",
        },
    }

    step_map = {e.get("step"): e for e in audit}

    speed = st.session_state.get("tb_demo_speed", 1.5)

    for group_label, ginfo in _STEP_GROUPS.items():
        group_steps = [step_map[s] for s in ginfo["steps"] if s in step_map]
        if not group_steps:
            continue

        with st.expander(
            f"{ginfo['icon']} **{group_label}** — {ginfo['desc']}",
            expanded=False,
        ):
            # Static summary table for this group
            for entry in group_steps:
                dir_sym = entry.get("direction", "→")
                color   = "#1f77b4" if dir_sym == "→" else "#2ca02c"
                st.markdown(
                    f'<div style="border-left:3px solid {color};'
                    f'padding:4px 12px;margin-bottom:4px">'
                    f'<b>Step {entry["step"]}</b>: {entry.get("actor","")} '
                    f'<span style="color:{color}">{dir_sym}</span> '
                    f'<em>{entry.get("label","")}</em>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if entry.get("payload"):
                    with st.expander(
                        f"📦 Payload — {entry.get('actor','')} (step {entry['step']})",
                        expanded=False,
                    ):
                        st.json(entry["payload"])

            # Per-category playback button
            if st.session_state.demo_mode:
                play_key = f"play_group_{group_label}"
                if st.button(
                    f"▶ Play {ginfo['icon']} {group_label}",
                    key=play_key,
                    type="secondary",
                ):
                    n_ph = st.empty()
                    p_ph = st.empty()
                    for entry in group_steps:
                        node_idx = _STEP_NODE_MAP.get(entry.get("step", 1), 0)
                        n_ph.markdown(
                            render_node_diagram(node_idx, network=_tb_network),
                            unsafe_allow_html=True,
                        )
                        render_playback_step(
                            entry, entry.get("step"), len(audit),
                            network=_tb_network,
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
                            time.sleep(speed * 0.3)
                    n_ph.empty()

    # Full play-all button (demo mode only)
    if st.session_state.demo_mode:
        st.markdown("---")
        st.markdown("#### 🎬 Full Transaction Replay")
        if st.button("▶ Play Full Transaction (all steps)", type="primary", key="tb_play_all"):
            n_ph = st.empty()
            p_ph = st.empty()
            for entry in audit:
                node_idx = _STEP_NODE_MAP.get(entry.get("step", 1), 0)
                n_ph.markdown(
                    render_node_diagram(node_idx, network=_tb_network),
                    unsafe_allow_html=True,
                )
                render_playback_step(
                    entry, entry.get("step"), len(audit),
                    network=_tb_network,
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
                    time.sleep(speed * 0.3)
            n_ph.empty()
            if dec == "APPROVED":
                st.success(f"✅ Transaction Complete — APPROVED (RC: {rc})")
            else:
                st.error(f"❌ Transaction Complete — {dec} (RC: {rc})")

# ── Test card reference table ─────────────────────────────────────────────────
st.markdown("---")
with st.expander("📋 Test Card Reference (T0.3)", expanded=False):
    st.markdown("""
| Network     | PAN                     | Length | Notes                            |
|-------------|-------------------------|--------|----------------------------------|
| Visa        | `4111 1111 1111 1111`   | 16     | Luhn-valid; routes via BIN `41`  |
| Mastercard  | `5555 5555 5555 4444`   | 16     | Luhn-valid; routes via BIN `55`  |
| Amex        | `3782 822463 10005`     | **15** | 4-digit CID; BIN `37`; AMSNET   |
| Discover    | `6011 1111 1111 1117`   | 16     | Luhn-valid; routes via BIN `6011`|

**Rules:**
- Amex PANs are 15 digits and use a 4-digit Card ID Code (CID), not 3-digit CVV.
- Entering a `4111…` PAN with network forced to **Amex** will produce a validation warning.
- A `37…` PAN auto-routes to Amex without needing a network override.
- The **network** stamped on the live transaction path (ledger, webhook, audit trail) now reflects the BIN routing result — not a hardcoded "VISANET".
    """)
