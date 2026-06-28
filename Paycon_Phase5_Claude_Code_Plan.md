# Paycon E2E-MS — Phase 5 Claude Code Plan
### Local demo fix, configurable AI key, ISO enrichment trace & mandate-driven AI

**Repo:** `github.com/srv2go/e2e-ms`
**Why this phase:** the product runs, but (1) the local (non-Docker) demo fails because
service URLs resolve only inside Docker, (2) there is still no UI to configure the Anthropic
key / provider, (3) the demo flow still hardcodes "Visa" despite multi-network routing, (4)
the demo doesn't show *what each hop adds to the ISO message* — the single most compelling
thing for an issuer-processor audience — and (5) the multi-network feature is static, when
its real value is **keeping pace with bi-annual network mandates** via AI-assisted
enrichment. This phase fixes the blockers and builds the two differentiating demos.

> **Kickoff prompt (paste into Claude Code):**
> "Read this plan and the existing code first. Work P0 → P2 in order; after each task run its
> Acceptance check and record it in CHANGELOG.md. The product is run **locally without
> Docker** — every task must work from a host launch. Don't change the JPF schema or the
> `pgfs.*` webhook shape unless a task says so. Key files: `backend/main.py`
> (`_execute_scenario_internal`, audit trail), `backend/network/originator.py`,
> `backend/mapping/engine.py` + `specs/*.yaml`, `backend/ai_routes.py`, `backend/ai_provider.py`,
> `frontend/utils/demo_mode.py`, `frontend/pages/02_scenario_lab.py`,
> `frontend/pages/07_ai_copilot.py`, `Makefile`/`start.sh`."

---

## P0 — Unblock the local demo & make the AI configurable

### T0.1 — Local (no-Docker) run profile
**Problem:** `_execute_scenario_internal` posts to `ACQUIRER_URL` which defaults to
`http://acquirer:8101/authorize` — a Docker service name that fails to resolve on the host
(`NameResolutionError: Failed to resolve 'acquirer'`).
**Fix:** add a `make demo-local` / `start-local.sh` target that launches each FastAPI service
on its localhost port with the upstream URLs pointed at localhost, then starts Streamlit:
```
export ACQUIRER_URL=http://localhost:8101/authorize
export CUSTOMER_JIT_URL=http://localhost:8001
export CUSTOMER_JIT_RESET_URL=http://localhost:8001/reset
export VISA_URL=http://localhost:8102/network/authorize          # read by acquirer
export MARQETA_URL=http://localhost:8103/issuer/authorize        # read by visa
# then: uvicorn each service on its port (8000/8101/8102/8103/8001) + streamlit on 8501
```
Document the local quickstart in the README (today it only documents `docker-compose up`).
**Acceptance:** on a host with no Docker, `make demo-local` brings up the stack and a demo
run completes end-to-end with no `NameResolutionError`.

### T0.2 — In-app AI provider & key settings (no env-only)
**Problem:** `_call_claude` reads `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` from env only; there is
no UI to enter or change them, and the fallback chain is hardcoded Claude→Ollama.
**Fix:**
- New `frontend/pages/10_ai_settings.py` + backend `GET /ai/providers`, `POST /ai/providers/config`.
- Let the user set **primary** (default Claude) + ordered **fallbacks** (OpenAI/Codex, Azure,
  Groq, vLLM, Ollama), each with model + base_url, and **enter/replace the API key in the UI**.
- **Key handling (security-critical):** persist keys to a local, git-ignored **encrypted
  secrets file** (e.g. `~/.paycon/secrets`, restricted perms) — never the app DB, never logs.
  Show key status as **detected / not detected (••••)**; never render the raw key. Env still
  works and takes precedence if set.
- Refactor `_call_claude` call sites to read the active provider/model/key from this config
  (in-app → env → default).
**Acceptance:** with no env var set, a user pastes an Anthropic key in the UI, the copilot
works on the next call, the key is masked, and `grep -ri <key> logs/ *.db` finds nothing.

### T0.3 — Genericize "Visa" → "Network"
**Problem:** `demo_mode._DEMO_NODES` node 3 is hardcoded `"Visa"`; `main.py` audit labels say
"Visa network" (lines ~255/261/263/297/299) even when routing resolved a different scheme.
**Fix:** replace the static "Visa" node/label with the **resolved network name** from the run
(`iso_message.network` / `router.select_network`). The node reads "Network" with the active
scheme shown (e.g. "Network · Mastercard"); audit labels interpolate the real network.
**Acceptance:** running an Amex-BIN PAN shows "Network · Amex" in the demo flow and audit
trail — never "Visa".

---

## P1 — The ISO enrichment demo (show what each hop adds)

