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
