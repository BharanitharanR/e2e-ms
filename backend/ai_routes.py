# e2e-marqeta-simulator/backend/ai_routes.py
"""AI-powered endpoints using the Anthropic Claude API.

Registered on the FastAPI app under the /ai prefix.
Requires ANTHROPIC_API_KEY environment variable.
Model is configurable via ANTHROPIC_MODEL env var (T0.6).

Provider contract (T0.4): every generation path returns a BARE scenario dict:
    {"id": "...", "name": "...", "event_type": "...", "request": {...},
     "expected_network_response_code": "...", "expected_customer_decision": "..."}

The "wrapper" shape returned by the Claude system prompt
    {"scenario": {...}, "explanation": ..., "suggested_rc": ..., "jit_behavior": ...}
is unpacked by _claude_scenario_fn before being returned.  Endpoints that need
the metadata attach it under a "meta" key that does NOT conflict with the bare
scenario fields.
"""
import os
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.ai_provider import generate_with_fallback

logger = logging.getLogger(__name__)

ai_router = APIRouter(prefix="/ai", tags=["AI Copilot"])
logger.info("AI Copilot router created — /ai/* endpoints registered")

# ── Model configuration (T0.6) ────────────────────────────────────────────────
_DEFAULT_MODEL = "claude-opus-4-5"
_CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)

# ── System prompts ────────────────────────────────────────────────────────────

_SCENARIO_SYSTEM = """\
You are an expert in Marqeta JIT (Just-In-Time) funding, ISO 8583 payment
messaging, EMV chip card protocols, and payment-network authorization flows.

Your task is to generate realistic, precise test scenarios for a Marqeta
transaction simulator. Each scenario JSON tests how a customer's JIT Funding
service handles specific authorization conditions.

The simulator scenario schema is:
{
  "id": "string (snake_case)",
  "name": "Human-readable name",
  "description": "What this scenario tests",
  "event_type": "authorization | advice | refund | reversal",
  "request": {
    "transaction_id": "TXN_XXXX",
    "pan": "4111111111111111",
    "amount": <integer cents>,
    "currency": "<ISO 4217 numeric: 840=USD, 978=EUR, 826=GBP>",
    "mcc": "<4-digit MCC>",
    "merchant_name": "string (max 25 chars)",
    "merchant_city": "string",
    "merchant_state": "string",
    "merchant_country": "USA | GBR | DEU | FRA | ...",
    "pos_entry_mode": "051=chip+PIN | 071=contactless | 011=mag-stripe | 010=manual",
    "terminal_id": "TERM0001",
    "acquiring_institution_id": "123456",
    "forwarding_institution_id": "123456"
  },
  "expected_network_response_code": "<ISO 8583 RC>",
  "expected_customer_decision": "APPROVED | DECLINED"
}

ISO 8583 RC reference:
00=Approved, 05=Do Not Honor, 51=Insufficient Funds, 54=Expired Card,
57=Transaction Not Permitted, 61=Exceeds Withdrawal Limit,
62=Restricted Card, 65=Exceeds Velocity Limit, 75=PIN Retries Exceeded,
91=Issuer Unavailable, 96=System Malfunction.

Common MCCs: 5411=Grocery, 5541=Gas Station, 5311=Department Store,
5812=Restaurant, 6011=ATM, 5999=Misc Retail, 7011=Hotel, 4111=Transit,
7523=Parking, 4829=Wire Transfer, 6012=Financial Institution.

Respond ONLY with a valid JSON object — no markdown fences, no commentary:
{
  "scenario": { <complete scenario object> },
  "explanation": "<1-2 sentences: what this tests and why>",
  "suggested_rc": "<most appropriate ISO 8583 RC>",
  "jit_behavior": "<what the customer JIT must do to trigger this outcome>"
}
"""

_ANOMALY_SYSTEM = """\
You are a senior payment-systems engineer specialising in Marqeta JIT Funding,
ISO 8583 authorization flows, and card-transaction debugging.

Given a failed end-to-end test audit trail and expected vs actual metadata,
diagnose the root cause and suggest a concrete fix.

Respond ONLY with a valid JSON object — no markdown fences, no commentary:
{
  "root_cause": "<clear 1-2 sentence explanation>",
  "likely_rule_triggered": "<specific rule or code path>",
  "suggested_fix": "<concrete, actionable fix>",
  "confidence": "high | medium | low",
  "relevant_step": <audit step number 1-9 where failure occurred>
}
"""

_SUITE_INSIGHTS_SYSTEM = """\
You are a QA lead specialising in card-payment test coverage and Marqeta JIT
Funding integrations.

Given a suite run result with multiple test failures, provide a high-level
summary suitable for an engineering manager. Identify patterns, systemic issues,
and prioritised remediation steps.

Respond ONLY with a valid JSON object — no markdown fences, no commentary:
{
  "summary": "<2-3 sentence executive summary>",
  "root_causes": ["<cause 1>", "<cause 2>", ...],
  "highest_risk_failure": "<name of most critical failing scenario>",
  "recommended_actions": ["<action 1>", "<action 2>", ...],
  "coverage_gaps": ["<gap 1>", "<gap 2>", ...]
}
"""

