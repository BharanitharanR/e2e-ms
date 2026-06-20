# frontend/pages/04_iso_mapper.py
"""ISO ↔ JPBOS ↔ JCF three-column canonical conversion workbench."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state

init_session_state()
st.set_page_config(page_title="e2MS — ISO Mapper", page_icon="🔄", layout="wide")

st.title("🔄 ISO 8583 ↔ JPBOS ↔ JCF Canonical Workbench")
st.caption(
    "Visualise how an issuer processor like Marqeta translates network scheme ISO 8583 messages "
    "into an internal JPBOS canonical form and then into the Marqeta JCF (JSON) payload sent to "
    "your JIT Funding endpoint."
)

# ── Load iso_mapping module ───────────────────────────────────────────────────
try:
    from iso_mapping import DEFAULT_ISO_JCF_MAPPING, extract_iso_jcf_values
    _iso_ok = True
except ImportError:
    _iso_ok = False

if not _iso_ok:
    st.error("iso_mapping.py not found in the frontend directory. Please check your installation.")
    st.stop()

if st.session_state.iso_jcf_mapping is None:
    st.session_state.iso_jcf_mapping = list(DEFAULT_ISO_JCF_MAPPING)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_map, tab_live, tab_explain = st.tabs([
    "📐 Mapping Table",
    "🔭 Live Translation",
    "📖 DE Reference",
])

# ── Tab 1: Mapping table ──────────────────────────────────────────────────────
with tab_map:
    st.subheader("ISO 8583 DE ↔ JPBOS ↔ JCF Mapping")
    st.caption("Editable — changes are session-scoped and reflect immediately in Live Translation.")
    edited = st.data_editor(
        pd.DataFrame(st.session_state.iso_jcf_mapping),
        num_rows="dynamic", use_container_width=True,
        column_config={
            "de":          st.column_config.TextColumn("DE #",        width=80),
            "iso_name":    st.column_config.TextColumn("ISO Name",    width=200),
            "jcf_field":   st.column_config.TextColumn("JCF Field",   width=150),
            "description": st.column_config.TextColumn("Description", width=250),
            "transform":   st.column_config.SelectboxColumn(
                "Transform", width=160,
                options=["passthrough","tokenize","format_iso8601",
                         "extract_time","truncate_25","numeric_to_alpha"]),
        }, key="iso_tbl_mapper")
    st.session_state.iso_jcf_mapping = edited.to_dict("records")
    c_reset, c_dl = st.columns(2)
    if c_reset.button("↩️ Reset to defaults", key="iso_reset_mapper"):
        st.session_state.iso_jcf_mapping = list(DEFAULT_ISO_JCF_MAPPING)
        st.rerun()
    csv_data = pd.DataFrame(st.session_state.iso_jcf_mapping).to_csv(index=False)
    c_dl.download_button("⬇ Export CSV", data=csv_data,
                         file_name="iso_jcf_mapping.csv", mime="text/csv", key="iso_csv")

# ── Tab 2: Live Translation ───────────────────────────────────────────────────
with tab_live:
    st.subheader("Three-Column Canonical Conversion View")
    st.caption(
        "Run any scenario then see its values mapped across ISO wire → JPBOS canonical → JCF JSON."
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
        iso_rows = extract_iso_jcf_values(
            st.session_state.iso_jcf_mapping,
            trace.get("request_sent", {}),
            trace.get("response_received", {}),
        )

        # Three-column layout
        col_iso, col_jpbos, col_jcf = st.columns(3)
        col_iso.markdown("#### 📡 ISO 8583 Wire")
        col_jpbos.markdown("#### ⚙️ JPBOS Canonical")
        col_jcf.markdown("#### 📦 Marqeta JCF")

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
            col_jpbos.markdown(
                f'<div style="font-size:0.8em;border:1px solid #b8daff;background:#e8f4fd;'
                f'border-radius:4px;padding:4px 8px;margin-bottom:4px">'
                f'{arrow} <em>{transform_label}</em><br>'
                f'<code>{row["jcf_value"]}</code></div>',
                unsafe_allow_html=True,
            )
            col_jcf.markdown(
                f'<div style="font-size:0.8em;border:1px solid #c3e6cb;background:#d4edda;'
                f'border-radius:4px;padding:4px 8px;margin-bottom:4px">'
                f'<b>{row["jcf_field"]}</b><br>'
                f'<code>{row["jcf_value"]}</code></div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("Run a scenario above to see the live translation.")

# ── Tab 3: DE Reference ───────────────────────────────────────────────────────
with tab_explain:
    st.subheader("ISO 8583 Data Element Quick Reference")
    reference = [
        ("DE2",  "Primary Account Number (PAN)", "Up to 19 digits. Tokenised by Marqeta."),
        ("DE3",  "Processing Code",              "6-digit code (purchase=000000, refund=200000, cash=010000)."),
        ("DE4",  "Amount, Transaction",          "12-digit right-justified, amount in minor units (cents)."),
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
        ("DE49", "Currency Code, Transaction",   "3-digit ISO 4217 numeric (840=USD, 978=EUR, 826=GBP)."),
        ("DE63", "Network Data",                 "Variable — network-specific routing/metadata."),
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
