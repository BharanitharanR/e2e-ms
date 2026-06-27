# tests/test_lifecycle.py
"""Lifecycle chain test — T1.4 definition of done.

Verifies the issuer ledger state transitions for the linked chain:
    auth → partial clearing ($50 of $75) → refund → reversal (remaining $25)

Also tests:
- Unknown original_transaction_id is rejected (T1.2).
- Linked messages carry DE90 + original STAN/RRN (T1.3).

All HTTP calls to acquirer and Marqeta microservices are mocked so the test
runs without Docker.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock
import pytest
import requests

from backend.network.originator import build_0100, build_linked
from backend.marqeta_simulator import (
    _ledger,
    _ledger_lock,
    _ledger_create,
    _ledger_apply_event,
    _ledger_get,
)
from backend.models import NetworkAuthRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_ledger():
    """Reset the in-memory ledger between tests."""
    with _ledger_lock:
        _ledger.clear()


def _mock_approved():
    m = mock.MagicMock()
    m.json.return_value = {
        "response_code": "00",
        "auth_code": "ABC123",
        "customer_decision": "APPROVED",
        "customer_status_code": 200,
        "network": "VISANET",
        "stan": "123456",
        "rrn": "261781837233",
        "marqeta_webhook_event_type": "transaction.authorization",
        "jit_funding_method": "pgfs.authorization",
        "customer_response_body": {"decision": "APPROVED"},
    }
    return m


_BASE_REQ = dict(
    pan="4111111111111111",
    amount=7500,
    currency="840",
    mcc="5411",
    pos_entry_mode="071",
    terminal_id="TERM_LC01",
    acquiring_institution_id="123456",
    event_type="authorization",
)


# ---------------------------------------------------------------------------
# T1.1 — Ledger entry created on approved auth
# ---------------------------------------------------------------------------

class TestLedgerCreate:

    def setup_method(self):
        _clear_ledger()

    def test_pending_entry_created_on_approve(self):
        """An approved auth creates a PENDING ledger entry with the full amount."""
        req = NetworkAuthRequest(transaction_id="TXN_LC_001", **{
            k: v for k, v in _BASE_REQ.items() if k != "event_type"
        })
        entry = _ledger_create(req, network="visa")

        assert entry["transaction_id"] == "TXN_LC_001"
        assert entry["amount"] == 7500
        assert entry["remaining_amount"] == 7500
        assert entry["state"] == "PENDING"
        assert entry["currency"] == "840"
        assert entry["network"] == "visa"
        assert entry["linked_events"] == []

    def test_ledger_lookup(self):
        req = NetworkAuthRequest(transaction_id="TXN_LC_002", **{
            k: v for k, v in _BASE_REQ.items() if k != "event_type"
        })
        _ledger_create(req, network="mastercard")
        found = _ledger_get("TXN_LC_002")
        assert found is not None
        assert found["network"] == "mastercard"

    def test_missing_returns_none(self):
        assert _ledger_get("DOES_NOT_EXIST") is None


# ---------------------------------------------------------------------------
# T1.2 — Lifecycle state transitions
# ---------------------------------------------------------------------------

class TestLifecycleTransitions:

    def setup_method(self):
        _clear_ledger()
        # Seed a PENDING auth
        req = NetworkAuthRequest(transaction_id="TXN_LC_CHAIN", **{
            k: v for k, v in _BASE_REQ.items() if k != "event_type"
        })
        _ledger_create(req, network="visa")

    def test_partial_clearing_decrements_remaining_amount(self):
        """$50 clearing of $75 auth leaves $25 remaining; state → CLEARED."""
        entry = _ledger_apply_event(
            original_txn_id="TXN_LC_CHAIN",
            event_type="advice",
            txn_id="TXN_CLEAR_001",
            amount=5000,          # $50
        )
        assert entry["state"] == "CLEARED"
        assert entry["remaining_amount"] == 2500   # $75 - $50 = $25
        assert len(entry["linked_events"]) == 1
        assert entry["linked_events"][0]["cleared_amount"] == 5000

    def test_refund_creates_credit_without_state_change(self):
        """Refund logs a credit event but doesn't change PENDING state."""
        entry = _ledger_apply_event(
            original_txn_id="TXN_LC_CHAIN",
            event_type="refund",
            txn_id="TXN_REFUND_001",
            amount=5000,
        )
        assert entry["state"] == "PENDING"   # no state change on refund
        credit = next(e for e in entry["linked_events"] if e.get("credit_amount"))
        assert credit["credit_amount"] == 5000

    def test_reversal_moves_to_reversed(self):
        """Reversal moves state to REVERSED."""
        entry = _ledger_apply_event(
            original_txn_id="TXN_LC_CHAIN",
            event_type="reversal",
            txn_id="TXN_REV_001",
            amount=7500,
        )
        assert entry["state"] == "REVERSED"
        assert entry["remaining_amount"] == 0
        assert entry["linked_events"][0]["reversed_amount"] == 7500

    def test_partial_reversal_decrements_correctly(self):
        """Partial reversal of $25 from $75 leaves $50 remaining."""
        entry = _ledger_apply_event(
            original_txn_id="TXN_LC_CHAIN",
            event_type="reversal",
            txn_id="TXN_REV_PARTIAL",
            amount=2500,          # $25
        )
        assert entry["state"] == "REVERSED"
        assert entry["remaining_amount"] == 5000   # $75 - $25 = $50

    def test_unknown_original_raises_value_error(self):
        """Lifecycle event for unknown original_txn_id raises ValueError."""
        with pytest.raises(ValueError, match="Unknown original_transaction_id"):
            _ledger_apply_event(
                original_txn_id="DOES_NOT_EXIST",
                event_type="advice",
                txn_id="TXN_ORPHAN",
                amount=1000,
            )