_COVERAGE_ADVISOR_SYSTEM = """\
You are a payment-testing expert with deep knowledge of ISO 8583, EMV, Visa/MC
network rules, PCI DSS, and Marqeta JIT Funding programmes.

Given a list of currently-covered response codes and test scenarios, identify
what is MISSING and why it matters from a risk / compliance perspective.

Respond ONLY with a valid JSON object — no markdown fences, no commentary:
{
  "covered_rcs": ["00", "05", ...],
  "missing_rcs": [
    {
      "rc": "54",
      "name": "Expired Card",
      "risk": "high | medium | low",
      "why_matters": "<brief explanation>",
      "how_to_test": "<what scenario to create>"
    },
    ...
  ],
  "missing_flows": ["<flow name>", ...],
  "overall_coverage_score": <0-100 integer>
}
"""

# ── Core Claude helper ────────────────────────────────────────────────────────

def _call_claude(system: str, user_msg: str, max_tokens: int = 1200) -> dict:
    """Call Claude and return parsed JSON response.

    Reads ANTHROPIC_MODEL from env (T0.6); falls back to _DEFAULT_MODEL.
    Strips markdown fences if Claude wraps the JSON despite instructions.
    """
    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic package not installed. Run: pip install anthropic"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY environment variable not set"}

    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)

    client = anthropic.Anthropic(api_key=api_key)
    raw = ""
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = msg.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Claude returned non-JSON: %s", e)
        return {"error": "AI response was not valid JSON", "raw": raw[:500]}
    except Exception as e:  # noqa: BLE001
        logger.error("Claude API error: %s", e)
        return {"error": str(e)}


# ── Provider functions (T0.4 — normalize to bare scenario dict) ───────────────

def _claude_scenario_fn(prompt: str) -> dict:
    """Call Claude for scenario generation and unpack the wrapper into a bare
    scenario dict, attaching metadata under 'meta'.

    Raises ValueError if the response contains an error or no 'scenario' key.
    """
    result = _call_claude(_SCENARIO_SYSTEM, prompt, max_tokens=1200)

    if "error" in result:
        raise ValueError(result["error"])

    # Unpack wrapper {"scenario": {...}, "explanation": ..., ...}
    if "scenario" in result:
        scenario = result["scenario"]
        # Attach extra Claude metadata without polluting the bare scenario
        scenario["_meta"] = {
            "explanation": result.get("explanation"),
            "suggested_rc": result.get("suggested_rc"),
            "jit_behavior": result.get("jit_behavior"),
        }
    else:
        # Claude returned a flat dict that is itself the scenario
        scenario = result

    # Ensure id is present
    if not scenario.get("id"):
        scenario["id"] = f"gen_{int(time.time())}"

    return scenario


# ── Endpoints ─────────────────────────────────────────────────────────────────

