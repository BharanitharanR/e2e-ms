# Changelog

All notable changes to this project are documented here.

---

## [Unreleased] — Phase 1: Origination + Multi-Network + ISO→JPF Mapping

### T1 — Network profiles (`backend/network/profiles/*.yaml` + `router.py`)
- Created four network dialect YAML profiles: `visa.yaml`, `mastercard.yaml`,
  `amex.yaml`, `discover.yaml` — each defining BIN ranges, MTI codes,
  private DE fields, and `private_de_values` templates.
- Added `backend/network/router.py` with `select_network(pan, override=None)`:
  routes by BIN prefix/range, override wins, fallback to Visa.
- **Verified:** Visa PAN → visa (private DEs 44/62/63); MC PAN → mastercard (48/61/63);
  Amex → amex (47/63); Discover → discover (62/63); override wins.

### T2 — ISO engine (`backend/network/packer.py`)
- Implemented pure-Python ISO 8583 packer/unpacker using `pyiso8583` (`iso8583` package).
- `pack(fields, network, mti, private_field_values)` → `PackResult(hex, fields, mti, network, private_des)`.
- `unpack(hex_str, network)` → `UnpackResult(fields, mti, network)`.
- Auto-populates network-private DEs from profile template with `{stan}` substitution.
- **Key fix:** `iso8583` library requires string keys throughout (not int); bitmap `"p"` and
  secondary bitmap `"1"` stripped from unpack output.
- **Verified:** All 4 networks round-trip losslessly; private DEs present; Visa ≠ MC field sets.

### T3 — Acquirer origination (`backend/network/originator.py`)
- Added `build_0100(request, network_override=None)` → `OriginationResult`.
- Stamps DE2/3/4/7/11/12/13/18/22/32/37/41/42/49; fresh STAN (6-digit) and RRN (12-char).
- **Key fix:** `_rrn()` format `%y%j%H%M%S` (11 chars) + 1 random digit = exactly 12 chars
  (ISO 8583 DE37 fixed width).
- **Verified:** Visa private DEs {44,62,63}; MC {48,61,63}; override works; round-trip clean.

### T4 — Mapping engine (`backend/mapping/engine.py` + `specs/*.yaml`)
- YAML-spec-driven ISO 8583 → JPF (JSON Payment Format) mapper.
- Four per-network spec files: `specs/visa.yaml`, `mastercard.yaml`, `amex.yaml`, `discover.yaml`.
- `map_to_jpf(iso_fields, network, icc_hex=None)` → `MappingResult(jpf, pii_safe, warnings, network)`.
- PII enforcement: PAN never stored clear; `card.pan_token` / `card.pan_last_four` / `card.pan_hash` stored.
- Minimal BER-TLV parser for DE55 EMV data; validation rules flag DE4↔9F02 / DE49↔5F2A mismatches.
- **Verified:** Visa JPF == MC JPF (ignoring network-private blocks, STAN/RRN, PAN fields);
  mismatch in 9F02 correctly flagged as warning.

### T5 — Wire into existing path (`backend/main.py`)
- Added imports for `build_0100` and `map_to_jpf` with graceful fallback (`_ISO_AVAILABLE`).
- Extended `_execute_scenario_internal(scenario, unique, network_override)`:
  builds ISO 8583 in-process alongside the HTTP path to the acquirer microservice.
- `/execute/{scenario_id}` response now includes `iso_message`, `jpf`, `iso_warnings`.
- `/execute/{scenario_id}?network=mastercard` forces network override via query param.
- **Verified:** iso_message present with correct private_des; full PAN absent from jpf;
  network_override flows through correctly.

### T6 — Vertical-slice pytest (`tests/test_vertical_slice.py`)
- 26 parametrized tests covering 3 scenario rows (grocery/electronics/e-commerce).
- `TestIsoNetworkDialects`: Visa {44,62,63} ≠ MC {48,61,63}; field key sets differ.
- `TestJpfDialectAgnostic`: JPF identical across networks; private blocks differ; PII safe.
- `TestSutDecision`: RC and decision match expectation for Visa and Mastercard.
- `TestStanRrnUniqueness`: STAN diverse (≥15 unique in 20 draws); RRN exactly 12 chars.
- `TestMismatchFlagging`: 9F02 mismatch flagged; clean message has zero warnings.
- Added `.github/workflows/vertical_slice.yml` GitHub Actions CI workflow.
- **Result:** 26/26 PASSED (no Docker required — acquirer HTTP call is mocked).

### T7 — UI: network selector (`frontend/pages/02_scenario_lab.py`)
- Added **Network** sidebar selector: "(auto — BIN routing)" / Visa / Mastercard / Amex / Discover.
- Run button passes `?network=<override>` to `/execute/{scenario_id}` when a network is chosen.
- After each run, renders **ISO 8583 ↔ JPF contrast panel**:
  - Left column: DE table with private DEs highlighted in amber (★).
  - Right column: JPF canonical JSON viewer + packed hex expander.
  - Network badge shows active dialect; MTI / STAN / RRN shown inline.
  - EMV validation warnings surfaced as `st.warning()` banners.

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

