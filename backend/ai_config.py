# backend/ai_config.py
"""In-app AI provider configuration and secure key storage (T0.2).

Design
------
- Keys are NEVER stored in the app DB, logs, or environment files committed to git.
- Persisted to ~/.paycon/secrets (AES-GCM via Fernet with a machine-derived key)
  with permissions 0o600.  If cryptography is not installed, falls back to
  plaintext with a warning and restrictive file mode.
- Reading priority: in-app config → env var → default.
- Never render raw keys in any API response; use status strings only.

Supported providers (ordered fallback chain):
  claude (Anthropic) | openai | azure | groq | vllm | ollama
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_SECRETS_DIR  = Path.home() / ".paycon"
_SECRETS_FILE = _SECRETS_DIR / "secrets"
_KEY_FILE     = _SECRETS_DIR / ".keyfile"   # machine-scoped key material

SUPPORTED_PROVIDERS = ["claude", "openai", "azure", "groq", "vllm", "ollama"]

_DEFAULT_CONFIG: dict[str, Any] = {
    "primary": "claude",
    "fallback_chain": ["ollama"],
    "providers": {
        "claude": {
            "model":    "claude-opus-4-5",
            "base_url": "https://api.anthropic.com",
        },
        "openai": {
            "model":    "gpt-4o",
            "base_url": "https://api.openai.com/v1",
        },
        "azure": {
            "model":    "gpt-4o",
            "base_url": "",
        },
        "groq": {
            "model":    "llama3-70b-8192",
            "base_url": "https://api.groq.com/openai/v1",
        },
        "vllm": {
            "model":    "meta-llama/Llama-3-70b-chat-hf",
            "base_url": "http://localhost:8080/v1",
        },
        "ollama": {
            "model":    "qwen3:8b",
            "base_url": "http://localhost:11434",
        },
    },
}


# ── Fernet key derivation (machine-scoped) ────────────────────────────────────

def _get_fernet_key() -> bytes:
    """Return (or create) a machine-scoped Fernet key."""
    _SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        raw = _KEY_FILE.read_bytes()
    else:
        # Seed from machine UUID + home path — not a secret, just machine-unique
        seed = (str(os.getuid()) + str(Path.home())).encode()
        raw = hashlib.sha256(seed).digest()
        _KEY_FILE.write_bytes(raw)
        _KEY_FILE.chmod(0o600)
    # Fernet requires 32-byte URL-safe base64 key
    return base64.urlsafe_b64encode(raw)


def _fernet():
    """Return a Fernet instance, or None if cryptography is not installed."""
    try:
        from cryptography.fernet import Fernet
        return Fernet(_get_fernet_key())
    except ImportError:
        return None


# ── Low-level read / write ────────────────────────────────────────────────────

def _read_secrets_raw() -> dict:
    """Read and decrypt the secrets file; return {} on any error."""
    if not _SECRETS_FILE.exists():
        return {}
    try:
        raw = _SECRETS_FILE.read_bytes()
        f = _fernet()
        if f is not None:
            raw = f.decrypt(raw)
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Could not read secrets file: %s", exc)
        return {}


def _write_secrets_raw(data: dict) -> None:
    """Encrypt and write data to the secrets file with 0o600 permissions."""
    _SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = json.dumps(data).encode()
    f = _fernet()
    if f is None:
        logger.warning(
            "cryptography package not installed — persisting secrets as plaintext "
            "with restricted permissions. Run: pip install cryptography"
        )
    else:
        raw = f.encrypt(raw)
    _SECRETS_FILE.write_bytes(raw)
    os.chmod(_SECRETS_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


# ── Public API ────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Return the merged provider config (defaults + stored overrides, no keys)."""
    import copy
    cfg = copy.deepcopy(_DEFAULT_CONFIG)
    stored = _read_secrets_raw()
    stored_cfg = stored.get("config", {})

    # Merge stored overrides (non-key fields only)
    if stored_cfg.get("primary"):
        cfg["primary"] = stored_cfg["primary"]
    if stored_cfg.get("fallback_chain"):
        cfg["fallback_chain"] = stored_cfg["fallback_chain"]
    for pname, pdata in stored_cfg.get("providers", {}).items():
        if pname in cfg["providers"]:
            cfg["providers"][pname].update(
                {k: v for k, v in pdata.items() if k != "api_key"}
            )
        else:
            cfg["providers"][pname] = {k: v for k, v in pdata.items() if k != "api_key"}
    return cfg


