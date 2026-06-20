# e2e-marqeta-simulator/backend/marqeta_simulator.py
"""Marqeta issuer-processor simulator (port 8103).

Receives the network message, builds the appropriate Marqeta JIT Funding webhook,
POSTs it to the customer's JIT endpoint (the System Under Test), interprets the
synchronous approve/decline, and returns a NetworkAuthResponse.
"""
import os
import time
import requests
from fastapi import FastAPI, Request
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
CUSTOMER_JIT_URL = os.getenv("CUSTOMER_JIT_URL", "http://customer_jit:8001/jit/authorize")


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


@app.post("/issuer/authorize")
async def issuer_authorize(request: Request):
    body = await request.json()
    event_type = (body.get("event_type") or "authorization").lower()
    original_txn = body.get("original_transaction_id") or ""
    advice_type = body.get("advice_type", "CLEARING")

    # Pydantic ignores the extra routing fields (event_type, etc.).
    req = NetworkAuthRequest(**body)

    if event_type == "advice":
        payload = MarqetaWebhookPayload.advice(req, original_txn, advice_type=advice_type)
    elif event_type == "refund":
        payload = MarqetaWebhookPayload.refund(req, original_txn)
    elif event_type == "reversal":
        payload = MarqetaWebhookPayload.reversal(req, original_txn)
    else:
        payload = MarqetaWebhookPayload.authorization(req)

    status_code = 0
    decision = "UNKNOWN"
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
    # If the JIT service returns a specific "rc" field, honour it (enables
    # RC 51, 57, 61, 65, 76 etc.). Otherwise fall back to 00/05.
    if decision == "APPROVED":
        iso_rc = "00"
    else:
        iso_rc = customer_body.get("rc") or "05"

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
    # Trace extras so the UI can show what Marqeta actually emitted.
    out["marqeta_webhook_event_type"] = payload.get("event_type")
    out["jit_funding_method"] = payload.get("jit_funding", {}).get("method")
    out["customer_response_body"] = customer_body
    return out


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8103)