---

## [Unreleased] — Phase 3: Authoritative Network Routing, Transaction Builder, Settlement, Interchange & jPOS Sidecar

### T0.2 — Authoritative network routing on the live HTTP path
- **Problem:** Network was resolved for display/audit only; the live POST to the acquirer
  microservice carried `network: "unknown"` in the body, so the issuer ledger and
  `pgfs.*` webhook events were tagged incorrectly.
- **Fix:** In `_execute_scenario_internal`, resolved network is now stamped onto
  `request_dict["network"] = resolved_network` **before** the live POST. Since
  `acquirer.py` → `visa.py` are thin pass-throughs, the field propagates transparently to
  `marqeta_simulator`, which already reads `body.get("network", "unknown")` when creating
  ledger entries.
- **payload_templates.py:** Changed hardcoded `"network": "VISANET"` to a dynamic lookup:
  `{"visa": "VISANET", "mastercard": "BANKNET", "amex": "AMSNET", "discover": "PULSE"}`.
- **Tests:** `TestNetworkStamping` — 6 tests: Visa/Amex/MC/Discover BIN routing, override
  wins, ISO network field == `request_dict["network"]` after the combined block.

### T0.3 — Per-network test card presets (correct PANs + Luhn-valid)
- Added `_TEST_CARD_PRESETS` dict in `backend/main.py`:
  - Visa: `4111111111111111` (16 digits, starts with 4)
  - Mastercard: `5555555555554444` (16 digits, starts with 5)
  - **Amex: `378282246310005` (15 digits, starts with 37)**
  - Discover: `6011111111111117` (16 digits, starts with 6011)
- Added `_detect_network_from_pan()` lightweight BIN detector (no YAML load).
- Added `_luhn_check()` standard Luhn-10 validator.
- Added `_LUHN_EXEMPT_PRESETS` set for test PANs that are already known-valid.
- `GET /network/test_cards` endpoint returns all four presets.
- **Tests:** `TestAdhocValidation` — 9 tests: Luhn valid/invalid, BIN detection × 4 networks,
  preset coverage, Amex 15-digit, `test_amex_preset_starts_with_37`, all presets Luhn-valid.

### T0.4 — Demo-mode JIT node: audit step 6 always populated
- **Problem:** In demo mode the JIT node (step 6 of the audit trail) was empty when
  `customer_response_body` was `None` or missing keys, causing blank animations.
- **Fix:** `_execute_scenario_internal` now builds `jit_decision_payload` dict that is
  **always** populated with `decision`, `rc`, `network`, `jit_method`, `event_type`,
  `transaction_id`, `amount`, `currency` before any merge with `customer_response_body`.
  `customer_body` is merged on top if it is a non-empty dict.
- **Tests:** `TestJitAuditStep` — 3 tests: step 6 populated, contains decision, contains rc.

### T0.1 — Transaction Builder page (`POST /execute_adhoc` + `frontend/pages/10_transaction_builder.py`)
- **New endpoint `POST /execute_adhoc`:** Builds a scenario on-the-fly from a flexible
  body (PAN, network, amount, currency, MCC, entry mode, merchant details). Validates PAN
  (Luhn, length, BIN-network consistency) and returns a full trace including
  `adhoc_warnings`.
- **Entry mode aliases:** `chip`, `contactless`, `swipe`, `manual`, `ecom` accepted and
  translated to ISO numeric codes (`051`, `071`, `011`, `010`, `810`).
- **New page `frontend/pages/10_transaction_builder.py`:**
  - Per-network preset sidebar (Visa/MC/Amex/Discover with correct test PANs).
  - Full form: PAN, network selector, amount, currency, MCC (with labels), merchant name,
    POS entry mode (with labels), expected RC/decision.
  - ISO 8583 ↔ JPF contrast panel with per-network colour badge.
  - Full audit trail expander + demo mode playback.
  - Test card reference table in expander.

### T1.1 — Clearing & settlement file generation + validation (`backend/settlement.py`)
- `generate_settlement_file(network_filter, currency_filter)` — pulls CLEARED ledger
  entries from `marqeta_simulator`, emits header / per-record / trailer structure with
  `hash_total` (sum of last 6 digits of each transaction_id).
- `validate_settlement_file(file_dict)` — 9 validation checks (V01–V09):
  - V01: `header.record_count` vs actual record count
  - V02: `header.gross_amount` vs sum of `cleared_amount` fields
  - V03/V04: trailer matches header counts and amounts
  - V05: `trailer.hash_total` recomputed and compared
  - V06: per-record `cleared_amount ≤ original_amount`
  - V07: per-record `state == CLEARED`
  - V08: per-record currency matches header currency
  - V09: live ledger cross-reference (amount reconciliation)
- FastAPI router mounted at `POST /settlement/generate` and `POST /settlement/validate`.
- **Tests:** `TestSettlementGeneration` — 7 tests: schema, record count, gross amount
  reconciliation, CLEARED-only filter, validation pass, V01 mismatch, V06 over-clearing.

