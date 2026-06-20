# e2e-marqeta-simulator/backend/main.py
"""Orchestrator + REST API for the simulator (port 8000).

Owns the scenario catalogue, drives the Terminal -> Acquirer chain, scores the
result against the scenario's expectations, and persists traces to SQLite.
"""
import os
import json
import glob
import time
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request, Query
from fastapi.responses import Response
import uvicorn

# Terminal lives in the same `backend` folder; support both run styles.
try:
    from backend.terminal import Terminal
except ImportError:  # pragma: no cover
    from terminal import Terminal

# Suite catalogue.
try:
    from backend.suites import SUITES
except ImportError:
    from suites import SUITES

# Chip/NFC card emulator.
try:
    from backend.chip_terminal import SoftwareCardEmulator
except ImportError:
    from chip_terminal import SoftwareCardEmulator

# SQLite persistence layer.
try:
    from backend.db import (
        init_db, persist_transaction, persist_suite_run,
        get_transactions_page, get_recent_transactions,
        get_suite_runs_page, get_rc_coverage, get_latency_stats,
        get_daily_trends, list_environments, create_environment,
        activate_environment, get_active_environment,
    )
except ImportError:
    from db import (
        init_db, persist_transaction, persist_suite_run,
        get_transactions_page, get_recent_transactions,
        get_suite_runs_page, get_rc_coverage, get_latency_stats,
        get_daily_trends, list_environments, create_environment,
        activate_environment, get_active_environment,
    )

# AI routes (optional — gracefully degrade if Anthropic SDK not installed).
try:
    try:
        from backend.ai_routes import ai_router
    except ImportError:
        from ai_routes import ai_router
    _AI_AVAILABLE = True
except Exception:  # pragma: no cover
    ai_router = None
    _AI_AVAILABLE = False

ACQUIRER_URL = os.getenv("ACQUIRER_URL", "http://acquirer:8101/authorize")
CUSTOMER_JIT_RESET_URL = os.getenv("CUSTOMER_JIT_RESET_URL", "http://customer_jit:8001/reset")
CUSTOMER_JIT_URL = os.getenv("CUSTOMER_JIT_URL", "http://customer_jit:8001")
MARQETA_SIM_URL = os.getenv("MARQETA_SIM_URL", "http://marqeta_simulator:8103")
ACQUIRER_SVC_URL = os.getenv("ACQUIRER_SVC_URL", "http://acquirer:8101")
VISA_SVC_URL = os.getenv("VISA_SVC_URL", "http://visa:8102")
SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "scenarios")

app = FastAPI(title="Marqeta E2E Simulator Orchestrator")

# Attach AI routes if available.
if ai_router is not None:
    app.include_router(ai_router)

