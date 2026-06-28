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
from backend.bootstrap import bootstrap
from backend.mongo_repository import (
    get_scenarios,
    get_scenario_by_id,
    save_scenario
)

# Terminal lives in the same `backend` folder; support both run styles.
try:
    from backend.terminal import Terminal
except ImportError:  # pragma: no cover
    from terminal import Terminal

# ISO 8583 origination + JPF mapping (T3/T4 — Phase 1).
try:
    from backend.network.originator import build_0100
    from backend.mapping.engine import map_to_jpf
    _ISO_AVAILABLE = True
except Exception:  # pragma: no cover
    _ISO_AVAILABLE = False

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

# ── OTel instrumentation ─────────────────────────────────────────────────────
# Import activates SDK + monkey-patches OllamaClient and heal_service at
# process start. Wrapped in try/except so a missing package never prevents
# the backend from starting.
try:
    import backend.instrumentation as _instrumentation
    print("OTel instrumentation loaded")
except Exception as _otel_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "OTel instrumentation not loaded: %s", _otel_err
    )
    _instrumentation = None

ACQUIRER_URL = os.getenv("ACQUIRER_URL", "http://acquirer:8101/authorize")
CUSTOMER_JIT_RESET_URL = os.getenv("CUSTOMER_JIT_RESET_URL", "http://customer_jit:8001/reset")
CUSTOMER_JIT_URL = os.getenv("CUSTOMER_JIT_URL", "http://customer_jit:8001")
MARQETA_SIM_URL = os.getenv("MARQETA_SIM_URL", "http://marqeta_simulator:8103")
ACQUIRER_SVC_URL = os.getenv("ACQUIRER_SVC_URL", "http://acquirer:8101")
VISA_SVC_URL = os.getenv("VISA_SVC_URL", "http://visa:8102")
SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "scenarios")

app = FastAPI(title="Marqeta E2E Simulator Orchestrator")

# Register OTel session-tracing middleware immediately after app creation.
if _instrumentation is not None:
    try:
        _instrumentation.register_middleware(app)
        print("OTel SessionTracingMiddleware registered")
    except Exception as _mw_err:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "OTel middleware registration failed: %s", _mw_err
        )

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
    print("=== STARTUP HOOK CALLED ===")
    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    init_db()
    bootstrap()


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
    scenario = get_scenario_by_id(scenario_id)
    if scenario:
        return scenario
    for s in _read_scenarios():
        if (
            s.get("id") == scenario_id
            or s.get("_file", "").rstrip(".json") == scenario_id
        ):
            return s
    return None