### T1.2 — DB validation: data-at-rest vs data-in-motion (`GET /validate/db/{transaction_id}`)
- New endpoint in `backend/main.py` cross-references the SQLite DB record (data-at-rest)
  against the live marqeta_simulator in-memory ledger (data-in-motion).
- Drift checks: `AMOUNT_DRIFT`, `CURRENCY_DRIFT`, `NETWORK_DRIFT`.
- Returns a structured report: `{transaction_id, db_record, ledger_record, drifts, valid}`.

### T1.3 — Interchange / qualification engine (`backend/interchange.py`)
- Representative rate tables for Visa, Mastercard, Amex, Discover with tier entries
  (`tier`, `rate_pct`, `fixed_cents`, `applies_when`).
- `qualify(network, pos_entry_mode, mcc, amount_cents, card_type)` resolves tier from:
  - Durbin-regulated debit → Regulated Debit (0.05% + $0.21)
  - E-commerce (810) → Electronic
  - Manual (010) → Standard (highest rate)
  - MCC 5812 + contactless → CPS/Restaurant
  - MCC 5411/5412 + chip/contactless → CPS/Supermarket
  - chip/contactless/mag → CPS/Retail
- FastAPI router at `POST /interchange/qualify` and `GET /interchange/rate_table`.
- **Tests:** `TestInterchangeQualification` — 9 tests: contactless < manual rate,
  supermarket tier, restaurant tier, Amex standard, fee calculation (MCC 5999 = CPS/Retail
  @ 1.51% + $0.10 = 161¢ on $100), regulated debit, Discover ecom, rate table coverage.

### T2.1 — jPOS sidecar for byte-authentic ISO 8583 packing (`iso-engine/`)
- Full Maven project (`iso-engine/pom.xml`): Java 17, jPOS 2.1.9, embedded Jetty 11,
  Jackson 2.17.1, Logback.
- `IsoEngineServer.java`: embedded Jetty on port 8200 (via `ISO_ENGINE_PORT` env var).
  - `GET /health` — liveness probe.
  - `POST /pack` — accepts `{network, mti, fields}`, packs via `GenericPackager`,
    returns `{hex, network, mti, length}`.
  - `POST /unpack` — accepts `{network, hex}`, unpacks, returns `{mti, fields, network}`.
  - Per-network packager cache (`ConcurrentHashMap`); falls back to `generic.xml` if
    network-specific spec absent.
- Packager XML specs: `generic.xml`, `visa.xml`, `mastercard.xml`, `amex.xml`,
  `discover.xml` (DE 0–128, standard IsoPackager format).
- Two-stage `Dockerfile`: `maven:3.9-eclipse-temurin-17` build → `eclipse-temurin:17-jre-alpine` runtime.
- `backend/network/jpos_bridge.py`: Python bridge with graceful degradation — returns
  `None` (not error) when `ISO_ENGINE_URL` env var is absent; callers fall back to the
  existing Python packer transparently.
- Proxy endpoints in `main.py`: `/iso-engine/health`, `/iso-engine/pack`, `/iso-engine/unpack`.
- **Tests:** `TestJposBridge` — 3 tests: pack/unpack/health return `None`/unavailable when
  `ISO_ENGINE_URL` is not set.

### T2.3 — CI extension (`tests/test_phase3.py` + `.github/workflows/phase3_ci.yml`)
- **`tests/test_phase3.py`** — 40 tests across 7 test classes:
  - `TestNetworkStamping` (6): T0.2 authoritative routing
  - `TestJitAuditStep` (3): T0.4 JIT payload always populated
  - `TestAdhocValidation` (9): T0.1/T0.3 PAN validation + presets
  - `TestSettlementGeneration` (7): T1.1 settlement engine
  - `TestInterchangeQualification` (9): T1.3 interchange tiers + fees
  - `TestJposBridge` (3): T2.1 graceful degradation
  - `TestPanGuard` (2): network field ≠ PAN; no track-2 in request body
- **`.github/workflows/phase3_ci.yml`** — GitHub Actions CI workflow running all three
  test suites (`test_vertical_slice.py`, `test_lifecycle.py`, `test_phase3.py`) on every
  push/PR touching backend, tests, or the iso-engine.
- **Total across all suites: 78 tests, 0 failures** (Python 3.14, no Docker required).

---

## [Unreleased] — Phase 5: Local Demo, AI Config, Dynamic Network Labels, Enrichment Trace, Mandate AI

### T0.3 — Genericize hardcoded "Visa" labels in demo/audit trail
- **Problem:** `demo_mode.py` hardcoded `("🌐", "Visa")` at node index 3; audit trail steps
  used `"Visa Network"` literally — wrong for MC/Amex/Discover transactions.
- **Fix:**
  - `frontend/utils/demo_mode.py` completely reworked: `_BASE_NODES[3] = None` (sentinel);
    `_build_nodes(network)` fills node 3 dynamically at render time with the resolved
    network name, emoji and colour from `_NETWORK_EMOJI` / `_NETWORK_COLOURS` dicts.
  - `render_node_diagram(active, network)` and `render_playback_step(..., network)` both
    accept a `network` parameter — no Visa assumption.
  - `backend/main.py` audit trail steps 3, 4, 7 use `f"{resolved_network.capitalize()} Network"`.
  - `frontend/pages/02_scenario_lab.py` extracts `_demo_network` from the trace and passes
    it to both render functions.