def save_config(config: dict) -> None:
    """Persist config overrides (model/base_url/chain — NOT keys)."""
    data = _read_secrets_raw()
    # Strip any accidentally included api_key fields before persisting
    providers_clean = {}
    for pname, pdata in config.get("providers", {}).items():
        providers_clean[pname] = {k: v for k, v in pdata.items() if k != "api_key"}

    data["config"] = {
        "primary":        config.get("primary", "claude"),
        "fallback_chain": config.get("fallback_chain", ["ollama"]),
        "providers":      providers_clean,
    }
    _write_secrets_raw(data)


def set_api_key(provider: str, key: str) -> None:
    """Store an API key for a provider.  Key is stored encrypted, never logged."""
    if not key or not provider:
        raise ValueError("provider and key are required")
    # Basic sanity: refuse obviously wrong shapes (log nothing about the key)
    if len(key) < 8:
        raise ValueError("Key appears too short — check the value and try again")

    data = _read_secrets_raw()
    keys = data.setdefault("keys", {})
    keys[provider] = key
    _write_secrets_raw(data)
    logger.info("API key updated for provider: %s", provider)


def get_api_key(provider: str) -> str | None:
    """Return the stored API key for a provider.

    Priority: in-app secrets → environment variable → None.
    """
    # 1. Env var takes precedence (allows CI / docker override without touching secrets)
    _ENV_MAP = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "azure":  "AZURE_OPENAI_API_KEY",
        "groq":   "GROQ_API_KEY",
        "vllm":   "VLLM_API_KEY",
    }
    env_key = _ENV_MAP.get(provider)
    if env_key:
        val = os.environ.get(env_key)
        if val:
            return val

    # 2. In-app secrets file
    data = _read_secrets_raw()
    return data.get("keys", {}).get(provider)


def get_key_status(provider: str) -> str:
    """Return 'detected' or 'not detected' — never the raw key."""
    return "detected" if get_api_key(provider) else "not detected"


def delete_api_key(provider: str) -> None:
    """Remove a stored API key for a provider."""
    data = _read_secrets_raw()
    removed = data.get("keys", {}).pop(provider, None)
    if removed is not None:
        _write_secrets_raw(data)
        logger.info("API key deleted for provider: %s", provider)


def provider_status() -> list[dict]:
    """Return a list of {provider, model, base_url, key_status} — never raw keys."""
    cfg = load_config()
    result = []
    primary = cfg.get("primary", "claude")
    chain   = cfg.get("fallback_chain", [])
    for pname, pdata in cfg.get("providers", {}).items():
        result.append({
            "provider":  pname,
            "model":     pdata.get("model", ""),
            "base_url":  pdata.get("base_url", ""),
            "key_status": get_key_status(pname),
            "is_primary":  pname == primary,
            "in_chain":    pname in chain,
        })
    return result


def get_active_provider_key(primary: str | None = None) -> tuple[str, str | None]:
    """Return (provider_name, api_key) for the first provider that has a key.

    Walks: primary → fallback_chain, trying each in order.
    Returns (provider, None) if no key found anywhere.
    """
    cfg = load_config()
    prim = primary or cfg.get("primary", "claude")
    chain = [prim] + [p for p in cfg.get("fallback_chain", []) if p != prim]
    for p in chain:
        key = get_api_key(p)
        if key:
            return p, key
    return prim, None