# ---------------------------------------------------------------------------
# T1.2 — Full chain: $75 auth → $50 partial clear → refund → $25 reversal
# ---------------------------------------------------------------------------

class TestFullLifecycleChain:

    def setup_method(self):
        _clear_ledger()

    def test_full_chain_coherent_ledger_transitions(self):
        """Auth → partial clearing → refund → reversal produces coherent state."""
        txn_id = "TXN_FULL_CHAIN"

        # Step 1: Auth
        req = NetworkAuthRequest(transaction_id=txn_id, **{
            k: v for k, v in _BASE_REQ.items() if k != "event_type"
        })
        auth_entry = _ledger_create(req, network="visa")
        assert auth_entry["state"] == "PENDING"
        assert auth_entry["remaining_amount"] == 7500

        # Step 2: Partial clearing $50
        clear_entry = _ledger_apply_event(
            original_txn_id=txn_id, event_type="advice",
            txn_id="TXN_CLEAR", amount=5000,
        )
        assert clear_entry["state"] == "CLEARED"
        assert clear_entry["remaining_amount"] == 2500   # $25 remains

        # Step 3: Refund $50 (credit)
        refund_entry = _ledger_apply_event(
            original_txn_id=txn_id, event_type="refund",
            txn_id="TXN_REFUND", amount=5000,
        )
        # State stays CLEARED after refund
        assert refund_entry["state"] == "CLEARED"
        credits = [e for e in refund_entry["linked_events"] if "credit_amount" in e]
        assert credits[0]["credit_amount"] == 5000

        # Step 4: Reverse remaining $25
        rev_entry = _ledger_apply_event(
            original_txn_id=txn_id, event_type="reversal",
            txn_id="TXN_REVERSAL", amount=2500,
        )
        assert rev_entry["state"] == "REVERSED"
        assert rev_entry["remaining_amount"] == 0
        assert len(rev_entry["linked_events"]) == 3   # clear + refund + reversal

        print("✅ Full lifecycle chain: PENDING→CLEARED→REVERSED, remaining=0")


# ---------------------------------------------------------------------------
# T1.3 — Linked origination carries DE90 + original STAN/RRN
# ---------------------------------------------------------------------------

class TestLinkedOrigination:

    def test_reversal_carries_de90_and_original_stan(self):
        """build_linked() for a reversal populates DE90 with original STAN."""
        auth_req = dict(
            pan="4111111111111111", amount=7500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM_LC01", acquiring_institution_id="123456",
            event_type="authorization",
        )
        auth_orig = build_0100(auth_req, network_override="visa")

        reversal_req = dict(
            pan="4111111111111111", amount=7500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM_LC01", acquiring_institution_id="123456",
            event_type="reversal",
        )
        rev_orig = build_linked(reversal_req, auth_orig, network_override="visa")

        # DE90 must be present
        assert "90" in rev_orig.iso_fields, "DE90 (Original Data Elements) must be present"

        de90 = rev_orig.iso_fields["90"]
        # DE90 format: MTI(4) + original STAN(6) + ...
        assert de90[:4] == auth_orig.mti, f"DE90 MTI mismatch: {de90[:4]} != {auth_orig.mti}"
        assert de90[4:10] == auth_orig.stan, f"DE90 STAN mismatch: {de90[4:10]} != {auth_orig.stan}"
        print(f"  ✅ DE90 = '{de90}' — original MTI+STAN embedded")

    def test_reversal_has_fresh_stan_and_rrn(self):
        """Linked message has a new STAN/RRN, not the same as the original."""
        auth_req = dict(
            pan="4111111111111111", amount=7500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM_LC01", acquiring_institution_id="123456",
            event_type="authorization",
        )
        auth_orig = build_0100(auth_req, network_override="visa")
        rev_orig  = build_linked(
            {**auth_req, "event_type": "reversal"}, auth_orig, network_override="visa"
        )
        assert rev_orig.stan != auth_orig.stan, "Linked message must have a fresh STAN"
        assert rev_orig.rrn  != auth_orig.rrn,  "Linked message must have a fresh RRN"
        assert len(rev_orig.rrn) == 12

    def test_processing_code_correct_for_event_type(self):
        """Processing code must reflect event type."""
        auth_req = dict(
            pan="4111111111111111", amount=7500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM_LC01", acquiring_institution_id="123456",
            event_type="authorization",
        )
        orig = build_0100(auth_req, network_override="visa")
        assert orig.iso_fields["3"] == "000000", "Auth processing code should be 000000"

        rev_req = {**auth_req, "event_type": "reversal"}
        rev = build_linked(rev_req, orig, network_override="visa")
        assert rev.iso_fields["3"] == "020000", "Reversal processing code should be 020000"

        refund_req = {**auth_req, "event_type": "refund"}
        ref = build_linked(refund_req, orig, network_override="visa")
        assert ref.iso_fields["3"] == "200000", "Refund processing code should be 200000"