- **Networks supported:** visa 🔵 `#1a1f71`, mastercard 🔴 `#eb001b`, amex 🔷 `#007bc1`,
  discover 🟠 `#f76f20`.
- **Tests:** `TestDynamicNetworkLabels` — 8 tests: node diagram colour per network,
  `_BASE_NODES[3] is None`, audit step actor labels, 4-network coverage.

### T0.1 — Local run profile: `start-local.sh` + `make demo-local`
- **Problem:** Running the simulator without Docker failed because inter-service URLs were
  Docker service names (`acquirer`, `visa-net`, etc.) that don't resolve on localhost.
- **Fix:**
  - Created `start-local.sh`: exports all inter-service env vars as `http://127.0.0.1:<port>`,
    launches all 6 microservices as background `uvicorn` processes on ports 8001/8101/8102/8103/8000/8501,
    health-checks each (up to 30 polls), runs a smoke test against `/execute/authorization_approve`,
    then launches Streamlit. `trap cleanup EXIT INT TERM` kills all PIDs on Ctrl-C.
  - Added `make demo-local` target in `Makefile`.
  - Added "Option A — No Docker (host quickstart)" to `README.md` before Docker section.
- **Tests:** `TestLocalRunProfile` — 4 tests: `start-local.sh` exists + is executable,
  `Makefile` has `demo-local`, `README.md` has quickstart section.

### T0.2 — In-app AI provider & key settings (UI + backend endpoints)
- **Problem:** API keys could only be injected via Docker env vars; no way to set or
  rotate keys without restarting the stack; no UI to switch primary LLM provider.
- **Fix:**
  - **`backend/ai_config.py`** (new): Fernet (AES-GCM) encrypted key store in
    `~/.paycon/secrets` with `0o600` permissions. Machine-scoped Fernet key derived from
    `sha256(uid + home)`. Public API: `set_api_key()`, `get_api_key()` (env first →
    secrets), `get_key_status()` returns only `"detected"/"not detected"` — raw key
    never returned. `provider_status()` strips key values from output. `get_active_provider_key()`
    walks the fallback chain.
  - **`backend/ai_routes.py`** (modified): `_call_claude()` reads key from `ai_config`
    first, then env var. Added 4 new endpoints: `GET /ai/providers`,
    `POST /ai/providers/config`, `POST /ai/providers/key`, `DELETE /ai/providers/key/{provider}`.
  - **`frontend/pages/11_ai_settings.py`** (new): Provider chain config (primary +
    ordered fallbacks), model/endpoint editable table with Key column colour-coded
    (green=detected / red=not detected). Key management form uses `type="password"`;
    `del entered_key` immediately after POST; key never assigned to session state.
    Test-provider button. Security notes section.
- **Security guarantee:** Key lookup order is env var → encrypted store → `None`. Raw
  key values are never logged, never stored in session state, never echoed back in any API response.
- **Tests:** `TestAiConfig` — 8 tests: provider_status no raw keys, key_status for
  nonexistent, load_config returns dict, supported providers present, set/get/delete
  roundtrip, short key rejected, `_call_claude` reads from config.
  `TestAiProviderEndpoints` — 3 tests: `/ai/providers` returns 200, no raw keys in
  response body, empty key body returns 400.

### T1.1 — Per-hop enrichment trace backend (`enrichment_trace` in `/execute` response)
- **Problem:** The audit trail showed hop actors but not *which ISO DEs* were added at
  each hop — making it impossible to explain to customers exactly how a field like DE55
  (EMV data) travelled through the network stack.
- **Fix:** Created `backend/enrichment.py` with `build_enrichment_trace()`:
  - 5 ordered hops: Terminal, Acquirer, Network, Issuer Processor, Customer JIT.
  - Each hop returns `{actor, adds: [{de, name, value}], cumulative_iso}`.
  - Hop-specific extras: Network adds `interchange_qualification`; Issuer Processor adds
    `iso_to_jpf` (JPF field mapping + DB field list) and `jpf_to_jit` (webhook shape);
    Customer JIT adds `decision` and `rc`.
  - `_DE_NAMES` covers 30 standard DE labels; `_PRIVATE_DE_MAP` lists per-network
    private DEs (visa: 44/62/63, mastercard: 48/61/63, amex: 47/63, discover: 62/63).
  - Graceful degradation: `from backend.interchange import qualify` wrapped in `try/except`.
  - `backend/main.py` imports `build_enrichment_trace` with `_ENRICHMENT_AVAILABLE` flag;
    result added as `"enrichment_trace"` key in the trace dict response.
- **Tests:** `TestEnrichmentTrace` — 9 tests: trace present in execute response, 5 hops,
  correct actors, Terminal adds STAN, Acquirer adds merchant, Network has network field,
  Issuer has `iso_to_jpf`, JIT has decision, Visa vs MC traces differ.