# --------------------------------------------------------------------------- #
# Core execution helper (shared by /execute and /execute_suite)
# --------------------------------------------------------------------------- #
def _execute_scenario_internal(
    scenario: dict,
    unique: bool = True,
    network_override: str | None = None,
) -> dict:
    """Run a scenario dict end-to-end and return a trace dict."""
    event_type = scenario.get("event_type", "authorization")
    base_request = dict(scenario.get("request", {}))

    ts_cardholder = datetime.now(timezone.utc).isoformat()

    request_dict = Terminal.swipe(base_request, unique=unique)

    if event_type != "authorization":
        request_dict["event_type"] = event_type
        request_dict["original_transaction_id"] = scenario.get("original_transaction_id")
        if event_type == "advice":
            request_dict["advice_type"] = scenario.get("advice_type", "CLEARING")
    else:
        request_dict["event_type"] = "authorization"

    # ── ISO 8583 origination + JPF mapping (T3/T4) ──────────────────────────
    # Build the 0100 from the scenario request and map it to the canonical JPF.
    # This runs in-process; it does NOT affect the HTTP path to the acquirer.
    iso_message: dict = {}
    jpf: dict = {}
    iso_warnings: list = []
    if _ISO_AVAILABLE:
        try:
            # Resolve network from scenario or override; default → BIN routing
            _net_override = network_override or scenario.get("network")
            orig = build_0100(request_dict, network_override=_net_override)
            iso_message = {
                "network":       orig.network,
                "mti":           orig.mti,
                "stan":          orig.stan,
                "rrn":           orig.rrn,
                "fields":        orig.iso_fields,
                "packed_hex":    orig.packed_hex,
                "unpacked":      orig.unpacked_fields,
                "private_des":   orig.private_des,
            }
            map_result = map_to_jpf(
                orig.unpacked_fields,
                orig.network,
                icc_hex=request_dict.get("icc_data"),
            )
            jpf = map_result.jpf
            iso_warnings = map_result.warnings
        except Exception as exc:  # pragma: no cover
            iso_message = {"error": str(exc)}

    ts_outbound = datetime.now(timezone.utc).isoformat()

    start = time.perf_counter()
    try:
        resp = requests.post(ACQUIRER_URL, json=request_dict, timeout=15)
        response_json = resp.json()
    except (requests.RequestException, ValueError) as e:
        response_json = {"error": str(e)}
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    ts_inbound = datetime.now(timezone.utc).isoformat()

    expected_rc  = scenario.get("expected_network_response_code")
    expected_dec = scenario.get("expected_customer_decision")
    actual_rc    = response_json.get("response_code")
    actual_dec   = response_json.get("customer_decision")

    passed = (actual_rc == expected_rc) and (
        expected_dec is None or actual_dec == expected_dec
    )

    marqeta_event_type = response_json.get("marqeta_webhook_event_type")
    jit_method         = response_json.get("jit_funding_method")
    customer_body      = response_json.get("customer_response_body")

    audit_trail = [
        {"step": 1, "actor": "Cardholder Tap",    "direction": "→",
         "label": "Cardholder initiates transaction at merchant terminal",
         "payload": base_request, "timestamp": ts_cardholder},
        {"step": 2, "actor": "Terminal",           "direction": "→",
         "label": "Terminal normalises request and stamps STAN / RRN",
         "payload": request_dict, "timestamp": ts_outbound},
        {"step": 3, "actor": "Acquirer",           "direction": "→",
         "label": "Acquirer forwards ISO-8583 message to Visa network",
         "payload": request_dict, "timestamp": ts_outbound},
        {"step": 4, "actor": "Visa Network",       "direction": "→",
         "label": "Visa network routes authorization request to Marqeta issuer processor",
         "payload": request_dict, "timestamp": ts_outbound},
        {"step": 5, "actor": "Marqeta Issuer Processor", "direction": "→",
         "label": (f"JIT Funding webhook dispatched to customer endpoint"
                   f" ({marqeta_event_type} / {jit_method})"),
         "payload": {
             "event_type": marqeta_event_type, "jit_funding_method": jit_method,
             "transaction_id": request_dict.get("transaction_id"),
             "amount": request_dict.get("amount"), "currency": request_dict.get("currency"),
             "merchant_name": request_dict.get("merchant_name"),
             "stan": request_dict.get("stan"), "rrn": request_dict.get("rrn"),
         }, "timestamp": ts_outbound},
        {"step": 6, "actor": "Customer JIT (System Under Test)", "direction": "←",
         "label": f"Customer JIT decision: {actual_dec}",
         "payload": customer_body, "timestamp": ts_inbound},
        {"step": 7, "actor": "Visa Network",       "direction": "←",
         "label": f"Visa returns network response code: {actual_rc}",
         "payload": {
             "response_code": response_json.get("response_code"),
             "auth_code": response_json.get("auth_code"),
             "customer_decision": actual_dec,
             "customer_status_code": response_json.get("customer_status_code"),
             "stan": response_json.get("stan"), "rrn": response_json.get("rrn"),
         }, "timestamp": ts_inbound},
        {"step": 8, "actor": "Acquirer",           "direction": "←",
         "label": "Acquirer relays authorization response to terminal",
         "payload": {
             "response_code": response_json.get("response_code"),
             "auth_code": response_json.get("auth_code"),
             "customer_decision": actual_dec, "network": response_json.get("network"),
         }, "timestamp": ts_inbound},
        {"step": 9, "actor": "Merchant Terminal",  "direction": "←",
         "label": "Final result displayed at merchant terminal",
         "payload": response_json, "timestamp": ts_inbound},
    ]

    trace = {
        "scenario_id":                   scenario.get("id"),
        "scenario_name":                 scenario.get("name"),
        "event_type":                    event_type,
        "timestamp":                     ts_cardholder,
        "request_sent":                  request_dict,
        "response_received":             response_json,
        "expected_network_response_code": expected_rc,
<<<<<<< Updated upstream
        "expected_customer_decision": expected_dec,
        "actual_network_response_code": actual_rc,
        "actual_customer_decision": actual_dec,
        "passed": passed,
        "duration_ms": duration_ms,
        "audit_trail": audit_trail,
        # ── Phase 1 additions (T5) ───────────────────────────────────────────
        "iso_message":   iso_message,   # packed ISO 8583 artefacts
        "jpf":           jpf,           # canonical JSON Payment Format
        "iso_warnings":  iso_warnings,  # validation flags (e.g. EMV mismatches)
=======
        "expected_customer_decision":    expected_dec,
        "actual_network_response_code":  actual_rc,
        "actual_customer_decision":      actual_dec,
        "passed":                        passed,
        "duration_ms":                   duration_ms,
        "audit_trail":                   audit_trail,
>>>>>>> Stashed changes
    }

    try:
        persist_transaction(trace)
    except Exception:
        pass

    return trace


