# frontend/utils/theme.py
"""Paycon e2ePS — shared design system.

Call inject_theme() once at the top of every page (after st.set_page_config).
This is the ONLY place app-level CSS lives — no per-page <style> blocks.
"""
import streamlit as st

# ── Brand tokens ───────────────────────────────────────────────────────────────
TEAL    = "#1fb7ac"
NAVY    = "#0a1730"
NAVY2   = "#0f2040"
TEXT    = "#eaf1ff"
MUTED   = "#7a9cc0"
SUCCESS = "#2ecc71"
DANGER  = "#e74c3c"
WARNING = "#f39c12"

# Network colours (consistent with demo_mode.py)
NET_COLOURS = {
    "visa":       "#1a1f71",
    "mastercard": "#eb001b",
    "amex":       "#007bc1",
    "discover":   "#f76f20",
}

_CSS = """
<style>
/* ── Global resets ─────────────────────────────────────── */
* { box-sizing: border-box; }

/* ── Card component ──────────────────────────────────────
   Usage: st.markdown('<div class="pc-card">…</div>', unsafe_allow_html=True)
*/
.pc-card {
    background: #0f2040;
    border: 1px solid #1e3a5f;
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 12px;
}
.pc-card-title {
    font-size: 0.78em;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #7a9cc0;
    margin-bottom: 6px;
}
.pc-card-value {
    font-size: 1.8em;
    font-weight: 700;
    color: #eaf1ff;
    line-height: 1.1;
}
.pc-card-sub {
    font-size: 0.78em;
    color: #7a9cc0;
    margin-top: 4px;
}

/* ── Chip / badge ────────────────────────────────────────
   Usage: <span class="pc-chip pc-chip-teal">APPROVED</span>
*/
.pc-chip {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.78em;
    font-weight: 700;
    letter-spacing: 0.04em;
}
.pc-chip-teal    { background: #1fb7ac22; color: #1fb7ac; border: 1px solid #1fb7ac55; }
.pc-chip-green   { background: #2ecc7122; color: #2ecc71; border: 1px solid #2ecc7155; }
.pc-chip-red     { background: #e74c3c22; color: #e74c3c; border: 1px solid #e74c3c55; }
.pc-chip-yellow  { background: #f39c1222; color: #f39c12; border: 1px solid #f39c1255; }
.pc-chip-blue    { background: #3498db22; color: #3498db; border: 1px solid #3498db55; }
.pc-chip-muted   { background: #1e3a5f;   color: #7a9cc0; border: 1px solid #2a4a6f; }

/* ── Brand header bar ────────────────────────────────────
   Used in app.py hero section
*/
.pc-brand-bar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 0 10px;
    border-bottom: 2px solid #1fb7ac33;
    margin-bottom: 20px;
}
.pc-brand-name {
    font-size: 1.5em;
    font-weight: 800;
    color: #eaf1ff;
    letter-spacing: -0.02em;
}
.pc-brand-name span { color: #1fb7ac; }
.pc-brand-tag {
    font-size: 0.76em;
    color: #7a9cc0;
    border: 1px solid #1e3a5f;
    border-radius: 4px;
    padding: 2px 7px;
}

/* ── Provider badge ──────────────────────────────────────
   Usage: <span class="pc-provider-badge">Claude ✓</span>
*/
.pc-provider-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: #1fb7ac22;
    color: #1fb7ac;
    border: 1px solid #1fb7ac44;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 0.78em;
    font-weight: 700;
}
.pc-provider-badge.pc-provider-none {
    background: #e74c3c11;
    color: #e74c3c;
    border-color: #e74c3c33;
}

/* ── Horizontal hop strip (enrichment flow) ──────────────
   Ensure hop columns have equal height and consistent borders
*/
.pc-hop-header {
    border-radius: 8px 8px 0 0;
    padding: 8px 0;
    text-align: center;
    font-weight: 700;
    font-size: 0.82em;
    letter-spacing: 0.05em;
}

/* ── Mono / code snippets ────────────────────────────────*/
.pc-mono {
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.85em;
    background: #091525;
    border-radius: 4px;
    padding: 2px 6px;
}

/* ── History row ─────────────────────────────────────────*/
.pc-hist-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 10px;
    border-radius: 6px;
    margin-bottom: 4px;
    background: #0f2040;
    border-left: 3px solid #1fb7ac;
    font-size: 0.88em;
}
.pc-hist-row.fail { border-left-color: #e74c3c; }

/* ── Gauge / progress bar ────────────────────────────────*/
.pc-gauge-wrap { margin: 8px 0 4px; }
.pc-gauge-bar {
    height: 8px;
    border-radius: 4px;
    background: linear-gradient(90deg, #1fb7ac, #2ecc71);
}
.pc-gauge-bar.fail { background: linear-gradient(90deg, #e74c3c, #f39c12); }
</style>
"""


def inject_theme() -> None:
    """Inject the shared Paycon CSS. Call once per page after set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


def provider_badge_html(provider: str | None, detected: bool) -> str:
    """Return HTML for the AI provider status badge shown on every AI surface."""
    if not provider or not detected:
        cls = "pc-provider-badge pc-provider-none"
        label = "AI: no key"
        icon  = "⚠️"
    else:
        cls   = "pc-provider-badge"
        label = provider.capitalize()
        icon  = "🤖"
    return f'<span class="{cls}">{icon} {label}</span>'


def chip(text: str, colour: str = "teal") -> str:
    """Return a pc-chip HTML snippet. colour: teal | green | red | yellow | blue | muted."""
    return f'<span class="pc-chip pc-chip-{colour}">{text}</span>'