### T1.2 — Horizontal enrichment visualization (`frontend/pages/12_enrichment_trace.py`)
- New page showing the per-hop DE addition strip:
  - Scenario selector at top + "Run new scenario" button.
  - Horizontal `st.columns` strip — one column per hop, each with a coloured header.
  - `_render_adds_table(adds)` shows field badges per hop inline.
  - Per-hop expandable drill-down with 5 sub-tabs: Cumulative ISO (DataFrame), Interchange
    (metrics), ISO→JPF (JSON + DB column table), JPF→JIT (JSON), JIT Decision (success/error).
  - Use-case preset buttons at bottom: AUTH / ATM / PRE-AUTH run and navigate to the trace.

### T1.3 — Use-case presets: AUTH / PRE-AUTH / ATM scenarios
- Three new scenario JSON files in `backend/scenarios/`:
  - **`atm_withdrawal_approve.json`** — ATM cash $20, MCC `6011`, `pos_entry_mode: "011"`.
  - **`preauth_approve.json`** — Hotel pre-authorization $150, MCC `7011`, POS `071`.
  - **`preauth_completion.json`** — Clearing advice $145, `event_type: "advice"`,
    `original_transaction_id: "TXN_PREAUTH_001"` — demonstrates ledger link + partial completion.
- **Tests:** `TestUseCasePresets` — 8 tests: files exist, ATM MCC is `6011`, ATM
  entry mode is `011`, preauth MCC is `7011`, preauth entry mode is `071`, completion is
  `advice` event type, completion links to `TXN_PREAUTH_001`, completion amount < preauth.

### T2.1 — `POST /ai/mandate` — AI-driven mandate analysis endpoint
- **Problem:** No structured way to translate a plain-English network mandate document
  into concrete ISO field additions, JPF schema changes, and DB migrations.
- **Fix:** New `POST /ai/mandate` endpoint in `backend/ai_routes.py`:
  - Sends mandate text + network to Claude with `_MANDATE_SYSTEM` prompt.
  - Claude returns structured JSON: `{design_summary, iso_mapping_additions,
    jpf_fields, db_columns, scenarios, validation_notes}`.
  - `_validate_mandate_proposal()` enforces guardrails: DE numbers 1-128 only; test BINs
    only (`4111`, `5555`, `3782`, `6011`, `4000`, `5200`); valid JPF types
    (`STRING`, `INTEGER`, `BOOLEAN`, `DECIMAL`, `DATETIME`, `BYTES`); valid DB column
    types; `canonical` field not missing from ISO additions.
  - Returns proposal + `validation_errors` list; caller decides whether to proceed.

### T2.2 — Mandate UI workflow (`frontend/pages/07_ai_copilot.py`, tab 5)
- Fifth tab "📋 Mandate → Impl" added to AI Copilot page:
  - Paste mandate text + select network → Analyze button → `POST /ai/mandate`.
  - Proposal stored in `st.session_state.mandate_proposal`.
  - Review panel: validation errors shown in red warning blocks. Design summary, ISO
    Additions table (with YAML diff expander), JPF Fields, DB Columns, Test Scenarios
    across 4 sub-tabs.
  - Apply gate: validation errors shown as blocking banner, OR user checks
    "I have reviewed the proposal and confirm it is correct" → Apply button →
    `POST /ai/mandate/apply` with `confirmed=True`.
  - Certify step: runs each scenario ID from the proposal via `/execute/{sc_id}`,
    shows pass/fail result table.
- `frontend/utils/session_state.py` — added `mandate_proposal`, `mandate_network`,
  `ai_last_run` defaults.

### T2.3 — Review gate & guardrails (`POST /ai/mandate/apply`)
- `POST /ai/mandate/apply` in `backend/ai_routes.py`:
  - Rejects with HTTP 400 if `confirmed != true` (review gate).
  - Re-runs `_validate_mandate_proposal()` and rejects with HTTP 422 if any errors remain
    (double-lock: client-side check + server-side re-validation).
  - On success: appends YAML block to `backend/mapping/specs/<network>.yaml` (auditable
    append, never overwrite). Saves each generated scenario via `mongo_repository.save_scenario()`.
  - Returns `{status: "applied", scenarios_saved: [...], spec_file: "..."}`.
- **Tests:** `TestMandateGuardrails` — 9 tests: valid proposal passes validation, bad DE
  number fails, non-integer DE fails, non-test PAN fails, test PAN passes, bad JPF type
  fails, missing canonical fails, `apply` without `confirmed=True` → HTTP 400, `apply`
  with invalid proposal → HTTP 422.

### Phase 5 test suite (`tests/test_phase5.py`)
- **49 tests across 7 test classes** — all pass with no Docker required:
  - `TestDynamicNetworkLabels` (8): T0.3 dynamic network colour/emoji/label
  - `TestLocalRunProfile` (4): T0.1 start-local.sh, Makefile, README
  - `TestAiConfig` (8): T0.2 encrypted key store safety + provider chain
  - `TestEnrichmentTrace` (9): T1.1 hop structure, actors, field additions
  - `TestUseCasePresets` (8): T1.3 ATM / pre-auth / clearing scenario files
  - `TestMandateGuardrails` (9): T2.1–T2.3 guardrails + review gate
  - `TestAiProviderEndpoints` (3): T0.2 `/ai/providers` endpoint contract
