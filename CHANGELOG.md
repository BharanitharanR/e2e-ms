# Changelog

All notable changes to this project are documented here.

---

## [Unreleased] — P0 AI Copilot fixes

### T0.1 — Fixed: `agent_repository.py` import break that silenced all `/ai/*` endpoints
- **Root cause:** `from backend.mongo_repository import db` — `mongo_repository.py` exports
  no `db` alias; this `ImportError` cascaded into `main.py`'s `try/except`, setting
  `ai_router = None` and silently removing every `/ai/*` route.
- **Fix:** Rewrote `agent_repository.py` to import the collection objects already exported
  by `mongo_repository.py` (`agents`, `prompts`, `guardrails`, `templates`) and query them
  by `_id` (matching the seed file key), not by the non-existent `db.agent_definitions`.
- **Verification:** `python3 -c "import backend.ai_routes"` succeeds; all 5 `/ai/*` routes
  listed in router.

### T0.2 — Fixed: `generate_with_fallback` undefined references + wired into endpoints
- **Root cause:** `ai_provider.py` referenced undefined `execute_agent` and `user_prompt`;
  instantiated an unused `OllamaClient()`; would raise `NameError` immediately.
- **Fix:** Replaced function body with correct Claude-first / Ollama-fallback logic using
  a lazy `from backend.agent_service import execute_agent` to avoid circular imports.
- **Wired in:** `/generate_scenario` and `/run_test` now call `generate_with_fallback`
  instead of calling `execute_agent` directly.
- **Verification:** `python3 -c "import backend.ai_routes"` — no `NameError`.

### T0.3 — Fixed: `_InMemoryCollection.find_one` only matched `"id"` queries
- **Root cause:** Mongo-down fallback returned `None` for any `_id` or `{}` query, so
  `get_agent()`, `get_prompt()`, and `get_template()` all returned `None`, causing the
  Ollama path to die with "No scenario template found".
- **Fix:** Extended `find_one` to match on `_id` (seed files use `_id`), `id` (scenario
  files use `id`), and `{}` (empty query returns first doc, used by `get_template()`).
  Also fixed `replace_one` key resolution order to prefer `_id`.
- **Verification:** Unit assertions pass for all three query shapes.

### T0.4 — Fixed: normalized provider return shapes
- **Root cause:** Claude system prompt returned a wrapper
  `{"scenario": {...}, "explanation": ..., "suggested_rc": ..., "jit_behavior": ...}`,
  while `execute_agent` (Ollama) returned the bare scenario dict. `/run_test` fed the
  wrapper directly to `_execute_scenario_internal`, which expected a bare dict.
- **Fix:** Introduced `_claude_scenario_fn(prompt)` that unpacks the Claude wrapper and
  attaches metadata under `_meta` (non-conflicting key). Both providers now return a
  bare scenario dict. `/run_test` strips `_meta` before execution and persists the bare
  dict. The `_meta` fields are still available to callers that need explanation text.
- **Verification:** `/run_test` response always has `{"scenario": <bare>, "execution_result": <trace>, "analysis": <dict>}`.

### T0.5 — Fixed: dead `_call_ollama` helper with wrong `OllamaClient` method
- **Root cause:** `_call_ollama` called `client.generate_scenario(prompt)` but
  `OllamaClient` only has `generate(model, prompt)` — `AttributeError` if invoked.
- **Fix:** Removed `_call_ollama` entirely. The Ollama path goes through `execute_agent`
  (in `agent_service.py`) which correctly calls `client.generate(model=..., prompt=...)`.
- **Verification:** `grep -rn "client.generate_scenario" backend/` → no matches.

### T0.6 — Fixed: Claude model hardcoded; now reads `ANTHROPIC_MODEL` env var
- **Root cause:** `_call_claude` used `model="claude-opus-4-5-20251101"` (hardcoded,
  also an invalid/stale model ID).
- **Fix:** `_call_claude` reads `os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-5")`
  at call time. Updated `docker-compose.yml` to pass
  `ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-opus-4-5}` so the default can be overridden
  via host env or `.env` file with no code changes.
- **Verification:** `inspect.getsource(_call_claude)` confirms `ANTHROPIC_MODEL` is read;
  model string `claude-opus-4-5` is a valid current Claude API model.