# --------------------------------------------------------------------------- #
# Health endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}


@app.get("/health/all")
async def health_all():
    services = {
        "orchestrator":      "http://localhost:8000/health",
        "acquirer":          f"{ACQUIRER_SVC_URL}/health",
        "visa":              f"{VISA_SVC_URL}/health",
        "marqeta_simulator": f"{MARQETA_SIM_URL}/health",
        "customer_jit":      f"{CUSTOMER_JIT_URL}/health",
    }
    results = {}
    for name, url in services.items():
        try:
            r = requests.get(url, timeout=3)
            results[name] = {
                "status": "ok" if r.status_code == 200 else "degraded",
                "http_status": r.status_code, "url": url,
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
    all_scenarios = get_scenarios()
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
            "id": s.get("id"), "name": s.get("name"),
            "description": s.get("description"),
            "event_type": s.get("event_type", "authorization"),
            "expected_network_response_code": s.get("expected_network_response_code"),
            "expected_customer_decision": s.get("expected_customer_decision"),
            "tags": s.get("tags", []),
        })
    total  = len(filtered)
    offset = (page - 1) * limit
    return {"items": filtered[offset: offset + limit], "total": total, "page": page, "limit": limit}


@app.post("/execute/{scenario_id}")
async def execute(
    scenario_id: str,
    unique: bool = True,
    network: str = Query(None, description="Force a specific network (visa|mastercard|amex|discover)"),
):
    scenario = _find_scenario(scenario_id)
    if scenario is None:
        return {"error": f"scenario '{scenario_id}' not found"}
    return _execute_scenario_internal(scenario, unique=unique, network_override=network)


# --------------------------------------------------------------------------- #
# Suite endpoints
# --------------------------------------------------------------------------- #
@app.get("/suites")
async def list_suites():
    return [
        {"key": k, "name": v["name"],
         "scenario_count": len(v["scenario_ids"]),
         "scenario_ids": v["scenario_ids"]}
        for k, v in SUITES.items()
    ]