- **Total across all suites: 127 tests, 0 failures** (Python 3.14, no Docker required).

---

## [Unreleased] — UI Enrichment (Five-Lens): Paycon Brand, Mission-Control Home, Caching, Error Boundaries

### T2.1 — Shared Paycon design system
- Created `.streamlit/config.toml`: dark base, primary teal `#1fb7ac`, bg navy `#0a1730`,
  text `#eaf1ff` — applied app-wide by Streamlit's theme engine automatically.
- Created `frontend/utils/theme.py: inject_theme()`: single CSS block holding cards
  (`.pc-card`), chips (`.pc-chip-*`), brand bar (`.pc-brand-bar`), provider badge
  (`.pc-provider-badge`), history rows (`.pc-hist-row`), gauge bar (`.pc-gauge-bar`),
  mono snippet (`.pc-mono`), hop header (`.pc-hop-header`).
- `inject_theme()` called once per page in `set_page_config` position — no per-page
  `<style>` blocks remain.
- Helper functions: `provider_badge_html(provider, detected)` → HTML badge;
  `chip(text, colour)` → inline coloured chip.
- **Acceptance:** `grep -rn "<style" frontend/pages` → 0 hits (all CSS in theme.py).

### T2.2 — Rebrand the shell
- `app.py`: `page_title` → "Paycon e2ePS — End-to-End Payment Simulator", `page_icon` → 🏦.
  Markdown nav table removed; sidebar owns navigation.
  Brand header uses `.pc-brand-bar` / `.pc-brand-name` classes.
- All 12 pages: `page_title` updated from "e2MS — …" to "Paycon e2ePS — …".
- **Acceptance:** no "e2MS", "Marqeta E2E", "JPBOS", or "JCF" strings remain in any page
  title, subheader, or caption.

### T2.3 — Mission-Control home (`01_home.py`)
- Row 1: service health cards (`/health/all`) + AI provider badge (`/ai/providers`) side by side.
- Row 2: all-time stats (6 metrics) + certification readiness gauge (last `cert_result`
  from session state, with teal/red gradient bar).
- Row 3: 5 use-case preset buttons (AUTH Approve / AUTH Decline / ATM / Pre-Auth / Reversal)
  + freeform scenario selectbox. Both paths show pass/fail inline without leaving the page.
- Row 4: recent runs rendered as `pc-hist-row` cards with network chip, RC mono badge,
  decision, latency, timestamp. Empty state guides users to Quick-Run presets.
- **Acceptance:** home shows live health + provider badge + last cert score + recent runs
  and reaches a scored run in ≤2 clicks.

### T3.1 — Cache read-only API calls (`utils/api_client.py`)
- Added `@st.cache_data(ttl=...)` cached helpers: `get_scenarios(ttl=30s)`,
  `get_suites(ttl=30s)`, `get_environments(ttl=15s)`, `get_test_cards(ttl=60s)`,
  `get_analytics_summary(ttl=10s)`.
- Navigating between pages no longer re-fetches static lists on every rerun.
- **Acceptance:** backend logs show scenario list fetched at most once per 30 s.

### T3.3 — Terminology + dead-code sweep
- Mass-replaced across all `frontend/` Python files:
  - `JPBOS` → `JPOS`
  - `JCF` → `JPF` (field names, column configs, expander titles, function names,
    `DEFAULT_ISO_JCF_MAPPING` → `DEFAULT_ISO_JPF_MAPPING`, `iso_jcf_mapping` →
    `iso_jpf_mapping`, `jcf_field` → `jpf_field`)
  - `e2MS` → `e2ePS` / `Paycon e2ePS`
  - `Marqeta E2E Simulator` → `Paycon e2ePS`
  - `Marqeta JIT` → `Paycon JIT`
- `frontend/utils/html_report.py`: certification report header updated to
  "Pay**con** · e2ePS" with teal accent; colour palette updated from navy `#1a1f71`
  to Paycon navy `#0a1730` + teal `#1fb7ac`; footer → "Generated by Paycon e2ePS".
- **Acceptance:** `grep -rn "JPBOS\|JCF\b\|e2MS\b\|Marqeta E2E" frontend/` → 0 matches.

### T4.2 — Certification report with Paycon branding
- `build_certification_report()` in `html_report.py`:
  - Brand header: "Pay**con** · e2ePS" with teal `#1fb7ac` accent.
  - Verdict bar: teal-to-green gradient for CERTIFIED; red-to-amber for NOT CERTIFIED.
  - Scorecard numbers: `#0a1730` dark navy.
  - Table header: `#0a1730`; lifecycle pills: teal-tinted `#d4f5ef / #0a6b5e`;
    RC pills: `#d0eaff / #0040a0`.
  - Footer: "Generated by Paycon e2ePS".
- **Acceptance:** one-click download from Certification page yields a branded Paycon report.

