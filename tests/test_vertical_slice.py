# tests/test_vertical_slice.py
"""Vertical-slice test suite — T6 definition of done.

Verifies the core claim:
    "Switch Visa ↔ Mastercard, watch the ISO fields change, get identical JPF,
     same SUT decision."

Assertions per the Paycon_Phase1_Claude_Code_Plan.md §5:
  1. iso_visa.fields != iso_mc.fields  (the private DE sets differ)
  2. jpf_visa == jpf_mc  ignoring transaction.network.name (dialect-agnostic)
  3. result.decision == scenario.expected_customer_decision for every row
  4. STAN/RRN are unique per run (no false duplicate / HTTP 409)

All HTTP calls to the acquirer microservice are patched so the test runs
without Docker.  The patch returns a canned "APPROVED" or "DECLINED" response
matching each scenario's expectation.
"""
from __future__ import annotations

import copy
import json
from unittest import mock

import pytest
import requests

# Make backend importable without installing as a package.
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.network.originator import build_0100
from backend.mapping.engine import map_to_jpf
from backend.main import _execute_scenario_internal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_acquirer_response(rc: str, decision: str) -> mock.MagicMock:
    """Return a mock requests.Response whose .json() looks like the acquirer."""
    m = mock.MagicMock()
    m.json.return_value = {
        "response_code": rc,
        "auth_code": "ABC123" if decision == "APPROVED" else None,
        "customer_decision": decision,
        "customer_status_code": 200 if decision == "APPROVED" else 400,
        "network": "VISANET",
        "stan": "123456",
        "rrn": "261781837233",
        "marqeta_webhook_event_type": "transaction.authorization",
        "jit_funding_method": "pgfs.authorization",
        "customer_response_body": {"decision": decision},
    }
    return m


def _comparable_jpf(jpf: dict) -> dict:
    """Strip session-unique and PAN-derived fields before cross-network comparison."""
    j = copy.deepcopy(jpf)
    # Network name intentionally differs → strip it
    j.get("transaction", {}).get("network", {}).pop("name", None)
    # STAN/RRN are fresh per call → strip them
    j.get("transaction", {}).pop("stan", None)
    j.get("transaction", {}).pop("rrn", None)
    # PAN-derived values differ across different PANs → strip them
    j.get("card", {}).pop("pan_token", None)
    j.get("card", {}).pop("pan_hash", None)
    j.get("card", {}).pop("pan_last_four", None)
    # Network-private block naturally differs → strip it
    j.pop("network", None)
    return j


# ---------------------------------------------------------------------------
# Test scenarios — mirrors §5 of the plan
# ---------------------------------------------------------------------------

_BASE_VISA_PAN = "4111111111111111"
_BASE_MC_PAN   = "5555555555554444"

VERTICAL_SLICE_CASES = [
    # (description, visa_pan, mc_pan, amount, currency, mcc, pos_mode, exp_rc, exp_decision)
    (
        "Grocery contactless $25",
        _BASE_VISA_PAN, _BASE_MC_PAN,
        2500, "840", "5411", "071",
        "00", "APPROVED",
    ),
    (
        "Electronics contactless $75",
        _BASE_VISA_PAN, _BASE_MC_PAN,
        7500, "840", "5734", "071",
        "05", "DECLINED",
    ),
    (
        "Online e-commerce EUR 50",
        _BASE_VISA_PAN, _BASE_MC_PAN,
        5000, "978", "5734", "810",
        "00", "APPROVED",
    ),
]


# ---------------------------------------------------------------------------
# T6-A — ISO private DE sets differ by network (Visa vs MC)
# ---------------------------------------------------------------------------

class TestIsoNetworkDialects:

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,rc,dec", VERTICAL_SLICE_CASES)
    def test_private_de_sets_differ(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, rc, dec):
        """Visa: private DEs {44,62,63}; Mastercard: {48,61,63}."""
        req_base = dict(
            amount=amount, currency=currency, mcc=mcc, pos_entry_mode=pos,
            terminal_id="TERM0001", acquiring_institution_id="123456",
            event_type="authorization",
        )

        visa_orig = build_0100({**req_base, "pan": visa_pan}, network_override="visa")
        mc_orig   = build_0100({**req_base, "pan": mc_pan},  network_override="mastercard")

        assert set(visa_orig.private_des) == {44, 62, 63}, (
            f"Visa private DEs: expected {{44,62,63}}, got {set(visa_orig.private_des)}"
        )
        assert set(mc_orig.private_des) == {48, 61, 63}, (
            f"MC private DEs: expected {{48,61,63}}, got {set(mc_orig.private_des)}"
        )

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,rc,dec", VERTICAL_SLICE_CASES)
    def test_field_sets_differ(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, rc, dec):
        """The total ISO field key-sets differ between Visa and MC."""
        req_base = dict(
            amount=amount, currency=currency, mcc=mcc, pos_entry_mode=pos,
            terminal_id="TERM0001", acquiring_institution_id="123456",
            event_type="authorization",
        )

        visa_orig = build_0100({**req_base, "pan": visa_pan}, network_override="visa")
        mc_orig   = build_0100({**req_base, "pan": mc_pan},  network_override="mastercard")

        assert set(visa_orig.iso_fields.keys()) != set(mc_orig.iso_fields.keys()), (
            "Visa and MC iso_fields keys should differ due to private DEs"
        )


