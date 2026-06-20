# e2e-marqeta-simulator/customer_jit/app.py
"""Customer JIT Funding service -- the System Under Test (SUT).

Receives Marqeta JIT Funding webhooks and synchronously returns approve/decline.
Configurable via environment variables for velocity rules, MCC blocking, and
daily spend limits. Swap for a real endpoint by repointing CUSTOMER_JIT_URL.
"""
import os
import logging
from datetime import date
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("customer_jit")

app = FastAPI(title="Customer JIT Funding Service (System Under Test)")

# ── Configuration from environment variables ──────────────────────────────────
# Approval threshold: amount (cents) at or below which to approve. $50.00 default.
APPROVAL_LIMIT_CENTS = int(os.getenv("APPROVAL_LIMIT_CENTS", "5000"))

# Comma-separated MCC codes to block (RC 57 — Transaction Not Permitted).
# Example: "6011,4829" blocks ATMs and wire transfers.
_BLOCKED_MCCS: set = set(filter(None, os.getenv("BLOCKED_MCCS", "").split(",")))

# Daily per-card spend limit in cents (0 = disabled). RC 61.
DAILY_LIMIT_CENTS = int(os.getenv("DAILY_LIMIT_CENTS", "0"))

# Max transactions per card per session before velocity decline (0 = disabled). RC 65.
VELOCITY_MAX_TXN = int(os.getenv("VELOCITY_MAX_TXN", "0"))

# ── In-memory state (cleared on /reset) ──────────────────────────────────────
_seen_transaction_ids: set = set()
_daily_spend: dict = {}       # card_last4 -> {date: YYYY-MM-DD, amount: int}
_velocity_count: dict = {}    # card_last4 -> int (count for today)


def _decide(payload: dict):
    txn = payload.get("transaction", {}) or {}
    txn_id = txn.get("id") or payload.get("transaction_id") or "UNKNOWN"
    amount = int(txn.get("amount", payload.get("amount", 0)) or 0)
    event_type = payload.get("event_type", "transaction.authorization")
    mcc = txn.get("merchant", {}).get("mcc", "") or ""
    card_last4 = payload.get("card", {}).get("last_four", "XXXX")
    today = date.today().isoformat()

    logger.info("JIT webhook: event=%s id=%s amount=%d mcc=%s card=****%s",
                event_type, txn_id, amount, mcc, card_last4)

    # ── Idempotency / duplicate detection ────────────────────────────────────
    if txn_id in _seen_transaction_ids:
        logger.warning("Duplicate transaction id: %s", txn_id)
        return 409, {"decision": "DUPLICATE", "transaction_id": txn_id,
                     "rc": "76"}   # RC 76 = duplicate
    _seen_transaction_ids.add(txn_id)

    # ── Non-authorization events (advice, refund, reversal) ──────────────────
    if event_type != "transaction.authorization":
        return 200, {"decision": "APPROVED", "transaction_id": txn_id,
                     "event_type": event_type}

    # ── MCC blocking (RC 57 — Transaction Not Permitted to Cardholder) ───────
    if _BLOCKED_MCCS and mcc in _BLOCKED_MCCS:
        logger.info("MCC blocked: %s", mcc)
        return 402, {"decision": "DECLINED", "reason": "mcc_blocked",
                     "rc": "57", "transaction_id": txn_id}

    # ── Velocity check (RC 65 — Exceeds Withdrawal Frequency Limit) ──────────
    if VELOCITY_MAX_TXN > 0:
        count = _velocity_count.get(card_last4, 0) + 1
        _velocity_count[card_last4] = count
        if count > VELOCITY_MAX_TXN:
            logger.info("Velocity exceeded for card ****%s: %d txns", card_last4, count)
            return 402, {"decision": "DECLINED", "reason": "velocity_exceeded",
                         "rc": "65", "transaction_id": txn_id}

    # ── Daily spend limit (RC 61 — Exceeds Withdrawal Amount Limit) ──────────
    if DAILY_LIMIT_CENTS > 0:
        entry = _daily_spend.get(card_last4)
        if entry and entry.get("date") != today:
            entry = None   # Reset for new day
        current_spent = (entry or {}).get("amount", 0)
        new_spent = current_spent + amount
        _daily_spend[card_last4] = {"date": today, "amount": new_spent}
        if new_spent > DAILY_LIMIT_CENTS:
            logger.info("Daily limit exceeded for card ****%s: %d > %d",
                        card_last4, new_spent, DAILY_LIMIT_CENTS)
            return 402, {"decision": "DECLINED", "reason": "daily_limit_exceeded",
                         "rc": "61", "transaction_id": txn_id}

    # ── Core funding decision (RC 51 — Insufficient Funds on over-limit) ─────
    if amount <= APPROVAL_LIMIT_CENTS:
        return 200, {"decision": "APPROVED", "transaction_id": txn_id}
    return 402, {"decision": "DECLINED", "reason": "insufficient_funds",
                 "rc": "51", "transaction_id": txn_id}


@app.post("/jit/authorize")
async def jit_authorize(request: Request):
    payload = await request.json()
    status, body = _decide(payload)
    return JSONResponse(status_code=status, content=body)


@app.post("/jit/advice")
async def jit_advice(request: Request):
    payload = await request.json()
    status, body = _decide(payload)
    return JSONResponse(status_code=status, content=body)


@app.post("/reset")
async def reset():
    """Clear all in-memory state so scenarios can re-run from a clean slate."""
    _seen_transaction_ids.clear()
    _daily_spend.clear()
    _velocity_count.clear()
    return {"status": "reset", "seen": 0}


@app.get("/config")
async def config():
    """Return current JIT configuration (for the Sandbox Config page)."""
    return {
        "approval_limit_cents": APPROVAL_LIMIT_CENTS,
        "approval_limit_display": f"${APPROVAL_LIMIT_CENTS / 100:.2f}",
        "blocked_mccs": sorted(_BLOCKED_MCCS),
        "daily_limit_cents": DAILY_LIMIT_CENTS,
        "velocity_max_txn": VELOCITY_MAX_TXN,
        "seen_transactions": len(_seen_transaction_ids),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "customer_jit"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
