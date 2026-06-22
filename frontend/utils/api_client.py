# frontend/utils/api_client.py
"""Centralised API client for all Streamlit pages.

Reads the active API URL from session state (set by the Sandbox Config page),
falling back to the API_URL env var, then to the default Docker service name.
"""
import os
import requests
import streamlit as st


def get_api_url() -> str:
    """Return the currently-active backend API URL."""
    return (
        st.session_state.get("active_api_url")
        or os.getenv("API_URL", "http://backend:8000")
    )


def api_get(path: str, params: dict = None, timeout: int = 20):
    """GET from the active backend and return parsed JSON, or None on error."""
    try:
        r = requests.get(f"{get_api_url()}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"GET {path} → HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except requests.RequestException as e:
        st.error(f"GET {path} failed: {e}")
        return None


def api_post(path: str, body: dict = None, timeout: int = 600):
    """POST to the active backend and return parsed JSON, or None on error."""
    try:
        r = requests.post(f"{get_api_url()}{path}", json=body or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        st.error(f"POST {path} → HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None
    except requests.RequestException as e:
        st.error(f"POST {path} failed: {e}")
        return None


def api_get_raw(path: str, params: dict = None, timeout: int = 60) -> bytes | None:
    """GET that returns raw bytes (for binary/XML downloads)."""
    try:
        r = requests.get(f"{get_api_url()}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.content
    except requests.RequestException as e:
        st.error(f"GET {path} failed: {e}")
        return None
