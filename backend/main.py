# e2e-marqeta-simulator/backend/main.py
"""Orchestrator + REST API for the simulator (port 8000).

Owns the scenario catalogue, drives the Terminal -> Acquirer chain, scores the
result against the scenario's expectations, and keeps a rolling history.
"""
import os
import json
import glob
import time
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request
import uvicorn

# Terminal lives in the same `backend` folder; support both run styles.
try:
    from backend.terminal import Terminal
except ImportError:  # pragma: no cover
    from terminal import Terminal

# Suite catalogue (Enhancement 2).
try:
    from backend.suites import SUITES
except ImportError:
    from suites import SUITES

# Chip/NFC card emulator (Enhancement 3).
try:
    from backend.chip_terminal import SoftwareCardEmulator
except ImportError:
    from chip_terminal import SoftwareCardEmulator

ACQUIRER_URL = os.getenv("ACQUIRER_URL", "http://acquirer:8101/authorize")
CUSTOMER_JIT_RESET_URL = os.getenv("CUSTOMER_JIT_RESET_URL", "http://customer_jit:8001/reset")
SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "scenarios")

app = FastAPI(title="Marqeta E2E Simulator Orchestrator")

# Rolling in-memory execution history (most recent last).
HISTORY = []

# Module-level chip card emulator singleton.
_chip_emulator = SoftwareCardEmulator()


# --------------------------------------------------------------------------- #
# Scenario helpers
# --------------------------------------------------------------------------- #
def _read_scenarios():
    scenarios = []
    for path in sorted(glob.glob(os.path.join(SCENARIOS_DIR, "*.json"))):
        try:
            with open(path) as fh:
                data = json.load(fh)
            data["_file"] = os.path.basename(path)
            scenarios.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return scenarios


def _find_scenario(scenario_id):
    for s in _read_scenarios():
        if s.get("id") == scenario_id or s.get("_file", "").rstrip(".json") == scenario_id:
            return s
    return None


def _ensure_scenarios_dir():
    os.makedirs(SCENARIOS_DIR, exist_ok=True)


@app.on_event("startup")
def _startup():
    _ensure_scenarios_dir()


# --------------------------------------------------------------------------- #
# Core execution helper (shared by /execute and /execute_suite)
# --------------------------------------------------------------------------- #
def _execute_scenario_internal(scenario: dict, unique: bool = True) -> dict:
    """Run a scenario dict end-to-end and return a trace dict."""
    event_type = scenario.get("event_type", "authorization")
    base_request = dict(scenario.get("request", {}))

    # Capture cardholder tap payload (pre-Terminal) for audit trail.
    ts_cardholder = datetime.now(timezone.utc).isoformat()

    # Terminal layer: normalise + stamp STAN/RRN + (optionally) unique txn id.
    request_dict = Terminal.swipe(base_request, unique=unique)

    # Attach routing info for non-authorization events.
    if event_type != "authorization":
        request_dict["event_type"] = event_type
        request_dict["original_transaction_id"] = scenario.get("original_transaction_id")
        if event_type == "advice":
            request_dict["advice_type"] = scenario.get("advice_type", "CLEARING")
    else:
        request_dict["event_type"] = "authorization"

    ts_outbound = datetime.now(timezone.utc).isoformat()

    start = time.perf_counter()
    try:
        resp = requests.post(ACQUIRER_URL, json=request_dict, timeout=15)
        response_json = resp.json()
    except (requests.RequestException, ValueError) as e:
        response_json = {"error": str(e)}
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    ts_inbound = datetime.now(timezone.utc).isoformat()

    expected_rc = scenario.get("expected_network_response_code")
    expected_dec = scenario.get("expected_customer_decision")
    actual_rc = response_json.get("response_code")
    actual_dec = response_json.get("customer_decision")

    passed = (actual_rc == expected_rc) and (expected_dec is None or actual_dec == expected_dec)

    # Build per-hop audit trail for debugging.
    marqeta_event_type = response_json.get("marqeta_webhook_event_type")
    jit_method = response_json.get("jit_funding_method")
    customer_body = response_json.get("customer_response_body")

    audit_trail = [
        {
            "step": 1,
            "actor": "Cardholder Tap",
            "direction": "\u2192",
            "label": "Cardholder initiates transaction at merchant terminal",
            "payload": base_request,
            "timestamp": ts_cardholder,
        },
        {
            "step": 2,
            "actor": "Terminal",
            "direction": "\u2192",
            "label": "Terminal normalises request and stamps STAN / RRN",
            "payload": request_dict,
            "timestamp": ts_outbound,
        },
        {
            "step": 3,
            "actor": "Acquirer",
            "direction": "\u2192",
            "label": "Acquirer forwards ISO-8583 message to Visa network",
            "payload": request_dict,
            "timestamp": ts_outbound,
        },
        {
            "step": 4,
            "actor": "Visa Network",
            "direction": "\u2192",
            "label": "Visa network routes authorization request to Marqeta issuer processor",
            "payload": request_dict,
            "timestamp": ts_outbound,
        },
        {
            "step": 5,
            "actor": "Marqeta Issuer Processor",
            "direction": "\u2192",
            "label": f"JIT Funding webhook dispatched to customer endpoint"
                     f" ({marqeta_event_type} / {jit_method})",
            "payload": {
                "event_type": marqeta_event_type,
                "jit_funding_method": jit_method,
                "transaction_id": request_dict.get("transaction_id"),
                "amount": request_dict.get("amount"),
                "currency": request_dict.get("currency"),
                "merchant_name": request_dict.get("merchant_name"),
                "stan": request_dict.get("stan"),
                "rrn": request_dict.get("rrn"),
            },
            "timestamp": ts_outbound,
        },
        {
            "step": 6,
            "actor": "Customer JIT (System Under Test)",
            "direction": "\u2190",
            "label": f"Customer JIT decision: {actual_dec}",
            "payload": customer_body,
            "timestamp": ts_inbound,
        },
        {
            "step": 7,
            "actor": "Visa Network",
            "direction": "\u2190",
            "label": f"Visa returns network response code: {actual_rc}",
            "payload": {
                "response_code": response_json.get("response_code"),
                "auth_code": response_json.get("auth_code"),
                "customer_decision": actual_dec,
                "customer_status_code": response_json.get("customer_status_code"),
                "stan": response_json.get("stan"),
                "rrn": response_json.get("rrn"),
            },
            "timestamp": ts_inbound,
        },
        {
            "step": 8,
            "actor": "Acquirer",
            "direction": "\u2190",
            "label": "Acquirer relays authorization response to terminal",
            "payload": {
                "response_code": response_json.get("response_code"),
                "auth_code": response_json.get("auth_code"),
                "customer_decision": actual_dec,
                "network": response_json.get("network"),
            },
            "timestamp": ts_inbound,
        },
        {
            "step": 9,
            "actor": "Merchant Terminal",
            "direction": "\u2190",
            "label": "Final result displayed at merchant terminal",
            "payload": response_json,
            "timestamp": ts_inbound,
        },
    ]

    trace = {
        "scenario_id": scenario.get("id"),
        "scenario_name": scenario.get("name"),
        "event_type": event_type,
        "timestamp": ts_cardholder,
        "request_sent": request_dict,
        "response_received": response_json,
        "expected_network_response_code": expected_rc,
        "expected_customer_decision": expected_dec,
        "actual_network_response_code": actual_rc,
        "actual_customer_decision": actual_dec,
        "passed": passed,
        "duration_ms": duration_ms,
        "audit_trail": audit_trail,
    }

    HISTORY.append(trace)
    del HISTORY[:-100]  # keep last 100
    return trace


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}


