# backend/network/jpos_bridge.py
"""Optional jPOS sidecar bridge (T2.1 — Phase 3).

When the ISO_ENGINE_URL environment variable is set, pack/unpack calls are
delegated to the jPOS Q2 sidecar instead of the Python pyiso8583 library.
This provides byte-authentic packing against the GenericPackager XML specs.

If ISO_ENGINE_URL is not set (or the sidecar is unreachable), the bridge
silently falls back to the existing Python packer — the same PackResult
dataclass is returned either way, so callers are unaware of which backend ran.

Usage:
    from backend.network.jpos_bridge import pack_via_jpos, unpack_via_jpos
    result = pack_via_jpos(fields, network, mti)
    if result is None:
        # sidecar unavailable — fall back to Python packer
        result = pack(fields, network, mti)

Contract (mirrors backend/network/packer.py):
    pack_via_jpos(fields, network, mti) → PackResult | None
    unpack_via_jpos(hex_str, network)   → UnpackResult | None
"""
from __future__ import annotations

import os
import logging
from typing import Optional

import requests as _requests

log = logging.getLogger("jpos_bridge")

ISO_ENGINE_URL: Optional[str] = os.getenv("ISO_ENGINE_URL")  # e.g. http://iso-engine:8200

_TIMEOUT = 5  # seconds


def _engine_available() -> bool:
    return bool(ISO_ENGINE_URL)


def pack_via_jpos(
    fields: dict,
    network: str,
    mti: str = "0100",
) -> Optional[object]:
    """Pack fields via the jPOS sidecar.

    Returns a PackResult-compatible object, or None if the sidecar is
    unavailable / returns an error.
    """
    if not _engine_available():
        return None

    try:
        resp = _requests.post(
            f"{ISO_ENGINE_URL}/pack",
            json={"network": network, "mti": mti, "fields": fields},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("jPOS /pack returned %s: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        # Return a duck-type object that matches PackResult
        return _JposPackResult(
            hex=data["hex"],
            fields=fields,
            network=network,
            mti=data.get("mti", mti),
            private_des=[],  # jPOS doesn't return private DE flags
        )

    except Exception as exc:
        log.debug("jPOS pack unavailable: %s", exc)
        return None


def unpack_via_jpos(
    hex_str: str,
    network: str,
) -> Optional[object]:
    """Unpack hex via the jPOS sidecar.

    Returns an UnpackResult-compatible object, or None if unavailable.
    """
    if not _engine_available():
        return None

    try:
        resp = _requests.post(
            f"{ISO_ENGINE_URL}/unpack",
            json={"network": network, "hex": hex_str},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("jPOS /unpack returned %s: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        return _JposUnpackResult(
            fields=data.get("fields", {}),
            mti=data.get("mti", "0100"),
            network=network,
        )

    except Exception as exc:
        log.debug("jPOS unpack unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Duck-type result classes (match PackResult / UnpackResult from packer.py)
# ---------------------------------------------------------------------------

class _JposPackResult:
    def __init__(self, hex: str, fields: dict, network: str, mti: str, private_des: list):
        self.hex         = hex
        self.fields      = fields
        self.network     = network
        self.mti         = mti
        self.private_des = private_des
        self.via_jpos    = True


class _JposUnpackResult:
    def __init__(self, fields: dict, mti: str, network: str):
        self.fields   = fields
        self.mti      = mti
        self.network  = network
        self.via_jpos = True


def health() -> dict:
    """Check whether the jPOS sidecar is reachable."""
    if not _engine_available():
        return {"available": False, "reason": "ISO_ENGINE_URL not set"}
    try:
        resp = _requests.get(f"{ISO_ENGINE_URL}/health", timeout=_TIMEOUT)
        return {"available": resp.status_code == 200, "response": resp.json()}
    except Exception as exc:
        return {"available": False, "error": str(exc)}