@ai_router.post("/generate_scenario")
async def generate_scenario(request: Request):
    """Generate a test scenario from a natural-language prompt.

    Uses Claude-first via generate_with_fallback; Ollama is the fallback.
    Returns a bare scenario dict (+ optional _meta from Claude).
    """
    body = await request.json()

    user_input = (
        body.get("prompt")
        or body.get("description")
        or body.get("input")
        or ""
    ).strip()

    if not user_input:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    try:
        scenario = generate_with_fallback(user_input, _claude_scenario_fn)

        # Persist via mongo_repository so the scenario is immediately runnable
        try:
            from backend.mongo_repository import save_scenario
            save_scenario({k: v for k, v in scenario.items() if k != "_meta"})
        except Exception as save_err:
            logger.warning("save_scenario failed (non-fatal): %s", save_err)

        return scenario

    except Exception as e:
        logger.exception("Scenario generation failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@ai_router.post("/run_test")
async def run_test(request: Request):
    """Generate a scenario from a prompt then immediately run it end-to-end.

    Returns {"scenario": <bare dict>, "execution_result": <trace>, "analysis": <dict>}.
    Always uses generate_with_fallback so Claude is tried first.
    """
    # Import here to avoid circular import (main imports ai_routes)
    from backend.main import _execute_scenario_internal

    body = await request.json()

    description = (
        body.get("description")
        or body.get("prompt")
        or ""
    ).strip()

    if not description:
        return JSONResponse({"error": "description is required"}, status_code=400)

    try:
        # Generate bare scenario dict
        scenario = generate_with_fallback(description, _claude_scenario_fn)

        # Strip internal _meta key before passing to the execution engine
        bare_scenario = {k: v for k, v in scenario.items() if k != "_meta"}

        # Persist so it's listed in /scenarios
        try:
            from backend.mongo_repository import save_scenario
            save_scenario(bare_scenario)
        except Exception as save_err:
            logger.warning("save_scenario failed (non-fatal): %s", save_err)

        # Run the scenario through the full stack
        execution_result = _execute_scenario_internal(bare_scenario)

        # Build a lightweight analysis (Claude if available, else heuristic)
        analysis = _analyze_result(bare_scenario, execution_result)

        return {
            "scenario": bare_scenario,
            "execution_result": execution_result,
            "analysis": analysis,
        }

    except Exception as e:
        logger.exception("AI test execution failed")
        return JSONResponse({"error": str(e)}, status_code=500)


def _analyze_result(scenario: dict, result: dict) -> dict:
    """Produce an analysis dict for a completed run.

    Tries Claude; falls back to a heuristic summary so /run_test never blocks
    on an unavailable AI provider.
    """
    passed = result.get("passed", False)
    actual_rc = result.get("actual_network_response_code", "?")
    expected_rc = result.get("expected_network_response_code", "?")
    actual_dec = result.get("actual_customer_decision", "?")

    # Heuristic fallback (always available)
    heuristic = {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "actual_rc": actual_rc,
        "expected_rc": expected_rc,
        "actual_decision": actual_dec,
        "summary": (
            f"Scenario '{scenario.get('name', scenario.get('id'))}' "
            + ("passed." if passed else f"failed — got RC {actual_rc}, expected {expected_rc}.")
        ),
    }

    if passed:
        return heuristic  # No need for AI on a clean pass

    # Try Claude for a richer analysis
    user_msg = (
        f"Transaction test failure:\n\n"
        f"Scenario: {scenario.get('name','Unknown')}\n"
        f"Expected RC: {expected_rc} | Actual RC: {actual_rc}\n"
        f"Expected Decision: {result.get('expected_customer_decision')} | Actual: {actual_dec}\n"
        f"Duration: {result.get('duration_ms')} ms\n\n"
        f"Audit Trail:\n{json.dumps(result.get('audit_trail', []), indent=2)}"
    )
    ai_result = _call_claude(_ANOMALY_SYSTEM, user_msg, max_tokens=600)
    if "error" not in ai_result:
        ai_result.update(heuristic)
        return ai_result

    return heuristic


@ai_router.post("/explain_failure")
async def explain_failure(request: Request):
    """Explain why a test failed given its audit trail."""
    body = await request.json()
    audit = body.get("audit_trail", [])

    user_msg = (
        f"Transaction test failure:\n\n"
        f"Scenario: {body.get('scenario_name','Unknown')}\n"
        f"Expected RC: {body.get('expected_rc')} | Actual RC: {body.get('actual_rc')}\n"
        f"Expected Decision: {body.get('expected_decision')} | Actual: {body.get('actual_decision')}\n"
        f"Duration: {body.get('duration_ms')} ms\n\n"
        f"Audit Trail:\n{json.dumps(audit, indent=2)}"
    )
    return _call_claude(_ANOMALY_SYSTEM, user_msg, max_tokens=600)


@ai_router.post("/suite_insights")
async def suite_insights(request: Request):
    """Provide executive-level insights for a completed suite run."""
    body = await request.json()
    suite_result = body.get("suite_result") or body  # accept both wrapping styles
    results = suite_result.get("results", [])
    failed = [r for r in results if not r.get("passed")]

    user_msg = (
        f"Suite: {suite_result.get('suite_name')}\n"
        f"Run at: {suite_result.get('run_at')}\n"
        f"Result: {suite_result.get('passed')}/{suite_result.get('total')} passed "
        f"in {suite_result.get('duration_ms')}ms\n\n"
        f"Failed tests ({len(failed)}):\n"
        + json.dumps(
            [
                {
                    "name": r.get("name"),
                    "expected_rc": r.get("expected_network_response_code"),
                    "actual_rc": r.get("actual_network_response_code"),
                    "expected_decision": r.get("expected_customer_decision"),
                    "actual_decision": r.get("actual_customer_decision"),
                }
                for r in failed
            ],
            indent=2,
        )
    )
    result = _call_claude(_SUITE_INSIGHTS_SYSTEM, user_msg, max_tokens=800)
    # Normalise: the frontend copilot page expects an "insights" key
    if "error" not in result and "summary" in result:
        result.setdefault("insights", result["summary"])
    return result


@ai_router.post("/coverage_advisor")
async def coverage_advisor(request: Request):
    """Analyse current RC coverage and identify gaps."""
    body = await request.json()
    user_msg = (
        f"Current test coverage:\n"
        f"- Covered response codes: {body.get('covered_rcs', [])}\n"
        f"- Total scenarios: {body.get('scenario_count', 0)}\n"
        f"- Suites configured: {body.get('suite_names', [])}\n\n"
        "Identify what's missing and why it matters for a production Marqeta "
        "JIT Funding integration that processes Visa/Mastercard transactions."
    )
    return _call_claude(_COVERAGE_ADVISOR_SYSTEM, user_msg, max_tokens=1200)
