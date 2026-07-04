# frontend/pages/05_terminal_emulator.py
"""NFC / Chip Card Terminal Emulator — software APDU emulation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — Terminal Emulator", page_icon="📱", layout="wide")
inject_theme()

st.title("📱 NFC / Chip Card Terminal Emulator")
st.caption(
    "Software-only EMV chip card simulator. No physical hardware required. "
    "Emulates SELECT, GET DATA, VERIFY PIN, READ RECORD, PUT DATA, GENERATE AC."
)

# ── Card state panel ──────────────────────────────────────────────────────────
st.subheader("🃏 Card State")
col_refresh, col_reset_card = st.columns([1, 1])
if col_refresh.button("🔄 Refresh Card State", key="chip_refresh"):
    resp = api_post("/chip/command", {"command": "GET_STATE"})
    if resp:
        st.session_state.card_state = resp.get("card_state", {})

if col_reset_card.button("♻️ Reset Card to Factory", key="chip_factory_reset"):
    resp = api_post("/chip/command", {"command": "RESET_CARD"})
    if resp:
        st.session_state.apdu_log = []
        st.session_state.card_state = resp.get("card_state", {})
        st.success("Card reset to factory defaults.")
        st.rerun()

cs = st.session_state.card_state or {}
if cs:
    c1, c2, c3 = st.columns(3)
    c1.metric("AID",    cs.get("aid", "—"))
    c2.metric("PAN",    cs.get("pan_masked", "—"))
    c3.metric("Status", cs.get("card_status", "—"))
    c4, c5, c6 = st.columns(3)
    c4.metric("Expiry",     cs.get("expiry", "—"))
    c5.metric("Svc Code",   cs.get("service_code", "—"))
    c6.metric("PIN Tries",  cs.get("pin_tries_remaining", "—"))
else:
    st.info("Click **Refresh Card State** to load card info.")

st.markdown("---")

# ── APDU command tabs ─────────────────────────────────────────────────────────
tabs = st.tabs(["SELECT", "GET DATA", "VERIFY PIN", "READ RECORD", "PUT DATA", "GENERATE AC"])

AID_OPTS = {
    "A0000000031010": "Visa Credit",
    "A0000000032010": "Visa Debit",
    "A0000000041010": "Mastercard Credit",
    "A0000000043060": "Mastercard Debit",
    "A000000065":     "JCB",
    "A0000000251010": "Amex",
}

TAG_OPTS = {
    "5A":   "PAN (Primary Account Number)",
    "5F24": "Expiry Date (YYMM)",
    "5F30": "Service Code",
    "9F36": "ATC (Application Transaction Counter)",
    "4F":   "AID (Application Identifier)",
    "5F20": "Cardholder Name",
}

# Tab 0 — SELECT
with tabs[0]:
    st.markdown("#### SELECT Application")
    st.caption("Selects the payment application on the card by AID.")
    sel_aid = st.selectbox("AID", list(AID_OPTS.keys()),
                           format_func=lambda k: f"{k} ({AID_OPTS[k]})", key="chip_sel_aid")
    if st.button("Send SELECT", key="chip_btn_sel", type="primary"):
        r = api_post("/chip/command", {"command": "SELECT", "aid": sel_aid})
        if r:
            st.session_state.apdu_log.insert(0, r)
            st.session_state.card_state = r.get("card_state", {})
            sw = r.get("sw", "????")
            if sw == "9000":
                st.success(f"SW: {sw} — Application selected ✅")
            else:
                st.error(f"SW: {sw} — {r.get('status','')}")

# Tab 1 — GET DATA
with tabs[1]:
    st.markdown("#### GET DATA")
    st.caption("Read a specific EMV data element by tag.")
    sel_tag = st.selectbox("Tag", list(TAG_OPTS.keys()),
                           format_func=lambda k: f"{k} — {TAG_OPTS[k]}", key="chip_get_tag")
    if st.button("Send GET DATA", key="chip_btn_gd", type="primary"):
        r = api_post("/chip/command", {"command": "GET_DATA", "tag": sel_tag})
        if r:
            st.session_state.apdu_log.insert(0, r)
            sw = r.get("sw", "????")
            data = r.get("data", "")
            if sw == "9000":
                st.success(f"SW: {sw} | Data: `{data}`")
            else:
                st.error(f"SW: {sw} — {r.get('status','')}")

# Tab 2 — VERIFY PIN
with tabs[2]:
    st.markdown("#### VERIFY PIN")
    st.caption("Verify the cardholder PIN. Default factory PIN is `1234`.")
    pin_in = st.text_input("PIN", value="1234", type="password", key="chip_pin")
    if st.button("Send VERIFY PIN", key="chip_btn_vfy", type="primary"):
        r = api_post("/chip/command", {"command": "VERIFY", "pin": pin_in})
        if r:
            st.session_state.apdu_log.insert(0, r)
            st.session_state.card_state = r.get("card_state", {})
            sw = r.get("sw", "????")
            status = r.get("status", "")
            tries  = (r.get("card_state") or {}).get("pin_tries_remaining", "?")
            if sw == "9000":
                st.success(f"SW: {sw} — PIN Verified ✅")
            elif sw.startswith("63"):
                st.warning(f"SW: {sw} — Wrong PIN. Tries remaining: {tries}")
            elif sw == "6983":
                st.error("SW: 6983 — Card BLOCKED. Too many wrong PINs.")
            else:
                st.error(f"SW: {sw} — {status}")

# Tab 3 — READ RECORD
with tabs[3]:
    st.markdown("#### READ RECORD")
    st.caption("Read a specific record from a Short File Identifier (SFI).")
    sfi_v = st.number_input("SFI", min_value=1, max_value=31, value=1, key="chip_sfi")
    rec_v = st.number_input("Record #", min_value=1, max_value=255, value=1, key="chip_rec")
    if st.button("Send READ RECORD", key="chip_btn_rr", type="primary"):
        r = api_post("/chip/command", {"command": "READ_RECORD", "sfi": sfi_v, "record_num": rec_v})
        if r:
            st.session_state.apdu_log.insert(0, r)
            sw = r.get("sw", "????")
            data = r.get("data", "")
            if sw == "9000":
                st.success(f"SW: {sw} | Record hex: `{data}`")
            else:
                st.error(f"SW: {sw} — {r.get('status','')}")

# Tab 4 — PUT DATA
with tabs[4]:
    st.markdown("#### PUT DATA")
    st.caption("Write a value to an EMV data element (e.g., update expiry, cardholder name, or PIN).")
    PUT_TAG_OPTS = {
        "5F24":  "Expiry Date (YYMM hex, e.g. 3236 = 2026-06)",
        "5F20":  "Cardholder Name (hex or ASCII)",
        "PINCHG": "Change PIN (enter new PIN as plaintext)",
    }
    put_tag = st.selectbox("Tag", list(PUT_TAG_OPTS.keys()),
                           format_func=lambda t: f"{t} — {PUT_TAG_OPTS[t]}", key="chip_put_tag")
    put_val = st.text_input(
        "Value (hex for 5F24/5F20, plaintext for PINCHG)", key="chip_put_val"
    )
    if st.button("Send PUT DATA", key="chip_btn_put", type="primary"):
        val_hex = put_val.encode().hex() if put_tag == "PINCHG" else put_val
        r = api_post("/chip/command", {"command": "PUT_DATA", "tag": put_tag, "value": val_hex})
        if r:
            st.session_state.apdu_log.insert(0, r)
            sw = r.get("sw", "????")
            if sw == "9000":
                st.success(f"SW: {sw} — Data written ✅")
            else:
                st.error(f"SW: {sw} — {r.get('status','')}")

# Tab 5 — GENERATE AC
with tabs[5]:
    st.markdown("#### GENERATE AC")
    st.caption(
        "Generate an Application Cryptogram (ARQC). "
        "Used during online authorization to prove card authenticity."
    )
    cdol_in = st.text_input("CDOL Data (hex, optional)", value="", key="chip_cdol")
    if st.button("Send GENERATE AC", key="chip_btn_genac", type="primary"):
        r = api_post("/chip/command", {"command": "GENERATE_AC", "cdol_data": cdol_in})
        if r:
            st.session_state.apdu_log.insert(0, r)
            st.session_state.card_state = r.get("card_state", {})
            sw = r.get("sw", "????")
            data = r.get("data", "")
            atc = (r.get("card_state") or {}).get("atc", "?")
            if sw == "9000":
                st.success(f"SW: {sw} | ARQC: `{data}` | ATC: {atc}")
            else:
                st.error(f"SW: {sw} — {r.get('status','')}")

st.markdown("---")

# ── APDU Log ──────────────────────────────────────────────────────────────────
log_col1, log_col2 = st.columns([4, 1])
log_col1.subheader("📋 APDU Log")
if log_col2.button("🗑️ Clear Log", key="chip_clear_log"):
    st.session_state.apdu_log = []
    st.rerun()

log = st.session_state.apdu_log
if not log:
    st.info("No APDU commands sent yet. Use the tabs above to interact with the card.")
else:
    st.caption(f"Showing {min(len(log), 30)} most recent commands (newest first)")
    for i, entry in enumerate(log[:30]):
        sw = entry.get("sw", "????")
        color = "#28a745" if sw == "9000" else ("#fd7e14" if sw.startswith("63") else "#dc3545")
        cmd   = entry.get("command", "?")
        data  = entry.get("data", "")
        status = entry.get("status", "")
        data_part = f"<br>↳ DATA: <code>{data}</code>" if data else ""
        st.markdown(
            f'<div style="font-family:monospace;font-size:0.85em;border-left:4px solid {color};'
            f'padding:6px 12px;margin-bottom:6px;background:#fafafa;border-radius:0 4px 4px 0">'
            f'[{i+1:02d}] <b>{cmd}</b> → SW: <b style="color:{color}">{sw}</b> '
            f'<span style="color:#555">({status})</span>{data_part}</div>',
            unsafe_allow_html=True,
        )
