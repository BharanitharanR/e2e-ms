# frontend/utils/demo_mode.py
"""Demo Mode node diagram and playback helpers — shared across pages."""
import streamlit as st

# Nodes in display order (index 0-5)
_DEMO_NODES = [
    ("💳", "Wallet"),      # 0
    ("🏪", "Terminal"),    # 1
    ("🏦", "Acquirer"),    # 2
    ("🌐", "Visa"),        # 3
    ("⚙️", "Marqeta"),     # 4
    ("✅", "JIT"),         # 5
]

# Audit step number (1-based) → node index to highlight
_STEP_NODE_MAP = {
    1: 0,   # Cardholder Tap       → Wallet
    2: 1,   # Terminal             → Terminal
    3: 2,   # Acquirer outbound    → Acquirer
    4: 3,   # Visa outbound        → Visa
    5: 4,   # Marqeta dispatch     → Marqeta
    6: 5,   # JIT decision         → JIT
    7: 3,   # Visa inbound         → Visa
    8: 2,   # Acquirer inbound     → Acquirer
    9: 1,   # Merchant Terminal    → Terminal
}


def render_node_diagram(active: int) -> str:
    """Return HTML for the animated 6-node flow diagram."""
    parts = []
    for i, (emoji, label) in enumerate(_DEMO_NODES):
        if i == active:
            s = (
                "background:#1f77b4;color:white;padding:6px 12px;"
                "border-radius:8px;font-weight:bold;white-space:nowrap;"
                "box-shadow:0 2px 6px rgba(31,119,180,0.4)"
            )
        else:
            s = "background:#f0f0f0;color:#555;padding:6px 12px;border-radius:8px;white-space:nowrap"
        parts.append(f'<span style="{s}">{emoji} {label}</span>')
    return (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:10px 0">'
        + ' <span style="color:#888">&rarr;</span> '.join(parts)
        + "</div>"
    )


def render_playback_step(entry: dict, step_num: int, total_steps: int):
    """Render a single audit step for the playback panel."""
    direction = entry.get("direction", "→")
    is_outbound = direction == "\u2192"
    dir_color = "#1f77b4" if is_outbound else "#2ca02c"
    dir_label = "OUTBOUND ▶" if is_outbound else "◀ INBOUND"
    node_idx = _STEP_NODE_MAP.get(step_num, 0)

    st.markdown(render_node_diagram(node_idx), unsafe_allow_html=True)
    st.markdown(
        f'<div style="background:#f8f9fa;border-left:4px solid {dir_color};'
        f'padding:10px 14px;border-radius:4px;margin:8px 0">'
        f'<b>Step {step_num} / {total_steps}</b> &nbsp;'
        f'<span style="background:{dir_color};color:#fff;padding:2px 9px;'
        f'border-radius:4px;font-size:0.8em;font-weight:bold">{dir_label}</span>'
        f'&nbsp; <b>{entry.get("actor","")}</b><br>'
        f'<span style="color:#555;font-size:0.9em">{entry.get("label","")}</span><br>'
        f'<span style="color:#999;font-size:0.78em">{entry.get("timestamp","")}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    payload = entry.get("payload")
    if payload:
        with st.expander(f"📦 Payload — {entry.get('actor','')}", expanded=True):
            st.json(payload)