### T1.1 — Per-hop enrichment trace (backend)
Extend the execute path to return an ordered `enrichment_trace`, where each hop lists the
data elements **it adds** plus the cumulative message:
```json
[
  {"actor":"Terminal","adds":[{"de":11,"name":"STAN"},{"de":37,"name":"RRN"},
     {"de":22,"name":"POS entry mode"},{"de":55,"name":"ICC/EMV data"}],"cumulative_iso":{...}},
  {"actor":"Acquirer","adds":[{"de":32,"name":"Acquiring institution"},{"de":42,"name":"Merchant ID"},
     {"de":43,"name":"Merchant name/location"},{"de":18,"name":"MCC"},{"de":19,"name":"Acquirer country"}]},
  {"actor":"Network","adds":[{"de":48,"name":"PDS / interchange data"},{"de":63,"name":"Network reference"},
     {"name":"interchange_qualification"}],"network":"mastercard"},
  {"actor":"Issuer Processor","iso_to_jpf":{"jpf":{...}, "db_fields":{"amount_cents":2500,"card_token":"...","stan":"..."}},
     "jpf_to_jit":{"event_type":"transaction.authorization","jit_funding":{"method":"pgfs.authorization",...}}},
  {"actor":"Customer JIT","decision":"APPROVED","rc":"00"}
]
```
Source the `adds` from the real `originator`/profile + `map_to_jpf` output so the trace is
truthful per network (reflects the simulator's layering).
**Acceptance:** `/execute` returns an `enrichment_trace`; the Network hop's `adds` differ
between Visa and Mastercard; the Issuer hop shows the JPF canonical, the DB field names, and
the JIT JSON.

### T1.2 — Horizontal enrichment visualization (frontend)
Render the trace left-to-right: **Acquirer → Network → Issuer Processor → Customer JIT**, each
hop a column showing "+ fields added here", with the cumulative ISO in a drill-down. The
Issuer column shows the **ISO → JPF (canonical + DB column names) → JIT JSON** transformation
explicitly — the "one canonical format becomes the JIT JSON" story. Reuse the horizontal node
component from `demo_mode.py`.
**Acceptance:** the demo reads horizontally; clicking the Network hop shows interchange/private
DEs added; clicking Issuer shows ISO→JPF→JIT JSON side by side.

### T1.3 — Use-case presets
Wire three demo presets using existing suites and correct processing codes:
- **AUTH (purchase)** — `suite_purchase_approve`, DE3 `000000`, `pgfs.authorization`.
- **PRE-AUTH + completion** — `suite_preauth` then a linked clearing advice; show the ledger
  link and partial completion.
- **ATM withdrawal** — `suite_atm_approve`, DE3 `01xxxx`, PIN (DE52), cash MCC (6011),
  surcharge field; show how the enrichment differs from a purchase.
**Acceptance:** each preset runs and the enrichment trace visibly differs (ATM shows PIN +
cash fields; pre-auth shows the completion link) — demonstrating the tool's breadth.

### T1.4 — Make routing authoritative (prerequisite)
If not already done (Phase 3 T0.2), resolve the network once and carry it through the live
path so the enrichment trace, ledger, and audit reflect the **real** resolved network, not a
display-only computation.
**Acceptance:** the enrichment trace's network and the issuer ledger entry agree on the same
resolved scheme.

---

## P2 — Mandate-driven AI copilot (the differentiator)

### T2.1 — `POST /ai/mandate`
Input: a network **mandate excerpt** (pasted/uploaded text) + target network. Output (via the
configured provider chain):
```json
{ "design_summary": "...plain-English change description...",
  "iso_mapping_additions": [ {"canonical":"...","source":{"de":104},"network":"visa"} ],   // spec YAML deltas
  "jpf_fields":  [ {"path":"transaction.wallet_indicator","type":"string"} ],
  "db_columns":  [ {"name":"wallet_indicator","type":"VARCHAR(2)"} ],
  "scenarios":   [ {...runnable scenario exercising the mandate...} ] }
```
**Acceptance:** a sample Visa/Mastercard mandate paragraph yields proposed mapping additions,
JPF + DB field names, and at least one runnable test scenario.

### T2.2 — "Mandate → Implementation" UI workflow
On the AI Copilot page, add a **Mandate** tab: paste the mandate → review the AI's proposed
ISO-mapper enrichment, JPF/DB fields, and scenarios → **Apply** writes the spec additions to
`backend/mapping/specs/<network>.yaml` and saves the scenarios → one click to **certify** the
change against the SUT.
**Acceptance:** pasting a mandate, applying it, and certifying produces a green run exercising
the new fields — mandate in, tested implementation out.

### T2.3 — Review gate & guardrails
The AI **proposes**; a human **approves**. Validate generated DEs/specs against the schema
(valid DE numbers, test BINs only, sane types); show a diff before applying; version the spec
change; never auto-apply without explicit confirmation.
**Acceptance:** an invalid AI proposal (bad DE, real-looking PAN) is flagged and cannot be
applied; every applied change is shown as a reviewable diff.

---

## Guardrails for the agent
- Everything must work from a **local, non-Docker** launch (`make demo-local`).
- **NEVER** log, print, or persist a raw API key; encrypted local secrets file + env only.
- The enrichment trace must reflect the **real** originator/mapper output, not mock fields.
- Mandate AI proposes; a human reviews and applies — no silent spec/schema changes.
- Don't change the JPF schema or `pgfs.*` webhook shape outside a task that says so.
- Every task ends with its Acceptance check, recorded in CHANGELOG.md.

## Definition of done
1. `make demo-local` runs the full demo on the host with no Docker and no `NameResolutionError`.
2. The Anthropic key (and provider chain) is configurable in the UI; keys never leak.
3. The demo flow and audit trail show the **resolved network**, never a hardcoded "Visa".
4. The demo shows per-hop ISO enrichment (Acquirer/Network/Issuer adds) and the ISO→JPF→JIT
   JSON transformation, across AUTH / PRE-AUTH+completion / ATM use cases.
5. A pasted network mandate yields AI-proposed ISO-mapper + JPF + DB-field enrichment and
   runnable tests, applied behind a human review gate and certifiable in one click.
