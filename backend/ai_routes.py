# e2e-marqeta-simulator/backend/ai_routes.py
"""AI-powered endpoints using the Anthropic Claude API.

Registered on the FastAPI app under the /ai prefix.
Requires ANTHROPIC_API_KEY environment variable.
"""
import os
import json
import logging
from backend.ollama_client import OllamaClient
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from backend.ai_provider import generate_with_fallback
from backend.agent_service import (
    execute_agent,
    analyze_execution
)

logger = logging.getLogger(__name__)

ai_router = APIRouter(prefix="/ai", tags=["AI Copilot"])

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

def _call_ollama(prompt):

    client = OllamaClient()

    return client.generate_scenario(prompt)

def _call_claude(system: str, user_msg: str, max_tokens: int = 1200) -> dict:
    """Call Claude and return parsed JSON response."""
    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic package not installed. Run: pip install anthropic"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY environment variable not set"}

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model="claude-opus-4-5-20251101",
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
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Claude returned non-JSON: %s", e)
        return {"error": "AI response was not valid JSON", "raw": raw[:500]}
    except Exception as e:  # noqa: BLE001
        logger.error("Claude API error: %s", e)
        return {"error": str(e)}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@ai_router.post("/generate_scenario")
async def generate_scenario(request: Request):

    body = await request.json()

    user_input = (
        body.get("prompt")
        or body.get("description")
        or body.get("input")
        or ""
    ).strip()

    if not user_input:
        return JSONResponse(
            {"error": "prompt is required"},
            status_code=400
        )

    try:

        return execute_agent(
            "scenario_generator",
            user_input
        )

    except Exception as e:

        logger.exception(
            "Scenario generation failed"
        )

        return JSONResponse(
            {"error": str(e)},
            status_code=500
        )

@ai_router.post("/explain_failure")
async def explain_failure(request: Request):
    """
    Explain why a test failed given its audit trail.

    Request body:
    {
        "audit_trail": [...],
        "expected_rc": "00",
        "actual_rc": "05",
        "expected_decision": "APPROVED",
        "actual_decision": "DECLINED",
        "scenario_name": "...",
        "duration_ms": 234.5
    }
    """
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
    """
    Provide executive-level insights for a completed suite run.

    Request body: the full suite_result dict from /execute_suite.
    """
    body = await request.json()
    results = body.get("results", [])
    failed = [r for r in results if not r.get("passed")]

    user_msg = (
        f"Suite: {body.get('suite_name')}\n"
        f"Run at: {body.get('run_at')}\n"
        f"Result: {body.get('passed')}/{body.get('total')} passed in {body.get('duration_ms')}ms\n\n"
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
    return _call_claude(_SUITE_INSIGHTS_SYSTEM, user_msg, max_tokens=800)

@ai_router.post("/run_test")
async def run_test(
    request: Request
):

    # Import here to avoid circular import
    from backend.main import (
        _execute_scenario_internal
    )

    body = await request.json()

    description = (
        body.get("description")
        or body.get("prompt")
        or ""
    ).strip()

    if not description:

        return JSONResponse(
            {
                "error": "description is required"
            },
            status_code=400
        )

    try:

        scenario = execute_agent(
            "scenario_generator",
            description
        )

        execution_result = (
            _execute_scenario_internal(
                scenario
            )
        )

        analysis = analyze_execution(
            scenario,
            execution_result
        )

        return {
            "scenario": scenario,
            "execution_result": execution_result,
            "analysis": analysis
        }

    except Exception as e:

        logger.exception(
            "AI test execution failed"
        )

        return JSONResponse(
            {
                "error": str(e)
            },
            status_code=500
        )
    
@ai_router.post("/coverage_advisor")
async def coverage_advisor(request: Request):
    """
    Analyse current RC coverage and identify gaps.

    Request body:
    {
        "covered_rcs": ["00", "05", "51"],
        "scenario_count": 14,
        "suite_names": ["full_regression", "auth_flows"]
    }
    """
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