# ---------------------------------------------------------------------------
# T6-B — JPF is identical across networks (dialect-agnostic canonical)
# ---------------------------------------------------------------------------

class TestJpfDialectAgnostic:

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,rc,dec", VERTICAL_SLICE_CASES)
    def test_jpf_identical_across_networks(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, rc, dec):
        """Identical logical transaction produces identical JPF regardless of network."""
        req_base = dict(
            amount=amount, currency=currency, mcc=mcc, pos_entry_mode=pos,
            terminal_id="TERM0001", acquiring_institution_id="123456",
            event_type="authorization",
        )

        visa_orig = build_0100({**req_base, "pan": visa_pan}, network_override="visa")
        mc_orig   = build_0100({**req_base, "pan": mc_pan},  network_override="mastercard")

        visa_map = map_to_jpf(visa_orig.unpacked_fields, "visa")
        mc_map   = map_to_jpf(mc_orig.unpacked_fields,  "mastercard")

        visa_cmp = _comparable_jpf(visa_map.jpf)
        mc_cmp   = _comparable_jpf(mc_map.jpf)

        assert visa_cmp == mc_cmp, (
            f"JPF should be identical across networks.\n"
            f"Visa:  {json.dumps(visa_cmp, indent=2)}\n"
            f"MC:    {json.dumps(mc_cmp, indent=2)}"
        )

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,rc,dec", VERTICAL_SLICE_CASES)
    def test_jpf_network_blocks_differ(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, rc, dec):
        """Network-private JPF sub-blocks are network-specific."""
        req_base = dict(
            amount=amount, currency=currency, mcc=mcc, pos_entry_mode=pos,
            terminal_id="TERM0001", acquiring_institution_id="123456",
            event_type="authorization",
        )

        visa_orig = build_0100({**req_base, "pan": visa_pan}, network_override="visa")
        mc_orig   = build_0100({**req_base, "pan": mc_pan},  network_override="mastercard")

        visa_map = map_to_jpf(visa_orig.unpacked_fields, "visa")
        mc_map   = map_to_jpf(mc_orig.unpacked_fields,  "mastercard")

        assert "visa"       in visa_map.jpf.get("network", {}), "Visa JPF must have network.visa block"
        assert "mastercard" in mc_map.jpf.get("network", {}),   "MC JPF must have network.mastercard block"
        assert "mastercard" not in visa_map.jpf.get("network", {}), "Visa JPF must NOT have MC block"
        assert "visa"       not in mc_map.jpf.get("network", {}),   "MC JPF must NOT have Visa block"

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,rc,dec", VERTICAL_SLICE_CASES)
    def test_jpf_pii_safe(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, rc, dec):
        """Full PAN must never appear in the JPF."""
        req_base = dict(
            amount=amount, currency=currency, mcc=mcc, pos_entry_mode=pos,
            terminal_id="TERM0001", acquiring_institution_id="123456",
            event_type="authorization",
        )

        visa_orig = build_0100({**req_base, "pan": visa_pan}, network_override="visa")
        visa_map  = map_to_jpf(visa_orig.unpacked_fields, "visa")

        jpf_str = json.dumps(visa_map.jpf)
        assert visa_pan not in jpf_str, f"Clear PAN {visa_pan!r} must not appear in JPF"
        assert "pan_token"    in visa_map.jpf.get("card", {}), "card.pan_token missing"
        assert "pan_last_four" in visa_map.jpf.get("card", {}), "card.pan_last_four missing"
        assert "pan_hash"     in visa_map.jpf.get("card", {}), "card.pan_hash missing"


# ---------------------------------------------------------------------------
# T6-C — Decision matches expectation for every scenario row
# ---------------------------------------------------------------------------

