# e2e-marqeta-simulator/backend/otel/middleware.py
"""
FastAPI middleware for session ID injection and per-request span creation.

What it does per request:
  1. Reads X-Session-ID header; generates a UUID if absent
  2. Sets the session ID into contextvars (propagates to OllamaClient patch)
  3. Opens an OTel span for the entire HTTP request lifetime
  4. Adds session_id, http.method, http.route as span attributes
  5. Writes the session ID back into the response as X-Session-ID

This is registered as a Starlette middleware — zero imports needed in
route handlers, zero parameter threading. Works transparently for both
sync and async endpoints.
"""
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.otel.session import set_session_id, new_session_id

logger = logging.getLogger(__name__)


class SessionTracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Honour a caller-supplied session ID (useful for multi-step flows
        # where the frontend sends the same ID across several requests).
        sid = request.headers.get("X-Session-ID") or new_session_id()
        set_session_id(sid)

        route = request.url.path

        # Try to open a span; degrade silently if OTel is uninitialised
        try:
            from backend.otel.setup import tracer
            ctx_manager = tracer.start_as_current_span(
                f"http.{request.method.lower()} {route}"
            )
        except Exception:
            ctx_manager = _NoopCtx()

        with ctx_manager as span:
            try:
                span.set_attribute("session.id",    sid)
                span.set_attribute("http.method",   request.method)
                span.set_attribute("http.route",    route)
                span.set_attribute("http.host",     request.headers.get("host", ""))
            except Exception:
                pass

            start = time.perf_counter()
            response: Response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

            try:
                span.set_attribute("http.status_code",  response.status_code)
                span.set_attribute("http.duration_ms",  elapsed_ms)
            except Exception:
                pass

        # Echo the session ID back so callers can thread it through UI/logs
        response.headers["X-Session-ID"] = sid
        return response


class _NoopCtx:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def set_attribute(self, *a): pass
