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

from groq import Groq
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.ai_provider import generate_with_fallback
from backend.ai_config import get_api_key
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

    Key resolution order (T0.2): in-app secrets → ANTHROPIC_API_KEY env → error.
    Model resolution order: ANTHROPIC_MODEL env → in-app config model → _DEFAULT_MODEL.
    Strips markdown fences if Claude wraps the JSON despite instructions.
    """
    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic package not installed. Run: pip install anthropic"}

    # T0.2: try in-app config first, then env
    api_key: str | None = None
    try:
        from backend.ai_config import get_api_key as _get_key
        api_key = _get_key("claude")
    except Exception:
        pass
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "error": (
                "No Anthropic API key found. Set one in the AI Settings page "
                "or via the ANTHROPIC_API_KEY environment variable."
            )
        }

    # Model: env > in-app config > default
    model = os.environ.get("ANTHROPIC_MODEL", "")
    if not model:
        try:
            from backend.ai_config import load_config as _load_cfg
            model = _load_cfg().get("providers", {}).get("claude", {}).get("model", "")
        except Exception:
            pass
    if not model:
        model = _DEFAULT_MODEL

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


# ── AI Provider config endpoints (T0.2) ───────────────────────────────────────

@ai_router.get("/providers")
async def get_providers():
    """Return current AI provider config (no raw keys — status only)."""
    try:
        from backend.ai_config import provider_status, load_config
        cfg = load_config()
        return {
            "primary": cfg.get("primary"),
            "fallback_chain": cfg.get("fallback_chain"),
            "providers": provider_status(),
        }
    except Exception as exc:
        logger.warning("ai_config unavailable: %s", exc)
        # Graceful fallback: report env-only status
        key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return {
            "primary": "claude",
            "fallback_chain": ["ollama"],
            "providers": [{
                "provider":   "claude",
                "model":      os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL),
                "base_url":   "https://api.anthropic.com",
                "key_status": "detected" if key_set else "not detected",
                "is_primary": True,
                "in_chain":   False,
            }],
        }


@ai_router.post("/providers/config")
async def update_provider_config(request: Request):
    """Update AI provider config (model, base_url, chain — NOT keys)."""
    body = await request.json()
    try:
        from backend.ai_config import save_config
        save_config(body)
        return {"status": "saved"}
    except Exception as exc:
        logger.error("save_config failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@ai_router.post("/providers/key")
async def set_provider_key(request: Request):
    """Set an API key for a provider.

    Body: {"provider": "claude", "api_key": "sk-ant-..."}
    The key is stored encrypted on disk; never echoed back.
    SECURITY: Never log or return the api_key value.
    """
    body = await request.json()
    provider = body.get("provider", "").strip()
    api_key  = body.get("api_key", "").strip()

    if not provider or not api_key:
        return JSONResponse({"error": "provider and api_key are required"}, status_code=400)

    # Sanity guard: ensure the key value never appears in any log/response
    try:
        from backend.ai_config import set_api_key, get_key_status
        set_api_key(provider, api_key)
        return {"status": "saved", "provider": provider, "key_status": get_key_status(provider)}
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.error("set_api_key failed for provider %s: %s", provider, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@ai_router.delete("/providers/key/{provider}")
async def delete_provider_key(provider: str):
    """Delete a stored API key for a provider."""
    try:
        from backend.ai_config import delete_api_key, get_key_status
        delete_api_key(provider)
        return {"status": "deleted", "provider": provider, "key_status": get_key_status(provider)}
    except Exception as exc:
        logger.error("delete_api_key failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    
def _call_groq(system: str, user_msg: str, max_tokens: int = 1200):

    api_key = get_api_key("groq")

    if not api_key:
        raise RuntimeError(
            "Groq API key has not been configured. Please save it from AI Settings."
        )

    client = Groq(
        api_key=os.environ["GROQ_API_KEY"]
    )

    response = client.chat.completions.create(

        model=os.getenv(
            "GROQ_MODEL",
            "llama-3.3-70b-versatile",
        ),

        temperature=0.2,

        messages=[

            {
                "role": "system",
                "content": system,
            },

            {
                "role": "user",
                "content": user_msg,
            },

        ],
    )

    raw = response.choices[0].message.content.strip()

    #
    # Remove markdown fences if present
    #

    if raw.startswith("```"):

        raw = raw.replace("```json", "")
        raw = raw.replace("```", "")
        raw = raw.strip()

    return json.loads(raw)

def _groq_scenario_fn(prompt):

    result = _call_groq(
        _SCENARIO_SYSTEM,
        prompt,
        max_tokens=1200,
    )

    if "scenario" in result:

        scenario = result["scenario"]

        scenario["_meta"] = {

            "explanation": result.get("explanation"),

            "suggested_rc": result.get("suggested_rc"),

            "jit_behavior": result.get("jit_behavior"),
        }

    else:

        scenario = result

    if not scenario.get("id"):

        import time

        scenario["id"] = f"gen_{int(time.time())}"

    return scenario

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
        scenario = generate_with_fallback(user_input, _groq_scenario_fn)

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
        scenario = generate_with_fallback(description, _groq_scenario_fn)

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


# ── Mandate-driven AI enrichment (T2.1 — Phase 5) ────────────────────────────

_MANDATE_SYSTEM = """\
You are a senior payments architect with deep expertise in ISO 8583, Visa/Mastercard
mandate bulletins, EMV specifications, and Marqeta JIT Funding integrations.