@app.post("/execute_suite")
async def execute_suite(request: Request, format: str = Query("json")):
    body           = await request.json()
    suite_key      = body.get("suite_name", "full_regression")
    scenario_ids   = body.get("scenario_ids") or SUITES.get(suite_key, {}).get("scenario_ids", [])
    suite_display_name = SUITES.get(suite_key, {}).get("name", suite_key)

    if body.get("reset_before", True):
        try:
            requests.post(CUSTOMER_JIT_RESET_URL, timeout=5)
        except requests.RequestException:
            pass

    run_at      = datetime.now(timezone.utc).isoformat()
    suite_start = time.perf_counter()
    results     = []

    for sid in scenario_ids:
        scenario = _find_scenario(sid)
        if scenario is None:
            results.append({"scenario_id": sid, "name": sid, "passed": False,
                            "error": "not found", "duration_ms": 0,
                            "expected_network_response_code": None,
                            "actual_network_response_code": None,
                            "expected_customer_decision": None,
                            "actual_customer_decision": None, "audit_trail": []})
            continue

        suite_flags    = scenario.get("suite_flags", {})
        run_count      = suite_flags.get("run_count", 1)
        force_unique   = suite_flags.get("force_unique", True)
        expect_second  = suite_flags.get("expect_second_decision")

        per_run = []
        for run_num in range(run_count):
            is_unique = force_unique if run_num == 0 else False
            per_run.append(_execute_scenario_internal(scenario, unique=is_unique))

        if run_count == 2 and expect_second:
            passed  = (per_run[0].get("passed") and
                       per_run[1].get("actual_customer_decision") == expect_second)
            primary = per_run[1]
        else:
            primary = per_run[-1]
            passed  = primary.get("passed", False)

        results.append({
            "scenario_id": sid, "name": scenario.get("name"), "passed": passed,
            "expected_network_response_code": primary.get("expected_network_response_code"),
            "actual_network_response_code":   primary.get("actual_network_response_code"),
            "expected_customer_decision":     primary.get("expected_customer_decision"),
            "actual_customer_decision":       primary.get("actual_customer_decision"),
            "duration_ms": primary.get("duration_ms"),
            "audit_trail": primary.get("audit_trail", []),
        })

    suite_duration_ms = round((time.perf_counter() - suite_start) * 1000, 2)
    passed_count      = sum(1 for r in results if r.get("passed"))

    suite_result = {
        "suite_name": suite_display_name, "suite_key": suite_key,
        "run_at": run_at, "total": len(results),
        "passed": passed_count, "failed": len(results) - passed_count,
        "duration_ms": suite_duration_ms, "results": results,
    }

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
    return get_suite_runs_page(page=page, limit=limit)


def _suite_result_to_junit(suite_result: dict) -> Response:
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
        dur  = f'{(r.get("duration_ms") or 0) / 1000:.3f}'
        lines.append(f'  <testcase name="{name}" classname="e2ms.suite" time="{dur}">')
        if not r.get("passed"):
            exp_rc = _esc(str(r.get("expected_network_response_code") or ""))
            act_rc = _esc(str(r.get("actual_network_response_code") or ""))
            exp_d  = _esc(str(r.get("expected_customer_decision") or ""))
            act_d  = _esc(str(r.get("actual_customer_decision") or ""))
            msg    = f"RC expected={exp_rc} actual={act_rc}; decision expected={exp_d} actual={act_d}"
            lines.append(f'    <failure message="{msg}" type="AssertionError">{msg}</failure>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return Response(content="\n".join(lines), media_type="application/xml")


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
@app.get("/history")
async def history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    scenario_id: str = Query(None),
    event_type: str = Query(None),
    passed: str = Query(None),
):
    passed_bool = None
    if passed == "true":  passed_bool = True
    elif passed == "false": passed_bool = False
    return get_transactions_page(
        page=page, limit=limit, scenario_id=scenario_id,
        event_type=event_type, passed=passed_bool,
    )


# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #
@app.post("/generate")
async def generate(request: Request):
    body = await request.json()
    print("=== NEW MONGO GENERATE ENDPOINT ===")
    print(body)
    scenario_id = (body.get("scenario_id") or body.get("id") or f"gen_{int(time.time())}")
    event_type  = body.get("event_type", "authorization")
    amount      = int(body.get("amount", body.get("request", {}).get("amount", 2500)))

    if "request" in body:
        scenario = body
    else:
        scenario = {
            "id": scenario_id, "name": body.get("name", scenario_id),
            "description": body.get("description", f"Generated {event_type}"),
            "event_type": event_type,
            "request": {
                "transaction_id": body.get("transaction_id", f"TXN_{scenario_id.upper()}"),
                "pan": body.get("pan", "4111111111111111"),
                "amount": amount, "currency": body.get("currency", "840"),
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

    save_scenario(scenario)
    print(f"SAVED SCENARIO: {scenario['id']}")
    return {"created": scenario["id"], "scenario": scenario}


# --------------------------------------------------------------------------- #
# Webhook replay
# --------------------------------------------------------------------------- #
@app.post("/replay_webhook")
async def replay_webhook(request: Request):
    body    = await request.json()
    jit_url = body.get("jit_url") or f"{CUSTOMER_JIT_URL}/jit/authorize"
    payload = body.get("payload", {})
    timeout = int(body.get("timeout", 10))
    try:
        r = requests.post(jit_url, json=payload, timeout=timeout)
        try:    resp_body = r.json()
        except ValueError: resp_body = {"raw": r.text}
        return {"status_code": r.status_code, "response": resp_body, "url": jit_url}
    except requests.RequestException as e:
        return {"error": str(e), "url": jit_url}


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #
@app.post("/reset")
async def reset():
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
    return list_environments()


@app.post("/environments")
async def create_env(request: Request):
    body    = await request.json()
    name    = body.get("name")
    api_url = body.get("api_url")
    if not name or not api_url:
        return {"error": "name and api_url are required"}
    env_id = create_environment(
        name=name, api_url=api_url,
        customer_jit_url=body.get("customer_jit_url"),
        notes=body.get("notes"),
    )
    return {"id": env_id, "created": True}


@app.put("/environments/{env_id}/activate")
async def activate_env(env_id: int):
    ok = activate_environment(env_id)
    if not ok:
        return {"error": f"environment {env_id} not found"}
    return {"activated": True, "environment": get_active_environment()}


@app.get("/environments/active")
async def get_active_env():
    env = get_active_environment()
    if env is None:
        return {"error": "no active environment"}
    return env


# --------------------------------------------------------------------------- #
# Chip/NFC card emulator
# --------------------------------------------------------------------------- #
@app.post("/chip/command")
async def chip_command(request: Request):
    body = await request.json()
    cmd  = body.get("command", "").upper()
    dispatch = {
        "SELECT":      lambda: _chip_emulator.select_application(aid=body.get("aid", "A0000000031010")),
        "GET_DATA":    lambda: _chip_emulator.get_data(tag=body.get("tag", "5A")),
        "VERIFY":      lambda: _chip_emulator.verify_pin(pin=body.get("pin", "")),
        "READ_RECORD": lambda: _chip_emulator.read_record(int(body.get("sfi", 1)), int(body.get("record_num", 1))),
        "PUT_DATA":    lambda: _chip_emulator.put_data(body.get("tag", ""), body.get("value", "")),
        "GENERATE_AC": lambda: _chip_emulator.generate_ac(body.get("cdol_data", "")),
        "RESET_CARD":  lambda: (_chip_emulator.reset_card() or
                                {"data": "", "sw": "9000", "sw1": "90", "sw2": "00", "status": "CARD_RESET"}),
        "GET_STATE":   lambda: {"data": "", "sw": "9000", "sw1": "90", "sw2": "00", "status": "OK"},
    }
    resp = dispatch[cmd]() if cmd in dispatch else {
        "data": "", "sw": "6D00", "sw1": "6D", "sw2": "00", "status": "INSTRUCTION_NOT_SUPPORTED"
    }
    resp["command"]    = cmd
    resp["card_state"] = _chip_emulator.get_card_state()
    return resp


# --------------------------------------------------------------------------- #
# Analytics endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/rc_coverage")
async def analytics_rc_coverage():
    return get_rc_coverage()


@app.get("/analytics/latency")
async def analytics_latency(limit: int = Query(50, ge=1, le=200)):
    return get_latency_stats(limit=limit)


@app.get("/analytics/trends")
async def analytics_trends(days: int = Query(7, ge=1, le=90)):
    return get_daily_trends(days=days)


@app.get("/analytics/summary")
async def analytics_summary():
    rc_data  = get_rc_coverage()
    latency  = get_latency_stats(limit=200)
    total    = sum(r["total"] for r in rc_data)
    passed   = sum(r["passed"] for r in rc_data)
    avg_lat  = round(sum(r["duration_ms"] for r in latency) / len(latency), 2) if latency else 0
    return {
        "total_transactions": total, "total_passed": passed,
        "total_failed": total - passed,
        "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
        "avg_latency_ms": avg_lat, "rc_codes_covered": len(rc_data),
    }


# --------------------------------------------------------------------------- #
# Certification endpoint (T0.1)
# --------------------------------------------------------------------------- #

#: Scenarios every SUT must handle to earn certification.
_CERTIFICATION_SCENARIOS = [
    # RC matrix
    "rc_51_insufficient_funds",
    "rc_54_expired_card",
    "rc_57_txn_not_permitted",
    "rc_61_exceeds_limit",
    "rc_62_restricted_card",
    "rc_65_velocity_exceeded",
    "rc_75_pin_retries",
    "rc_91_issuer_unavailable",
    "rc_96_system_error",
    # Lifecycle happy paths
    "authorization_approve",
    "authorization_decline",
    "advice_clearing",
    "refund",
    "suite_reversal",
]

_DEFAULT_CERT_THRESHOLD = 95   # pass-rate % required for certified=true


@app.post("/certify")
async def certify(request: Request):
    """Run the fixed certification suite against the active environment.

    Body (all optional):
        threshold  int   — pass-rate % for certified=true  (default 95)
        reset      bool  — reset JIT state before run      (default true)

    Returns:
        sut, timestamp, results, coverage{lifecycle_events_covered,
        rc_codes_covered, score}, certified, threshold
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    threshold = int(body.get("threshold", _DEFAULT_CERT_THRESHOLD))
    reset_jit = body.get("reset", True)

    sut = get_active_environment()

    if reset_jit:
        try:
            requests.post(CUSTOMER_JIT_RESET_URL, timeout=5)
        except requests.RequestException:
            pass

    run_at = datetime.now(timezone.utc).isoformat()
    results = []
    lifecycle_covered: set = set()
    rc_covered: set = set()

    for scenario_id in _CERTIFICATION_SCENARIOS:
        scenario = _find_scenario(scenario_id)
        if scenario is None:
            results.append({
                "scenario_id":       scenario_id,
                "name":              scenario_id,
                "event_type":        "unknown",
                "expected_rc":       None,
                "actual_rc":         None,
                "expected_decision": None,
                "actual_decision":   None,
                "passed":            False,
                "duration_ms":       0,
                "error":             "scenario not found",
            })
            continue

        trace = _execute_scenario_internal(scenario, unique=True)
        evt    = scenario.get("event_type", "authorization")
        act_rc = trace.get("actual_network_response_code")

        if trace.get("passed"):
            lifecycle_covered.add(evt)
            if act_rc:
                rc_covered.add(act_rc)

        results.append({
            "scenario_id":       scenario_id,
            "name":              scenario.get("name", scenario_id),
            "event_type":        evt,
            "expected_rc":       trace.get("expected_network_response_code"),
            "actual_rc":         act_rc,
            "expected_decision": trace.get("expected_customer_decision"),
            "actual_decision":   trace.get("actual_customer_decision"),
            "passed":            trace.get("passed", False),
            "duration_ms":       trace.get("duration_ms", 0),
            "audit_trail":       trace.get("audit_trail", []),
            "iso_message":       trace.get("iso_message", {}),
            "jpf":               trace.get("jpf", {}),
        })

    total        = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    score        = round(passed_count / total * 100, 1) if total else 0.0
    certified    = score >= threshold

    return {
        "sut":       sut,
        "timestamp": run_at,
        "results":   results,
        "coverage": {
            "lifecycle_events_covered": sorted(lifecycle_covered),
            "rc_codes_covered":         sorted(rc_covered),
            "total_scenarios":          total,
            "passed_scenarios":         passed_count,
            "score":                    score,
        },
        "certified": certified,
        "threshold": threshold,
    }


# --------------------------------------------------------------------------- #
# Audit export endpoint (T2.3)
# --------------------------------------------------------------------------- #

@app.get("/history/export")
async def history_export(
    format: str = Query("json", description="json or csv"),
    limit: int = Query(500, ge=1, le=5000),
):
    """Export transaction history as JSON or CSV for audit purposes."""
    import io
    from fastapi.responses import StreamingResponse

    page_result = get_transactions_page(page=1, limit=limit)
    items = page_result.get("items", [])

    if format == "csv":
        import csv
        output = io.StringIO()
        fieldnames = [
            "id", "scenario_id", "scenario_name", "event_type", "timestamp",
            "passed", "expected_rc", "actual_rc", "expected_decision",
            "actual_decision", "duration_ms",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in items:
            writer.writerow(row)
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
        )

    payload = json.dumps({
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }, indent=2, default=str)
    return StreamingResponse(
        io.BytesIO(payload.encode()),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit_export.json"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)