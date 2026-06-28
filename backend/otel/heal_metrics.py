# e2e-marqeta-simulator/backend/otel/heal_metrics.py
"""
Heal loop outcome instrumentation.

Monkey-patches heal_service.run_heal_loop() to record outcome metrics
after every run without touching heal_service.py itself.

Metrics emitted:
  heal.outcome.total          counter  — by status + root_cause_category
  heal.iterations             histogram — iterations per run
  heal.duration_ms            histogram — total wall-clock time per run

Called once from backend/instrumentation.py at startup.
"""
import time
import logging
import functools

logger = logging.getLogger(__name__)
_PATCHED = False


def apply_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        import backend.heal_service as hs
        from backend.otel import setup as otel
        from backend.otel.session import get_session_id

        # Create an extra histogram that setup.py doesn't define
        # (heal_service-specific duration)
        heal_duration_ms = otel.meter.create_histogram(
            "heal.duration_ms",
            description="Wall-clock time for a full heal loop run",
            unit="ms",
        ) if otel.meter else otel._NoopHistogram()

        original_run = hs.run_heal_loop

        @functools.wraps(original_run)
        def instrumented_run(scenario: dict) -> dict:
            session_id = get_session_id()
            start = time.perf_counter()

            result = original_run(scenario)

            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            status   = result.get("status", "unknown")
            category = (result.get("last_analysis") or {}).get(
                "root_cause_category", "n/a"
            )
            scenario_id = result.get("scenario_id", "unknown")
            iterations  = result.get("iterations", 0)

            attrs = {
                "status":              status,
                "root_cause_category": category,
                "scenario_id":         scenario_id,
                "session_id":          session_id,
            }

            otel.heal_outcome_count.add(1, attrs)
            otel.heal_iterations.record(iterations, attrs)
            heal_duration_ms.record(elapsed_ms, attrs)

            # Add a span event on the active span (the /ai/heal HTTP span)
            try:
                from opentelemetry import trace
                span = trace.get_current_span()
                span.set_attribute("heal.status",         status)
                span.set_attribute("heal.iterations",     iterations)
                span.set_attribute("heal.root_cause",     category)
                span.set_attribute("heal.scenario_id",    scenario_id)
                span.set_attribute("heal.duration_ms",    elapsed_ms)
            except Exception:
                pass

            return result

        hs.run_heal_loop = instrumented_run
        _PATCHED = True
        logger.info("heal_service.run_heal_loop patched with OTel instrumentation")

    except ImportError as e:
        logger.warning("Could not apply heal_metrics patch (%s).", e)
    except AttributeError as e:
        logger.warning("heal_service not fully initialised yet (%s) — skipping heal patch.", e)
