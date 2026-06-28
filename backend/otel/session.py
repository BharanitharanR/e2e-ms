# e2e-marqeta-simulator/backend/otel/session.py
"""
Session ID propagation via Python contextvars.

A session ID is a single UUID that ties one upstream HTTP request (e.g.
POST /ai/heal) to every Ollama call made while handling it, making all
spans for that request group into a traceable unit in Jaeger.

Using contextvars means the ID travels through async task boundaries and
thread-pool executors automatically — no parameter threading, no globals.

Usage (set by FastAPI middleware, read by OllamaClient patch):
    from backend.otel.session import set_session_id, get_session_id
    set_session_id("abc-123")   # in middleware
    get_session_id()            # anywhere downstream → "abc-123"
"""
import uuid
from contextvars import ContextVar

_session_id_var: ContextVar[str] = ContextVar("session_id", default="")


def set_session_id(sid: str) -> None:
    _session_id_var.set(sid)


def get_session_id() -> str:
    sid = _session_id_var.get()
    if not sid:
        sid = f"auto-{uuid.uuid4().hex[:12]}"
        _session_id_var.set(sid)
    return sid


def new_session_id() -> str:
    """Generate a fresh UUID, set it in context, and return it."""
    sid = uuid.uuid4().hex
    _session_id_var.set(sid)
    return sid