Given a network mandate excerpt, produce a STRUCTURED implementation proposal that
covers: which ISO 8583 data elements are added/changed, what canonical JPF fields
map to them, which DB columns are needed, and at least one runnable test scenario
that exercises the mandate.

IMPORTANT SECURITY RULES:
- Only use test BINs (4111..., 5555..., 3782..., 6011...) in scenarios — NEVER real PANs.
- Only reference DE numbers 1-128 (ISO 8583 standard range).
- Field types must be: STRING, INTEGER, BOOLEAN, DECIMAL, DATETIME, or BYTES.

Respond ONLY with a valid JSON object (no markdown fences, no commentary):
{
  "design_summary": "<plain-English description of what this mandate changes>",
  "iso_mapping_additions": [
    {
      "canonical": "<jpf.dotted.path>",
      "source": {"de": <integer DE number>, "transform": "passthrough|n12_to_cents|sha256"},
      "network": "<visa|mastercard|amex|discover|all>",
      "description": "<what this field carries>"
    }
  ],
  "jpf_fields": [
    {"path": "<jpf.dotted.path>", "type": "<STRING|INTEGER|BOOLEAN|DECIMAL|DATETIME|BYTES>",
     "description": "<purpose>"}
  ],
  "db_columns": [
    {"name": "<snake_case_name>", "type": "<VARCHAR(n)|INTEGER|BOOLEAN|DECIMAL|TIMESTAMP>",
     "description": "<purpose>"}
  ],
  "scenarios": [
    {
      "id": "<snake_case_id>",
      "name": "<Human readable name>",
      "description": "<what this scenario tests>",
      "event_type": "authorization",
      "request": {
        "transaction_id": "TXN_MANDATE_001",
        "pan": "4111111111111111",
        "amount": <integer cents>,
        "currency": "840",
        "mcc": "<4-digit MCC>",
        "merchant_name": "<25 char max>",
        "pos_entry_mode": "051|071|011|010|810"
      },
      "expected_network_response_code": "00",
      "expected_customer_decision": "APPROVED"
    }
  ],
  "validation_notes": "<any caveats about the proposal, e.g. if the mandate is ambiguous>"
}
"""


_APPLY_SYSTEM = """\
You are a code-generation assistant specialised in YAML spec files for ISO 8583 → JPF
mapping engines. Given a list of mapping additions (in the format produced by
/ai/mandate), generate the YAML snippet that should be appended to the existing
specs/<network>.yaml file.

The YAML format for a field entry is:
  - canonical: transaction.some.field
    source:
      de: <integer>
      transform: passthrough
    description: "<what this DE carries>"