@app.get("/scenarios")
async def list_scenarios():
    return [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "description": s.get("description"),
            "event_type": s.get("event_type", "authorization"),
        }
        for s in _read_scenarios()
    ]


@app.post("/execute/{scenario_id}")
async def execute(scenario_id: str, unique: bool = True):
    scenario = _find_scenario(scenario_id)
    if scenario is None:
        return {"error": f"scenario '{scenario_id}' not found"}
    return _execute_scenario_internal(scenario, unique=unique)


@app.get("/suites")
async def list_suites():
    """Return the suite catalogue with scenario counts."""
    return [
        {
            "key": k,
            "name": v["name"],
            "scenario_count": len(v["scenario_ids"]),
            "scenario_ids": v["scenario_ids"],
        }
        for k, v in SUITES.items()
    ]


@app.post("/execute_suite")
async def execute_suite(request: Request):
    """Run a named suite (or a custom list of scenario IDs) and return a suite result."""
    body = await request.json()
    suite_key = body.get("suite_name", "full_regression")
    scenario_ids = body.get("scenario_ids") or SUITES.get(suite_key, {}).get("scenario_ids", [])
    suite_display_name = SUITES.get(suite_key, {}).get("name", suite_key)

    # Optionally reset customer JIT state before suite run.
    if body.get("reset_before", True):
        try:
            requests.post(CUSTOMER_JIT_RESET_URL, timeout=5)
        except requests.RequestException:
            pass

    run_at = datetime.now(timezone.utc).isoformat()
    suite_start = time.perf_counter()
    results = []

    for sid in scenario_ids:
        scenario = _find_scenario(sid)
        if scenario is None:
            results.append({
                "scenario_id": sid,
                "name": sid,
                "passed": False,
                "error": "not found",
                "duration_ms": 0,
                "expected_network_response_code": None,
                "actual_network_response_code":   None,
                "expected_customer_decision":     None,
                "actual_customer_decision":       None,
                "audit_trail": [],
            })
            continue

        suite_flags = scenario.get("suite_flags", {})
        run_count    = suite_flags.get("run_count", 1)
        force_unique = suite_flags.get("force_unique", True)
        expect_second = suite_flags.get("expect_second_decision")

        per_run = []
        for run_num in range(run_count):
            is_unique = force_unique if run_num == 0 else False
            per_run.append(_execute_scenario_internal(scenario, unique=is_unique))

        # For duplicate scenarios: first run must pass AND second must match the
        # expected second-run decision (e.g. DUPLICATE).
        if run_count == 2 and expect_second:
            passed = (
                per_run[0].get("passed") and
                per_run[1].get("actual_customer_decision") == expect_second
            )
            primary = per_run[1]
        else:
            primary = per_run[-1]
            passed = primary.get("passed", False)

        results.append({
            "scenario_id": sid,
            "name": scenario.get("name"),
            "passed": passed,
            "expected_network_response_code": primary.get("expected_network_response_code"),
            "actual_network_response_code":   primary.get("actual_network_response_code"),
            "expected_customer_decision":     primary.get("expected_customer_decision"),
            "actual_customer_decision":       primary.get("actual_customer_decision"),
            "duration_ms": primary.get("duration_ms"),
            "audit_trail": primary.get("audit_trail", []),
        })

    suite_duration_ms = round((time.perf_counter() - suite_start) * 1000, 2)
    passed_count = sum(1 for r in results if r.get("passed"))

    suite_result = {
        "suite_name": suite_display_name,
        "run_at": run_at,
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "duration_ms": suite_duration_ms,
        "results": results,
    }

    HISTORY.append({
        "suite_run": True,
        "suite_name": suite_display_name,
        "passed": passed_count == len(results),
        "timestamp": run_at,
    })
    del HISTORY[:-100]
    return suite_result


