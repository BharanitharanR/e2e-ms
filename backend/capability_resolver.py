"""
Builds the capability grounding text injected into every heal prompt by
fetching customer_jit's live /config endpoint.

This is the only file that needs updating when customer_jit grows new
decision logic -- change _KNOWN_RESPONSE_CODES and _UNIMPLEMENTED_FEATURES
the day the real code changes.
"""
import os
import requests

CUSTOMER_JIT_URL = os.getenv("CUSTOMER_JIT_URL", "http://customer_jit:8001")

# These are the ONLY codes customer_jit/_decide() can ever return.
# Derived directly from reading customer_jit/app.py.
_KNOWN_RESPONSE_CODES = {
    "00": "APPROVED — amount <= approval_limit_cents and no other rule triggered",
    "51": "DECLINED — amount > approval_limit_cents (insufficient funds)",
    "57": "DECLINED — mcc is in blocked_mccs list (transaction not permitted)",
    "61": "DECLINED — cumulative daily spend > daily_limit_cents",
    "65": "DECLINED — transaction count this session > velocity_max_txn",
    "76": "DUPLICATE — transaction_id already seen (HTTP 409)",
}

# Update this list the moment any of these actually lands in customer_jit/app.py
_UNIMPLEMENTED_FEATURES = [
    "pin_validation (no pin field in NetworkAuthRequest)",
    "emv_cryptogram_validation (icc_data is passed through, never inspected)",
    "fraud_scoring",
    "card_expiry_validation",
    "cvv_validation",
    "geo_ip_checks",
    "3ds_authentication",
]


def get_capabilities_text(timeout: float = 3.0) -> str:
    """
    Returns prompt-ready grounding text.
    Falls back to a safe conservative message if customer_jit is unreachable,
    which pushes the LLM toward 'unknown'/escalate rather than guessing.
    """
    try:
        resp = requests.get(f"{CUSTOMER_JIT_URL}/config", timeout=timeout)
        resp.raise_for_status()
        cfg = resp.json()
    except requests.RequestException as e:
        return (
            f"WARNING: could not reach customer_jit /config ({e}). "
            "Treat ALL assertions about system behaviour as UNVERIFIED. "
            "Default to root_cause_category 'unknown'."
        )

    blocked = cfg.get("blocked_mccs") or []
    daily = cfg.get("daily_limit_cents") or 0
    velocity = cfg.get("velocity_max_txn") or 0

    rc_lines = "\n".join(f"  {rc}: {desc}" for rc, desc in _KNOWN_RESPONSE_CODES.items())
    unimpl = "\n".join(f"  - {f}" for f in _UNIMPLEMENTED_FEATURES)

    return f"""Live customer_jit configuration (fetched from {CUSTOMER_JIT_URL}/config):
  approval_limit_cents : {cfg.get('approval_limit_cents')}  (amount <= this → RC 00 APPROVED)
  blocked_mccs         : {blocked if blocked else '[] — no MCC blocking active'}
  daily_limit_cents    : {daily if daily else '0 — disabled'}
  velocity_max_txn     : {velocity if velocity else '0 — disabled'}

Response codes this system can EVER emit (no others are possible):
{rc_lines}

Features that DO NOT EXIST in this system (no code path, no matter what the request contains):
{unimpl}

Any scenario with expected_network_response_code outside {{00,51,57,61,65,76}} is testing
a feature_not_implemented case. Any decline reason involving PIN, EMV, fraud, expiry, CVV,
or 3DS is equally impossible — there is no code for it."""