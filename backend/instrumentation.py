# e2e-marqeta-simulator/backend/instrumentation.py
"""
Single activation point for all observability.

Import this module (one line) in backend/main.py and everything wires up:

    import backend.instrumentation  # noqa: F401  ← add to main.py

What activates on import:
  1. OTel SDK (TracerProvider + MeterProvider → OTLP Collector)
  2. Monkey-patch on OllamaClient.generate   (token/latency/rate metrics)
  3. Monkey-patch on heal_service.run_heal_loop (outcome/iteration metrics)
  4. SessionTracingMiddleware exported for main.py to register

The middleware must be registered by main.py after app creation:

    from backend.instrumentation import register_middleware
    register_middleware(app)

This is done in _startup() or just after FastAPI() construction.

Design constraint: this module NEVER modifies business logic.
It wraps, records, and returns — nothing else.
"""
import logging

logger = logging.getLogger(__name__)


def _boot() -> None:
    """Run once at module import. Order matters: SDK first, then patches."""
    from backend.otel.setup import init
    init()                              # TracerProvider + MeterProvider

    from backend.otel.ollama_patch import apply_patch as patch_ollama
    patch_ollama()                      # OllamaClient.generate wrapper

    from backend.otel.heal_metrics import apply_patch as patch_heal
    patch_heal()                        # heal_service.run_heal_loop wrapper

    logger.info("backend.instrumentation boot complete")


def register_middleware(app) -> None:
    """
    Call once after FastAPI() is constructed and before routes are registered.

    from backend.instrumentation import register_middleware
    register_middleware(app)
    """
    from backend.otel.middleware import SessionTracingMiddleware
    app.add_middleware(SessionTracingMiddleware)
    logger.info("SessionTracingMiddleware registered")


_boot()