@app.post("/chip/command")
async def chip_command(request: Request):
    """Dispatch an APDU command to the software chip card emulator."""
    body = await request.json()
    cmd = body.get("command", "").upper()

    dispatch = {
        "SELECT":      lambda: _chip_emulator.select_application(
                           aid=body.get("aid", "A0000000031010")),
        "GET_DATA":    lambda: _chip_emulator.get_data(
                           tag=body.get("tag", "5A")),
        "VERIFY":      lambda: _chip_emulator.verify_pin(
                           pin=body.get("pin", "")),
        "READ_RECORD": lambda: _chip_emulator.read_record(
                           int(body.get("sfi", 1)), int(body.get("record_num", 1))),
        "PUT_DATA":    lambda: _chip_emulator.put_data(
                           body.get("tag", ""), body.get("value", "")),
        "GENERATE_AC": lambda: _chip_emulator.generate_ac(
                           body.get("cdol_data", "")),
        "RESET_CARD":  lambda: (
                           _chip_emulator.reset_card() or
                           {"data": "", "sw": "9000", "sw1": "90", "sw2": "00",
                            "status": "CARD_RESET"}),
        "GET_STATE":   lambda: {
                           "data": "", "sw": "9000", "sw1": "90", "sw2": "00",
                           "status": "OK"},
    }

    if cmd in dispatch:
        resp = dispatch[cmd]()
    else:
        resp = {
            "data": "", "sw": "6D00", "sw1": "6D", "sw2": "00",
            "status": "INSTRUCTION_NOT_SUPPORTED",
        }

    resp["command"] = cmd
    resp["card_state"] = _chip_emulator.get_card_state()
    return resp


@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    scenario_id = body.get("scenario_id") or f"gen_{int(time.time())}"
    event_type = body.get("event_type", "authorization")
    amount = int(body.get("amount", 2500))

    scenario = {
        "id": scenario_id,
        "name": body.get("name", scenario_id),
        "description": body.get("description", f"Generated {event_type} for {amount} cents"),
        "event_type": event_type,
        "request": {
            "transaction_id": body.get("transaction_id", f"TXN_{scenario_id.upper()}"),
            "pan": body.get("pan", "4111111111111111"),
            "amount": amount,
            "currency": "840",
            "mcc": body.get("mcc", "5411"),
            "merchant_name": body.get("merchant_name", "Generated Merchant"),
            "merchant_city": body.get("merchant_city", "San Francisco"),
            "merchant_state": body.get("merchant_state", "CA"),
            "merchant_country": body.get("merchant_country", "USA"),
            "pos_entry_mode": body.get("pos_entry_mode", "051"),
            "terminal_id": body.get("terminal_id", "TERM9999"),
            "acquiring_institution_id": "123456",
            "forwarding_institution_id": "123456",
            "datetime": datetime.now(timezone.utc).isoformat(),
        },
        "expected_network_response_code": body.get("expected_response_code", "00"),
        "expected_customer_decision": body.get("expected_customer_decision", "APPROVED"),
    }
    if event_type != "authorization":
        scenario["original_transaction_id"] = body.get("original_transaction_id", "TXN_AUTH_001")

    _ensure_scenarios_dir()
    out_path = os.path.join(SCENARIOS_DIR, f"{scenario_id}.json")
    with open(out_path, "w") as fh:
        json.dump(scenario, fh, indent=2)
    return {"created": os.path.basename(out_path), "scenario": scenario}


@app.get("/history")
async def history():
    # Most recent first.
    return list(reversed(HISTORY[-100:]))


@app.post("/reset")
async def reset():
    """Proxy a reset to the customer JIT service so scenarios re-run cleanly."""
    try:
        r = requests.post(CUSTOMER_JIT_RESET_URL, timeout=5)
        return {"status": "ok", "customer_jit": r.json()}
    except (requests.RequestException, ValueError) as e:
        return {"status": "error", "detail": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
