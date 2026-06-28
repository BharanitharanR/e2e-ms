# backend/settlement.py
"""Clearing & settlement file generation and validation (T1.1 — Phase 3).

Generates a representative ACH/ISO 8583-style settlement file from the ledger's
CLEARED transactions and validates that:
  - Every record's amount matches the ledger's remaining_amount (reconciliation).
  - The file header totals (record count, gross amount) are consistent.
  - No record is in an unexpected state.

Endpoints exposed via FastAPI router:
    POST /settlement/generate   → generates a settlement file from the ledger
    POST /settlement/validate   → validates a settlement file dict

Settlement file format (JSON representation of a flat-file structure):
    {
        "header": {
            "file_id":      str,    # unique per run
            "created_at":   str,    # ISO8601
            "network":      str,    # network label
            "record_count": int,
            "gross_amount": int,    # sum of all cleared amounts (minor units)
            "currency":     str,
        },
        "records": [
            {
                "seq":            int,     # 1-based
                "transaction_id": str,
                "network":        str,
                "original_amount":int,     # authorized amount
                "cleared_amount": int,     # settled amount
                "currency":       str,
                "state":          str,     # CLEARED | REVERSED | REFUNDED
                "created_at":     str,
            },
            ...
        ],
        "trailer": {
            "record_count": int,    # must match header
            "gross_amount": int,    # must match header
            "hash_total":   str,    # last 10 digits of sum of cleared_amounts
        }
    }
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter

# Settlement router — included in main.py
router = APIRouter(prefix="/settlement", tags=["settlement"])


# ---------------------------------------------------------------------------
# Ledger import (imported lazily to avoid circular imports in tests)
# ---------------------------------------------------------------------------

def _get_ledger_snapshot() -> list[dict]:
    """Return a snapshot of the in-memory issuer ledger."""
    try:
        from backend.marqeta_simulator import _ledger, _ledger_lock
    except ImportError:
        from marqeta_simulator import _ledger, _ledger_lock  # type: ignore
    import threading
    with _ledger_lock:
        return list(_ledger.values())


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_settlement_file(
    network_filter: Optional[str] = None,
    currency_filter: Optional[str] = None,
) -> dict:
    """Generate a settlement file from all CLEARED ledger entries.

    Args:
        network_filter:  If set, only include entries for this network.
        currency_filter: If set, only include entries for this currency.

    Returns:
        Settlement file dict (see module docstring for schema).
    """
    entries = _get_ledger_snapshot()
    now = datetime.now(timezone.utc).isoformat()
    file_id = f"SETTLE_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # Include CLEARED entries (REVERSED entries were cancelled; PENDING not yet settled)
    clearable_states = {"CLEARED"}
    records = []
    seq = 1
    for entry in entries:
        if entry["state"] not in clearable_states:
            continue
        if network_filter and entry.get("network", "").lower() != network_filter.lower():
            continue
        if currency_filter and entry.get("currency") != currency_filter:
            continue

        # The cleared_amount is original_amount - remaining_amount
        cleared_amount = entry["amount"] - entry.get("remaining_amount", 0)

        records.append({
            "seq":              seq,
            "transaction_id":   entry["transaction_id"],
            "network":          entry.get("network", "unknown"),
            "original_amount":  entry["amount"],
            "cleared_amount":   cleared_amount,
            "currency":         entry.get("currency", "840"),
            "state":            entry["state"],
            "created_at":       entry.get("created_at", now),
        })
        seq += 1

    gross_amount   = sum(r["cleared_amount"] for r in records)
    record_count   = len(records)
    # Hash total: last 10 digits of the sum of all cleared amounts (industry convention)
    hash_total     = str(gross_amount)[-10:].zfill(10)
    currency_label = (
        records[0]["currency"] if records else (currency_filter or "840")
    )

    return {
        "header": {
            "file_id":      file_id,
            "created_at":   now,
            "network":      network_filter or "ALL",
            "record_count": record_count,
            "gross_amount": gross_amount,
            "currency":     currency_label,
        },
        "records": records,
        "trailer": {
            "record_count": record_count,
            "gross_amount": gross_amount,
            "hash_total":   hash_total,
        },
    }


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class SettlementValidationError:
    def __init__(self, code: str, message: str, record_seq: Optional[int] = None):
        self.code       = code
        self.message    = message
        self.record_seq = record_seq

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.record_seq is not None:
            d["record_seq"] = self.record_seq
        return d


def validate_settlement_file(file_dict: dict) -> dict:
    """Validate a settlement file dict.

    Checks:
      V01 — header.record_count matches len(records)
      V02 — header.gross_amount matches sum of cleared_amounts
      V03 — trailer.record_count matches header.record_count
      V04 — trailer.gross_amount matches header.gross_amount
      V05 — trailer.hash_total is correct last-10-digits
      V06 — per-record: cleared_amount ≤ original_amount
      V07 — per-record: state is CLEARED (others shouldn't appear in settlement)
      V08 — per-record: currency matches header currency (if header has one)
      V09 — cross-reference against live ledger (amount reconciliation)

    Returns:
        {valid: bool, errors: [...], warnings: [...], record_count: int,
         gross_amount: int, reconciled: int}
    """
    errors: list[SettlementValidationError] = []
    warnings: list[str] = []

    header  = file_dict.get("header", {})
    records = file_dict.get("records", [])
    trailer = file_dict.get("trailer", {})

    h_count  = header.get("record_count", -1)
    h_gross  = header.get("gross_amount", -1)
    h_curr   = header.get("currency")
    t_count  = trailer.get("record_count", -1)
    t_gross  = trailer.get("gross_amount", -1)
    t_hash   = trailer.get("hash_total", "")
    actual_count = len(records)
    actual_gross = sum(r.get("cleared_amount", 0) for r in records)

    # V01
    if h_count != actual_count:
        errors.append(SettlementValidationError(
            "V01",
            f"header.record_count={h_count} but actual record count={actual_count}",
        ))

    # V02
    if h_gross != actual_gross:
        errors.append(SettlementValidationError(
            "V02",
            f"header.gross_amount={h_gross} but sum of cleared_amounts={actual_gross}",
        ))

    # V03
    if t_count != h_count:
        errors.append(SettlementValidationError(
            "V03",
            f"trailer.record_count={t_count} does not match header.record_count={h_count}",
        ))

    # V04
    if t_gross != h_gross:
        errors.append(SettlementValidationError(
            "V04",
            f"trailer.gross_amount={t_gross} does not match header.gross_amount={h_gross}",
        ))

    # V05
    expected_hash = str(actual_gross)[-10:].zfill(10)
    if t_hash and t_hash != expected_hash:
        errors.append(SettlementValidationError(
            "V05",
            f"trailer.hash_total={t_hash!r} expected {expected_hash!r}",
        ))

    # Per-record checks
    for rec in records:
        seq = rec.get("seq")

        # V06
        if rec.get("cleared_amount", 0) > rec.get("original_amount", 0):
            errors.append(SettlementValidationError(
                "V06",
                f"cleared_amount {rec['cleared_amount']} > original_amount "
                f"{rec['original_amount']} — over-clearing",
                record_seq=seq,
            ))

        # V07
        if rec.get("state") != "CLEARED":
            errors.append(SettlementValidationError(
                "V07",
                f"Record state '{rec.get('state')}' is not CLEARED — "
                "should not appear in settlement file",
                record_seq=seq,
            ))

        # V08
        if h_curr and rec.get("currency") != h_curr:
            warnings.append(
                f"Record seq={seq} currency={rec.get('currency')} "
                f"differs from header currency={h_curr}"
            )

    # V09 — cross-reference against live ledger
    reconciled = 0
    try:
        entries = _get_ledger_snapshot()
        ledger_map = {e["transaction_id"]: e for e in entries}
        for rec in records:
            txn_id = rec.get("transaction_id")
            ledger_entry = ledger_map.get(txn_id)
            if ledger_entry is None:
                warnings.append(
                    f"Record transaction_id={txn_id!r} not found in live ledger "
                    "(may have been cleared in a previous run)."
                )
            else:
                ledger_cleared = ledger_entry["amount"] - ledger_entry.get("remaining_amount", 0)
                if ledger_cleared != rec.get("cleared_amount"):
                    errors.append(SettlementValidationError(
                        "V09",
                        f"Amount mismatch for {txn_id}: "
                        f"settlement cleared={rec['cleared_amount']}, "
                        f"ledger cleared={ledger_cleared}",
                        record_seq=rec.get("seq"),
                    ))
                else:
                    reconciled += 1
    except Exception as exc:
        warnings.append(f"Ledger cross-reference unavailable: {exc}")

    valid = len(errors) == 0
    return {
        "valid":        valid,
        "errors":       [e.to_dict() for e in errors],
        "warnings":     warnings,
        "record_count": actual_count,
        "gross_amount": actual_gross,
        "reconciled":   reconciled,
    }


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------

@router.post("/generate")
async def settlement_generate(request=None):
    """Generate a settlement file from all CLEARED ledger entries.

    Body (optional):
        network   str   — filter by network (default: all networks)
        currency  str   — filter by currency code (default: all)
    """
    body = {}
    if request is not None:
        try:
            body = await request.json()
        except Exception:
            pass

    network_filter  = body.get("network")
    currency_filter = body.get("currency")
    file_dict = generate_settlement_file(
        network_filter=network_filter,
        currency_filter=currency_filter,
    )
    return file_dict


@router.post("/validate")
async def settlement_validate(request=None):
    """Validate a settlement file dict.

    Body: the settlement file dict returned by POST /settlement/generate.
    """
    body = {}
    if request is not None:
        try:
            body = await request.json()
        except Exception:
            pass

    return validate_settlement_file(body)
