# frontend/pages/11_ai_settings.py
"""AI Provider & Key Settings — configure the active LLM provider chain (T0.2).

Security rules enforced here:
- Raw API keys are NEVER displayed, rendered, stored in session state, or logged.
- The UI only shows key *status* (detected / not detected).
- Keys are submitted via a dedicated form and immediately discarded from the
  Python locals after the POST — they are never assigned to session state.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from utils.api_client import api_get, api_post
from utils.session_state import init_session_state
from utils.theme import inject_theme

init_session_state()
st.set_page_config(page_title="Paycon e2ePS — AI Settings", page_icon="🔑", layout="wide")
inject_theme()

st.title("🔑 AI Provider Settings")
st.caption(
    "Configure which LLM powers the AI Copilot, set API keys securely, "
    "and define the fallback chain. Keys are stored encrypted on the local machine — "
    "**never in the database, never in logs, never echoed back**."
)

# ── Load current config from backend ──────────────────────────────────────────
providers_resp = api_get("/ai/providers") or {}
current_primary    = providers_resp.get("primary", "claude")
current_chain      = providers_resp.get("fallback_chain", ["ollama"])
provider_list      = providers_resp.get("providers", [])

SUPPORTED = ["claude", "openai", "azure", "groq", "vllm", "ollama"]

# Build a quick lookup: provider name → row dict
_pmap = {p["provider"]: p for p in provider_list}

# ── Layout ────────────────────────────────────────────────────────────────────
col_left, col_right = st.columns([2, 1])

with col_left:
    # ── Provider chain config ────────────────────────────────────────────────
    st.subheader("🔗 Provider Chain")
    st.caption(
        "The primary provider is tried first. If it fails or has no key, "
        "the stack tries each fallback in order."
    )

    new_primary = st.selectbox(
        "Primary provider",
        SUPPORTED,
        index=SUPPORTED.index(current_primary) if current_primary in SUPPORTED else 0,
        key="ai_prim",
    )

    remaining = [p for p in SUPPORTED if p != new_primary]
    new_chain_labels = st.multiselect(
        "Fallback chain (ordered)",
        remaining,
        default=[p for p in current_chain if p in remaining],
        key="ai_chain",
        help="Providers tried in order when the primary fails. Drag to reorder.",
    )

    st.markdown("---")
    st.subheader("⚙️ Model & Endpoint Settings")
    st.caption(
        "Edit the **Model** or **Base URL** cells directly, then click **Save**. "
        "The **Key** column is read-only (manage keys on the right)."
    )

    import pandas as pd
    rows = []
    for pname in SUPPORTED:
        pdata = _pmap.get(pname, {})
        rows.append({
            "Provider": pname,
            "Model":    pdata.get("model", ""),
            "Base URL": pdata.get("base_url", ""),
            "Key":      pdata.get("key_status", "not detected"),
        })
    df_edit = pd.DataFrame(rows)

    edited_df = st.data_editor(
        df_edit,
        use_container_width=True,
        hide_index=True,
        disabled=["Provider", "Key"],   # only Model and Base URL are editable
        column_config={
            "Key": st.column_config.TextColumn(
                "Key",
                help="'detected' = key is stored/available. Manage keys on the right.",
            ),
        },
        key="ai_model_table",
    )

    if st.button("💾 Save provider chain & settings", type="primary", key="ai_save_chain"):
        # Build updated_providers from the *edited* dataframe rows
        updated_providers = {}
        for _, row in edited_df.iterrows():
            pname = row["Provider"]
            updated_providers[pname] = {
                "model":    row["Model"],
                "base_url": row["Base URL"],
            }
        result = api_post("/ai/providers/config", {
            "primary":        new_primary,
            "fallback_chain": new_chain_labels,
            "providers":      updated_providers,
        })
        if result and result.get("status") == "saved":
            st.success("✅ Provider chain saved.")
            st.rerun()
        else:
            st.error(f"Failed to save: {(result or {}).get('error', 'unknown error')}")


with col_right:
    # ── Key management ───────────────────────────────────────────────────────
    st.subheader("🔑 API Key Management")
    st.caption(
        "Enter a key to store it encrypted on this machine. "
        "The key is **never displayed** after saving."
    )

    key_provider = st.selectbox(
        "Provider",
        SUPPORTED,
        key="ai_key_provider",
        index=SUPPORTED.index(current_primary) if current_primary in SUPPORTED else 0,
    )

    # Show current status (detected/not detected) — never the raw key
    status = (_pmap.get(key_provider) or {}).get("key_status", "not detected")
    if status == "detected":
        st.success(f"🔐 Key status: **detected** (key is set for {key_provider})")
    else:
        st.warning(f"⚠️ Key status: **not detected** for {key_provider}")

    # Key input — type="password" masks input; we discard after POST
    key_env_hint = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "azure":  "AZURE_OPENAI_API_KEY",
        "groq":   "GROQ_API_KEY",
        "vllm":   "VLLM_API_KEY",
        "ollama": "(no key needed — Ollama is local)",
    }
    st.caption(f"Env-var alternative: `{key_env_hint.get(key_provider, '')}`")

    with st.form(key="ai_key_form", clear_on_submit=True):
        entered_key = st.text_input(
            "Paste API key here",
            type="password",
            help="The key is sent to the backend over localhost and stored encrypted. "
                 "It will NOT be shown again.",
            placeholder="sk-ant-... / sk-... / gsk-... (leave blank to keep current)",
        )
        col_save, col_del = st.columns(2)
        save_key = col_save.form_submit_button("💾 Save key", type="primary")
        del_key  = col_del.form_submit_button("🗑️ Delete key")

    if save_key:
        if not entered_key.strip():
            st.info("No key entered — nothing changed.")
        elif key_provider == "ollama":
            st.info("Ollama runs locally — no API key required.")
        else:
            result = api_post("/ai/providers/key", {
                "provider": key_provider,
                "api_key":  entered_key,
                # Note: entered_key goes straight into the POST body;
                # it is NOT stored in session_state.
            })
            # Immediately discard the entered key reference
            del entered_key
            if result and result.get("status") == "saved":
                st.success(
                    f"✅ Key saved for **{key_provider}**. "
                    f"Status: {result.get('key_status', 'detected')}"
                )
                st.rerun()
            else:
                st.error(f"Failed: {(result or {}).get('error', 'unknown')}")
        entered_key = ""  # defensive clear

    if del_key:
        result = api_post(f"/ai/providers/key/{key_provider}", {})
        # Use DELETE via POST since api_post wraps requests.post
        import requests as _req
        from utils.api_client import get_api_url
        try:
            r = _req.delete(f"{get_api_url()}/ai/providers/key/{key_provider}", timeout=10)
            r.raise_for_status()
            del_result = r.json()
        except Exception as exc:
            del_result = {"error": str(exc)}
        if del_result.get("status") == "deleted":
            st.success(f"🗑️ Key deleted for **{key_provider}**.")
            st.rerun()
        else:
            st.error(f"Delete failed: {del_result.get('error', 'unknown')}")

    st.markdown("---")
    st.subheader("🧪 Test Active Provider")
    if st.button("🤖 Send test prompt to primary provider", key="ai_test_btn"):
        with st.spinner(f"Testing {new_primary}…"):
            result = api_post("/ai/generate_scenario", {
                "prompt": "A $10 contactless coffee shop purchase that is approved"
            })
        if result and "error" not in result:
            st.success(f"✅ Provider **{new_primary}** responded successfully.")
            with st.expander("Scenario returned", expanded=False):
                st.json(result)
        else:
            st.error(
                f"❌ Provider test failed: "
                f"{(result or {}).get('error', 'No response')}"
            )

    st.markdown("---")
    st.subheader("ℹ️ Security notes")
    st.markdown("""
- Keys are stored **encrypted** in `~/.paycon/secrets` (AES-GCM via Fernet).
- File permissions: `0o600` (owner read/write only).
- Keys are **never** stored in the app DB, session state, or logs.
- Environment variables (`ANTHROPIC_API_KEY` etc.) take precedence if set.
- Run `grep -r 'sk-ant-' .` to verify no key leaked into the repo.
""")