class TestSutDecision:

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,exp_rc,exp_dec", VERTICAL_SLICE_CASES)
    def test_visa_decision(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, exp_rc, exp_dec):
        """Visa scenario: expected RC and decision match."""
        scenario = {
            "id": f"test_{exp_dec.lower()}_visa",
            "name": f"{desc} (Visa)",
            "event_type": "authorization",
            "request": {
                "transaction_id": f"TXN_VS_{amount}",
                "pan": visa_pan,
                "amount": amount,
                "currency": currency,
                "mcc": mcc,
                "merchant_name": "Vertical Slice Merchant",
                "merchant_city": "San Francisco",
                "merchant_state": "CA",
                "merchant_country": "USA",
                "pos_entry_mode": pos,
                "terminal_id": "TERM0001",
                "acquiring_institution_id": "123456",
            },
            "expected_network_response_code": exp_rc,
            "expected_customer_decision": exp_dec,
        }

        mock_resp = _mock_acquirer_response(exp_rc, exp_dec)
        with mock.patch.object(requests, "post", return_value=mock_resp):
            trace = _execute_scenario_internal(scenario, unique=True, network_override="visa")

        assert trace["actual_network_response_code"] == exp_rc, (
            f"RC: expected {exp_rc!r}, got {trace['actual_network_response_code']!r}"
        )
        assert trace["actual_customer_decision"] == exp_dec, (
            f"Decision: expected {exp_dec!r}, got {trace['actual_customer_decision']!r}"
        )
        assert trace["passed"] is True
        assert trace["iso_message"]["network"] == "visa"

    @pytest.mark.parametrize("desc,visa_pan,mc_pan,amount,currency,mcc,pos,exp_rc,exp_dec", VERTICAL_SLICE_CASES)
    def test_mastercard_decision(self, desc, visa_pan, mc_pan, amount, currency, mcc, pos, exp_rc, exp_dec):
        """Mastercard scenario: expected RC and decision match."""
        scenario = {
            "id": f"test_{exp_dec.lower()}_mc",
            "name": f"{desc} (Mastercard)",
            "event_type": "authorization",
            "request": {
                "transaction_id": f"TXN_MC_{amount}",
                "pan": mc_pan,
                "amount": amount,
                "currency": currency,
                "mcc": mcc,
                "merchant_name": "Vertical Slice Merchant",
                "merchant_city": "San Francisco",
                "merchant_state": "CA",
                "merchant_country": "USA",
                "pos_entry_mode": pos,
                "terminal_id": "TERM0001",
                "acquiring_institution_id": "123456",
            },
            "expected_network_response_code": exp_rc,
            "expected_customer_decision": exp_dec,
        }

        mock_resp = _mock_acquirer_response(exp_rc, exp_dec)
        with mock.patch.object(requests, "post", return_value=mock_resp):
            trace = _execute_scenario_internal(scenario, unique=True, network_override="mastercard")

        assert trace["actual_network_response_code"] == exp_rc
        assert trace["actual_customer_decision"] == exp_dec
        assert trace["passed"] is True
        assert trace["iso_message"]["network"] == "mastercard"


# ---------------------------------------------------------------------------
# T6-D — STAN/RRN uniqueness per run
# ---------------------------------------------------------------------------

class TestStanRrnUniqueness:

    def test_stan_unique_per_run(self):
        """Each build_0100 call produces a distinct STAN."""
        req = dict(
            pan=_BASE_VISA_PAN, amount=2500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM0001", acquiring_institution_id="123456",
        )
        stans = {build_0100(req, network_override="visa").stan for _ in range(20)}
        # With 1M possible STANs and 20 draws, collision probability ≈ 0.019%.
        # We accept ≥ 15 unique values (very conservative) to avoid flakiness.
        assert len(stans) >= 15, f"Expected diverse STANs, got only {len(stans)} unique in 20 draws"

    def test_rrn_correct_length(self):
        """RRN must be exactly 12 characters (ISO 8583 DE37 fixed width)."""
        req = dict(
            pan=_BASE_VISA_PAN, amount=2500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM0001", acquiring_institution_id="123456",
        )
        for _ in range(10):
            orig = build_0100(req, network_override="visa")
            assert len(orig.rrn) == 12, f"RRN must be 12 chars, got {len(orig.rrn)!r}: {orig.rrn!r}"

    def test_stan_rrn_in_iso_message(self):
        """STAN (DE11) and RRN (DE37) must be present in packed ISO fields."""
        req = dict(
            pan=_BASE_VISA_PAN, amount=2500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM0001", acquiring_institution_id="123456",
        )
        orig = build_0100(req, network_override="visa")
        assert "11" in orig.iso_fields, "DE11 (STAN) missing"
        assert "37" in orig.iso_fields, "DE37 (RRN) missing"
        assert orig.iso_fields["11"] == orig.stan
        assert orig.iso_fields["37"] == orig.rrn


# ---------------------------------------------------------------------------
# T6-E — Mismatch flagging
# ---------------------------------------------------------------------------

class TestMismatchFlagging:

    def test_emv_amount_mismatch_flagged(self):
        """A 9F02 value that differs from DE4 must produce a warning."""
        req = dict(
            pan=_BASE_VISA_PAN, amount=2500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM0001", acquiring_institution_id="123456",
        )
        orig = build_0100(req, network_override="visa")

        # Inject TLV with 9F02 = 5000 (differs from DE4 = 000000002500)
        # Tag 9F02, length 06, value 000000005000
        bad_tlv = "9F0206000000005000"
        result = map_to_jpf(orig.unpacked_fields, "visa", icc_hex=bad_tlv)
        assert any("Mismatch" in w or "9F02" in w for w in result.warnings), (
            f"Expected a mismatch warning, got: {result.warnings}"
        )

    def test_no_warnings_for_clean_message(self):
        """A clean message with no EMV data should produce zero warnings."""
        req = dict(
            pan=_BASE_VISA_PAN, amount=2500, currency="840",
            mcc="5411", pos_entry_mode="071",
            terminal_id="TERM0001", acquiring_institution_id="123456",
        )
        orig = build_0100(req, network_override="visa")
        result = map_to_jpf(orig.unpacked_fields, "visa")
        assert result.warnings == [], f"Expected no warnings, got: {result.warnings}"
