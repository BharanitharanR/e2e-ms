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

ACQUIRER_URL = os.getenv("ACQUIRER_URL", "http://acquirer:8101/authorize")
CUSTOMER_JIT_RESET_URL = os.getenv("CUSTOMER_JIT_RESET_URL", "http://customer_jit:8001/reset")
SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "scenarios")

app = FastAPI(title="Marqeta E2E Simulator Orchestrator")

# Rolling in-memory execution history (most recent last).
HISTORY = []


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

    event_type = scenario.get("event_type", "authorization")
    base_request = dict(scenario.get("request", {}))

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

    start = time.perf_counter()
    try:
        resp = requests.post(ACQUIRER_URL, json=request_dict, timeout=15)
        response_json = resp.json()
    except (requests.RequestException, ValueError) as e:
        response_json = {"error": str(e)}
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    expected_rc = scenario.get("expected_network_response_code")
    expected_dec = scenario.get("expected_customer_decision")
    actual_rc = response_json.get("response_code")
    actual_dec = response_json.get("customer_decision")

    passed = (actual_rc == expected_rc) and (expected_dec is None or actual_dec == expected_dec)

    trace = {
        "scenario_id": scenario.get("id"),
        "scenario_name": scenario.get("name"),
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_sent": request_dict,
        "response_received": response_json,
        "expected_network_response_code": expected_rc,
        "expected_customer_decision": expected_dec,
        "actual_network_response_code": actual_rc,
        "actual_customer_decision": actual_dec,
        "passed": passed,
        "duration_ms": duration_ms,
    }

    HISTORY.append(trace)
    del HISTORY[:-100]  # keep last 100
    return trace


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