### T4.3 — Empty states + AI provider badge
- Every list surface (`01_home.py` scenarios / history / analytics) has a `.pc-card`
  empty state with a next-step CTA guiding the user to their first action.
- AI provider badge (`pc-provider-badge`) in sidebar of `07_ai_copilot.py` and
  `12_enrichment_trace.py` — green teal when a key is detected, red when not.
- **Acceptance:** empty pages guide the next action; badge visible on all AI surfaces.

### T5.2 — Error boundaries + friendly messages
- `api_get()` and `api_post()` now distinguish `ConnectionError` (backend down →
  CTA to start stack), `HTTPError` (structured error message), `Timeout` (warning with
  duration), and generic `Exception` (safe fallback). Never a raw stack trace.
- Spinner (`with st.spinner(…)`) wraps every network call on Home and all preset buttons.
- **Acceptance:** stopping the backend shows a friendly "Cannot reach backend" message
  with a start-stack hint on every page, not a Python traceback.

### WS-1 — Idea backlog
- Created `docs/ui_backlog.md` listing all shipped ideas vs parked ideas with one-line
  rationale for each parking decision.
- **Parked (post-PMF):** Cmd-K palette, audit scrubber, always-on AI dock, mandate diff
  viewer, run permalinks, card-tap animation, React migration, page-count collapse,
  first-run wizard, analytics pagination.

---

## [Unreleased] — Improvements Sprint: Critical Fixes + ISO Mapper Workbench + TCP/IP Channel + jPOS Wiring + AI Backlog

### P0-F1 — Fixed: ISO Mapper crash (`AttributeError: iso_jpf_mapping`)
- **Root cause:** `frontend/utils/session_state.py` initialized `"iso_jcf_mapping": None`
  (old JCF terminology retained after the T3.3 sweep); every page that reads
  `st.session_state.iso_jpf_mapping` raised `AttributeError` on first access.
- **Fix:** Changed key from `"iso_jcf_mapping"` to `"iso_jpf_mapping"` in `session_state.py`.
- **Collateral fix:** Column variable names in `04_iso_mapper.py` renamed
  `col_jpbos` → `col_jpos`, `col_jcf` → `col_jpf`.

### P0-F2 — Fixed: sidebar showed raw lowercase filenames ("app", "01_home", etc.)
- **Root cause:** `frontend/app.py` relied on Streamlit auto-discovery which uses the
  filename (minus extension) as the label.
- **Fix:** Replaced auto-discovery with explicit `st.navigation()` call using 4 grouped,
  icon-labeled sections:
  - **🏠 Run:** Home · Transaction Builder · Suite Runner · Certification
  - **🔭 Inspect:** Scenario Lab · ISO Mapper · Terminal Emulator · Analytics · Enrichment Trace
  - **⚙️ Configure:** Sandbox Config
  - **🤖 AI:** AI Copilot · AI Settings
- Sidebar now shows clean group headings and page titles with emoji icons.

### P0-F3 — Improved: Service Health graceful degradation
- **Problem:** Health panel showed a single ORCHESTRATOR ✗ UNREACHABLE card with no
  guidance when the backend was down; operators had no actionable next step.
- **Fix in `frontend/pages/01_home.py`:**
  - Each service rendered as its own colour-coded status card (✅ OK / ⚠️ DEGRADED / ❌ UNREACHABLE).
  - Unreachable cards display the exact URL that was tried.
  - Collapsible "🛠 Troubleshooting" expander lists startup commands and the Config page
    link when any service is unreachable.
  - If the `/health/all` call itself fails, a warning banner with CTA is shown instead.

### P1-T1.1+T1.2+T1.3 — ISO Mapper: network-aware DE workbench + simulate-from-wire
- **Full rewrite of `frontend/pages/04_iso_mapper.py`** into 4 tabs:

  **🔧 DE Workbench (T1.1 + T1.2):**
  - Network selector drives the private DE set:
    Visa (DE44/62/63), Mastercard (DE48/61/63), Amex (DE47/63), Discover (DE62/63).
  - Pre-filled `_DEFAULT_WIRE_VALUES` dict per network with realistic test values.
  - 3-column layout: (1) wire value inputs → (2) JPOS canonical packing → (3) JPF mapped output.
  - Inline EMV validation: DE4 vs 9F02 (amount mismatch) and DE49 vs 5F2A (currency mismatch)
    flagged as `st.warning()` banners below the translate output.
  - "Reset wire values" button restores network defaults.

  **🔭 Live Translation (T1.3):**
  - "▶ Simulate from the Wire" button posts to `POST /execute_adhoc` with current wire values.
  - Returns full trace (RC, decision, audit trail, ISO/JPF artefacts) inline.
  - Stores trace to `st.session_state.last_trace` for Demo Mode or Enrichment Trace navigation.

  **📐 Mapping Table:**
  - Editable `st.data_editor` for the full DE→JPF mapping (session-scoped, reset to defaults).

  **📖 DE Reference:**
  - Searchable table of ISO 8583 data element names, types, and formats.
  - Inline "Explain this DE" AI Copilot button for non-ISO experts.