# Module-level chip card emulator singleton.
_chip_emulator = SoftwareCardEmulator()


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
@app.on_event("startup")
def _startup():
    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    init_db()


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
            "direction": "→",
            "label": "Cardholder initiates transaction at merchant terminal",
            "payload": base_request,
            "timestamp": ts_cardholder,
        },
        {
            "step": 2,
            "actor": "Terminal",
            "direction": "→",
            "label": "Terminal normalises request and stamps STAN / RRN",
            "payload": request_dict,
            "timestamp": ts_outbound,
        },
        {
            "step": 3,
            "actor": "Acquirer",
            "direction": "→",
            "label": "Acquirer forwards ISO-8583 message to Visa network",
            "payload": request_dict,
            "timestamp": ts_outbound,
        },
        {
            "step": 4,
            "actor": "Visa Network",
            "direction": "→",
            "label": "Visa network routes authorization request to Marqeta issuer processor",
            "payload": request_dict,
            "timestamp": ts_outbound,
        },
        {
            "step": 5,
            "actor": "Marqeta Issuer Processor",
            "direction": "→",
            "label": (
                f"JIT Funding webhook dispatched to customer endpoint"
                f" ({marqeta_event_type} / {jit_method})"
            ),
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
            "direction": "←",
            "label": f"Customer JIT decision: {actual_dec}",
            "payload": customer_body,
            "timestamp": ts_inbound,
        },
        {
            "step": 7,
            "actor": "Visa Network",
            "direction": "←",
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
            "direction": "←",
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
            "direction": "←",
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

    # Persist to SQLite.
    try:
        persist_transaction(trace)
    except Exception:
        pass  # never block execution on DB failure

    return trace


# --------------------------------------------------------------------------- #
# Health endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}


@app.get("/health/all")
async def health_all():
    """Check health of every service in the stack and return aggregated status."""
    services = {
        "orchestrator":        f"http://localhost:8000/health",
        "acquirer":            f"{ACQUIRER_SVC_URL}/health",
        "visa":                f"{VISA_SVC_URL}/health",
        "marqeta_simulator":   f"{MARQETA_SIM_URL}/health",
        "customer_jit":        f"{CUSTOMER_JIT_URL}/health",
    }
    results = {}
    for name, url in services.items():
        try:
            r = requests.get(url, timeout=3)
            results[name] = {
                "status": "ok" if r.status_code == 200 else "degraded",
                "http_status": r.status_code,
                "url": url,
            }
        except requests.RequestException as e:
            results[name] = {"status": "unreachable", "error": str(e), "url": url}

    overall = "ok" if all(v["status"] == "ok" for v in results.values()) else "degraded"
    return {"overall": overall, "services": results}


# --------------------------------------------------------------------------- #
# Scenario endpoints
# --------------------------------------------------------------------------- #
@app.get("/scenarios")
async def list_scenarios(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    event_type: str = Query(None),
    search: str = Query(None),
):
    """Return paginated scenario list with optional filtering."""
    all_scenarios = _read_scenarios()

    # Filter
    filtered = []
    for s in all_scenarios:
        if event_type and s.get("event_type", "authorization") != event_type:
            continue
        if search:
            needle = search.lower()
            haystack = f"{s.get('id','')} {s.get('name','')} {s.get('description','')}".lower()
            if needle not in haystack:
                continue
        filtered.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "description": s.get("description"),
            "event_type": s.get("event_type", "authorization"),
            "expected_network_response_code": s.get("expected_network_response_code"),
            "expected_customer_decision": s.get("expected_customer_decision"),
            "tags": s.get("tags", []),
        })

    total = len(filtered)
    offset = (page - 1) * limit
    items = filtered[offset: offset + limit]
    return {"items": items, "total": total, "page": page, "limit": limit}


@app.post("/execute/{scenario_id}")
async def execute(scenario_id: str, unique: bool = True):
    scenario = _find_scenario(scenario_id)
    if scenario is None:
        return {"error": f"scenario '{scenario_id}' not found"}
    return _execute_scenario_internal(scenario, unique=unique)


# --------------------------------------------------------------------------- #
# Suite endpoints
# --------------------------------------------------------------------------- #
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
async def execute_suite(
    request: Request,
    format: str = Query("json"),
):
    """Run a named suite (or custom scenario list).

    format=json  → JSON suite result (default)
    format=junit → JUnit XML for CI pipelines
    """
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
                "actual_network_response_code": None,
                "expected_customer_decision": None,
                "actual_customer_decision": None,
                "audit_trail": [],
            })
            continue

        suite_flags = scenario.get("suite_flags", {})
        run_count = suite_flags.get("run_count", 1)
        force_unique = suite_flags.get("force_unique", True)
        expect_second = suite_flags.get("expect_second_decision")

        per_run = []
        for run_num in range(run_count):
            is_unique = force_unique if run_num == 0 else False
            per_run.append(_execute_scenario_internal(scenario, unique=is_unique))

        # For duplicate scenarios: first run must pass AND second must match
        # the expected second-run decision (e.g. DUPLICATE).
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
            "actual_network_response_code": primary.get("actual_network_response_code"),
            "expected_customer_decision": primary.get("expected_customer_decision"),
            "actual_customer_decision": primary.get("actual_customer_decision"),
            "duration_ms": primary.get("duration_ms"),
            "audit_trail": primary.get("audit_trail", []),
        })

    suite_duration_ms = round((time.perf_counter() - suite_start) * 1000, 2)
    passed_count = sum(1 for r in results if r.get("passed"))

    suite_result = {
        "suite_name": suite_display_name,
        "suite_key": suite_key,
        "run_at": run_at,
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "duration_ms": suite_duration_ms,
        "results": results,
    }

    # Persist suite run to SQLite.
    try:
        persist_suite_run(suite_key, suite_result)
    except Exception:
        pass

    if format == "junit":
        return _suite_result_to_junit(suite_result)

    return suite_result


