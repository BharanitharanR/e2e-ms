# e2e-marqeta-simulator/backend/suites.py
"""Suite catalogue: maps suite keys to ordered scenario ID lists.

Each suite entry has:
  name          - Human-readable display name
  scenario_ids  - Ordered list of scenario IDs to execute
"""

SUITES = {
    "full_regression": {
        "name": "Full Regression Suite",
        "scenario_ids": [
            "suite_purchase_approve",
            "suite_purchase_decline",
            "suite_atm_approve",
            "suite_atm_decline",
            "suite_pin_verify",
            "suite_preauth",
            "suite_oct",
            "suite_refund",
            "suite_advice",
            "suite_reversal",
            "suite_duplicate",
            "suite_zero_amount",
            "suite_large_decline",
            "suite_multicurrency_eur",
        ],
    },
    "auth_flows": {
        "name": "Auth Flows Only",
        "scenario_ids": [
            "suite_purchase_approve",
            "suite_purchase_decline",
            "suite_atm_approve",
            "suite_atm_decline",
            "suite_pin_verify",
            "suite_preauth",
            "suite_oct",
        ],
    },
    "clearing_flows": {
        "name": "Clearing & Refund Flows",
        "scenario_ids": [
            "suite_advice",
            "suite_refund",
            "suite_reversal",
        ],
    },
    "edge_cases": {
        "name": "Edge Cases",
        "scenario_ids": [
            "suite_duplicate",
            "suite_zero_amount",
            "suite_large_decline",
            "suite_multicurrency_eur",
        ],
    },
}
