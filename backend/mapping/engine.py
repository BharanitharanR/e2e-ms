# backend/mapping/engine.py
"""YAML-spec-driven ISO 8583 → JPF (JSON Payment Format) mapping engine.

Public API
----------
map_to_jpf(iso_fields, network, icc_hex=None) -> MappingResult
    iso_fields: dict[str, str]  — string-keyed DE map from packer.unpack()
    network:    str             — 'visa' | 'mastercard' | 'amex' | 'discover'
    icc_hex:    str | None      — hex content of DE55 (BER-TLV EMV data), if any

    Returns MappingResult(jpf, pii_safe, warnings, network)
    jpf       — canonical JPF dict (no clear PAN — PAN is tokenised / last_four / hash)
    pii_safe  — same structure but with PAN fields explicitly masked
    warnings  — list of validation flag strings (e.g. amount mismatch)
    network   — network name string passed in

Design
------
- Spec YAML has `fields` list and `validate` list (see specs/*.yaml).
- Transforms applied to raw DE value strings:
    passthrough    — value as-is
    n12_to_cents   — strip leading zeros → int (already minor units, just cast)
    emv_amount     — BCD 6-byte → int cents
    emv_currency   — BCD 2-byte → ISO 4217 numeric string
    sha256         — hex digest of utf-8 value
- PII rule: `pii: true` means the raw value is NOT stored; instead the `store`
  sub-keys control what IS stored (token, last_four, hash).
- DE55 TLV tags are parsed from icc_hex with a minimal BER-TLV walker.
- Validation rules (rule: equal, a/b as DE or tag refs) flag mismatches as
  warnings; they never block the JPF from being returned.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Spec loader
# ---------------------------------------------------------------------------

_SPECS_DIR = Path(__file__).parent / "specs"


@lru_cache(maxsize=None)
def _load_spec(network: str) -> dict:
    path = _SPECS_DIR / f"{network}.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Minimal BER-TLV parser for DE55 (EMV chip data)
# ---------------------------------------------------------------------------

def _parse_tlv(hex_str: str) -> dict[str, str]:
    """Parse BER-TLV bytes into {tag_hex_upper: value_hex} dict.

    Handles single-byte and two-byte constructed/primitive tags.
    Skips constructed tags (contents are not recursively expanded — the
    raw value hex is kept for the consumer to decode).
    """
    if not hex_str:
        return {}

    try:
        data = bytes.fromhex(hex_str.replace(" ", ""))
    except ValueError:
        return {}

    tags: dict[str, str] = {}
    idx = 0
    n = len(data)

    while idx < n:
        # --- Tag ---
        tag_start = idx
        b = data[idx]
        idx += 1
        if (b & 0x1F) == 0x1F:          # two-byte tag
            if idx >= n:
                break
            idx += 1
        tag_bytes = data[tag_start:idx]
        tag_hex = tag_bytes.hex().upper()

        # --- Length ---
        if idx >= n:
            break
        length_byte = data[idx]
        idx += 1
        if length_byte == 0x81:
            if idx >= n:
                break
            length = data[idx]
            idx += 1
        elif length_byte == 0x82:
            if idx + 1 >= n:
                break
            length = (data[idx] << 8) | data[idx + 1]
            idx += 2
        else:
            length = length_byte

        # --- Value ---
        value_bytes = data[idx: idx + length]
        idx += length
        tags[tag_hex] = value_bytes.hex().upper()

    return tags


# ---------------------------------------------------------------------------
# Transform functions
# ---------------------------------------------------------------------------

def _apply_transform(raw: str, transform: str) -> Any:
    """Apply a named transform to a raw DE string value."""
    if transform in ("passthrough", ""):
        return raw
    if transform == "n12_to_cents":
        try:
            return int(raw.lstrip("0") or "0")
        except ValueError:
            return 0
    if transform == "emv_amount":
        # BCD-encoded 6 bytes = 12 decimal digits; last value is cents
        try:
            return int(raw, 16)
        except ValueError:
            return 0
    if transform == "emv_currency":
        try:
            return str(int(raw, 16))
        except ValueError:
            return raw
    if transform == "sha256":
        return hashlib.sha256(raw.encode()).hexdigest()
    # Unknown transforms — return raw value
    return raw


def _token(raw: str) -> str:
    """Return a deterministic pseudo-token for the PAN (sha256 prefix)."""
    h = hashlib.sha256(raw.encode()).hexdigest()
    return f"tok_{h[:16]}"


def _last_four(raw: str) -> str:
    return raw[-4:] if len(raw) >= 4 else raw


# ---------------------------------------------------------------------------
# Nested-key setter
# ---------------------------------------------------------------------------

def _set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted key path."""
    parts = dotted_key.split(".")
    node = d
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MappingResult:
    """All artefacts produced when mapping an ISO 8583 message to JPF."""
    network: str
    jpf: dict                  # canonical JPF (PAN replaced by PII-safe fields)
    pii_safe: dict             # same as jpf — kept for API symmetry
    warnings: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Core mapper
