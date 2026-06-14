# e2e-marqeta-simulator/customer_jit/app.py
"""Customer JIT Funding service -- the System Under Test (SUT).

This is the endpoint a real Marqeta program would host. It receives the JIT
Funding webhook and synchronously returns approve / decline. Swap this out for a
real endpoint by repointing CUSTOMER_JIT_URL on the Marqeta simulator.
"""
import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("customer_jit")

app = FastAPI(title="Customer JIT Funding Service (System Under Test)")

# Approve at or below this amount (cents). $50.00 by default.
APPROVAL_LIMIT_CENTS = int(os.getenv("APPROVAL_LIMIT_CENTS", "5000"))

# In-memory idempotency store. Reset via POST /reset.
_seen_transaction_ids: set = set()


def _decide(payload: dict):
    txn = payload.get("transaction", {}) or {}
    txn_id = txn.get("id") or payload.get("transaction_id") or "UNKNOWN"
    amount = txn.get("amount", payload.get("amount", 0)) or 0
    event_type = payload.get("event_type", "transaction.authorization")

    logger.info("JIT webhook received: event=%s id=%s amount=%s", event_type, txn_id, amount)

    # Idempotency / duplicate detection
    if txn_id in _seen_transaction_ids:
        logger.warning("Duplicate transaction id: %s", txn_id)
        return 409, {"decision": "DUPLICATE", "transaction_id": txn_id}
    _seen_transaction_ids.add(txn_id)

    # Advice / clearing / refund / reversal are funding confirmations: acknowledge.
    if event_type != "transaction.authorization":
        return 200, {"decision": "APPROVED", "transaction_id": txn_id, "event_type": event_type}

    # Core JIT funding decision for fresh authorizations.
    if amount <= APPROVAL_LIMIT_CENTS:
        return 200, {"decision": "APPROVED", "transaction_id": txn_id}
    return 402, {"decision": "DECLINED", "reason": "insufficient_funds", "transaction_id": txn_id}


@app.post("/jit/authorize")
async def jit_authorize(request: Request):
    payload = await request.json()
    status, body = _decide(payload)
    return JSONResponse(status_code=status, content=body)


@app.post("/jit/advice")
async def jit_advice(request: Request):
    # Kept for parity; advice webhooks may be routed here. Same logic for now.
    payload = await request.json()
    status, body = _decide(payload)
    return JSONResponse(status_code=status, content=body)


@app.post("/reset")
async def reset():
    """Clear the idempotency store so scenarios can be re-run from a clean state."""
    _seen_transaction_ids.clear()
    return {"status": "reset", "seen": 0}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
