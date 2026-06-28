# backend/enrichment.py
"""Per-hop ISO 8583 enrichment trace builder (T1.1).

Each hop in the authorization flow *adds* specific data elements to the
message.  This module reconstructs that sequence from the artefacts already
produced by the originator and mapper so the trace is *truthful* — it reflects
what the simulator actually stamped, not a static mock.

Exported:
    build_enrichment_trace(request_dict, iso_message, jpf, jit_payload,
                           resolved_network, response_json) -> list[dict]

Each entry in the returned list:
    {
        "actor": str,
        "adds":  [{"de": int|None, "name": str, "value": str|None}],
        "cumulative_iso": dict,          # ISO fields known at this hop
        "network": str | None,
        # actor-specific extras:
        "interchange_qualification": dict | None,   # Network hop
        "iso_to_jpf": dict | None,                  # Issuer hop
        "jpf_to_jit": dict | None,                  # Issuer hop
        "decision": str | None,                     # JIT hop
        "rc": str | None,                           # JIT hop
    }
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# DE label lookup (standard ISO 8583 names — concise for display)
# ---------------------------------------------------------------------------
_DE_NAMES: dict[int, str] = {
    2:  "PAN",
    3:  "Processing Code",
    4:  "Transaction Amount",
    7:  "Transmission Date/Time",
    11: "STAN",
    12: "Local Time",
    13: "Local Date",
    14: "Expiry Date",
    18: "MCC",
    19: "Acquiring Country",
    22: "POS Entry Mode",
    25: "POS Condition Code",
    32: "Acquiring Institution ID",
    37: "RRN",
    41: "Terminal ID",
    42: "Merchant ID",
    43: "Merchant Name/Location",
    49: "Currency Code",
    52: "PIN Data",
    55: "ICC / EMV Data",
    90: "Original Data Elements",
    # Network-private (representative labels)
    44: "Visa — Additional Response Data",
    47: "Amex — Additional Data National",
    48: "Mastercard — PDS / Interchange",
    61: "Mastercard — POS Data",
    62: "Discover — Private DE62",
    63: "Network Reference / BankNet Ref",
}


def _de_name(de: int) -> str:
    return _DE_NAMES.get(de, f"DE{de}")


def _val_or_none(fields: dict, de: int) -> str | None:
    return fields.get(str(de))


# ---------------------------------------------------------------------------
# Network-private DE ranges per network
# ---------------------------------------------------------------------------
_PRIVATE_DE_MAP: dict[str, list[int]] = {
    "visa":       [44, 62, 63],
    "mastercard": [48, 61, 63],
    "amex":       [47, 63],
    "discover":   [62, 63],
}


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_enrichment_trace(
    request_dict: dict,
    iso_message:  dict,
    jpf:          dict,
    jit_payload:  dict,
    resolved_network: str,
    response_json: dict,
) -> list[dict]:
    """Build the ordered per-hop enrichment trace.

    Args:
        request_dict:     The normalized request dict (post-Terminal).
        iso_message:      The iso_message dict from _execute_scenario_internal.
        jpf:              The canonical JPF dict.
        jit_payload:      The jit_decision_payload dict (step 6).
        resolved_network: The resolved network name string.
        response_json:    The HTTP response from the acquirer chain.

    Returns:
        List of hop dicts in wire order:
        [Terminal, Acquirer, Network, Issuer Processor, Customer JIT]
    """
    iso_fields = iso_message.get("fields", {}) if isinstance(iso_message, dict) else {}
    private_des = set(
        str(d) for d in (iso_message.get("private_des", []) if isinstance(iso_message, dict) else [])
    )
    net = (resolved_network or "visa").lower()

    # ── HOP 1: Terminal ─────────────────────────────────────────────────────
    terminal_des = [2, 3, 4, 7, 11, 12, 13, 22, 49]
    if iso_fields.get("55"):
        terminal_des.append(55)
    terminal_iso = {de: iso_fields[str(de)] for de in terminal_des if str(de) in iso_fields}

    terminal_hop: dict[str, Any] = {
        "actor":          "Terminal",
        "adds":           [
            {"de": de, "name": _de_name(de), "value": _val_or_none(iso_fields, de)}
            for de in terminal_des if str(de) in iso_fields
        ],
        "cumulative_iso": dict(terminal_iso),
        "network":        None,
    }

    # ── HOP 2: Acquirer ─────────────────────────────────────────────────────
    acquirer_des = [18, 32, 37, 41, 42]
    acquirer_adds = [
        {"de": de, "name": _de_name(de), "value": _val_or_none(iso_fields, de)}
        for de in acquirer_des if str(de) in iso_fields
    ]
    # Also note: acquirer sets merchant name/location (DE43) when available
    if request_dict.get("merchant_name"):
        acquirer_adds.append({
            "de": 43, "name": _de_name(43),
            "value": request_dict["merchant_name"][:25],
        })
    if request_dict.get("merchant_country"):
        acquirer_adds.append({
            "de": 19, "name": _de_name(19),
            "value": request_dict.get("merchant_country", "")[:3],
        })

    acquirer_iso = dict(terminal_iso)
    acquirer_iso.update({de: iso_fields[str(de)] for de in acquirer_des if str(de) in iso_fields})

    acquirer_hop: dict[str, Any] = {
        "actor":          "Acquirer",
        "adds":           acquirer_adds,
        "cumulative_iso": dict(acquirer_iso),
        "network":        None,
    }

    # ── HOP 3: Network ──────────────────────────────────────────────────────
    network_private_des = _PRIVATE_DE_MAP.get(net, [])
    network_adds = [
        {"de": de, "name": _de_name(de), "value": _val_or_none(iso_fields, de)}
        for de in network_private_des if str(de) in iso_fields
    ]
    # Interchange qualification note
    interchange_note: dict | None = None
    try:
        from backend.interchange import qualify
        pos_mode = str(request_dict.get("pos_entry_mode", "071"))
        mcc      = str(request_dict.get("mcc", "5411"))
        amount   = int(request_dict.get("amount", 0))
        iq = qualify(net, pos_mode, mcc, amount)
        interchange_note = {
            "tier":                 iq.get("tier"),
            "rate_pct":             iq.get("rate_pct"),
            "fixed_cents":          iq.get("fixed_cents"),
            "interchange_fee_cents": iq.get("interchange_fee_cents"),
            "network":              net,
        }
        network_adds.append({
            "de": None, "name": "Interchange Qualification",
            "value": f"{iq.get('tier')} @ {iq.get('rate_pct')}% + {iq.get('fixed_cents')}¢",
        })
    except Exception:
        pass  # interchange module not critical for enrichment trace

    network_iso = dict(acquirer_iso)
    network_iso.update({de: iso_fields[str(de)] for de in network_private_des if str(de) in iso_fields})

    network_hop: dict[str, Any] = {
        "actor":                    f"{net.capitalize()} Network",
        "adds":                     network_adds,
        "cumulative_iso":           dict(network_iso),
        "network":                  net,
        "interchange_qualification": interchange_note,
    }

    # ── HOP 4: Issuer Processor ─────────────────────────────────────────────
    # Show the ISO→JPF mapping and the JPF→JIT webhook transformation.
    db_fields = {
        "amount_cents":    request_dict.get("amount"),
        "currency":        request_dict.get("currency"),
        "stan":            request_dict.get("stan"),
        "rrn":             request_dict.get("rrn"),
        "card_token":      (jpf.get("card") or {}).get("pan_token", "(tokenised)"),
        "merchant_name":   request_dict.get("merchant_name"),
        "mcc":             request_dict.get("mcc"),
        "network":         net,
        "transaction_id":  request_dict.get("transaction_id"),
    }

    jit_webhook_shape = {
        "event_type":     response_json.get("marqeta_webhook_event_type", "transaction.authorization"),
        "jit_funding": {
            "method":         response_json.get("jit_funding_method", "pgfs.authorization"),
            "transaction_id": request_dict.get("transaction_id"),
            "amount":         request_dict.get("amount"),
            "currency":       request_dict.get("currency"),
        },
        "transaction": {
            "merchant": {"mcc": request_dict.get("mcc"), "name": request_dict.get("merchant_name")},
            "network":  net,
        },
        "card": {
            "last_four": (jpf.get("card") or {}).get("pan_last_four", "****"),
        },
    }

    issuer_hop: dict[str, Any] = {
        "actor":          "Marqeta Issuer Processor",
        "adds":           [
            {"de": None, "name": "ISO → JPF canonical mapping", "value": f"{len(jpf)} top-level JPF fields"},
            {"de": None, "name": "DB columns written",           "value": ", ".join(db_fields.keys())},
            {"de": None, "name": "JIT Funding webhook dispatched", "value": jit_webhook_shape.get("event_type", "")},
        ],
        "cumulative_iso": dict(network_iso),
        "network":        net,
        "iso_to_jpf": {
            "jpf":       jpf,
            "db_fields": db_fields,
        },
        "jpf_to_jit": {
            "event_type":    jit_webhook_shape.get("event_type"),
            "jit_funding":   jit_webhook_shape.get("jit_funding"),
            "webhook_shape": jit_webhook_shape,
        },
    }

    # ── HOP 5: Customer JIT ─────────────────────────────────────────────────
    jit_hop: dict[str, Any] = {
        "actor":   "Customer JIT (SUT)",
        "adds":    [
            {"de": None, "name": "JIT Decision",        "value": jit_payload.get("decision", "UNKNOWN")},
            {"de": None, "name": "Response Code",       "value": jit_payload.get("rc", "?")},
            {"de": None, "name": "JIT Funding Method",  "value": jit_payload.get("jit_method", "")},
        ],
        "cumulative_iso": dict(network_iso),
        "network":  net,
        "decision": jit_payload.get("decision"),
        "rc":       jit_payload.get("rc"),
    }

    return [terminal_hop, acquirer_hop, network_hop, issuer_hop, jit_hop]