### P2-T2.1+T2.2 — TCP/IP ISO 8583 channel (`backend/network/tcp_channel.py`)
- **New file `backend/network/tcp_channel.py`:**
  - MLI (Message Length Indicator) encoder/decoder: 2E, 2I, 4E, 4I modes.
  - `send_iso_tcp(host, port, packed_hex, mli_mode, tls, ...)` — synchronous wrapper
    around `asyncio.open_connection()` with configurable connect/read timeouts.
  - TLS support via `ssl.SSLContext` (optional PEM CA cert injection).
  - `_build_net_mgmt_hex(de70, stan)` — builds a bare-bones 0800 ISO message with DE70.
  - `sign_on(...)`, `echo(...)`, `sign_off(...)` — network management helpers for MTI 0800/0810.
- **New endpoint `POST /iso-engine/send-tcp`** in `backend/main.py`:
  - Tries jPOS sidecar `POST /send-tcp` first (if `ISO_ENGINE_URL` is set).
  - Falls back to Python `send_iso_tcp()` transparently.
  - Returns `{hex_sent, hex_received, mti_response, fields_response, duration_ms, engine}`.
- **New endpoint `POST /iso-engine/net-mgmt`** in `backend/main.py`:
  - Accepts `{action: "sign_on"|"echo"|"sign_off", host, port, ...}` body.
  - Delegates to the appropriate `tcp_channel` function.

### P2-T2.3+T2.4 — Pluggable SUT transport + auth gate (`frontend/pages/06_sandbox_config.py`)
- **New "🔌 ISO TCP Channel" tab** (5th tab in Sandbox Config):
  - **Authorization gate:** operator must check "I confirm this is a test/sandbox environment
    — direct ISO TCP connections are NOT for production" before the panel unlocks.
    Gate state is stored in `st.session_state.iso_tcp_confirmed` (not persisted across sessions).
  - **SUT transport type:** radio selector `http` vs `iso_tcp`; both persisted to session state.
  - **TCP connection parameters:** host, port, MLI mode (2E/2I/4E/4I), TLS toggle,
    connect + read timeouts.
  - **Network Management sub-panel:** Sign-On (DE70=001), Echo Test (DE70=301),
    Sign-Off (DE70=002) buttons with inline response display (SW, fields, latency).
  - **Manual ISO send:** textarea accepting raw packed hex; posts to `/iso-engine/send-tcp`.

### P3-T3.1 — jPOS sidecar wired into originator live path
- **Modified `backend/network/originator.py`:**
  - `_pack_via_jpos` imported at module level from `jpos_bridge` (graceful import fallback
    to `None` when `jpos_bridge` is unavailable).
  - `build_0100()`: attempts jPOS pack first; falls back to Python `pack()` if jPOS returns `None`.
  - `build_linked()`: same jPOS-first pattern for advice/reversal/refund messages.
  - **Uniqueness fix:** `_rrn()` now uses a thread-safe monotonic counter (`_rrn_counter`,
    protected by `threading.Lock`) for the last digit instead of `random.randint`. This
    guarantees distinct RRNs even when `build_0100` and `build_linked` are called in the
    same UTC second (fixes `test_reversal_has_fresh_stan_and_rrn` flaky failure).

### P3-T3.2 — jPOS health badge on Home + `make iso-engine` Makefile target
- **`frontend/pages/01_home.py`:** Added "🔧 ISO Engine" section under the AI Provider column:
  - Calls `GET /iso-engine/health` to probe the jPOS sidecar.
  - Shows teal `☕ jPOS (Java) vX.X` badge when sidecar is available.
  - Shows grey `🐍 pyiso8583 (fallback)` badge with `make iso-engine` hint when not.
- **`Makefile`:** Added `make iso-engine` target:
  - Detects Maven (`iso-engine/pom.xml`) → runs `mvn package -DskipTests && java -jar ...`.
  - Detects Gradle (`iso-engine/build.gradle`) → runs `./gradlew run`.
  - Prints "Python pyiso8583 packer used as fallback" if neither is found.
  - Added to `make help` output.

### P4 — AI feature backlog (`docs/ai_backlog.md`)
- Created `docs/ai_backlog.md` documenting the full AI feature landscape:
  - **Shipped (6 features):** AI Mandate → Scenario generation, "Explain this DE" copilot,
    AI anomaly detection, AI test-data synthesis, AI provider badge, multi-provider support.
  - **Parked post-PMF (8 features):** Conversational AI chat dock, audit-trail replay scrubber,
    canonical-model diff timeline, AI-powered RC root-cause advisor, natural-language history query,
    AI mandate compliance checker, auto-generated certification narrative, federated learning baseline.
  - Includes mandate-to-implementation flow diagram, provider priority order (Claude → GPT-4o → Ollama),
    and security/PII guarantees (PAN never in prompts, keys AES-GCM encrypted).

### Test results
- **127/127 tests pass** across all suites (`test_lifecycle.py`, `test_phase3.py`,
  `test_phase5.py`, `test_vertical_slice.py`).
- No Docker required for any test.
