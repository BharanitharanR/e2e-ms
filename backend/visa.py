# e2e-marqeta-simulator/backend/visa.py
"""Visa network simulator (port 8102).

Receives the acquirer's request and forwards to the Marqeta issuer processor.
Pure pass-through routing leg.
"""
import os
import time
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Visa Network Simulator")

def _resolve_url(docker_name: str, docker_port: int, path: str = "") -> str:
    """Resolve Docker service URL to localhost when running on host OS."""
    if os.path.exists("/.dockerenv"):
        return f"http://{docker_name}:{docker_port}{path}"
    return f"http://127.0.0.1:{docker_port}{path}"

MARQETA_URL = os.getenv("MARQETA_URL", _resolve_url("marqeta_simulator", 8103, "/issuer/authorize"))


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
    return {"status": "ok", "service": "visa"}


@app.post("/network/authorize")
async def network_authorize(request: Request):
    body = await request.json()
    try:
        resp = _post_with_retry(MARQETA_URL, body)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except requests.RequestException as e:
        return JSONResponse(status_code=502,
                            content={"error": "visa -> marqeta failed", "detail": str(e)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8102)
