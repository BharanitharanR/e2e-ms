# backend/network/packer.py
"""Pure-Python ISO 8583 packer/unpacker using the `iso8583` package (pyiso8583).

Implements the same /pack + /unpack contract that the jPOS sidecar would expose,
so T3-T6 work immediately.  The jPOS sidecar can replace this later without
touching the mapping engine.

Public API
----------
pack(fields: dict, network: str) -> PackResult
    fields  — {de_number_int: value_str, ...}  e.g. {2: "4111...", 4: "000000002500"}
    network — 'visa' | 'mastercard' | 'amex' | 'discover'
    returns PackResult(hex=str, fields=dict, mti=str, network=str)

unpack(hex_str: str, network: str) -> UnpackResult
    returns UnpackResult(fields=dict, mti=str, network=str)

Both raise PackerError on validation failure.
"""
from __future__ import annotations

import binascii
from dataclasses import dataclass, field as dc_field
from typing import Any

import iso8583
from iso8583.specs import default_ascii as _BASE_SPEC


# ---------------------------------------------------------------------------
# Spec builder — we extend the base ASCII spec with network private fields
# ---------------------------------------------------------------------------

def _build_spec(private_fields: list[dict]) -> dict:
    """Clone the base spec (string keys) and add/override entries for network private DEs.

    iso8583 library uses string keys for all fields including bitmaps ('p','1')
    and the MTI ('t').  Standard DEs are '2' .. '128'.
    """
    spec = {k: dict(v) for k, v in _BASE_SPEC.items()}

    for pf in private_fields:
        de = str(pf["de"])          # must be string key
        fmt = pf.get("format", "ans..255 LLLVAR")
        length_type = "LLLVAR" if "LLL" in fmt.upper() else ("LLVAR" if "LL" in fmt.upper() else "FIXED")
        parts = fmt.upper().split()
        try:
            max_len = int("".join(c for c in parts[0] if c.isdigit() or c == ".").strip("."))
        except ValueError:
            max_len = 255

        spec[de] = {
            "data_enc": "ascii",
            "len_enc": "ascii",
            "len_type": 3 if length_type == "LLLVAR" else (2 if length_type == "LLVAR" else 0),
            "max_len": max_len,
            "desc": pf.get("name", f"DE{de}"),
        }
    return spec


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PackResult:
    hex: str
    fields: dict
    mti: str
    network: str
    private_des: list[int] = dc_field(default_factory=list)


@dataclass
class UnpackResult:
    fields: dict
    mti: str
    network: str


class PackerError(Exception):
    pass


# ---------------------------------------------------------------------------
# Core pack / unpack
# ---------------------------------------------------------------------------

def pack(fields: dict, network: str, mti: str = "0100",
         private_field_values: dict | None = None) -> PackResult:
    """Pack an ISO 8583 message.

    Args:
        fields:               {de_int: value_str} standard fields
        network:              network name string (used for profile lookup)
        mti:                  Message Type Indicator (default '0100')
        private_field_values: {de_int: value_str} network-private DE values;
                              if None, taken from the profile's private_de_values
                              with {stan} substituted from fields.get(11,'000000')

    Returns:
        PackResult with .hex (hex string of the packed message) and .fields
    """
    from backend.network.router import select_network
    profile = select_network("4", override=network)  # PAN irrelevant; override used
    spec = _build_spec(profile.get("private_fields", []))

    # Merge standard fields with private DE values — all keys are STRINGS for iso8583
    all_fields: dict[str, str] = {}
    for k, v in fields.items():
        all_fields[str(k)] = str(v)

    # Auto-populate private DEs from profile template
    stan = all_fields.get("11", "000000")
    private_des: list[int] = []
    pv = {str(k): v for k, v in (private_field_values or {}).items()}
    for pf in profile.get("private_fields", []):
        de_int = pf["de"]
        de_str = str(de_int)
        private_des.append(de_int)
        if de_str not in all_fields:
            template = pv.get(de_str) or profile.get("private_de_values", {}).get(de_str, f"{network.upper()}_{de_str}_{stan}")
            all_fields[de_str] = str(template).replace("{stan}", stan)

    # iso8583 encode: msg dict has string DE keys + "t" for MTI
    msg = {"t": mti}
    msg.update(all_fields)

    try:
        raw_bytes, _ = iso8583.encode(msg, spec)
    except iso8583.EncodeError as e:
        raise PackerError(f"Pack error ({network}): {e}") from e

    return PackResult(
        hex=binascii.hexlify(raw_bytes).decode(),
        fields=all_fields,          # string keys
        mti=mti,
        network=network,
        private_des=private_des,    # int keys for easy set arithmetic
    )


def unpack(hex_str: str, network: str) -> UnpackResult:
    """Unpack a hex-encoded ISO 8583 message.

    Args:
        hex_str: Hex string of the packed message (no spaces).
        network: Network name to select the right spec/private-field definitions.

    Returns:
        UnpackResult with .fields {de_int: value_str} and .mti
    """
    from backend.network.router import select_network
    profile = select_network("4", override=network)
    spec = _build_spec(profile.get("private_fields", []))

    raw = binascii.unhexlify(hex_str.replace(" ", ""))
    try:
        msg, _ = iso8583.decode(raw, spec)
    except iso8583.DecodeError as e:
        raise PackerError(f"Unpack error ({network}): {e}") from e

    mti = msg.pop("t", "0000")
    msg.pop("p", None)   # remove primary bitmap entry
    msg.pop("1", None)   # remove secondary bitmap entry if present
    # iso8583 lib returns string keys; keep as-is for consistency with pack()
    fields = dict(msg)

    return UnpackResult(fields=fields, mti=mti, network=network)
