# Paycon E2E-MS — Claude Code Implementation Plan

**Repo:** `github.com/srv2go/e2e-ms`
**Goal:** Make the AI Copilot actually load and run reliably for a live demo, fix the
broken provider fallback, and add the features that support the "certify a customer's
JIT integration" (Marqeta) story.

> **Kickoff prompt (paste into Claude Code):**
> "Read this plan top to bottom. Work task by task in priority order (P0 → P2). After
> each task, run the verification step before moving on. Do not change public endpoint
> paths or response shapes unless a task explicitly says to. Keep a running CHANGELOG.md.
> The demo must work with **only** `ANTHROPIC_API_KEY` set — no MongoDB and no Ollama
> running — by the end of P0. Confirm that with `docker-compose up backend` plus a curl
> to `/ai/run_test` before declaring P0 done."

---

## 0. Context: how the AI path is wired today

- `main.py` tries `from backend.ai_routes import ai_router`; **on any import error it
  catches and sets `ai_router = None`, so the `/ai/*` endpoints silently disappear.**
- `ai_routes.py` imports `agent_service` → `agent_repository` → `from backend.mongo_repository import db`.
- `_call_claude()` (Claude path) works standalone and only needs `ANTHROPIC_API_KEY`.
- `execute_agent()` (Ollama path) needs MongoDB **and** a running `ollama` container **and**
  seeded agent/prompt/template docs.
- `generate_with_fallback()` exists but is **never actually called** by any endpoint.

The fastest path to a reliable demo is **Claude-first generation**, with Ollama as the
documented offline fallback. That removes Mongo/Ollama from the demo's critical path.

---

## P0 — Make the AI Copilot load and run reliably

### T0.1 — Fix the import break that disables the whole AI router
**File:** `backend/agent_repository.py`
**Bug:** `from backend.mongo_repository import db` — `mongo_repository.py` exports **no
`db`**. This ImportError cascades and disables every `/ai/*` endpoint via the
`try/except` in `main.py`. It also references collections by the wrong names
(`db.agent_definitions`, `db.prompt_templates`) that don't match the exported
collection objects (`agents`, `prompts`, `guardrails`, `templates`).

**Fix:** Rewrite `agent_repository.py` to use the collection objects already exported by
`mongo_repository.py` instead of a non-existent `db`:
- `get_agent(id)` → `agents.find_one({<key>: id}, {"_id": 0})`
- `get_prompt(id)` → `prompts.find_one({<key>: id}, {"_id": 0})`
- `get_guardrail(id)` → `guardrails.find_one({<key>: id}, {"_id": 0})`
- `get_template()` → `templates.find_one({}, {"_id": 0})`

`<key>` must match the key the seed files use (see T0.3).

**Acceptance:** `python -c "import backend.ai_routes"` succeeds with no exception, and on
`docker-compose up backend` the startup log shows the AI router was registered (add an
`INFO` log line on successful `include_router`). Hitting `GET /openapi.json` lists the
`/ai/*` routes.

### T0.2 — Fix `generate_with_fallback` and actually wire it in
**File:** `backend/ai_provider.py`
**Bug:** references undefined `execute_agent` and `user_prompt`; instantiates an unused
`OllamaClient()`; raises `NameError` the moment the Claude branch throws.

**Fix:** Replace the function body with:
```python
import logging
logger = logging.getLogger(__name__)

def generate_with_fallback(prompt, claude_function):
    """Try Claude first; on ANY failure, fall back to the local Ollama agent.
    Both branches MUST return the same bare scenario-dict shape (see T0.4)."""
    try:
        logger.info("AI provider: trying Claude")
        return claude_function(prompt)
    except Exception as claude_error:
        logger.warning("Claude failed (%s); falling back to Ollama agent", claude_error)
        try:
            from backend.agent_service import execute_agent  # lazy import avoids cycles
            return execute_agent("scenario_generator", prompt)
        except Exception as ollama_error:
            raise RuntimeError(
                f"Both AI providers failed — Claude: {claude_error}; "
                f"Ollama: {ollama_error}"
            ) from ollama_error
```

