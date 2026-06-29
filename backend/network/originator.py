# backend/network/originator.py
"""Acquirer origination layer — build a network 0100/0420 from a scenario request.

build_0100(request_dict, network_override=None)
  → OriginationResult(iso_fields, packed_hex, unpacked_fields, network, profile,
                       private_des)

Phase 2 additions (T1.3):
  build_linked(request_dict, original_result, network_override=None)
    → OriginationResult for advice / reversal / refund that carries the original
      STAN/RRN in the message and populates DE90 (original data elements) where
      the network dialect expects it.

Stamps all required standard DEs (DE7, DE11/fresh STAN, DE32, DE37/fresh RRN,
DE41, DE42, DE49) plus the network-private DEs defined by the profile.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any

from backend.network.router import select_network
from backend.network.packer import pack, PackResult

# P3 T3.1 — Try jPOS sidecar for byte-authentic packing; fall back to Python.
try:
    from backend.network.jpos_bridge import pack_via_jpos as _pack_via_jpos
except ImportError:
    _pack_via_jpos = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stan() -> str:
    return f"{random.randint(0, 999999):06d}"

# Thread-safe monotonic counter for the last digit of RRN.
# Guarantees uniqueness even when _rrn() is called multiple times within the
# same UTC second (e.g. build_0100 + build_linked in rapid succession in tests).
_rrn_counter: int = 0
_rrn_lock = threading.Lock()

def _rrn() -> str:
    # DE37 is exactly 12 alphanumeric chars: yDDDHHMMSS + 1 counter digit = 12
    global _rrn_counter
    with _rrn_lock:
        _rrn_counter = (_rrn_counter + 1) % 10
        counter = _rrn_counter
    ts = datetime.now(timezone.utc).strftime("%y%j%H%M%S")  # 11 chars: 2+3+2+2+2
    return f"{ts}{counter:01d}"                              # + 1 digit = 12

def _now_mmddhhmmss() -> str:
    return datetime.now(timezone.utc).strftime("%m%d%H%M%S")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class OriginationResult:
    """All artefacts produced when originating a 0100."""
    network: str
    mti: str
    iso_fields: dict         # string-keyed DE map used for packing
    packed_hex: str          # hex of the packed ISO 8583 message
    unpacked_fields: dict    # fields as decoded back (round-trip verification)
    private_des: list[int]   # DE numbers that are network-private
    profile: dict            # full network profile dict
    stan: str
    rrn: str


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

_PROCESSING_CODE_MAP = {
    "authorization": "000000",
    "refund":        "200000",
    "reversal":      "020000",
    "advice":        "000000",
}

_POS_ENTRY_ALIASES = {
    # Some scenarios use numeric strings directly; map common aliases too
    "chip":         "051",
    "contactless":  "071",
    "magstripe":    "011",
    "manual":       "010",
    "ecommerce":    "810",
}


def build_0100(request: dict, network_override: str | None = None) -> OriginationResult:
    """Build and pack a network 0100 (authorization request) from a scenario request dict.

    Args:
        request:          Scenario request dict (keys: pan, amount, currency, mcc,
                          merchant_name, pos_entry_mode, terminal_id, …).
        network_override: If given, skip BIN routing and force this network.

    Returns:
        OriginationResult with all ISO artefacts.
    """
    pan = str(request.get("pan", "4111111111111111"))
    profile = select_network(pan, override=network_override)
    network = profile["network"]

    stan = _stan()
    rrn  = _rrn()
    now  = _now_mmddhhmmss()

    # Amount: ensure 12-digit right-justified zero-padded cents
    amount_raw = request.get("amount", 0)
    amount_str = f"{int(amount_raw):012d}"

    # Processing code
    event_type = request.get("event_type", "authorization")
    proc_code = _PROCESSING_CODE_MAP.get(event_type, "000000")

    # POS entry mode
    pos_mode = str(request.get("pos_entry_mode", "071"))
    pos_mode = _POS_ENTRY_ALIASES.get(pos_mode.lower(), pos_mode).zfill(3)

    # Currency: ISO 4217 numeric string, right-padded to 3
    currency = str(request.get("currency", "840")).zfill(3)

    # Terminal & merchant IDs — left-justified, space-padded to ISO widths
    terminal_id = str(request.get("terminal_id", "TERM0001"))[:8].ljust(8)
    merchant_id = str(request.get("acquiring_institution_id", "123456"))[:15].ljust(15)

    # Standard DE set
    iso_fields: dict[str, str] = {
        "2":  pan,
        "3":  proc_code,
        "4":  amount_str,
        "7":  now,
        "11": stan,
        "12": datetime.now(timezone.utc).strftime("%H%M%S"),
        "13": datetime.now(timezone.utc).strftime("%m%d"),
        "18": str(request.get("mcc", "5411")),
        "22": pos_mode,
        "32": str(request.get("acquiring_institution_id", "123456"))[:11],
        "37": rrn,
        "41": terminal_id,
        "42": merchant_id,
        "49": currency,
    }

    # DE55 (ICC/EMV data) if present in the scenario
    icc_data = request.get("icc_data")
    if icc_data:
        iso_fields["55"] = str(icc_data)

    mti = profile["mti"]["auth_request"]

    # P3 T3.1 — Use jPOS sidecar for byte-authentic packing when available.
    pack_result: PackResult = None  # type: ignore
    if _pack_via_jpos is not None:
        jpos_result = _pack_via_jpos(iso_fields, network, mti=mti)
        if jpos_result is not None:
            pack_result = jpos_result  # type: ignore
    if pack_result is None:
        pack_result = pack(iso_fields, network, mti=mti)

    # Round-trip unpack for audit/mapping
    from backend.network.packer import unpack
    unpack_result = unpack(pack_result.hex, network)

    return OriginationResult(
        network=network,
        mti=mti,
        iso_fields=pack_result.fields,   # includes private DEs added by pack()
        packed_hex=pack_result.hex,
        unpacked_fields=unpack_result.fields,
        private_des=pack_result.private_des,
        profile=profile,
        stan=stan,
        rrn=rrn,
    )


# ---------------------------------------------------------------------------
# T1.3 — Linked lifecycle origination (advice / reversal / refund + DE90)
# ---------------------------------------------------------------------------

#: MTI profile key for each event type (falls back to auth_request if absent).
_LIFECYCLE_MTI_KEY = {
    "advice":   "auth_request",   # 0100 + DE25=advice for some networks
    "reversal": "reversal",       # 0420
    "refund":   "auth_request",   # 0100 with processing code 200000
}

def build_linked(
    request: dict,
    original_result: OriginationResult,
    network_override: str | None = None,
) -> OriginationResult:
    """Build a linked lifecycle message (advice/reversal/refund) that references
    the original authorization via its STAN (DE11) and RRN (DE37), and includes
    DE90 (Original Data Elements) where the network dialect expects it.

    Args:
        request:         Scenario request dict; must include event_type.
        original_result: The OriginationResult from the original build_0100 call.
        network_override: Force a specific network; defaults to original_result.network.

    Returns:
        OriginationResult for the linked message.
    """
    # Use the same network as the original auth unless explicitly overridden.
    net_override = network_override or original_result.network
    pan = str(request.get("pan", "4111111111111111"))
    profile = select_network(pan, override=net_override)
    network = profile["network"]

    event_type = request.get("event_type", "reversal")
    proc_code  = _PROCESSING_CODE_MAP.get(event_type, "020000")

    # Fresh STAN/RRN for the linked message.
    stan = _stan()
    rrn  = _rrn()
    now  = _now_mmddhhmmss()

    amount_raw = request.get("amount", original_result.iso_fields.get("4", "0"))
    amount_str = f"{int(str(amount_raw).lstrip('0') or '0'):012d}"

    pos_mode = str(request.get("pos_entry_mode",
                               original_result.iso_fields.get("22", "071")))
    pos_mode = _POS_ENTRY_ALIASES.get(pos_mode.lower(), pos_mode).zfill(3)
    currency = str(request.get("currency",
                               original_result.iso_fields.get("49", "840"))).zfill(3)
    terminal_id = str(request.get("terminal_id",
                                   original_result.iso_fields.get("41", "TERM0001")))[:8].ljust(8)
    merchant_id = str(request.get("acquiring_institution_id",
                                   original_result.iso_fields.get("42", "123456")))[:15].ljust(15)

    iso_fields: dict[str, str] = {
        "2":  pan,
        "3":  proc_code,
        "4":  amount_str,
        "7":  now,
        "11": stan,                                  # fresh STAN for this message
        "12": datetime.now(timezone.utc).strftime("%H%M%S"),
        "13": datetime.now(timezone.utc).strftime("%m%d"),
        "18": str(request.get("mcc", original_result.iso_fields.get("18", "5411"))),
        "22": pos_mode,
        "32": str(request.get("acquiring_institution_id",
                               original_result.iso_fields.get("32", "123456")))[:11],
        "37": rrn,                                   # fresh RRN for this message
        "41": terminal_id,
        "42": merchant_id,
        "49": currency,
        # Original STAN and RRN — carried in DE90 (Original Data Elements).
        # Format: MTI(4) + STAN(6) + LocalTime(6) + LocalDate(4) +
        #         AcquiringInstCode(11) + ForwardInstCode(11) = 42 chars.
        "90": (
            f"{original_result.mti}"
            f"{original_result.stan}"
            f"{original_result.iso_fields.get('12','000000')}"
            f"{original_result.iso_fields.get('13','0101')}"
            f"{original_result.iso_fields.get('32','123456'):>11}"
            f"{original_result.iso_fields.get('32','123456'):>11}"
        ),
    }

    # Choose MTI based on event type.
    mti_key = _LIFECYCLE_MTI_KEY.get(event_type, "auth_request")
    mti = profile["mti"].get(mti_key, profile["mti"]["auth_request"])

    # P3 T3.1 — Use jPOS sidecar for byte-authentic packing when available.
    pack_result = None  # type: ignore
    if _pack_via_jpos is not None:
        jpos_result = _pack_via_jpos(iso_fields, network, mti=mti)
        if jpos_result is not None:
            pack_result = jpos_result  # type: ignore
    if pack_result is None:
        pack_result = pack(iso_fields, network, mti=mti)

    from backend.network.packer import unpack
    unpack_result = unpack(pack_result.hex, network)

    return OriginationResult(
        network=network,
        mti=mti,
        iso_fields=pack_result.fields,
        packed_hex=pack_result.hex,
        unpacked_fields=unpack_result.fields,
        private_des=pack_result.private_des,
        profile=profile,
        stan=stan,
        rrn=rrn,
    )
