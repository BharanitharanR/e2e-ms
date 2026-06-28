# backend/interchange.py
"""Interchange qualification engine (T1.3 — Phase 3).

Computes the interchange qualification tier and representative rate for a
transaction based on:
  - Card product / network (Visa, Mastercard, Amex, Discover)
  - POS entry mode (contactless chip, contact chip, magstripe, manual, ecom)
  - MCC (merchant category code — used for regulated/unregulated and
    supermarket / restaurant fast-food / T&E tiers)
  - Transaction amount (Amex has no per-swipe cap; Visa/MC do for some tiers)

Rate tables are representative and suitable for testing / certification
scenarios — they are NOT a substitute for the actual interchange schedules
published by each network.

Endpoints:
    POST /interchange/qualify      → qualify a single transaction
    GET  /interchange/rate_table   → return the full rate table

Qualification tiers (representative):
    Visa:
        - CPS/Retail (contactless chip, chip, swipe):   1.51 % + $0.10
        - CPS/Supermarket (MCC 5411 + chip):            1.22 % + $0.05
        - CPS/Restaurant (MCC 5812 + contactless):      1.19 % + $0.10
        - Electronic (ecom):                            1.80 % + $0.10
        - Standard (manual / fallback):                 2.30 % + $0.10
        - Regulated debit (all entry modes):            0.05 % + $0.21  (Durbin)

    Mastercard:
        - Merit III (contactless chip, chip, swipe):    1.58 % + $0.10
        - Supermarket (MCC 5411 + chip):                1.22 % + $0.05
        - Restaurant (MCC 5812 + contactless):          1.29 % + $0.10
        - Electronic (ecom):                            1.89 % + $0.10
        - Standard (manual / fallback):                 2.45 % + $0.10
        - Regulated debit:                              0.05 % + $0.21  (Durbin)

    Amex:
        - OptBlue Restaurant:                           2.05 % + $0.10
        - OptBlue Retail:                               1.95 % + $0.10
        - OptBlue ecom:                                 2.40 % + $0.10
        - Standard:                                     3.50 % + $0.15

    Discover:
        - Retail chip/swipe:                            1.56 % + $0.10
        - Electronic:                                   1.81 % + $0.10
        - Standard:                                     2.30 % + $0.10
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/interchange", tags=["interchange"])

# ---------------------------------------------------------------------------
# Rate table
# ---------------------------------------------------------------------------
# Each entry: (tier_name, rate_pct, fixed_cents, description)
_RATE_TABLE: dict[str, list[dict]] = {
    "visa": [
        {
            "tier":        "CPS/Retail",
            "rate_pct":    1.51,
            "fixed_cents": 10,
            "applies_when": "Contactless chip, contact chip, or magstripe; any MCC",
        },
        {
            "tier":        "CPS/Supermarket",
            "rate_pct":    1.22,
            "fixed_cents": 5,
            "applies_when": "MCC 5411 + chip or contactless",
        },
        {
            "tier":        "CPS/Restaurant",
            "rate_pct":    1.19,
            "fixed_cents": 10,
            "applies_when": "MCC 5812 + contactless",
        },
        {
            "tier":        "Electronic",
            "rate_pct":    1.80,
            "fixed_cents": 10,
            "applies_when": "E-commerce (entry mode 810)",
        },
        {
            "tier":        "Standard",
            "rate_pct":    2.30,
            "fixed_cents": 10,
            "applies_when": "Manual keyed or unqualified fallback",
        },
        {
            "tier":        "Regulated Debit",
            "rate_pct":    0.05,
            "fixed_cents": 21,
            "applies_when": "Durbin-regulated debit card, any entry mode",
        },
    ],
    "mastercard": [
        {
            "tier":        "Merit III",
            "rate_pct":    1.58,
            "fixed_cents": 10,
            "applies_when": "Contactless chip, contact chip, or magstripe; any MCC",
        },
        {
            "tier":        "Supermarket",
            "rate_pct":    1.22,
            "fixed_cents": 5,
            "applies_when": "MCC 5411 + chip or contactless",
        },
        {
            "tier":        "Restaurant",
            "rate_pct":    1.29,
            "fixed_cents": 10,
            "applies_when": "MCC 5812 + contactless",
        },
        {
            "tier":        "Electronic",
            "rate_pct":    1.89,
            "fixed_cents": 10,
            "applies_when": "E-commerce (entry mode 810)",
        },
        {
            "tier":        "Standard",
            "rate_pct":    2.45,
            "fixed_cents": 10,
            "applies_when": "Manual keyed or unqualified fallback",
        },
        {
            "tier":        "Regulated Debit",
            "rate_pct":    0.05,
            "fixed_cents": 21,
            "applies_when": "Durbin-regulated debit card, any entry mode",
        },
    ],
    "amex": [
        {
            "tier":        "OptBlue Restaurant",
            "rate_pct":    2.05,
            "fixed_cents": 10,
            "applies_when": "MCC 5812 or 5814",
        },
        {
            "tier":        "OptBlue Retail",
            "rate_pct":    1.95,
            "fixed_cents": 10,
            "applies_when": "Chip or contactless; non-restaurant MCC",
        },
        {
            "tier":        "OptBlue Electronic",
            "rate_pct":    2.40,
            "fixed_cents": 10,
            "applies_when": "E-commerce (entry mode 810)",
        },
        {
            "tier":        "Standard",
            "rate_pct":    3.50,
            "fixed_cents": 15,
            "applies_when": "Manual keyed or unqualified",
        },
    ],
    "discover": [
        {
            "tier":        "Retail Chip",
            "rate_pct":    1.56,
            "fixed_cents": 10,
            "applies_when": "Chip or contactless; any MCC",
        },
        {
            "tier":        "Electronic",
            "rate_pct":    1.81,
            "fixed_cents": 10,
            "applies_when": "E-commerce (entry mode 810)",
        },
        {
            "tier":        "Standard",
            "rate_pct":    2.30,
            "fixed_cents": 10,
            "applies_when": "Manual keyed or unqualified",
        },
    ],
}

# MCC groups
_MCC_SUPERMARKET = {"5411", "5412", "5422"}
_MCC_RESTAURANT  = {"5812", "5814", "5813"}


# ---------------------------------------------------------------------------
# Qualification logic
# ---------------------------------------------------------------------------

def qualify(
    network: str,
    pos_entry_mode: str,
    mcc: str,
    amount_cents: int,
    card_type: str = "credit",   # "credit" | "debit"
) -> dict:
    """Qualify a transaction and return the interchange tier + fee.

    Args:
        network:        visa | mastercard | amex | discover
        pos_entry_mode: ISO DE22 3-char code (071=contactless, 051=chip, 011=mag,
                        010=manual, 810=ecom) or plain alias.
        mcc:            4-digit MCC string.
        amount_cents:   Transaction amount in minor units (cents).
        card_type:      "credit" or "debit" (affects Durbin regulated tier).

    Returns:
        {
            network, tier, rate_pct, fixed_cents,
            interchange_fee_cents, interchange_fee_formatted,
            pos_entry_mode_resolved, mcc, card_type,
            qualification_notes: [str]
        }
    """
    net = network.lower()
    # Normalise entry mode aliases
    _aliases = {
        "contactless": "071",
        "chip":        "051",
        "magstripe":   "011",
        "mag":         "011",
        "manual":      "010",
        "keyed":       "010",
        "ecommerce":   "810",
        "ecom":        "810",
    }
    mode = str(pos_entry_mode).lower()
    mode = _aliases.get(mode, mode)
    # Normalise to 3-char numeric (strip trailing chars like "0" / extra digits)
    if not mode.isdigit():
        mode = "010"   # fallback to manual if unrecognised
    elif len(mode) < 3:
        mode = mode.zfill(3)

    is_chip         = mode in ("051", "052", "053")       # contact chip
    is_contactless  = mode in ("071", "072", "073", "07") # contactless
    is_mag          = mode in ("011", "012", "013", "090")
    is_ecom         = mode in ("810", "811", "812")
    is_manual       = mode in ("010", "014")

    mcc_s = str(mcc).zfill(4)
    is_supermarket  = mcc_s in _MCC_SUPERMARKET
    is_restaurant   = mcc_s in _MCC_RESTAURANT
    is_debit_regulated = (card_type == "debit")

    notes: list[str] = []
    tier        = "Standard"
    rate_pct    = 2.30
    fixed_cents = 10

    if net == "visa":
        if is_debit_regulated:
            tier, rate_pct, fixed_cents = "Regulated Debit", 0.05, 21
            notes.append("Durbin-regulated debit — capped at 0.05% + $0.21.")
        elif is_ecom:
            tier, rate_pct, fixed_cents = "Electronic", 1.80, 10
            notes.append("E-commerce transaction qualifies for CPS/Electronic.")
        elif is_manual:
            tier, rate_pct, fixed_cents = "Standard", 2.30, 10
            notes.append("Manual / keyed entry — does not qualify for CPS tiers.")
        elif is_restaurant and is_contactless:
            tier, rate_pct, fixed_cents = "CPS/Restaurant", 1.19, 10
            notes.append("MCC 5812 contactless qualifies for CPS/Restaurant.")
        elif is_supermarket and (is_chip or is_contactless):
            tier, rate_pct, fixed_cents = "CPS/Supermarket", 1.22, 5
            notes.append("MCC 5411 chip/contactless qualifies for CPS/Supermarket.")
        elif is_chip or is_contactless or is_mag:
            tier, rate_pct, fixed_cents = "CPS/Retail", 1.51, 10
            notes.append("Chip/contactless/mag qualifies for CPS/Retail.")
        else:
            notes.append("Fallback to Standard — entry mode not recognised for CPS.")

    elif net == "mastercard":
        if is_debit_regulated:
            tier, rate_pct, fixed_cents = "Regulated Debit", 0.05, 21
            notes.append("Durbin-regulated debit — capped at 0.05% + $0.21.")
        elif is_ecom:
            tier, rate_pct, fixed_cents = "Electronic", 1.89, 10
            notes.append("E-commerce qualifies for Mastercard Electronic.")
        elif is_manual:
            tier, rate_pct, fixed_cents = "Standard", 2.45, 10
            notes.append("Manual/keyed entry — Standard rate applies.")
        elif is_restaurant and is_contactless:
            tier, rate_pct, fixed_cents = "Restaurant", 1.29, 10
            notes.append("MCC 5812 contactless qualifies for Restaurant tier.")
        elif is_supermarket and (is_chip or is_contactless):
            tier, rate_pct, fixed_cents = "Supermarket", 1.22, 5
            notes.append("MCC 5411 chip/contactless qualifies for Supermarket tier.")
        elif is_chip or is_contactless or is_mag:
            tier, rate_pct, fixed_cents = "Merit III", 1.58, 10
            notes.append("Chip/contactless/mag qualifies for Merit III.")
        else:
            notes.append("Fallback to Standard.")

    elif net == "amex":
        if is_ecom:
            tier, rate_pct, fixed_cents = "OptBlue Electronic", 2.40, 10
            notes.append("E-commerce qualifies for OptBlue Electronic.")
        elif is_manual:
            tier, rate_pct, fixed_cents = "Standard", 3.50, 15
            notes.append("Manual/keyed — Amex Standard rate.")
        elif is_restaurant:
            tier, rate_pct, fixed_cents = "OptBlue Restaurant", 2.05, 10
            notes.append("Restaurant MCC qualifies for OptBlue Restaurant.")
        elif is_chip or is_contactless or is_mag:
            tier, rate_pct, fixed_cents = "OptBlue Retail", 1.95, 10
            notes.append("Chip/contactless/mag qualifies for OptBlue Retail.")
        else:
            notes.append("Fallback to Standard.")

    elif net == "discover":
        if is_ecom:
            tier, rate_pct, fixed_cents = "Electronic", 1.81, 10
            notes.append("E-commerce qualifies for Discover Electronic.")
        elif is_manual:
            tier, rate_pct, fixed_cents = "Standard", 2.30, 10
            notes.append("Manual/keyed — Discover Standard rate.")
        elif is_chip or is_contactless or is_mag:
            tier, rate_pct, fixed_cents = "Retail Chip", 1.56, 10
            notes.append("Chip/contactless/mag qualifies for Retail Chip tier.")
        else:
            notes.append("Fallback to Standard.")

    else:
        notes.append(f"Unknown network '{network}' — using generic Standard rate.")

    # Compute fee
    fee_cents = round(amount_cents * rate_pct / 100) + fixed_cents
    fee_usd   = fee_cents / 100

    return {
        "network":                    net,
        "tier":                       tier,
        "rate_pct":                   rate_pct,
        "fixed_cents":                fixed_cents,
        "interchange_fee_cents":      fee_cents,
        "interchange_fee_formatted":  f"${fee_usd:.2f}",
        "pos_entry_mode_resolved":    mode,
        "mcc":                        mcc_s,
        "card_type":                  card_type,
        "amount_cents":               amount_cents,
        "qualification_notes":        notes,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/qualify")
async def interchange_qualify(request=None):
    """Qualify a single transaction for interchange tier and fee.

    Body:
        network         str   — visa | mastercard | amex | discover
        pos_entry_mode  str   — 071 | 051 | 011 | 010 | 810 | contactless | chip | …
        mcc             str   — 4-digit MCC
        amount          int   — minor units (cents)
        card_type       str   — "credit" | "debit"  (default "credit")
    """
    body = {}
    if request is not None:
        try:
            body = await request.json()
        except Exception:
            pass

    network  = body.get("network", "visa")
    mode     = str(body.get("pos_entry_mode", "071"))
    mcc      = str(body.get("mcc", "5411"))
    amount   = int(body.get("amount", 1000))
    ctype    = body.get("card_type", "credit")

    return qualify(network=network, pos_entry_mode=mode, mcc=mcc,
                   amount_cents=amount, card_type=ctype)


@router.get("/rate_table")
async def interchange_rate_table():
    """Return the full representative interchange rate table."""
    return _RATE_TABLE