**File:** `backend/ai_routes.py` — make `/generate_scenario` and `/run_test` call
`generate_with_fallback(user_input, claude_fn)` instead of calling `execute_agent`
directly, where `claude_fn` wraps `_call_claude(_SCENARIO_SYSTEM, prompt)` and returns a
**normalized bare scenario dict** (see T0.4).

**Acceptance:** With a valid `ANTHROPIC_API_KEY` and **Ollama/Mongo stopped**,
`POST /ai/generate_scenario {"prompt": "..."}` returns a scenario. With the key
unset/invalid, the response is a clean error, not a 500 stack trace.

### T0.3 — Make the in-memory fallback usable without MongoDB
**File:** `backend/mongo_repository.py`
**Bug:** `_InMemoryCollection.find_one` only matches `"id"` queries; `agent_repository`
queries by `_id`. Under the Mongo-down fallback, agent/prompt/template lookups return
`None` and the Ollama path dies with "No scenario template found".

**Fix:**
- Make `_InMemoryCollection.find_one` match on **both** `id` and `_id` (and support `{}`
  → first doc, which `get_template()` relies on).
- Ensure `bootstrap()` seeds `agents`, `prompts`, `guardrails`, `scenario_templates`
  into the in-memory collections too (today seeding only meaningfully works against real
  Mongo). Align the seed key (`_id` vs `id`) used in `backend/seed/**` with the key used
  by the repository queries in T0.1.

**Acceptance:** With `MONGO_URI` pointing nowhere (force the fallback) **and** Ollama
running, `POST /ai/generate_scenario` still succeeds via the Ollama agent. (This proves
the fallback chain end-to-end; the demo itself will use Claude.)

### T0.4 — Normalize provider return shapes
**Bug:** the Claude system prompt returns a **wrapper**
`{"scenario": {...}, "explanation": ..., "suggested_rc": ..., "jit_behavior": ...}`,
while `execute_agent` (Ollama) returns the **bare** scenario dict. `/run_test` then does
`scenario = execute_agent(...)` and feeds it to `_execute_scenario_internal(scenario)` —
which would choke on the Claude wrapper.

**Fix:** Standardize so generation **always returns the bare scenario dict** (with a
valid `id`), and surface the extra Claude metadata separately (e.g. attach under a
non-conflicting key the UI can read, or return `{"scenario": <bare>, "meta": {...}}` and
update `/run_test` + the copilot page to read `["scenario"]`). Persist via
`save_scenario(...)` in **both** paths. Pick one contract and make both providers conform.

**Acceptance:** `/run_test` returns `{"scenario", "execution_result", "analysis"}` where
`execution_result` is a real scored run (has `passed`, `response_received`, etc.),
whether the scenario came from Claude or Ollama.

### T0.5 — Fix the dead `_call_ollama` helper
**File:** `backend/ai_routes.py`
**Bug:** `_call_ollama` calls `client.generate_scenario(prompt)`, but `OllamaClient` only
has `generate(model, prompt)`. AttributeError if ever invoked.

**Fix:** Either delete `_call_ollama` (the Ollama path goes through `execute_agent`
already) or correct it to `client.generate(model=<agent model>, prompt=prompt)`.

**Acceptance:** No reference to a non-existent `OllamaClient` method remains
(`grep -rn "generate_scenario" backend/` is clean).

### T0.6 — Parametrize the Claude model
**File:** `backend/ai_routes.py`
**Bug:** model hardcoded as `claude-opus-4-5-20251101`.

**Fix:** Read `os.environ.get("ANTHROPIC_MODEL", "<current default>")`. Add `ANTHROPIC_MODEL`
to the backend service env in `docker-compose.yml` (default to a current Claude model).
Verify the chosen model id is valid before the demo.

**Acceptance:** Switching `ANTHROPIC_MODEL` in `.env` changes the model with no code edit.

---

## P1 — Demo features that sell the Marqeta certification story

