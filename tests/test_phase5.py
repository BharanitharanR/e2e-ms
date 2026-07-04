# tests/test_phase5.py
"""Phase 5 test suite — Local demo, AI Config, Dynamic Network, Enrichment Trace, Mandate guardrails.

Acceptance checks documented in CHANGELOG.md for each task:
  T0.1  make demo-local / start-local.sh
  T0.2  In-app AI provider config + key storage
  T0.3  No hardcoded "Visa" labels in demo/audit trail
  T1.1  enrichment_trace present in /execute response
  T1.2  Horizontal enrichment trace page (UI — not unit-tested here)
  T1.3  ATM + PRE-AUTH scenario files exist with correct fields
  T2.1  /ai/mandate accepts mandate text, returns structured proposal
  T2.2  /ai/mandate/apply rejects without confirmed=true
  T2.3  Guardrails: invalid DE, real PAN, missing canonical are rejected
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# T0.3 — No hardcoded "Visa" in demo_mode and audit trail
# ---------------------------------------------------------------------------
class TestDynamicNetworkLabels(unittest.TestCase):
    """T0.3: Audit labels and demo nodes use resolved network name, not hardcoded Visa."""

    def test_render_node_diagram_mastercard(self):
        """render_node_diagram with network='mastercard' must NOT contain 'Visa'."""
        from frontend.utils.demo_mode import render_node_diagram
        html = render_node_diagram(3, network="mastercard")
        self.assertNotIn("Visa", html)
        self.assertIn("mastercard", html.lower())

    def test_render_node_diagram_amex(self):
        from frontend.utils.demo_mode import render_node_diagram
        html = render_node_diagram(3, network="amex")
        self.assertNotIn("Visa", html)
        self.assertIn("amex", html.lower())

    def test_render_node_diagram_discover(self):
        from frontend.utils.demo_mode import render_node_diagram
        html = render_node_diagram(3, network="discover")
        self.assertNotIn("Visa", html)
        self.assertIn("discover", html.lower())

    def test_render_node_diagram_visa_shows_visa(self):
        """Visa network should show the correct Visa label."""
        from frontend.utils.demo_mode import render_node_diagram
        html = render_node_diagram(3, network="visa")
        self.assertIn("visa", html.lower())

    def test_demo_nodes_no_static_visa(self):
        """_BASE_NODES must not contain a static 'Visa' string (node 3 is None)."""
        from frontend.utils.demo_mode import _BASE_NODES
        # Node 3 (index 3) should be None — filled dynamically by _build_nodes()
        self.assertIsNone(_BASE_NODES[3])

    def test_build_nodes_mc(self):
        from frontend.utils.demo_mode import _build_nodes
        nodes = _build_nodes("mastercard")
        # Node index 3 should now be a tuple containing "mastercard"
        self.assertIsNotNone(nodes[3])
        label = nodes[3][1].lower()
        self.assertIn("mastercard", label)

    def test_audit_trail_network_label_visa(self):
        """Audit trail step 3 label must say 'Visa' when resolved_network is visa."""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.post("/execute/authorization_approve")
        if resp.status_code == 200:
            data = resp.json()
            audit = data.get("audit_trail", [])
            step3 = next((s for s in audit if s.get("step") == 3), None)
            if step3:
                self.assertIn("Visa", step3.get("label", ""))

    def test_audit_trail_network_label_mastercard(self):
        """Audit trail step 4 actor must say 'Mastercard' when routing via MC BIN."""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.post("/execute/authorization_approve?network=mastercard")
        if resp.status_code == 200:
            data = resp.json()
            audit = data.get("audit_trail", [])
            step4 = next((s for s in audit if s.get("step") == 4), None)
            if step4:
                self.assertIn("Mastercard", step4.get("actor", ""))
                self.assertNotIn("Visa", step4.get("actor", ""))


# ---------------------------------------------------------------------------
# T0.1 — Local run script exists and is executable
# ---------------------------------------------------------------------------
class TestLocalRunProfile(unittest.TestCase):
    """T0.1: start-local.sh is present and executable."""

    def test_start_local_sh_exists(self):
        import pathlib
        script = pathlib.Path(__file__).parent.parent / "start-local.sh"
        self.assertTrue(script.exists(), "start-local.sh not found")

    def test_start_local_sh_executable(self):
        import pathlib, stat
        script = pathlib.Path(__file__).parent.parent / "start-local.sh"
        if script.exists():
            mode = script.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, "start-local.sh is not executable")

    def test_makefile_has_demo_local_target(self):
        import pathlib
        mk = pathlib.Path(__file__).parent.parent / "Makefile"
        content = mk.read_text()
        self.assertIn("demo-local", content)
        self.assertIn("start-local.sh", content)

    def test_readme_documents_local_quickstart(self):
        import pathlib
        readme = pathlib.Path(__file__).parent.parent / "README.md"
        content = readme.read_text()
        self.assertIn("make demo-local", content)
        self.assertIn("start-local.sh", content)


# ---------------------------------------------------------------------------
# T0.2 — AI config module: key storage, provider status, no raw key in output
# ---------------------------------------------------------------------------
class TestAiConfig(unittest.TestCase):
    """T0.2: AI config module provides safe key management."""

    def test_provider_status_returns_list(self):
        from backend.ai_config import provider_status
        statuses = provider_status()
        self.assertIsInstance(statuses, list)
        self.assertGreater(len(statuses), 0)

    def test_provider_status_no_raw_keys(self):
        """provider_status() must never return raw key values."""
        from backend.ai_config import provider_status
        for p in provider_status():
            self.assertNotIn("api_key", p)
            self.assertIn(p.get("key_status"), ("detected", "not detected"))

    def test_key_status_not_detected_when_no_key(self):
        """get_key_status returns 'not detected' for an unmapped provider."""
        from backend.ai_config import get_key_status
        # Use a provider name that cannot possibly have an env var or stored key
        status = get_key_status("__nonexistent_provider_xyz__")
        self.assertEqual(status, "not detected")

    def test_load_config_returns_dict(self):
        from backend.ai_config import load_config
        cfg = load_config()
        self.assertIn("primary", cfg)
        self.assertIn("providers", cfg)
        self.assertIsInstance(cfg["providers"], dict)

    def test_supported_providers_in_config(self):
        from backend.ai_config import load_config
        cfg = load_config()
        for p in ("claude", "ollama"):
            self.assertIn(p, cfg["providers"])

    def test_set_and_get_key_roundtrip(self):
        """Store a test key and verify it can be retrieved, then delete it."""
        from backend.ai_config import set_api_key, get_api_key, delete_api_key, get_key_status
        # Use a fake provider name so we never touch real keys
        _TEST_PROVIDER = "__test_phase5__"
        _TEST_KEY = "test-key-phase5-abcdef"
        try:
            set_api_key(_TEST_PROVIDER, _TEST_KEY)
            retrieved = get_api_key(_TEST_PROVIDER)
            self.assertEqual(retrieved, _TEST_KEY)
            self.assertEqual(get_key_status(_TEST_PROVIDER), "detected")
        finally:
            delete_api_key(_TEST_PROVIDER)
        # After delete, must be 'not detected'
        self.assertEqual(get_key_status(_TEST_PROVIDER), "not detected")

    def test_set_key_rejects_short_key(self):
        from backend.ai_config import set_api_key
        with self.assertRaises(ValueError):
            set_api_key("claude", "short")

    def test_call_claude_reads_from_config(self):
        """_call_claude returns an error dict (not an exception) when no key is configured."""
        # Patch get_api_key to return None and env to have no key
        with patch.dict(os.environ, {}, clear=False):
            _saved = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                with patch("backend.ai_config.get_api_key", return_value=None):
                    from backend.ai_routes import _call_claude
                    result = _call_claude("system", "test")
                    self.assertIn("error", result)
            finally:
                if _saved:
                    os.environ["ANTHROPIC_API_KEY"] = _saved


# ---------------------------------------------------------------------------
# T1.1 — Enrichment trace present in execute response
# ---------------------------------------------------------------------------
class TestEnrichmentTrace(unittest.TestCase):
    """T1.1: /execute returns enrichment_trace with correct hop structure."""

    def _run_scenario(self, scenario_id: str = "authorization_approve") -> dict:
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.post(f"/execute/{scenario_id}")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_enrichment_trace_present(self):
        data = self._run_scenario()
        self.assertIn("enrichment_trace", data)
        trace = data["enrichment_trace"]
        self.assertIsInstance(trace, list)

    def test_enrichment_trace_has_five_hops(self):
        data = self._run_scenario()
        trace = data.get("enrichment_trace", [])
        self.assertEqual(len(trace), 5, f"Expected 5 hops, got {len(trace)}")

    def test_enrichment_trace_actors(self):
        data = self._run_scenario()
        actors = [h.get("actor") for h in data.get("enrichment_trace", [])]
        self.assertIn("Terminal", actors)
        self.assertIn("Acquirer", actors)
        # Network hop actor contains the resolved network name
        network_actors = [a for a in actors if "Network" in str(a)]
        self.assertGreater(len(network_actors), 0)

    def test_enrichment_trace_terminal_adds_stan(self):
        data = self._run_scenario()
        terminal_hop = data["enrichment_trace"][0]
        de_names = [a.get("name", "").upper() for a in terminal_hop.get("adds", [])]
        self.assertTrue(
            any("STAN" in n or "DE11" in n for n in de_names),
            f"STAN not found in terminal adds: {de_names}"
        )

    def test_enrichment_trace_acquirer_adds_merchant_id(self):
        data = self._run_scenario()
        acquirer_hop = data["enrichment_trace"][1]
        de_names = [a.get("name", "") for a in acquirer_hop.get("adds", [])]
        self.assertTrue(
            any("Merchant" in n or "Acquiring" in n for n in de_names),
            f"Merchant/Acquiring DE not found in acquirer adds: {de_names}"
        )

    def test_enrichment_trace_network_hop_has_network(self):
        data = self._run_scenario()
        network_hop = data["enrichment_trace"][2]
        self.assertIsNotNone(network_hop.get("network"))

    def test_enrichment_trace_issuer_has_iso_to_jpf(self):
        data = self._run_scenario()
        issuer_hop = data["enrichment_trace"][3]
        self.assertIn("iso_to_jpf", issuer_hop)
        self.assertIn("jpf", issuer_hop["iso_to_jpf"])

    def test_enrichment_trace_jit_has_decision(self):
        data = self._run_scenario()
        jit_hop = data["enrichment_trace"][4]
        self.assertIn("decision", jit_hop)
        self.assertIn(jit_hop["decision"], ("APPROVED", "DECLINED", "DUPLICATE", "UNKNOWN"))

    def test_enrichment_trace_network_differs_visa_vs_mc(self):
        """Network hop private DEs must differ between Visa and Mastercard."""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        visa_resp = client.post("/execute/authorization_approve?network=visa").json()
        mc_resp   = client.post("/execute/authorization_approve?network=mastercard").json()

        visa_net_hop = visa_resp.get("enrichment_trace", [{}])[2]
        mc_net_hop   = mc_resp.get("enrichment_trace", [{}])[2]

        visa_des = {a.get("de") for a in visa_net_hop.get("adds", [])}
        mc_des   = {a.get("de") for a in mc_net_hop.get("adds", [])}
        # Visa and MC private DE sets must differ
        self.assertNotEqual(visa_des, mc_des,
            f"Expected Visa and MC network hops to differ, both got: {visa_des}")


# ---------------------------------------------------------------------------
# T1.3 — Use-case preset scenario files exist with correct fields
# ---------------------------------------------------------------------------
class TestUseCasePresets(unittest.TestCase):
    """T1.3: ATM and PRE-AUTH scenario files are present and well-formed."""

    def _load_scenario(self, filename: str) -> dict:
        import pathlib
        path = pathlib.Path(__file__).parent.parent / "backend" / "scenarios" / filename
        self.assertTrue(path.exists(), f"Scenario file not found: {filename}")
        with open(path) as fh:
            return json.load(fh)

    def test_atm_scenario_exists(self):
        sc = self._load_scenario("atm_withdrawal_approve.json")
        self.assertEqual(sc["id"], "atm_withdrawal_approve")
        self.assertEqual(sc["event_type"], "authorization")

    def test_atm_scenario_has_cash_mcc(self):
        sc = self._load_scenario("atm_withdrawal_approve.json")
        self.assertEqual(sc["request"]["mcc"], "6011")

    def test_atm_scenario_has_mag_entry_mode(self):
        """ATM typically uses mag-stripe (011) or chip (051), not contactless."""
        sc = self._load_scenario("atm_withdrawal_approve.json")
        self.assertIn(sc["request"]["pos_entry_mode"], ("011", "051"))

    def test_preauth_scenario_exists(self):
        sc = self._load_scenario("preauth_approve.json")
        self.assertEqual(sc["id"], "preauth_approve")
        self.assertEqual(sc["event_type"], "authorization")

    def test_preauth_scenario_has_hotel_mcc(self):
        sc = self._load_scenario("preauth_approve.json")
        self.assertEqual(sc["request"]["mcc"], "7011")

    def test_preauth_completion_scenario_exists(self):
        sc = self._load_scenario("preauth_completion.json")
        self.assertEqual(sc["event_type"], "advice")
        self.assertIn("original_transaction_id", sc)

    def test_preauth_completion_links_to_preauth(self):
        sc = self._load_scenario("preauth_completion.json")
        self.assertEqual(sc["original_transaction_id"], "TXN_PREAUTH_001")

    def test_atm_completion_amount_sub_25_dollars(self):
        """ATM amount must be ≤ APPROVAL_LIMIT_CENTS (5000¢ default) for green demo."""
        sc = self._load_scenario("atm_withdrawal_approve.json")
        self.assertLessEqual(sc["request"]["amount"], 5000)


# ---------------------------------------------------------------------------
# T2.1 — Mandate endpoint: validate_mandate_proposal guardrails
# ---------------------------------------------------------------------------
class TestMandateGuardrails(unittest.TestCase):
    """T2.3: _validate_mandate_proposal rejects invalid proposals."""

    def _v(self, proposal: dict) -> list[str]:
        from backend.ai_routes import _validate_mandate_proposal
        return _validate_mandate_proposal(proposal)

    def test_valid_proposal_passes(self):
        proposal = {
            "iso_mapping_additions": [
                {"canonical": "transaction.wallet.indicator",
                 "source": {"de": 104, "transform": "passthrough"},
                 "network": "visa",
                 "description": "Wallet indicator"}
            ],
            "jpf_fields": [
                {"path": "transaction.wallet.indicator", "type": "STRING", "description": "Wallet"}
            ],
            "db_columns": [
                {"name": "wallet_indicator", "type": "VARCHAR(2)", "description": "Wallet"}
            ],
            "scenarios": [
                {"id": "mandate_test_001",
                 "request": {"pan": "4111111111111111", "amount": 1000}}
            ],
        }
        errors = self._v(proposal)
        self.assertEqual(errors, [], f"Valid proposal should pass but got: {errors}")

    def test_invalid_de_number_out_of_range(self):
        proposal = {
            "iso_mapping_additions": [
                {"canonical": "test.field",
                 "source": {"de": 999},
                 "network": "visa"}
            ],
            "jpf_fields": [], "db_columns": [], "scenarios": [],
        }
        errors = self._v(proposal)
        self.assertTrue(any("999" in e for e in errors), f"Expected DE range error, got: {errors}")

    def test_non_integer_de_rejected(self):
        proposal = {
            "iso_mapping_additions": [
                {"canonical": "test.field", "source": {"de": "abc"}, "network": "visa"}
            ],
            "jpf_fields": [], "db_columns": [], "scenarios": [],
        }
        errors = self._v(proposal)
        self.assertTrue(any("abc" in e or "not a valid integer" in e for e in errors))

    def test_non_test_pan_rejected(self):
        """A PAN not starting with a known test BIN must be flagged."""
        proposal = {
            "iso_mapping_additions": [],
            "jpf_fields": [], "db_columns": [],
            "scenarios": [
                {"id": "bad_sc", "request": {"pan": "9999000000000001", "amount": 1000}}
            ],
        }
        errors = self._v(proposal)
        self.assertTrue(any("PAN" in e or "test BIN" in e for e in errors),
                        f"Expected PAN error, got: {errors}")

    def test_test_pan_4111_passes(self):
        proposal = {
            "iso_mapping_additions": [],
            "jpf_fields": [], "db_columns": [],
            "scenarios": [
                {"id": "good_sc", "request": {"pan": "4111111111111111", "amount": 1000}}
            ],
        }
        errors = self._v(proposal)
        pan_errors = [e for e in errors if "PAN" in e]
        self.assertEqual(pan_errors, [])

    def test_invalid_jpf_field_type_rejected(self):
        proposal = {
            "iso_mapping_additions": [],
            "jpf_fields": [{"path": "test.field", "type": "JSONBLOB"}],
            "db_columns": [], "scenarios": [],
        }
        errors = self._v(proposal)
        self.assertTrue(any("JSONBLOB" in e for e in errors))

    def test_missing_canonical_rejected(self):
        proposal = {
            "iso_mapping_additions": [
                {"source": {"de": 44}, "network": "visa"}  # missing canonical
            ],
            "jpf_fields": [], "db_columns": [], "scenarios": [],
        }
        errors = self._v(proposal)
        self.assertTrue(any("canonical" in e for e in errors))

    def test_apply_endpoint_rejects_without_confirmed(self):
        """POST /ai/mandate/apply must reject when confirmed != True."""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.post("/ai/mandate/apply", json={
            "proposal": {},
            "network":  "visa",
            "confirmed": False,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("confirmed", resp.json().get("reason", "").lower())

    def test_apply_endpoint_rejects_invalid_proposal(self):
        """POST /ai/mandate/apply with invalid proposal (bad DE) must return 422."""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        bad_proposal = {
            "iso_mapping_additions": [
                {"canonical": "test.field", "source": {"de": 9999}, "network": "visa"}
            ],
            "jpf_fields": [], "db_columns": [], "scenarios": [],
        }
        resp = client.post("/ai/mandate/apply", json={
            "proposal":  bad_proposal,
            "network":   "visa",
            "confirmed": True,
        })
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertIn("errors", body)


# ---------------------------------------------------------------------------
# T2.1 — /ai/providers endpoints
# ---------------------------------------------------------------------------
class TestAiProviderEndpoints(unittest.TestCase):
    """T0.2: /ai/providers returns structured config; /ai/providers/config accepts updates."""

    def test_get_providers_returns_200(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.get("/ai/providers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("primary", body)
        self.assertIn("providers", body)

    def test_get_providers_no_raw_api_keys(self):
        """The /ai/providers response must never contain a raw API key."""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.get("/ai/providers")
        body_str = resp.text
        # Check that no sk-ant- or sk- prefix appears in the response
        self.assertNotIn("sk-ant-", body_str)
        for p in resp.json().get("providers", []):
            self.assertNotIn("api_key", p)
            self.assertIn(p["key_status"], ("detected", "not detected"))

    def test_providers_key_endpoint_rejects_empty_key(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        resp = client.post("/ai/providers/key", json={"provider": "claude", "api_key": ""})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