# ---------------------------------------------------------------------------

def map_to_jpf(
    iso_fields: dict[str, str],
    network: str,
    icc_hex: str | None = None,
) -> MappingResult:
    """Map unpacked ISO 8583 fields to a canonical JPF dict.

    Args:
        iso_fields: String-keyed DE map (e.g. {"2": "4111...", "4": "000000002500"}).
        network:    Network name ('visa' | 'mastercard' | 'amex' | 'discover').
        icc_hex:    Hex content of DE55 (BER-TLV); auto-extracted from iso_fields
                    if not supplied.

    Returns:
        MappingResult with .jpf (canonical dict) and .warnings list.
    """
    spec = _load_spec(network)
    jpf: dict = {}
    warnings: list[str] = []

    # Parse DE55 TLV tags once
    de55_hex = icc_hex or iso_fields.get("55", "")
    tlv_tags = _parse_tlv(de55_hex)

    # ── Field mapping ────────────────────────────────────────────────────────
    for field_spec in spec.get("fields", []):
        canonical = field_spec["canonical"]
        source    = field_spec.get("source", {})
        emv_src   = field_spec.get("emv_source", {})
        is_pii    = field_spec.get("pii", False)
        store_rules = field_spec.get("store", {})

        raw_value: str | None = None

        # 1. Try EMV source (DE55 tag) first if present
        if emv_src and "tag" in emv_src:
            tag_key = emv_src["tag"].upper().lstrip("0") or emv_src["tag"].upper()
            # Try exact key and zero-padded variants
            for candidate in (emv_src["tag"].upper(), tag_key):
                if candidate in tlv_tags:
                    raw_value = tlv_tags[candidate]
                    transform = emv_src.get("transform", "passthrough")
                    raw_value = str(_apply_transform(raw_value, transform))
                    break

        # 2. Fall back to ISO DE
        if raw_value is None and "de" in source:
            de_key = str(source["de"])
            # Check if this source is a DE55 sub-tag
            if "tag" in source:
                tag_key = source["tag"].upper()
                raw_value = tlv_tags.get(tag_key)
            else:
                raw_value = iso_fields.get(de_key)

            if raw_value is not None:
                transform = source.get("transform", "passthrough")
                raw_value = str(_apply_transform(raw_value, transform))

        if raw_value is None:
            continue  # field not present in this message — skip

        # ── PII handling ─────────────────────────────────────────────────────
        if is_pii:
            if store_rules.get("token"):
                _set_nested(jpf, f"{canonical}_token", _token(raw_value))
            if store_rules.get("last_four"):
                _set_nested(jpf, f"{canonical}_last_four", _last_four(raw_value))
            if store_rules.get("hash"):
                _set_nested(jpf, f"{canonical}_hash",
                            hashlib.sha256(raw_value.encode()).hexdigest())
            # Never store clear PAN in jpf
        else:
            _set_nested(jpf, canonical, raw_value)

    # ── Network name ─────────────────────────────────────────────────────────
    _set_nested(jpf, "transaction.network.name", network)

    # ── Validation rules ─────────────────────────────────────────────────────
    for rule_spec in spec.get("validate", []):
        rule = rule_spec.get("rule", "equal")
        if rule != "equal":
            continue
        a_spec = rule_spec.get("a", {})
        b_spec = rule_spec.get("b", {})
        on_mismatch = rule_spec.get("on_mismatch", "flag")

        a_val = _resolve_ref(a_spec, iso_fields, tlv_tags)
        b_val = _resolve_ref(b_spec, iso_fields, tlv_tags)

        if a_val is None or b_val is None:
            continue  # one side absent — can't validate
        if a_val != b_val:
            de_label  = a_spec.get("de", "?")
            tag_label = b_spec.get("tag", "?")
            msg = (f"Mismatch: DE{de_label}={a_val!r} vs EMV tag {tag_label}={b_val!r}")
            warnings.append(msg)

    return MappingResult(
        network=network,
        jpf=jpf,
        pii_safe=dict(jpf),   # already PII-safe (no clear PAN)
        warnings=warnings,
    )


def _resolve_ref(
    ref: dict,
    iso_fields: dict[str, str],
    tlv_tags: dict[str, str],
) -> str | None:
    """Resolve a DE or TLV-tag reference to its raw string value."""
    if "de" in ref:
        return iso_fields.get(str(ref["de"]))
    if "tag" in ref:
        tag = ref["tag"].upper()
        return tlv_tags.get(tag)
    return None
