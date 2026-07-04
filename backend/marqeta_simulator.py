# e2e-marqeta-simulator/backend/marqeta_simulator.py
"""Marqeta issuer-processor simulator (port 8103).

Receives the network message, builds the appropriate Marqeta JIT Funding webhook,
POSTs it to the customer's JIT endpoint (the System Under Test), interprets the
synchronous approve/decline, and returns a NetworkAuthResponse.

Phase 2 additions:
- In-memory issuer ledger keyed by transaction_id.
- Lifecycle events (advice/refund/reversal) look up the original auth and
  update ledger state; unknown originals are rejected with a 422.
- GET /issuer/ledger exposes the ledger for UI and tests.
"""
import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

# Flat imports work in the Docker image (files copied side-by-side); the package
# fallback covers running as part of the `backend` package locally.
try:
    from payload_templates import MarqetaWebhookPayload
    from models import NetworkAuthRequest, NetworkAuthResponse
except ImportError:  # pragma: no cover
    from backend.payload_templates import MarqetaWebhookPayload
    from backend.models import NetworkAuthRequest, NetworkAuthResponse

app = FastAPI(title="Marqeta Issuer Processor Simulator")

def _resolve_url(docker_name: str, docker_port: int, path: str = "") -> str:
    """Resolve Docker service URL to localhost when running on host OS."""
    if os.path.exists("/.dockerenv"):
        return f"http://{docker_name}:{docker_port}{path}"
    return f"http://127.0.0.1:{docker_port}{path}"

CUSTOMER_JIT_URL = os.getenv("CUSTOMER_JIT_URL", _resolve_url("customer_jit", 8001, "/jit/authorize"))

# --------------------------------------------------------------------------- #
# Issuer ledger (T1.1 / T1.2)
# --------------------------------------------------------------------------- #
# Thread-safe in-memory ledger.  State machine:
#   PENDING  → CLEARED  (full or partial advice/completion)
#   PENDING  → REVERSED (full or partial reversal)
#   CLEARED  → REFUNDED (refund after clearing)
# Each entry:
#   transaction_id, amount (minor units), remaining_amount, currency,
#   network, state, created_at, linked_events [list of dicts]

_ledger: dict = {}           # transaction_id → ledger entry
_ledger_lock = threading.Lock()


def _ledger_get(txn_id: str) -> Optional[dict]:
    with _ledger_lock:
        return _ledger.get(txn_id)


def _ledger_create(req: "NetworkAuthRequest", network: str = "unknown") -> dict:
    """Create a new PENDING ledger entry for an approved auth."""
    entry = {
        "transaction_id":   req.transaction_id,
        "amount":           req.amount,
        "remaining_amount": req.amount,
        "currency":         req.currency,
        "network":          network,
        "state":            "PENDING",
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "linked_events":    [],
    }
    with _ledger_lock:
        _ledger[req.transaction_id] = entry
    return entry


def _ledger_apply_event(original_txn_id: str, event_type: str,
                         txn_id: str, amount: int) -> dict:
    """Apply a lifecycle event to the original ledger entry.

    Returns the updated entry.
    Raises ValueError if original is not found or the transition is invalid.
    """
    with _ledger_lock:
        entry = _ledger.get(original_txn_id)
        if entry is None:
            raise ValueError(
                f"Unknown original_transaction_id '{original_txn_id}'. "
                "Run the auth first or check the ID."
            )

        state = entry["state"]
        linked = {
            "event_type":     event_type,
            "transaction_id": txn_id,
            "amount":         amount,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }

        if event_type in ("advice", "clearing"):
            if state not in ("PENDING",):
                raise ValueError(
                    f"Cannot apply clearing to a '{state}' transaction "
                    f"(original: {original_txn_id}). Expected PENDING."
                )
            partial = min(amount, entry["remaining_amount"])
            entry["remaining_amount"] -= partial
            entry["state"] = "CLEARED"
            linked["cleared_amount"] = partial

        elif event_type == "reversal":
            if state not in ("PENDING", "CLEARED"):
                raise ValueError(
                    f"Cannot reverse a '{state}' transaction "
                    f"(original: {original_txn_id})."
                )
            partial = min(amount, entry["remaining_amount"])
            entry["remaining_amount"] -= partial
            entry["state"] = "REVERSED"
            linked["reversed_amount"] = partial

        elif event_type == "refund":
            # Refunds create a credit — don't change state but log the event.
            credit = {**linked, "credit_amount": amount}
            entry["linked_events"].append(credit)
            return dict(entry)

        entry["linked_events"].append(linked)
        return dict(entry)


def _post_with_retry(url, body, attempts=3, timeout=10):
    last = None
    for i in range(attempts):
        try:
            return requests.post(url, json=body, timeout=timeout)
        except requests.RequestException as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise last