Respond ONLY with valid YAML — no markdown, no explanation, no preamble.
"""


@ai_router.post("/mandate")
async def analyze_mandate(request: Request):
    """Analyze a network mandate excerpt and propose implementation changes.

    Body: {"mandate_text": "...", "network": "visa|mastercard|amex|discover"}

    Returns a structured proposal with:
    - design_summary (plain-English)
    - iso_mapping_additions (spec YAML deltas)
    - jpf_fields (canonical field names + types)
    - db_columns (DB column names + types)
    - scenarios (runnable test scenarios exercising the mandate)

    All generated PANs are test BINs only; DE numbers are validated (1-128).
    Human approval is REQUIRED before applying — use POST /ai/mandate/apply.
    """
    body = await request.json()
    mandate_text = (body.get("mandate_text") or "").strip()
    network = (body.get("network") or "visa").lower().strip()

    if not mandate_text:
        return JSONResponse({"error": "mandate_text is required"}, status_code=400)
    if network not in ("visa", "mastercard", "amex", "discover"):
        return JSONResponse({"error": f"Unknown network: {network}"}, status_code=400)

    user_msg = (
        f"Network: {network}\n\n"
        f"Mandate excerpt:\n{mandate_text[:4000]}"  # cap to avoid huge payloads
    )
    result = _call_claude(_MANDATE_SYSTEM, user_msg, max_tokens=2000)

    if "error" in result:
        return JSONResponse(result, status_code=500)

    # ── Guardrail validation (T2.3) ───────────────────────────────────────────
    errors = _validate_mandate_proposal(result)
    result["_validation"] = {
        "errors":   errors,
        "valid":    len(errors) == 0,
        "network":  network,
        "reviewed": False,   # requires explicit human Apply action
    }
    return result


def _validate_mandate_proposal(proposal: dict) -> list[str]:
    """Validate a mandate proposal against security and correctness guardrails.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []
    _REAL_PAN_PREFIXES = ["1", "2", "3", "4", "5", "6"]  # all real, but test PANs are safe
    _TEST_BINS = {"4111", "5555", "3782", "6011", "4000", "5200"}
    _VALID_TYPES = {"STRING", "INTEGER", "BOOLEAN", "DECIMAL", "DATETIME", "BYTES"}
    _VALID_DB_TYPES = {"VARCHAR", "INTEGER", "BOOLEAN", "DECIMAL", "TIMESTAMP", "TEXT"}

    # Check ISO mapping additions
    for i, mapping in enumerate(proposal.get("iso_mapping_additions", [])):
        de = mapping.get("source", {}).get("de")
        if de is not None:
            try:
                de_int = int(de)
                if not (1 <= de_int <= 128):
                    errors.append(f"iso_mapping_additions[{i}]: DE {de} out of range (1-128)")
            except (ValueError, TypeError):
                errors.append(f"iso_mapping_additions[{i}]: DE '{de}' is not a valid integer")
        if not mapping.get("canonical"):
            errors.append(f"iso_mapping_additions[{i}]: missing 'canonical' JPF path")

    # Check JPF field types
    for i, field in enumerate(proposal.get("jpf_fields", [])):
        ftype = str(field.get("type", "")).upper()
        if ftype and ftype not in _VALID_TYPES:
            errors.append(f"jpf_fields[{i}]: invalid type '{ftype}' (must be one of {sorted(_VALID_TYPES)})")
        if not field.get("path"):
            errors.append(f"jpf_fields[{i}]: missing 'path'")

    # Check DB column types
    for i, col in enumerate(proposal.get("db_columns", [])):
        dtype = str(col.get("type", "")).upper().split("(")[0]   # strip VARCHAR(n) → VARCHAR
        if dtype and dtype not in _VALID_DB_TYPES:
            errors.append(f"db_columns[{i}]: invalid type '{dtype}'")
        if not col.get("name"):
            errors.append(f"db_columns[{i}]: missing 'name'")

    # Check scenarios: no real-looking PANs
    for i, sc in enumerate(proposal.get("scenarios", [])):
        req = sc.get("request", {})
        pan = str(req.get("pan", ""))
        if pan and len(pan) >= 4:
            prefix = pan[:4]
            if prefix not in _TEST_BINS:
                errors.append(
                    f"scenarios[{i}]: PAN '{pan[:6]}...' does not start with a known test BIN. "
                    "Use 4111..., 5555..., 3782..., or 6011..."
                )
        # DE field names cannot look like PAN (simple check)
        for field_name, field_val in req.items():
            val_str = str(field_val or "")
            if len(val_str) >= 13 and val_str.isdigit() and field_name != "pan":
                errors.append(
                    f"scenarios[{i}].request.{field_name}: "
                    f"value '{val_str[:6]}...' looks like a PAN — use only 'pan' for card numbers"
                )

    return errors


