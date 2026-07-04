# frontend/utils/api_client.py
"""Centralised API client for all Streamlit pages (T3.1 — caching; T5.2 — error boundaries).

- Read-only endpoints are wrapped with @st.cache_data(ttl=...) to prevent
  re-fetching static lists on every Streamlit rerun.
- All errors produce friendly inline messages (never raw stack traces).
- api_get / api_post return None on any error; callers handle the None case.
"""
import os
from urllib.parse import urlparse
import requests
import streamlit as st


def _running_in_docker() -> bool:
    return os.path.exists("/.dockerenv")


def _normalise_api_url(url: str) -> str:
    """Map Docker hostnames to localhost when UI runs on host OS."""
    if not url:
        return url
    parsed = urlparse(url)
    if _running_in_docker():
        return url
    if parsed.hostname in {"backend", "acquirer", "visa", "marqeta_simulator", "customer_jit"}:
        netloc = f"127.0.0.1:{parsed.port}" if parsed.port else "127.0.0.1"
        return parsed._replace(netloc=netloc).geturl()
    return url


def get_api_url() -> str:
    """Return the currently-active backend API URL."""
    raw_url = (
        st.session_state.get("active_api_url")
        or os.getenv("API_URL", "http://127.0.0.1:8000")
    )
    return _normalise_api_url(raw_url)


def api_get(path: str, params: dict = None, timeout: int = 20):
    """GET from the active backend and return parsed JSON, or None on error."""
    try:
        r = requests.get(f"{get_api_url()}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        status = e.response.status_code
        body   = e.response.text[:300]
        st.error(f"Backend error on GET {path} (HTTP {status}): {body}")
        return None
    except requests.ConnectionError:
        st.error(
            f"Cannot reach backend at {get_api_url()}. "
            "Is the stack running? Try `make demo-local` or `docker-compose up --build`."
        )
        return None
    except requests.Timeout:
        st.warning(f"Request timed out: GET {path} (>{timeout}s)")
        return None
    except Exception as e:
        st.error(f"Unexpected error on GET {path}: {e}")
        return None


def api_post(path: str, body: dict = None, timeout: int = 600):
    """POST to the active backend and return parsed JSON, or None on error."""
    try:
        r = requests.post(f"{get_api_url()}{path}", json=body or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        status = e.response.status_code
        body   = e.response.text[:300]
        st.error(f"Backend error on POST {path} (HTTP {status}): {body}")
        return None
    except requests.ConnectionError:
        st.error(
            f"Cannot reach backend at {get_api_url()}. "
            "Is the stack running? Try `make demo-local` or `docker-compose up --build`."
        )
        return None
    except requests.Timeout:
        st.warning(f"Request timed out: POST {path} (>{timeout}s)")
        return None
    except Exception as e:
        st.error(f"Unexpected error on POST {path}: {e}")
        return None


def api_get_raw(path: str, params: dict = None, timeout: int = 60) -> bytes | None:
    """GET that returns raw bytes (for binary/XML downloads)."""
    try:
        r = requests.get(f"{get_api_url()}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.content
    except requests.ConnectionError:
        st.error(f"Cannot reach backend at {get_api_url()}.")
        return None
    except requests.RequestException as e:
        st.error(f"Download failed: GET {path} — {e}")
        return None


# ── Cached read-only helpers (T3.1) ───────────────────────────────────────────
# These wrap the common static-list endpoints so Streamlit does not re-fetch
# them on every interaction rerun.  TTL values are generous for a pilot but
# short enough that a newly added scenario / suite shows up quickly.

@st.cache_data(ttl=30)
def get_scenarios(limit: int = 200) -> list:
    """Return scenario list, cached for 30 s."""
    resp = api_get("/scenarios", params={"limit": limit})
    return (resp or {}).get("items", [])


@st.cache_data(ttl=30)
def get_suites() -> list:
    """Return test-suite list, cached for 30 s."""
    resp = api_get("/suites")
    return (resp or {}).get("items", []) or (resp if isinstance(resp, list) else [])


@st.cache_data(ttl=15)
def get_environments() -> list:
    """Return environment list, cached for 15 s."""
    resp = api_get("/environments")
    return (resp or {}).get("items", []) or (resp if isinstance(resp, list) else [])


@st.cache_data(ttl=60)
def get_test_cards() -> dict:
    """Return per-network test card presets, cached for 60 s."""
    return api_get("/network/test_cards") or {}


@st.cache_data(ttl=10)
def get_analytics_summary() -> dict:
    """Return analytics summary, cached for 10 s."""
    return api_get("/analytics/summary") or {}
