# e2e-marqeta-simulator/backend/heal_service.py
"""
Closed-loop self-healing, extending the existing agent_service.py pattern.

Flow:
    Execute scenario
        └─ PASS  →  done
        └─ FAIL  →  analyze_failure()    [heal_analyze_agent + live /config]
                        └─ fixable?  →  propose_fix()  [heal_fix_agent]
                                            └─ apply patch → re-execute → loop
                        └─ escalate  →  persist result, stop

Writes:
  - healing_runs collection (MongoDB) — full audit trail per run
  - scenarios collection (MongoDB)    — versioned healed scenario on success
  - SQLite transactions table         — every execution attempt, same as /execute

Nothing here touches files on disk. All seed docs (agents, prompts, guardrails)
are loaded from MongoDB via agent_repository, exactly as execute_agent() does.
"""
import json
import copy
import time
import logging
from datetime import datetime, timezone

from backend.agent_repository import get_agent, get_prompt, get_guardrail
from backend.ollama_client import OllamaClient
from backend.mongo_repository import db, save_scenario
from backend.capability_resolver import get_capabilities_text

logger = logging.getLogger(__name__)

# Mongo collection for healing audit trail — does not exist yet, Mongo
# creates it on first insert, no migration needed.
healing_runs = db["healing_runs"]


# ── Internal: prompt builder (mirrors build_prompt in agent_service.py) ─────

def _build_heal_prompt(agent_id: str, substitutions: dict) -> tuple[dict, str]:
    """
    Load agent + prompt template from Mongo, apply {{key}} substitutions.
    Returns (agent_doc, filled_prompt_string).
    """
    agent = get_agent(agent_id)
    if agent is None:
        raise RuntimeError(f"Agent '{agent_id}' not found in MongoDB. "
                           "Re-run bootstrap to seed heal agents.")

    prompt_doc = get_prompt(agent["prompt_template_id"])
    if prompt_doc is None:
        raise RuntimeError(f"Prompt '{agent['prompt_template_id']}' not found. "
                           "Re-run bootstrap.")

    prompt = prompt_doc["template"]
    for key, value in substitutions.items():
        prompt = prompt.replace(
            f"{{{{{key}}}}}",
            value if isinstance(value, str) else json.dumps(value, indent=2)
        )
    return agent, prompt


def _call_ollama(agent: dict, prompt: str) -> dict:
    client = OllamaClient()
    return client.generate(model=agent["model"], prompt=prompt)


# ── Step 1: analyze why a failure occurred ───────────────────────────────────

def analyze_failure(scenario: dict, execution_result: dict, capabilities_text: str) -> dict:
    """
    Calls heal_analyze_agent. Returns structured dict with root_cause_category
    and confidence — the fields that drive the fixable/escalate fork.
    """
    agent, prompt = _build_heal_prompt("heal_analyze_agent", {
        "capabilities": capabilities_text,
        "scenario": scenario,
        "execution_result": execution_result,
    })
    result = _call_ollama(agent, prompt)
    result.setdefault("root_cause_category", "unknown")
    result.setdefault("confidence", "low")
    return result


# ── Step 2: propose a fix for fixable failures ────────────────────────────────

def propose_fix(scenario: dict, execution_result: dict,
                analysis: dict, capabilities_text: str) -> dict:
    """
    Calls heal_fix_agent. Returns a partial patch dict, e.g.:
      {"request": {"amount": 7500}, "rationale": "..."}
    Only call this when analysis['root_cause_category'] is in fixable set.
    """
    agent, prompt = _build_heal_prompt("heal_fix_agent", {
        "capabilities": capabilities_text,
        "scenario": scenario,
        "execution_result": execution_result,
        "analysis": analysis,
    })
    return _call_ollama(agent, prompt)


# ── Step 3: apply the proposed patch ─────────────────────────────────────────

def _apply_fix(scenario: dict, fix: dict) -> dict:
    """
    Returns a new scenario dict with the fix applied.
    Never mutates the original.
    """
    patched = copy.deepcopy(scenario)
    if "request" in fix and isinstance(fix["request"], dict):
        patched.setdefault("request", {}).update(fix["request"])
    if "expected_network_response_code" in fix:
        patched["expected_network_response_code"] = fix["expected_network_response_code"]
    if "expected_customer_decision" in fix:
        patched["expected_customer_decision"] = fix["expected_customer_decision"]
    return patched


# ── Step 4: save versioned healed scenario to Mongo ──────────────────────────

