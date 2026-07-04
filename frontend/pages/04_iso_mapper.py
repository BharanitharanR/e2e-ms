# frontend/pages/04_iso_mapper.py
"""ISO ↔ JPOS ↔ JPF three-column canonical conversion workbench.

P1 improvements:
  T1.1 — Network selector drives the DE set (private DEs change per network).
  T1.2 — Editable DE workbench: edit iso_wire_values → Translate → live map_to_jpf().
          Inline DE4↔9F02 and DE49↔5F2A validation mismatch flags.
  T1.3 — "Simulate this from the wire" handoff to /execute_adhoc.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — ISO Mapper", page_icon="🔄", layout="wide")
inject_theme()

st.title("🔄 ISO 8583 ↔ JPOS ↔ JPF Canonical Workbench")
st.caption(
    "Visualise how an issuer processor like Marqeta translates network scheme ISO 8583 messages "
    "into an internal JPOS canonical form and then into the JPF (JSON) payload sent to "
    "your JIT Funding endpoint."
)

# ── Load iso_mapping module ───────────────────────────────────────────────────
try:
    from iso_mapping import DEFAULT_ISO_JPF_MAPPING, extract_iso_jpf_values
    _iso_ok = True
except ImportError:
    _iso_ok = False

if not _iso_ok:
    st.error("iso_mapping.py not found in the frontend directory. Please check your installation.")
    st.stop()

if st.session_state.iso_jpf_mapping is None:
    st.session_state.iso_jpf_mapping = list(DEFAULT_ISO_JPF_MAPPING)

# ── Private DEs per network (T1.1) ────────────────────────────────────────────
# These supplement the base DE set when a specific network is selected.
_NETWORK_PRIVATE_DES = {
    "visa": [
        {"de": "DE44", "iso_name": "Additional Response Data",    "jpf_field": "additional_response_data", "description": "Visa: additional auth response data",       "transform": "passthrough"},
        {"de": "DE62", "iso_name": "Custom Payment Service",      "jpf_field": "visa_tid",                 "description": "Visa Transaction ID (DE62)",               "transform": "passthrough"},
        {"de": "DE63", "iso_name": "Network Data (V.I.P.)",       "jpf_field": "network_data",             "description": "Visa V.I.P. network data",                 "transform": "passthrough"},
    ],
    "mastercard": [
        {"de": "DE48", "iso_name": "Additional Data (PDS)",       "jpf_field": "mc_pds",                   "description": "Mastercard PDS sub-elements (DE48)",       "transform": "passthrough"},
        {"de": "DE61", "iso_name": "POS Data",                    "jpf_field": "mc_pos_data",              "description": "Mastercard POS data (DE61)",               "transform": "passthrough"},
        {"de": "DE63", "iso_name": "Network Data (Banknet)",      "jpf_field": "banknet_ref",              "description": "Mastercard Banknet reference (DE63)",      "transform": "passthrough"},
    ],
    "amex": [
        {"de": "DE47", "iso_name": "Additional Data (National)",  "jpf_field": "amex_national_data",       "description": "Amex national-use additional data (DE47)", "transform": "passthrough"},
        {"de": "DE63", "iso_name": "Network Data",                "jpf_field": "amex_network_data",        "description": "Amex network data (DE63)",                 "transform": "passthrough"},
    ],
    "discover": [
        {"de": "DE62", "iso_name": "Additional Data (Discover)",  "jpf_field": "disc_additional_data",     "description": "Discover additional data (DE62)",          "transform": "passthrough"},
        {"de": "DE63", "iso_name": "Network Data",                "jpf_field": "disc_network_data",        "description": "Discover network data (DE63)",             "transform": "passthrough"},
    ],
}

# ── Default wire values per network for the workbench (T1.2) ─────────────────
_DEFAULT_WIRE_VALUES = {
    "visa": {
        "2":  "4111111111111111",
        "3":  "000000",
        "4":  "000000002500",
        "7":  "0101120000",
        "11": "123456",
        "12": "120000",
        "18": "5411",
        "22": "051",
        "37": "123456789012",
        "41": "TERM0001",
        "42": "MERCH000001",
        "43": "Test Merchant San Francisco CA",
        "49": "840",
        "62": "VISA_TID_123456",
        "63": "VIP_DATA_123456",
    },
    "mastercard": {
        "2":  "5555555555554444",
        "3":  "000000",
        "4":  "000000002500",
        "7":  "0101120000",
        "11": "234567",
        "12": "120000",
        "18": "5411",
        "22": "051",
        "37": "234567890123",
        "41": "TERM0002",
        "42": "MERCH000002",
        "43": "Test Merchant New York NY",
        "49": "840",
        "48": "MC_PDS_234567",
        "61": "MC_POS_234567",
        "63": "BANKNET_234567",
    },
    "amex": {
        "2":  "378282246310005",
        "3":  "000000",
        "4":  "000000002500",
        "7":  "0101120000",
        "11": "345678",
        "12": "120000",
        "18": "5411",
        "22": "051",
        "37": "345678901234",
        "41": "TERM0003",
        "42": "MERCH000003",
        "43": "Test Merchant Chicago IL",
        "49": "840",
        "47": "AMEX_NAT_345678",
        "63": "AMEX_NET_345678",
    },
    "discover": {
        "2":  "6011111111111117",
        "3":  "000000",
        "4":  "000000002500",
        "7":  "0101120000",
        "11": "456789",
        "12": "120000",
        "18": "5411",
        "22": "051",
        "37": "456789012345",
        "41": "TERM0004",
        "42": "MERCH000004",
        "43": "Test Merchant Houston TX",
        "49": "840",
        "62": "DISC_ADD_456789",
        "63": "DISC_NET_456789",
    },
}

# ISO 4217 numeric → alpha lookup
_CURRENCY_MAP = {
    "840": "USD", "978": "EUR", "826": "GBP", "124": "CAD",
    "036": "AUD", "392": "JPY", "756": "CHF", "356": "INR",
}


def _get_effective_mapping(network: str) -> list:
    """Return the base mapping + network-private DEs for the selected network."""
    base = list(st.session_state.iso_jpf_mapping)
    private = _NETWORK_PRIVATE_DES.get(network, [])
    # Only add private DEs not already present in the base
    existing_des = {r.get("de") for r in base}
    for row in private:
        if row["de"] not in existing_des:
            base.append(row)
    return base


def _apply_wire_transform(transform: str, value: str) -> str:
    """Apply a transform for the wire-→-JPF column in the workbench."""
    if transform in ("passthrough", ""):
        return value
    if transform == "tokenize":
        v = str(value)
        return v[:4] + "****" + v[-4:] if len(v) > 8 else "****" + v[-4:]
    if transform == "format_iso8601":
        return value
    if transform == "extract_time":
        try:
            return str(value).split("T")[1][:8].replace(":", "")
        except Exception:
            return value
    if transform == "truncate_25":
        return str(value)[:25]
    if transform == "numeric_to_alpha":
        return _CURRENCY_MAP.get(str(value), str(value))
    return value


def _check_emv_agreement(wire_values: dict) -> list:
    """Check DE4↔9F02 (amount) and DE49↔5F2A (currency) agreements.
    Returns a list of warning strings."""
    warnings = []
    de4 = wire_values.get("4", "")
    tag9f02 = wire_values.get("9F02", wire_values.get("9f02", ""))
    if de4 and tag9f02:
        try:
            de4_int = int(de4.lstrip("0") or "0")
            tag_int = int(tag9f02, 16) if all(c in "0123456789abcdefABCDEF" for c in tag9f02) else int(tag9f02)
            if de4_int != tag_int:
                warnings.append(f"⚠️ **Amount mismatch**: DE4=`{de4}` ({de4_int}) vs 9F02=`{tag9f02}` ({tag_int})")
        except Exception:
            pass
    de49 = wire_values.get("49", "")
    tag5f2a = wire_values.get("5F2A", wire_values.get("5f2a", ""))
    if de49 and tag5f2a:
        if str(de49) != str(tag5f2a):
            warnings.append(f"⚠️ **Currency mismatch**: DE49=`{de49}` vs 5F2A=`{tag5f2a}`")
    return warnings


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_workbench, tab_live, tab_map, tab_explain = st.tabs([
    "🔧 DE Workbench",
    "🔭 Live Translation",
    "📐 Mapping Table",
    "📖 DE Reference",
])

# ── Tab 1: DE Workbench (T1.1 + T1.2 + T1.3) ─────────────────────────────────
with tab_workbench:
    st.subheader("Network-Aware DE Workbench")
    st.caption(
        "Select a network to load its private DE set. Edit wire values in Column 1, "
        "click Translate to see JPOS canonical → JPF output. "
        "Validation flags highlight DE4↔9F02 and DE49↔5F2A mismatches."
    )

    # T1.1 — Network selector
    wb_col_net, wb_col_btn = st.columns([3, 1])
    network_sel = wb_col_net.selectbox(
        "Network",
        options=["visa", "mastercard", "amex", "discover"],
        format_func=lambda n: {
            "visa":       "🟦 Visa",
            "mastercard": "🟥 Mastercard",
            "amex":       "🟩 American Express",
            "discover":   "🟧 Discover",
        }[n],
        key="wb_network",
    )

    # Load default wire values when network changes
    _wire_key = f"wb_wire_{network_sel}"
    if _wire_key not in st.session_state:
        st.session_state[_wire_key] = dict(_DEFAULT_WIRE_VALUES.get(network_sel, {}))

    wire_values: dict = st.session_state[_wire_key]
    effective_mapping = _get_effective_mapping(network_sel)

    # T1.2 — Three-column layout: ISO wire | JPOS canonical | JPF
    translate_btn = wb_col_btn.button("🔄 Translate", type="primary", key="wb_translate")

    # Validation warnings
    emv_warns = _check_emv_agreement(wire_values)
    if emv_warns:
        for w in emv_warns:
            st.warning(w)

    st.markdown("---")
    col_wire, col_jpos, col_jpf = st.columns(3)
    col_wire.markdown("#### 📡 Column 1 — ISO 8583 Wire")
    col_jpos.markdown("#### ⚙️ Column 2 — JPOS Canonical")
    col_jpf.markdown("#### 📦 Column 3 — JPF")

    col_wire.caption("Edit values; click **Translate** to propagate.")
    col_jpos.caption("Transform applied (read-only)")
    col_jpf.caption("JPF field name + transformed value (read-only)")

    updated_wire: dict = {}
    for row in effective_mapping:
        de_num = row.get("de", "").replace("DE", "")
        iso_name = row.get("iso_name", "")
        jpf_field = row.get("jpf_field", "")
        transform = row.get("transform", "passthrough")
        current_val = wire_values.get(de_num, "")

        # Column 1 — Editable input
        new_val = col_wire.text_input(
            f"{row.get('de','')} — {iso_name}",
            value=current_val,
            key=f"wb_de_{network_sel}_{de_num}",
            help=row.get("description", ""),
        )
        updated_wire[de_num] = new_val

        # Column 2 — JPOS canonical (transform display)
        arrow = "🔄" if transform not in ("passthrough", "") else "→"
        transformed_val = _apply_wire_transform(transform, new_val) if new_val else "(none)"
        col_jpos.markdown(
            f'<div style="font-size:0.8em;border:1px solid #b8daff;background:#e8f4fd;'
            f'border-radius:4px;padding:6px 8px;margin-bottom:6px;margin-top:6px">'
            f'{arrow} <em style="color:#4a6a8a">{transform}</em><br>'
            f'<code style="color:#0a1730">{transformed_val if new_val else "(none)"}</code>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Column 3 — JPF field + value
        jpf_val_display = transformed_val if new_val else "(not set)"
        col_jpf.markdown(
            f'<div style="font-size:0.8em;border:1px solid #c3e6cb;background:#d4edda;'
            f'border-radius:4px;padding:6px 8px;margin-bottom:6px;margin-top:6px">'
            f'<b style="color:#0a5c2b">{jpf_field}</b><br>'
            f'<code style="color:#0a1730">{jpf_val_display}</code>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Persist updated wire values on Translate
    if translate_btn:
        st.session_state[_wire_key] = updated_wire
        st.success(f"✅ Translated {len(updated_wire)} DE fields for **{network_sel.capitalize()}**")

    # T1.3 — "Simulate this from the wire" handoff to /execute_adhoc
    st.markdown("---")
    st.subheader("🚀 Simulate from the Wire")
    st.caption(
        "Sends the current wire DE values as an ad-hoc transaction to your SUT "
        "via `/execute_adhoc`. The response code and JIT decision are returned."
    )
    sim_col1, sim_col2, sim_col3 = st.columns([2, 1, 2])
    exp_rc  = sim_col1.text_input("Expected RC",       value="00",       key="wb_exp_rc")
    exp_dec = sim_col2.selectbox("Expected Decision",  ["APPROVED", "DECLINED"], key="wb_exp_dec")

    if sim_col3.button("▶ Simulate from Wire", type="primary", key="wb_simulate"):
        wv = st.session_state.get(_wire_key, updated_wire)

        # Build adhoc body from current wire values
        _pan = wv.get("2", _DEFAULT_WIRE_VALUES[network_sel]["2"])
        try:
            _amount = int(wv.get("4", "000000002500").lstrip("0") or "0")
        except ValueError:
            _amount = 2500
        _currency = wv.get("49", "840")
        _mcc      = wv.get("18", "5411")
        _merchant = wv.get("43", "Wire Merchant")[:25]
        _pos_mode = wv.get("22", "051")

        adhoc_body = {
            "pan":               _pan,
            "network":           network_sel,
            "amount":            _amount,
            "currency":          _currency,
            "mcc":               _mcc,
            "merchant_name":     _merchant,
            "pos_entry_mode":    _pos_mode,
            "expected_rc":       exp_rc,
            "expected_decision": exp_dec,
        }

        with st.spinner("Sending to SUT…"):
            sim_trace = api_post("/execute_adhoc", adhoc_body)

        if sim_trace and "error" not in sim_trace:
            st.session_state.last_trace = sim_trace
            ok   = sim_trace.get("passed", False)
            a_rc = sim_trace.get("actual_network_response_code", "?")
            a_dec = sim_trace.get("actual_customer_decision", "?")
            net  = sim_trace.get("request_sent", {}).get("network", network_sel)
            if ok:
                st.success(f"✅ PASSED — Network: {net.capitalize()} | RC: `{a_rc}` | Decision: {a_dec}")
            else:
                st.error(f"❌ FAILED — Network: {net.capitalize()} | RC: `{a_rc}` | Decision: {a_dec}")
            warns = sim_trace.get("adhoc_warnings", [])
            if warns:
                for w in warns:
                    st.caption(f"⚠️ {w}")
        else:
            st.error(f"Simulation error: {(sim_trace or {}).get('error', 'unknown')}")

    st.markdown("---")
    c_reset_wb, _ = st.columns([1, 3])
    if c_reset_wb.button("↩️ Reset Wire Values to Defaults", key="wb_reset"):
        if _wire_key in st.session_state:
            del st.session_state[_wire_key]
        st.rerun()


# ── Tab 2: Live Translation ───────────────────────────────────────────────────
with tab_live:
    st.subheader("Three-Column Canonical Conversion View")
    st.caption(
        "Run any scenario then see its values mapped across ISO wire → JPOS canonical → JPF JSON."
    )

    scenarios_resp = api_get("/scenarios", params={"limit": 200})
    scenarios = (scenarios_resp or {}).get("items", [])
    if scenarios:
        sc_map = {s["id"]: s["name"] for s in scenarios}
        col_sc, col_run = st.columns([4, 1])
        live_sc = col_sc.selectbox("Scenario", list(sc_map.keys()),
                                   format_func=lambda k: sc_map[k], key="iso_live_sc")
        if col_run.button("▶ Run", type="primary", key="iso_live_run"):
            with st.spinner("Executing…"):
                trace = api_post(f"/execute/{live_sc}")
            if trace and "error" not in trace:
                st.session_state.last_trace = trace

    trace = st.session_state.last_trace
    if trace and "error" not in trace:
        iso_rows = extract_iso_jpf_values(
            st.session_state.iso_jpf_mapping,
            trace.get("request_sent", {}),
            trace.get("response_received", {}),
        )

        # Three-column layout
        col_iso, col_jpos, col_jpf = st.columns(3)
        col_iso.markdown("#### 📡 ISO 8583 Wire")
        col_jpos.markdown("#### ⚙️ JPOS Canonical")
        col_jpf.markdown("#### 📦 JPF")

        for row in iso_rows:
            col_iso.markdown(
                f'<div style="font-size:0.8em;border:1px solid #ddd;border-radius:4px;'
                f'padding:4px 8px;margin-bottom:4px">'
                f'<b>{row["de"]}</b> {row["iso_name"]}<br>'
                f'<code>{row["iso_value"]}</code></div>',
                unsafe_allow_html=True,
            )
            transform_label = row.get("transform", "passthrough")
            arrow = "🔄" if row.get("transformed") else "→"
            col_jpos.markdown(
                f'<div style="font-size:0.8em;border:1px solid #b8daff;background:#e8f4fd;'
                f'border-radius:4px;padding:4px 8px;margin-bottom:4px">'
                f'{arrow} <em>{transform_label}</em><br>'
                f'<code>{row["jcf_value"]}</code></div>',
                unsafe_allow_html=True,
            )
            col_jpf.markdown(
                f'<div style="font-size:0.8em;border:1px solid #c3e6cb;background:#d4edda;'
                f'border-radius:4px;padding:4px 8px;margin-bottom:4px">'
                f'<b>{row["jpf_field"]}</b><br>'
                f'<code>{row["jcf_value"]}</code></div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("Run a scenario above to see the live translation.")


# ── Tab 3: Mapping Table ──────────────────────────────────────────────────────
with tab_map:
    st.subheader("ISO 8583 DE ↔ JPOS ↔ JPF Mapping")
    st.caption("Editable — changes are session-scoped and reflect immediately in Live Translation.")
    edited = st.data_editor(
        pd.DataFrame(st.session_state.iso_jpf_mapping),
        num_rows="dynamic", use_container_width=True,
        column_config={
            "de":          st.column_config.TextColumn("DE #",        width=80),
            "iso_name":    st.column_config.TextColumn("ISO Name",    width=200),
            "jpf_field":   st.column_config.TextColumn("JPF Field",   width=150),
            "description": st.column_config.TextColumn("Description", width=250),
            "transform":   st.column_config.SelectboxColumn(
                "Transform", width=160,
                options=["passthrough","tokenize","format_iso8601",
                         "extract_time","truncate_25","numeric_to_alpha"]),
        }, key="iso_tbl_mapper")
    st.session_state.iso_jpf_mapping = edited.to_dict("records")
    c_reset, c_dl = st.columns(2)
    if c_reset.button("↩️ Reset to defaults", key="iso_reset_mapper"):
        st.session_state.iso_jpf_mapping = list(DEFAULT_ISO_JPF_MAPPING)
        st.rerun()
    csv_data = pd.DataFrame(st.session_state.iso_jpf_mapping).to_csv(index=False)
    c_dl.download_button("⬇ Export CSV", data=csv_data,
                         file_name="iso_jpf_mapping.csv", mime="text/csv", key="iso_csv")


# ── Tab 4: DE Reference ───────────────────────────────────────────────────────
with tab_explain:
    st.subheader("ISO 8583 Data Element Quick Reference")
    reference = [
        ("DE2",  "Primary Account Number (PAN)", "Up to 19 digits. Tokenised by Marqeta."),
        ("DE3",  "Processing Code",              "6-digit code (purchase=000000, refund=200000, cash=010000)."),
        ("DE4",  "Amount, Transaction",          "12-digit right-justified, amount in minor units (cents). Must match EMV 9F02."),
        ("DE7",  "Transmission Date & Time",     "MMDDhhmmss UTC."),
        ("DE11", "STAN",                         "6-digit System Trace Audit Number — unique per message."),
        ("DE12", "Local Transaction Time",       "hhmmss — local time at merchant."),
        ("DE18", "Merchant Type (MCC)",          "4-digit ISO 18245 Merchant Category Code."),
        ("DE22", "POS Entry Mode",               "3-digit: 051=chip, 071=contactless, 010=manual, 002=magnetic stripe."),
        ("DE37", "Retrieval Reference Number",   "12-char alphanumeric assigned by acquirer."),
        ("DE38", "Authorization ID Response",    "6-char approval code from issuer."),
        ("DE39", "Response Code",                "2-char ISO 8583 response code (00=approved, 05=declined…)."),
        ("DE41", "Terminal ID",                  "8-char terminal identifier."),
        ("DE42", "Card Acceptor ID Code",        "15-char merchant/acquirer institution code."),
        ("DE43", "Card Acceptor Name/Location",  "≤40 chars: merchant name + city + state + country."),
        ("DE44", "Additional Response Data",     "[Visa] Auth response data — CVV2 result, street/ZIP verification."),
        ("DE47", "Additional Data (National)",   "[Amex] National-use additional data."),
        ("DE48", "Additional Data (PDS)",        "[Mastercard] Payment Detail Sub-elements — sub-element coded data."),
        ("DE49", "Currency Code, Transaction",   "3-digit ISO 4217 numeric (840=USD, 978=EUR, 826=GBP). Must match EMV 5F2A."),
        ("DE61", "POS Data",                     "[Mastercard] POS data — terminal type, cardholder presence, etc."),
        ("DE62", "Custom Payment Service",       "[Visa] Visa Transaction ID (TID). [Discover] Additional data."),
        ("DE63", "Network Data",                 "[Visa] V.I.P. network data. [Mastercard] Banknet reference. [Amex/Discover] Network data."),
    ]
    ref_df = pd.DataFrame(reference, columns=["DE", "Name", "Notes"])
    st.dataframe(ref_df, use_container_width=True, hide_index=True)
    st.caption(
        "**Transform legend:** "
        "`passthrough` — copied verbatim; "
        "`tokenize` — PAN replaced with Marqeta card_token; "
        "`format_iso8601` — date converted to ISO-8601 UTC; "
        "`extract_time` — time component extracted (HHMMSS); "
        "`truncate_25` — string truncated to 25 characters; "
        "`numeric_to_alpha` — ISO 4217 numeric → 3-letter alpha code."
    )
    st.markdown("---")
    st.subheader("EMV Tag Agreement Rules")
    st.markdown("""
| Rule | DE | vs EMV Tag | Meaning |
|------|----|-----------|---------|
| Amount | DE4 | 9F02 | Transaction amount in minor units must match |
| Currency | DE49 | 5F2A | ISO 4217 numeric currency code must match |

These are validated in the **DE Workbench** automatically when both fields are populated.
    """)