@app.get("/suite_runs")
async def list_suite_runs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    """Return paginated suite run history from SQLite."""
    return get_suite_runs_page(page=page, limit=limit)


def _suite_result_to_junit(suite_result: dict) -> Response:
    """Convert a suite result dict to JUnit XML and return as HTTP response."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<testsuite name="{_esc(suite_result["suite_name"])}"'
        f' tests="{suite_result["total"]}"'
        f' failures="{suite_result["failed"]}"'
        f' time="{suite_result["duration_ms"] / 1000:.3f}"'
        f' timestamp="{_esc(suite_result["run_at"])}">',
    ]
    for r in suite_result.get("results", []):
        name = _esc(r.get("name") or r.get("scenario_id", "unknown"))
        dur = f'{(r.get("duration_ms") or 0) / 1000:.3f}'
        lines.append(f'  <testcase name="{name}" classname="e2ms.suite" time="{dur}">')
        if not r.get("passed"):
            exp_rc = _esc(str(r.get("expected_network_response_code") or ""))
            act_rc = _esc(str(r.get("actual_network_response_code") or ""))
            exp_d = _esc(str(r.get("expected_customer_decision") or ""))
            act_d = _esc(str(r.get("actual_customer_decision") or ""))
            msg = f"RC expected={exp_rc} actual={act_rc}; decision expected={exp_d} actual={act_d}"
            lines.append(f'    <failure message="{msg}" type="AssertionError">{msg}</failure>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    xml_str = "\n".join(lines)
    return Response(content=xml_str, media_type="application/xml")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# --------------------------------------------------------------------------- #
# History endpoint (paginated)
# --------------------------------------------------------------------------- #
@app.get("/history")
async def history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    scenario_id: str = Query(None),
    event_type: str = Query(None),
    passed: str = Query(None),   # "true" | "false" | None
):
    """Return paginated transaction history from SQLite."""
    passed_bool = None
    if passed == "true":
        passed_bool = True
    elif passed == "false":
        passed_bool = False

    return get_transactions_page(
        page=page,
        limit=limit,
        scenario_id=scenario_id,
        event_type=event_type,
        passed=passed_bool,
    )


# --------------------------------------------------------------------------- #
# Generate (ad-hoc scenario builder)
# --------------------------------------------------------------------------- #
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
            "currency": body.get("currency", "840"),
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
        "tags": body.get("tags", []),
    }
    if event_type != "authorization":
        scenario["original_transaction_id"] = body.get(
            "original_transaction_id", "TXN_AUTH_001"
        )

    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    out_path = os.path.join(SCENARIOS_DIR, f"{scenario_id}.json")
    with open(out_path, "w") as fh:
        json.dump(scenario, fh, indent=2)
    return {"created": os.path.basename(out_path), "scenario": scenario}


# --------------------------------------------------------------------------- #
# Webhook replay
# --------------------------------------------------------------------------- #
@app.post("/replay_webhook")
async def replay_webhook(request: Request):
    """Re-POST a raw Marqeta JIT webhook payload directly to the customer JIT
    endpoint and return the raw response — useful for debugging specific payloads."""
    body = await request.json()
    jit_url = body.get("jit_url") or f"{CUSTOMER_JIT_URL}/jit/authorize"
    payload = body.get("payload", {})
    timeout = int(body.get("timeout", 10))

    try:
        r = requests.post(jit_url, json=payload, timeout=timeout)
        try:
            resp_body = r.json()
        except ValueError:
            resp_body = {"raw": r.text}
        return {
            "status_code": r.status_code,
            "response": resp_body,
            "url": jit_url,
        }
    except requests.RequestException as e:
        return {"error": str(e), "url": jit_url}


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #
@app.post("/reset")
async def reset():
    """Proxy a reset to the customer JIT service so scenarios re-run cleanly."""
    try:
        r = requests.post(CUSTOMER_JIT_RESET_URL, timeout=5)
        return {"status": "ok", "customer_jit": r.json()}
    except (requests.RequestException, ValueError) as e:
        return {"status": "error", "detail": str(e)}


# --------------------------------------------------------------------------- #
# Environment management
# --------------------------------------------------------------------------- #
@app.get("/environments")
async def get_environments():
    """List all configured environments."""
    return list_environments()


@app.post("/environments")
async def create_env(request: Request):
    """Create a new environment entry."""
    body = await request.json()
    name = body.get("name")
    api_url = body.get("api_url")
    if not name or not api_url:
        return {"error": "name and api_url are required"}
    env_id = create_environment(
        name=name,
        api_url=api_url,
        customer_jit_url=body.get("customer_jit_url"),
        notes=body.get("notes"),
    )
    return {"id": env_id, "created": True}


@app.put("/environments/{env_id}/activate")
async def activate_env(env_id: int):
    """Set an environment as active (deactivates all others)."""
    ok = activate_environment(env_id)
    if not ok:
        return {"error": f"environment {env_id} not found"}
    env = get_active_environment()
    return {"activated": True, "environment": env}


@app.get("/environments/active")
async def get_active_env():
    """Return the currently active environment."""
    env = get_active_environment()
    if env is None:
        return {"error": "no active environment"}
    return env


# --------------------------------------------------------------------------- #
# Chip/NFC card emulator
# --------------------------------------------------------------------------- #
@app.post("/chip/command")
async def chip_command(request: Request):
    """Dispatch an APDU command to the software chip card emulator."""
    body = await request.json()
    cmd = body.get("command", "").upper()

    dispatch = {
        "SELECT": lambda: _chip_emulator.select_application(
            aid=body.get("aid", "A0000000031010")
        ),
        "GET_DATA": lambda: _chip_emulator.get_data(
            tag=body.get("tag", "5A")
        ),
        "VERIFY": lambda: _chip_emulator.verify_pin(
            pin=body.get("pin", "")
        ),
        "READ_RECORD": lambda: _chip_emulator.read_record(
            int(body.get("sfi", 1)), int(body.get("record_num", 1))
        ),
        "PUT_DATA": lambda: _chip_emulator.put_data(
            body.get("tag", ""), body.get("value", "")
        ),
        "GENERATE_AC": lambda: _chip_emulator.generate_ac(
            body.get("cdol_data", "")
        ),
        "RESET_CARD": lambda: (
            _chip_emulator.reset_card() or
            {"data": "", "sw": "9000", "sw1": "90", "sw2": "00", "status": "CARD_RESET"}
        ),
        "GET_STATE": lambda: {
            "data": "", "sw": "9000", "sw1": "90", "sw2": "00", "status": "OK"
        },
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


# --------------------------------------------------------------------------- #
# Analytics endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/rc_coverage")
async def analytics_rc_coverage():
    """Return per-response-code pass/fail counts."""
    return get_rc_coverage()


@app.get("/analytics/latency")
async def analytics_latency(limit: int = Query(50, ge=1, le=200)):
    """Return recent transaction latencies for charting."""
    return get_latency_stats(limit=limit)


@app.get("/analytics/trends")
async def analytics_trends(days: int = Query(7, ge=1, le=90)):
    """Return daily pass/fail counts for the trend chart."""
    return get_daily_trends(days=days)


@app.get("/analytics/summary")
async def analytics_summary():
    """Return a high-level summary of all-time test activity."""
    rc_data = get_rc_coverage()
    latency = get_latency_stats(limit=200)
    total_txns = sum(r["total"] for r in rc_data)
    total_passed = sum(r["passed"] for r in rc_data)
    avg_latency = (
        round(sum(r["duration_ms"] for r in latency) / len(latency), 2)
        if latency else 0
    )
    return {
        "total_transactions": total_txns,
        "total_passed": total_passed,
        "total_failed": total_txns - total_passed,
        "pass_rate_pct": round(total_passed / total_txns * 100, 1) if total_txns else 0,
        "avg_latency_ms": avg_latency,
        "rc_codes_covered": len(rc_data),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
