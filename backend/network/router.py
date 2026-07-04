# backend/network/router.py
"""Network BIN router and profile loader.

select_network(pan, override=None) → profile dict
  - override wins if provided (use to force mastercard, amex, etc. in tests/UI)
  - otherwise routes by PAN prefix matching each profile's bin_ranges
  - falls back to 'visa' if no range matches
"""
import os
import yaml
from functools import lru_cache
from pathlib import Path

_PROFILES_DIR = Path(__file__).parent / "profiles"

# Ordered list used for BIN matching (longest prefix → shortest so "6011" beats "6")
_NETWORK_ORDER = ["discover", "amex", "mastercard", "visa"]


@lru_cache(maxsize=None)
def _load_profile(name: str) -> dict:
    path = _PROFILES_DIR / f"{name}.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)


def load_all_profiles() -> dict[str, dict]:
    """Return {network_name: profile} for all four networks."""
    return {n: _load_profile(n) for n in _NETWORK_ORDER}


def _bin_matches(pan: str, bin_ranges: list) -> bool:
    """Return True if any entry in bin_ranges matches the PAN prefix.

    Entries may be a plain prefix ("4", "37") or a numeric range ("2221-2720")
    checked against the first four digits.
    """
    pan_s = str(pan).replace(" ", "")
    first4 = int(pan_s[:4]) if len(pan_s) >= 4 else int(pan_s)

    for entry in bin_ranges:
        entry = str(entry)
        if "-" in entry:
            lo, hi = entry.split("-", 1)
            if int(lo) <= first4 <= int(hi):
                return True
        else:
            if pan_s.startswith(entry):
                return True
    return False


def select_network(pan: str, override: str = None) -> dict:
    """Return the network profile dict for a given PAN.

    Args:
        pan:      Full PAN string (digits only).
        override: Explicit network name ('visa', 'mastercard', 'amex', 'discover').
                  When supplied, BIN routing is skipped and the named profile is returned.

    Returns:
        Profile dict (keys: network, bin_ranges, mti, private_fields, edits, …).

    Raises:
        ValueError: If override names an unknown network.
    """
    if override:
        override = override.lower()
        if override not in _NETWORK_ORDER:
            raise ValueError(
                f"Unknown network override '{override}'. "
                f"Valid: {_NETWORK_ORDER}"
            )
        return _load_profile(override)

    # BIN routing — try networks in specificity order (most-specific prefixes first)
    for name in _NETWORK_ORDER:
        profile = _load_profile(name)
        if _bin_matches(pan, profile.get("bin_ranges", [])):
            return profile

    # Default fallback
    return _load_profile("visa")
