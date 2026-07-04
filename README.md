# End-to-End Marqeta JIT Transaction Simulator

A containerised proof-of-concept that simulates the full card-transaction path —
**Cardholder → Terminal → Acquirer → Visa → Marqeta Issuer Processor → Customer
JIT endpoint → Response** — so you can test a **Marqeta JIT Funding** integration
without touching live rails. The "Customer JIT" service is the *System Under Test*
and can be swapped for your real endpoint.

## Architecture

```
                       ┌─────────────────────────────────────────────┐
                       │          Streamlit UI  (:8501)              │
                       └───────────────────────┬─────────────────────┘
                                               │ REST
                       ┌───────────────────────▼─────────────────────┐
                       │      Orchestrator / Backend  (:8000)         │
                       │  scenarios • Terminal layer • scoring        │
                       └───────────────────────┬─────────────────────┘
                                               │ POST /authorize
   Cardholder ▶ Terminal(STAN/RRN) ─────────▶ Acquirer (:8101)
                                               │ POST /network/authorize
                                               ▼
                                          Visa (:8102)
                                               │ POST /issuer/authorize
                                               ▼
                                  Marqeta Simulator (:8103)
                            builds JIT Funding webhook (jit_funding.*)
                                               │ POST /jit/authorize
                                               ▼
                              Customer JIT  (:8001)  ◀── System Under Test
                            approve / decline / duplicate
                                               │
                          NetworkAuthResponse ◀┘  (response_code, auth_code …)
```

## Services

| Service             | Port | Role                                                            |
|---------------------|------|-----------------------------------------------------------------|
| `frontend`          | 8501 | Streamlit control panel: pick a scenario, run, see PASS/FAIL.   |
| `backend`           | 8000 | Orchestrator + REST API; owns scenarios, Terminal, scoring.     |
| `acquirer`          | 8101 | Forwards the auth message to the network leg.                   |
| `visa`              | 8102 | Network routing leg → issuer processor.                         |
| `marqeta_simulator` | 8103 | Builds the Marqeta JIT Funding webhook and calls the customer.  |
| `customer_jit`      | 8001 | **System Under Test** – returns approve/decline/duplicate.      |

## Run it

### Option A — No Docker (host quickstart) ✅ Recommended for development

```bash
# Prerequisites: Python 3.9+, pip
pip install fastapi uvicorn requests streamlit pyyaml pyiso8583 anthropic

# Optional: set your Anthropic key for AI Copilot features
export ANTHROPIC_API_KEY=sk-ant-...

# Start all 6 services on localhost:
make demo-local
# or directly:
bash start-local.sh
```

This launches `customer_jit` (:8001), `acquirer` (:8101), `visa` (:8102),
`marqeta_simulator` (:8103), `backend` (:8000), and `Streamlit` (:8501) as
background processes — all on `127.0.0.1`. Press **Ctrl-C** to stop all.

Service logs are written to `.runlogs/`.

### Option B — Docker (recommended for demos / shared environments)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
make demo          # or: docker-compose up --build
```

Then open the UI at **http://localhost:8501**.

Pick a scenario in the sidebar and click **Run End-to-End Transaction**. Bundled
scenarios:

- **Standard Purchase – Approve** ($25 → approved)
- **High Value Purchase – Decline** ($75 → declined, over the $50 limit)
- **Clearing Advice** for an approved auth
- **Refund** of a previous purchase

## Transaction flow

`Cardholder tap → Terminal (stamps STAN/RRN) → Acquirer → Visa → Marqeta issuer
processor (emits JIT Funding webhook) → Customer JIT (decision) → response back up
the chain → orchestrator scores it against the scenario's expectations.`

## Supported message types

| Event          | Marqeta webhook `event_type`            | JIT method                    |
|----------------|------------------------------------------|-------------------------------|
| authorization  | `transaction.authorization`              | `pgfs.authorization`          |
| clearing advice| `transaction.authorization.clearing`     | `pgfs.authorization.advice`   |
| refund         | `transaction.refund`                     | `pgfs.refund`                 |
| reversal       | `transaction.reversal`                   | `pgfs.authorization.reversal` |

Decision logic in `customer_jit` (tune via `APPROVAL_LIMIT_CENTS`):
amount ≤ 5000 cents → **APPROVED**, > 5000 → **DECLINED**, repeated id → **DUPLICATE** (409).

## Point at a real customer JIT endpoint

Override the Marqeta simulator's target:

```yaml
  marqeta_simulator:
    environment:
      - CUSTOMER_JIT_URL=https://your-host.example.com/jit/authorize
```

Your endpoint must accept the JIT Funding webhook body and reply with
`{"decision": "APPROVED" | "DECLINED", "transaction_id": "..."}`.

## Idempotency / re-running

The UI checkbox **"Unique transaction id per run"** (default on) makes the Terminal
mint a fresh `transaction_id` each run, so demos stay green. Uncheck it to *replay*
a fixed id and watch the customer return **DUPLICATE (409)** — the idempotency path.
**Reset customer state** clears the seen-id store.

## Add custom scenarios

Either use the **Generate a new scenario** panel in the UI (writes a JSON file via
`POST /generate`), or drop a JSON file into `backend/scenarios/` shaped like:

```json
{
  "id": "my_case_01",
  "name": "My Case",
  "description": "...",
  "event_type": "authorization",
  "request": { "transaction_id": "TXN_X", "pan": "4111111111111111",
               "amount": 2500, "mcc": "5411", "merchant_name": "...",
               "merchant_country": "USA", "pos_entry_mode": "051",
               "terminal_id": "TERM0001" },
  "expected_network_response_code": "00",
  "expected_customer_decision": "APPROVED"
}
```

> Generated scenarios persist inside the backend container only. Mount a volume on
> `./backend/scenarios` if you want them written back to the host.

## Webhook realism note

The Marqeta simulator emits webhook payloads modelled on Marqeta Core API v3 JIT
Funding gateway requests: a `jit_funding` object (`token`, `method`, `user_token`,
`acting_user_token`, `amount`, `currency_code`) plus `transaction`, `card`, `user`
and `merchant` context, and `original_transaction_token` on advice/refund/reversal.
Tokens are randomised per call. It is a faithful *shape*, not a certified contract —
validate against current Marqeta docs before production use.

## REST API (orchestrator, :8000)

- `GET  /scenarios` – list scenarios
- `POST /execute/{scenario_id}?unique=true` – run one end-to-end
- `POST /generate` – create a scenario file
- `GET  /history` – last 100 runs
- `POST /reset` – clear customer idempotency store