@ai_router.post("/mandate/apply")
async def apply_mandate(request: Request):
    """Apply a reviewed mandate proposal to the spec files.

    Body:
    {
      "proposal":   <mandate proposal dict from POST /ai/mandate>,
      "network":    "visa|mastercard|amex|discover",
      "confirmed":  true   ← REQUIRED: explicit human approval
    }

    SECURITY: Will NOT apply if:
    - confirmed != true
    - _validation.errors is non-empty
    - proposal.iso_mapping_additions is absent or empty

    Returns: {"status": "applied"|"rejected", "diff": "<YAML additions>", "errors": [...]}
    """
    body = await request.json()
    proposal = body.get("proposal") or {}
    network  = (body.get("network") or "visa").lower()
    confirmed = body.get("confirmed", False)

    # Human approval gate
    if not confirmed:
        return JSONResponse({
            "status": "rejected",
            "reason": "confirmed must be true — no silent auto-apply",
            "diff":   None,
        }, status_code=400)

    # Validation gate
    validation = proposal.get("_validation", {})
    errors = validation.get("errors") or _validate_mandate_proposal(proposal)
    if errors:
        return JSONResponse({
            "status":  "rejected",
            "reason":  "Proposal failed validation — fix errors before applying",
            "errors":  errors,
            "diff":    None,
        }, status_code=422)

    additions = proposal.get("iso_mapping_additions", [])
    if not additions:
        return JSONResponse({
            "status": "rejected",
            "reason": "No iso_mapping_additions in proposal",
        }, status_code=400)

    # ── Generate YAML diff ────────────────────────────────────────────────────
    yaml_lines: list[str] = [
        f"# Mandate-driven addition — applied via /ai/mandate/apply",
        f"# Network: {network}",
        f"# design_summary: {(proposal.get('design_summary') or '')[:120]}",
        "",
    ]
    for mapping in additions:
        net_filter = mapping.get("network", "all")
        if net_filter not in ("all", network):
            continue
        source = mapping.get("source", {})
        yaml_lines += [
            f"  - canonical: {mapping.get('canonical', '')}",
            f"    source:",
            f"      de: {source.get('de', '?')}",
            f"      transform: {source.get('transform', 'passthrough')}",
            f"    description: \"{mapping.get('description', '')}\"",
        ]

    diff_yaml = "\n".join(yaml_lines)

    # ── Write to spec file ────────────────────────────────────────────────────
    import os as _os
    specs_dir = _os.path.join(_os.path.dirname(__file__), "mapping", "specs")
    spec_path = _os.path.join(specs_dir, f"{network}.yaml")

    if not _os.path.exists(spec_path):
        return JSONResponse({
            "status": "rejected",
            "reason": f"Spec file not found: mapping/specs/{network}.yaml",
            "diff":   diff_yaml,
        }, status_code=404)

    try:
        with open(spec_path, "a") as fh:
            fh.write("\n# ── Mandate additions (auto-applied) ──────────────\n")
            fh.write(diff_yaml + "\n")
        logger.info("Mandate applied to %s: %d additions", spec_path, len(additions))
    except OSError as exc:
        return JSONResponse({
            "status": "error",
            "reason": str(exc),
            "diff":   diff_yaml,
        }, status_code=500)

    # Persist the mandate-generated scenarios
    saved_scenarios = []
    for sc in proposal.get("scenarios", []):
        try:
            from backend.mongo_repository import save_scenario
            bare = {k: v for k, v in sc.items() if k not in ("_meta",)}
            save_scenario(bare)
            saved_scenarios.append(bare.get("id"))
        except Exception as save_err:
            logger.warning("save_scenario failed for mandate scenario: %s", save_err)

    return {
        "status":           "applied",
        "network":          network,
        "diff":             diff_yaml,
        "additions_count":  len([m for m in additions if m.get("network","all") in ("all", network)]),
        "scenarios_saved":  saved_scenarios,
    }