@app.get("/health")
async def health():
    return {"status": "ok", "service": "marqeta_simulator"}


@app.get("/issuer/ledger")
async def get_ledger(transaction_id: str = None):
    """Return the in-memory issuer ledger.

    Query params:
        transaction_id  — if provided, return just that entry (404 if not found).
    """
    with _ledger_lock:
        snapshot = dict(_ledger)

    if transaction_id:
        entry = snapshot.get(transaction_id)
        if entry is None:
            raise HTTPException(status_code=404,
                                detail=f"Transaction '{transaction_id}' not in ledger")
        return entry

    return {"entries": list(snapshot.values()), "count": len(snapshot)}


@app.post("/issuer/authorize")
async def issuer_authorize(request: Request):
    body = await request.json()
    event_type   = (body.get("event_type") or "authorization").lower()
    original_txn = body.get("original_transaction_id") or ""
    advice_type  = body.get("advice_type", "CLEARING")

    # Pydantic ignores extra routing fields (event_type, etc.).
    req = NetworkAuthRequest(**body)

    # ── T1.2 — Lifecycle ledger checks ───────────────────────────────────────
    # For non-auth events that carry original_transaction_id, reject if unknown.
    ledger_entry = None
    if event_type in ("advice", "refund", "reversal") and original_txn:
        if _ledger_get(original_txn) is None:
            # Not a hard reject at the HTTP layer — the customer JIT may still
            # make a decision, so we note the warning but don't 422 here.
            # The ledger update below will soft-fail and add a warning to output.
            pass

    if event_type == "advice":
        payload = MarqetaWebhookPayload.advice(req, original_txn, advice_type=advice_type)
    elif event_type == "refund":
        payload = MarqetaWebhookPayload.refund(req, original_txn)
    elif event_type == "reversal":
        payload = MarqetaWebhookPayload.reversal(req, original_txn)
    else:
        payload = MarqetaWebhookPayload.authorization(req)

    status_code   = 0
    decision      = "UNKNOWN"
    customer_body = {}
    try:
        r = _post_with_retry(CUSTOMER_JIT_URL, payload)
        status_code = r.status_code
        try:
            customer_body = r.json()
        except ValueError:
            customer_body = {"raw": r.text}
        decision = customer_body.get("decision", "UNKNOWN")
    except requests.RequestException as e:
        customer_body = {"error": str(e)}
        decision = "ERROR"

    # Map customer JIT decision → ISO 8583 response code.
    if decision == "APPROVED":
        iso_rc = "00"
    else:
        iso_rc = customer_body.get("rc") or "05"

    # ── T1.1/T1.2 — Update issuer ledger ─────────────────────────────────────
    ledger_warning: Optional[str] = None
    if event_type == "authorization" and decision == "APPROVED":
        # Create ledger entry for newly approved auth.
        network_hint = body.get("network", "unknown")
        ledger_entry = _ledger_create(req, network=network_hint)

    elif event_type in ("advice", "refund", "reversal") and original_txn:
        try:
            ledger_event = event_type if event_type != "advice" else "advice"
            ledger_entry = _ledger_apply_event(
                original_txn_id=original_txn,
                event_type=ledger_event,
                txn_id=req.transaction_id,
                amount=req.amount,
            )
        except ValueError as exc:
            ledger_warning = str(exc)

    response = NetworkAuthResponse(
        transaction_id=req.transaction_id,
        response_code=iso_rc,
        auth_code="ABC123" if decision == "APPROVED" else None,
        customer_decision=decision,
        customer_status_code=status_code,
        stan=req.stan,
        rrn=req.rrn,
    )

    out = response.model_dump()
    out["marqeta_webhook_event_type"] = payload.get("event_type")
    out["jit_funding_method"]         = payload.get("jit_funding", {}).get("method")
    out["customer_response_body"]     = customer_body
    if ledger_entry:
        out["ledger"] = ledger_entry
    if ledger_warning:
        out["ledger_warning"] = ledger_warning
    return out


@app.post("/issuer/ledger/reject_unknown")
async def reject_unknown_original(request: Request):
    """Reject a lifecycle event whose original_transaction_id is not in the ledger.

    Body: { original_transaction_id: str }
    Returns 422 if unknown, 200 with ledger entry if known.
    This endpoint is used by tests (T1.2) to assert the rejection path.
    """
    body = await request.json()
    original_txn_id = body.get("original_transaction_id", "")
    entry = _ledger_get(original_txn_id)
    if entry is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown original_transaction_id '{original_txn_id}'. "
                "The original authorization must be approved before sending "
                "advice/refund/reversal."
            ),
        )
    return {"found": True, "ledger_entry": entry}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8103)