### T1.1 — One-click Certification Report / Coverage Scorecard
The single most valuable demo artifact for a Marqeta audience: run a fixed
**certification suite** across the response-code matrix already in
`backend/scenarios/` (`rc_51`, `rc_54`, `rc_57`, `rc_61`, `rc_62`, `rc_65`, `rc_75`,
`rc_96`, plus the auth/advice/refund/reversal happy paths) against the configured SUT,
and produce a **PASS/FAIL certification report** with an overall coverage score.

- Backend: add `POST /certify` that runs the suite and returns per-scenario results +
  a coverage score + a `certified: true|false` verdict (configurable threshold).
- Reuse `frontend/utils/html_report.py` to render a branded, downloadable report
  (HTML, and PDF if quick). Header: "Marqeta JIT Integration — Certification Report",
  SUT URL, timestamp, score, pass/fail per lifecycle event and per RC.
- Frontend: a "Certify this SUT" button on the suite runner page that calls `/certify`
  and offers the report for download.

**Acceptance:** Clicking "Certify this SUT" produces a downloadable report listing every
lifecycle event + RC scenario with PASS/FAIL and an aggregate score.

### T1.2 — Inline AI failure explanation in the suite runner
`/ai/explain_failure` already exists. Surface it where it lands in a demo: when a
scenario in the suite/certification run FAILS, show an "Explain with AI" action that
posts the audit trail + expected/actual to `/ai/explain_failure` and renders
`root_cause` / `likely_rule_triggered` / `suggested_fix` inline.

**Acceptance:** A deliberately failing scenario shows a one-click AI explanation with a
concrete suggested fix.

### T1.3 — Make the SUT pluggable and visible
Reframe the UI around the SUT being swappable (the deck's clarification). On the
sandbox-config page: let the user set the **Customer JIT URL** to (a) the bundled
simulated SUT, or (b) a real external endpoint, and run a **connectivity + contract
check** (ping `/health`, send one synthetic auth, confirm a well-formed
`NetworkAuthResponse`) before allowing a certification run.

**Acceptance:** Pointing the SUT at an external URL and running "Certify" works against
that endpoint; a bad URL fails the pre-flight check with a clear message.

---

## P2 — Demo reliability & polish

### T2.1 — Provider status badge
Add `GET /ai/status` returning `{claude: ok|unavailable, ollama: ok|unavailable,
mongo: ok|in_memory}` and show it as a small badge on the AI Copilot page, so the
presenter always knows which provider is live before going on stage.

### T2.2 — `make demo` one-command startup
Add a `Makefile` / `start.sh` target that: validates `ANTHROPIC_API_KEY` is set, brings
up only the services the demo needs, waits for `/health` on each, and prints the UI URL.
Document a **Claude-only demo profile** (no `ollama`/`mongodb` services) in the README.

### T2.3 — Smoke test in CI
Add a pytest that, with a mocked Claude client, exercises:
`/ai/generate_scenario` → `/run_test` → `/certify` → `/ai/explain_failure`, asserting
shapes. Wire it into a GitHub Action so the AI path can't silently break again (which is
exactly what happened with the `db` import).

---

## Guardrails for the agent
- Don't rename existing endpoints or change response shapes outside T0.4 / T1.x.
- Every task ends with its **Acceptance** check run and recorded in `CHANGELOG.md`.
- Prefer lazy imports inside functions where needed to avoid circular imports
  (`main` ↔ `ai_routes` already do this for `_execute_scenario_internal`).
- Secrets only via env (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`); never commit keys.
- After P0, prove the Claude-only demo path with no Mongo/Ollama running.

## Definition of done
1. `/ai/*` routes are registered and listed in `/openapi.json`.
2. With only `ANTHROPIC_API_KEY` set, NL → scenario → full-path run → AI analysis works
   end to end from the copilot page.
3. "Certify this SUT" produces a downloadable PASS/FAIL certification report.
4. A failing scenario yields an inline AI explanation.
5. CI smoke test for the AI path is green.