def _save_healed_scenario(original_id: str, healed: dict):
    """
    Writes the healed scenario as a new versioned document.
    Keeps the original scenario untouched in the scenarios collection
    (save_scenario does replace_one by id, so we tag the healed copy
    with a new id to preserve lineage).
    """
    healed_id = f"{original_id}__healed_{int(time.time())}"
    doc = copy.deepcopy(healed)
    doc["id"] = healed_id
    doc["healed_from"] = original_id
    doc["healed_at"] = datetime.now(timezone.utc).isoformat()
    doc["status"] = "healed"
    save_scenario(doc)
    return healed_id


# ── Main entry point ─────────────────────────────────────────────────────────

def run_heal_loop(scenario: dict) -> dict:
    """
    Full closed loop. Import _execute_scenario_internal at call time to
    avoid a circular import (ai_routes does the same pattern).

    Returns a heal_run dict that is also persisted to healing_runs.
    """
    from backend.main import _execute_scenario_internal

    # Load guardrail policy from Mongo (or fall back if unseeded)
    guardrail = get_guardrail("heal_guardrail_v1") or {}
    fixable = set(guardrail.get("fixable_categories",
                                ["request_malformed", "missing_required_field",
                                 "wrong_data_format"]))
    confidence_floor = guardrail.get("min_confidence_for_auto_fix", "medium")
    max_iter = int(guardrail.get("max_iterations", 3))

    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    floor_rank = confidence_rank.get(confidence_floor, 1)

    # Fetch live capabilities once per loop run
    capabilities_text = get_capabilities_text()

    scenario_id = scenario.get("id", "unknown")
    started_at = datetime.now(timezone.utc).isoformat()
    attempts = []
    current_scenario = copy.deepcopy(scenario)

    # ── First execution ───────────────────────────────────────────────────────
    trace = _execute_scenario_internal(current_scenario, unique=True)

    if trace.get("passed"):
        return _finish(scenario_id, "PASSED_FIRST_TRY", scenario, current_scenario,
                       attempts, trace, started_at, capabilities_text, None)

    # ── Healing iterations ────────────────────────────────────────────────────
    for iteration in range(1, max_iter + 1):
        analysis = analyze_failure(current_scenario, trace, capabilities_text)
        category = analysis.get("root_cause_category", "unknown")
        confidence = analysis.get("confidence", "low")

        attempt = {
            "iteration": iteration,
            "scenario_id": current_scenario.get("id"),
            "trace": trace,
            "analysis": analysis,
        }

        # Fixability gate: category must be in fixable set AND confidence
        # must be at or above the floor configured in the guardrail.
        is_fixable = (
            category in fixable and
            confidence_rank.get(confidence, 0) >= floor_rank
        )

        if not is_fixable:
            attempt["action"] = "ESCALATED"
            attempts.append(attempt)
            logger.warning(
                "Scenario %s escalated: category=%s confidence=%s",
                scenario_id, category, confidence
            )
            return _finish(scenario_id, "ESCALATED", scenario, current_scenario,
                           attempts, trace, started_at, capabilities_text, analysis)

        # Propose and apply fix
        fix = propose_fix(current_scenario, trace, analysis, capabilities_text)
        patched = _apply_fix(current_scenario, fix)
        attempt["proposed_fix"] = fix
        attempt["action"] = "RETRY"

        # Re-execute with the patched scenario
        retry_trace = _execute_scenario_internal(patched, unique=True)
        attempt["retry_trace"] = retry_trace
        attempts.append(attempt)

        current_scenario = patched
        trace = retry_trace

        if retry_trace.get("passed"):
            healed_id = _save_healed_scenario(scenario_id, current_scenario)
            return _finish(healed_id, "HEALED", scenario, current_scenario,
                           attempts, trace, started_at, capabilities_text, analysis)

    # Exhausted iterations without passing
    attempts_summary = [
        {"iteration": a["iteration"], "category": a.get("analysis", {}).get("root_cause_category"),
         "action": a.get("action")}
        for a in attempts
    ]
    logger.warning("Scenario %s unresolved after %d iterations: %s",
                   scenario_id, max_iter, attempts_summary)
    return _finish(scenario_id, "UNRESOLVED", scenario, current_scenario,
                   attempts, trace, started_at, capabilities_text, None)


def _finish(scenario_id, status, original, final, attempts, last_trace,
            started_at, capabilities_text, last_analysis) -> dict:
    run = {
        "scenario_id": scenario_id,
        "status": status,                  # PASSED_FIRST_TRY | HEALED | ESCALATED | UNRESOLVED
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "iterations": len(attempts),
        "attempts": attempts,
        "original_scenario": original,
        "final_scenario": final,
        "last_trace": last_trace,
        "last_analysis": last_analysis,
        # Snapshot the exact capabilities text used — reproducible even after
        # customer_jit config changes later.
        "capabilities_snapshot": capabilities_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    healing_runs.insert_one(run)
    run.pop("_id", None)   # ObjectId not JSON-serializable
    return run