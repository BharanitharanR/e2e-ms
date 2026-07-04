# frontend/utils/demo_mode.py
"""Demo Mode node diagram and playback helpers — shared across pages.

T0.3: The network node label is now dynamic — callers pass the resolved
network name (e.g. "Mastercard") so the diagram never shows a hardcoded "Visa".
"""
import streamlit as st

# Network → colour (badge, node highlight)
_NETWORK_COLOURS = {
    "visa":       "#1a1f71",   # Visa blue
    "mastercard": "#eb001b",   # Mastercard red
    "amex":       "#007bc1",   # Amex blue
    "discover":   "#f76f20",   # Discover orange
}

# Network → emoji badge (shown next to label)
_NETWORK_EMOJI = {
    "visa":       "🔵",
    "mastercard": "🔴",
    "amex":       "🔷",
    "discover":   "🟠",
}

# Base nodes (index 0–5); index 3 is the dynamic network node.
_BASE_NODES = [
    ("💳", "Wallet"),       # 0
    ("🏪", "Terminal"),     # 1
    ("🏦", "Acquirer"),     # 2
    None,                   # 3  ← filled dynamically from resolved network
    ("⚙️", "Marqeta"),     # 4
    ("✅", "JIT"),          # 5
]

# Audit step number (1-based) → node index to highlight
_STEP_NODE_MAP = {
    1: 0,   # Cardholder Tap       → Wallet
    2: 1,   # Terminal             → Terminal
    3: 2,   # Acquirer outbound    → Acquirer
    4: 3,   # Network outbound     → Network
    5: 4,   # Marqeta dispatch     → Marqeta
    6: 5,   # JIT decision         → JIT
    7: 3,   # Network inbound      → Network
    8: 2,   # Acquirer inbound     → Acquirer
    9: 1,   # Merchant Terminal    → Terminal
}


def _build_nodes(network: str = "visa") -> list:
    """Return the 6-node list with the dynamic network entry filled in."""
    net = (network or "visa").lower()
    emoji = _NETWORK_EMOJI.get(net, "🌐")
    label = f"Network · {net.capitalize()}"
    nodes = list(_BASE_NODES)
    nodes[3] = (emoji, label)
    return nodes


def render_node_diagram(active: int, network: str = "visa") -> str:
    """Return HTML for the animated 6-node flow diagram.

    Args:
        active:  Index of the currently active node (0-based).
        network: Resolved network name (e.g. 'mastercard') — sets node 3 label.
    """
    nodes = _build_nodes(network)
    net = (network or "visa").lower()
    active_colour = _NETWORK_COLOURS.get(net, "#1f77b4") if active == 3 else "#1f77b4"

    parts = []
    for i, node in enumerate(nodes):
        if node is None:
            continue
        emoji, label = node
        if i == active:
            s = (
                f"background:{active_colour};color:white;padding:6px 12px;"
                "border-radius:8px;font-weight:bold;white-space:nowrap;"
                f"box-shadow:0 2px 6px rgba(0,0,0,0.3)"
            )
        else:
            s = "background:#f0f0f0;color:#555;padding:6px 12px;border-radius:8px;white-space:nowrap"
        parts.append(f'<span style="{s}">{emoji} {label}</span>')
    return (
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:10px 0">'
        + ' <span style="color:#888">&rarr;</span> '.join(parts)
        + "</div>"
    )


def render_playback_step(entry: dict, step_num: int, total_steps: int, network: str = "visa"):
    """Render a single audit step for the playback panel.

    Args:
        entry:       Audit trail entry dict.
        step_num:    1-based step number.
        total_steps: Total number of steps in the trail.
        network:     Resolved network for the dynamic node label (T0.3).
    """
    direction = entry.get("direction", "→")
    is_outbound = direction == "\u2192"
    dir_color = "#1f77b4" if is_outbound else "#2ca02c"
    dir_label = "OUTBOUND ▶" if is_outbound else "◀ INBOUND"
    node_idx = _STEP_NODE_MAP.get(step_num, 0)

    st.markdown(render_node_diagram(node_idx, network=network), unsafe_allow_html=True)
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
