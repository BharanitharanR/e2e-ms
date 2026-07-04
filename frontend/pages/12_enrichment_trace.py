# frontend/pages/12_enrichment_trace.py
"""ISO Enrichment Trace — horizontal per-hop visualization (T1.2).

Shows what each hop ADDS to the ISO 8583 message, with drill-down into the
cumulative message, ISO→JPF transformation, and JIT webhook at the Issuer hop.
Renders left-to-right: Terminal → Acquirer → Network → Issuer → JIT.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme, provider_badge_html

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — ISO Enrichment Trace", page_icon="🔬", layout="wide")
inject_theme()
# ── AI provider status badge ─────────────────────────────────────────────────
_providers_resp = (lambda r: r if r else {})((__import__('utils.api_client', fromlist=['api_get']).api_get('/ai/providers')))
_primary = _providers_resp.get('primary', 'claude')
_pmap = {p['provider']: p for p in _providers_resp.get('providers', [])}
_detected = (_pmap.get(_primary) or {}).get('key_status') == 'detected'
st.sidebar.markdown(provider_badge_html(_primary, _detected), unsafe_allow_html=True)

st.title("🔬 ISO 8583 Enrichment Trace")
st.caption(
    "See exactly what **each hop** adds to the authorization message as it flows "
    "Terminal → Acquirer → Network → Issuer Processor → Customer JIT. "
    "Click any hop to drill down into the cumulative ISO fields, ISO→JPF mapping, and JIT webhook."
)

# ── Network colour palette ─────────────────────────────────────────────────────
_NET_COLOURS = {
    "visa":       "#1a1f71",
    "mastercard": "#eb001b",
    "amex":       "#007bc1",
    "discover":   "#f76f20",
}
_HOP_ICONS = {
    "Terminal":                    "🏪",
    "Acquirer":                    "🏦",
    "Network":                     "🌐",
    "Marqeta Issuer Processor":    "⚙️",
    "Customer JIT (SUT)":          "✅",
}

def _hop_icon(actor: str) -> str:
    for key, icon in _HOP_ICONS.items():
        if key.lower() in actor.lower():
            return icon
    return "🔷"


def _badge(text: str, colour: str = "#555") -> str:
    return (
        f'<span style="background:{colour};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8em;font-weight:bold;white-space:nowrap">{text}</span>'
    )


def _render_adds_table(adds: list[dict], net_colour: str) -> None:
    """Render the 'fields added at this hop' as a compact table."""
    if not adds:
        st.caption("*No new fields added by this hop.*")
        return
    for item in adds:
        de_label = f"DE{item['de']}" if item.get("de") else "ℹ️"
        val = str(item.get("value") or "")
        if len(val) > 40:
            val = val[:37] + "…"
        st.markdown(
            f'<div style="display:flex;gap:8px;align-items:center;padding:2px 0">'
            f'{_badge(de_label, net_colour)}'
            f'<span style="font-size:0.9em"><b>{item.get("name","")}</b>'
            + (f' <code style="color:#888">{val}</code>' if val else "")
            + "</span></div>",
            unsafe_allow_html=True,
        )


# ── Scenario selector ─────────────────────────────────────────────────────────
st.markdown("---")

# Try to use last_trace from session (populated by Scenario Lab)
last_trace = st.session_state.get("last_trace") or {}
enrichment_trace = last_trace.get("enrichment_trace", [])
resolved_network = (
    (last_trace.get("iso_message") or {}).get("network")
    or (last_trace.get("request_sent") or {}).get("network")
    or "visa"
)

col_run, col_sc = st.columns([1, 3])
with col_run:
    run_fresh = st.button("▶ Run new scenario", type="primary", key="et_run_btn")
with col_sc:
    scenarios_resp = api_get("/scenarios", params={"limit": 100}) or {}
    scenarios = scenarios_resp.get("items", [])
    scenario_map = {s["id"]: f"[{s.get('event_type','auth').upper()[:3]}] {s['name']}" for s in scenarios}
    sel_sc_id = st.selectbox(
        "Scenario to trace",
        list(scenario_map.keys()) if scenario_map else ["authorization_approve"],
        format_func=lambda k: scenario_map.get(k, k),
        key="et_sc_sel",
    )

if run_fresh and sel_sc_id:
    with st.spinner("Executing scenario…"):
        trace = api_post(f"/execute/{sel_sc_id}")
    if trace and "error" not in trace:
        st.session_state.last_trace = trace
        enrichment_trace = trace.get("enrichment_trace", [])
        resolved_network = (
            (trace.get("iso_message") or {}).get("network")
            or (trace.get("request_sent") or {}).get("network")
            or "visa"
        )
        st.success(f"✅ Scenario executed — {'PASSED' if trace.get('passed') else 'FAILED'}")
    else:
        st.error(f"Error: {(trace or {}).get('error', 'unknown')}")

if not enrichment_trace:
    st.info(
        "No enrichment trace yet — run a scenario above or execute one from the **Scenario Lab** page. "
        "Once run, the trace will appear here automatically."
    )
    st.stop()

net_colour = _NET_COLOURS.get(resolved_network.lower(), "#555")

# ── Horizontal hop strip ──────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    f"### {_badge(resolved_network.upper(), net_colour)} &nbsp; Authorization Enrichment Flow",
    unsafe_allow_html=True,
)
st.caption(
    f"Network: **{resolved_network.capitalize()}** — {len(enrichment_trace)} hops. "
    "Each column shows the data elements **added at that hop**."
)

# Render hop columns
hop_cols = st.columns(len(enrichment_trace))
for col, hop in zip(hop_cols, enrichment_trace):
    actor    = hop.get("actor", "?")
    adds     = hop.get("adds", [])
    icon     = _hop_icon(actor)
    n_adds   = len(adds)

    with col:
        # Hop header
        st.markdown(
            f'<div style="background:{net_colour};color:#fff;padding:8px;border-radius:8px 8px 0 0;'
            f'text-align:center;font-weight:bold">{icon} {actor}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="background:#f8f9fa;padding:8px;border:1px solid #dee2e6;'
            f'border-top:none;border-radius:0 0 8px 8px;min-height:120px">',
            unsafe_allow_html=True,
        )

        st.markdown(f"**+{n_adds} field(s) added**")
        _render_adds_table(adds, net_colour)

        st.markdown("</div>", unsafe_allow_html=True)

# ── Drill-down expanders ──────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🔎 Hop Detail Drill-Down")

for hop in enrichment_trace:
    actor = hop.get("actor", "Unknown")
    icon  = _hop_icon(actor)
    with st.expander(f"{icon} {actor} — full detail", expanded=False):
        tabs = ["📦 Cumulative ISO"]
        if hop.get("iso_to_jpf"):
            tabs += ["🔄 ISO → JPF", "📤 JPF → JIT Webhook"]
        if hop.get("interchange_qualification"):
            tabs.insert(1, "💳 Interchange")
        if hop.get("decision"):
            tabs.append("✅ JIT Decision")

        rendered_tabs = st.tabs(tabs)
        tab_idx = 0

        # Tab: Cumulative ISO
        with rendered_tabs[tab_idx]:
            cumulative = hop.get("cumulative_iso", {})
            if cumulative:
                try:
                    import pandas as pd
                    rows = [
                        {"DE": f"DE{k}", "Value": str(v)[:80]}
                        for k, v in sorted(cumulative.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999)
                    ]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                except ImportError:
                    st.json(cumulative)
            else:
                st.caption("No cumulative ISO fields at this hop.")
        tab_idx += 1

        # Tab: Interchange Qualification
        if hop.get("interchange_qualification"):
            with rendered_tabs[tab_idx]:
                iq = hop["interchange_qualification"]
                c1, c2, c3 = st.columns(3)
                c1.metric("Tier",     iq.get("tier", "—"))
                c2.metric("Rate",     f"{iq.get('rate_pct', 0):.2f}%")
                c3.metric("Fee",      f"{iq.get('interchange_fee_cents', 0)}¢")
                st.json(iq)
            tab_idx += 1

        # Tabs: ISO → JPF and JPF → JIT
        if hop.get("iso_to_jpf"):
            with rendered_tabs[tab_idx]:
                st.markdown("**Canonical JPF** (dialect-agnostic)")
                st.json(hop["iso_to_jpf"].get("jpf", {}))
                st.markdown("**DB columns written**")
                try:
                    import pandas as pd
                    db_rows = [
                        {"Column": k, "Value": str(v)}
                        for k, v in (hop["iso_to_jpf"].get("db_fields") or {}).items()
                    ]
                    st.dataframe(pd.DataFrame(db_rows), use_container_width=True, hide_index=True)
                except ImportError:
                    st.json(hop["iso_to_jpf"].get("db_fields", {}))
            tab_idx += 1

            with rendered_tabs[tab_idx]:
                st.markdown("**JIT Funding webhook dispatched**")
                st.json(hop.get("jpf_to_jit", {}).get("webhook_shape", {}))
            tab_idx += 1

        # Tab: JIT Decision
        if hop.get("decision"):
            with rendered_tabs[tab_idx]:
                decision = hop.get("decision", "UNKNOWN")
                rc = hop.get("rc", "?")
                if decision == "APPROVED":
                    st.success(f"✅ **{decision}** — Response Code: `{rc}`")
                else:
                    st.error(f"❌ **{decision}** — Response Code: `{rc}`")


# ── Use-case presets strip ─────────────────────────────────────────────────────
st.markdown("---")
st.subheader("🎯 Use-Case Presets")
st.caption(
    "Run a preset to see how the enrichment trace differs across authorization types. "
    "ATM adds PIN (DE52) and cash MCC; PRE-AUTH shows the completion link."
)

preset_cols = st.columns(3)
_PRESETS = [
    ("AUTH — Purchase",          "authorization_approve",   "🛒"),
    ("ATM Withdrawal",           "atm_withdrawal_approve",  "🏧"),
    ("PRE-AUTH (Hold)",          "preauth_approve",         "🔒"),
]
for col, (label, sc_id, emoji) in zip(preset_cols, _PRESETS):
    with col:
        st.markdown(f"**{emoji} {label}**")
        if st.button(f"▶ Run {emoji}", key=f"preset_{sc_id}"):
            with st.spinner(f"Running {label}…"):
                pt = api_post(f"/execute/{sc_id}")
            if pt and "error" not in pt:
                st.session_state.last_trace = pt
                st.success("Done — scroll up to see the trace.")
                st.rerun()
            else:
                st.error(
                    f"Scenario `{sc_id}` not found — "
                    "run from Scenario Lab first or check scenario catalogue."
                )
