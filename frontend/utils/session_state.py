# frontend/utils/session_state.py
"""Shared session state initialisation for all Streamlit pages.

Call init_session_state() at the top of every page (after st.set_page_config).
Only sets keys that don't already exist, so navigating between pages never
resets in-progress work.
"""
import streamlit as st


def init_session_state():
    """Set all session state defaults (idempotent — skips keys already present)."""
    defaults = {
        # ── Core simulator keys (preserved from original app.py) ────────────
        "iso_mode":           False,
        "iso_jpf_mapping":    None,
        "suite_result":       None,
        "apdu_log":           [],
        "card_state":         None,
        "demo_mode":          False,
        "demo_running":       False,
        "last_trace":         None,
        # Demo playback
        "demo_audit_steps":   [],
        "demo_playback_idx":  0,
        "demo_playback_mode": False,
        # ── Enterprise / multi-page keys ────────────────────────────────────
        "active_api_url":     None,      # None → use API_URL env var
        "active_env_name":    "Local Docker",
        "active_env_id":      None,
        # Pagination state
        "history_page":       1,
        "scenario_page":      1,
        "suite_runs_page":    1,
        # AI Copilot
        "ai_last_scenario":   None,
        "ai_last_explanation": None,
        "ai_generating":      False,
        # Mandate workflow (T2.1-T2.3 — Phase 5)
        "mandate_proposal":   None,
        "mandate_network":    "visa",
        "ai_last_run":        None,
        # Sandbox health cache
        "health_cache":       {},
        # ISO workbench Column 1 DE values
        "iso_wire_values":    {},
        # Webhook replay
        "replay_result":      None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
