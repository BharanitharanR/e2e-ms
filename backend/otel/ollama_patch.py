# e2e-marqeta-simulator/backend/otel/ollama_patch.py
"""
Non-invasive instrumentation for OllamaClient.generate().

Replaces the generate() method on the OllamaClient class with a wrapper
that:
  1. Opens an OTel span for every call (model, agent_id, session_id as attributes)
  2. Records latency, prompt tokens, completion tokens, tokens/sec as metrics
  3. Attaches a span event with the full timing breakdown from Ollama's response
  4. Returns the result UNCHANGED — the patch is transparent to all callers

The raw Ollama /api/generate response includes:
  prompt_eval_count  — input tokens
  eval_count         — output tokens (completions)
  total_duration     — nanoseconds (wall clock)
  eval_duration      — nanoseconds (token generation only)
  load_duration      — nanoseconds (model load if cold)

This is extracted from the raw response.json() BEFORE the existing code
does json.loads(result["response"]), so we don't alter that return path.

Called once from backend/instrumentation.py at startup.
Idempotent — second call is a no-op (checks _PATCHED sentinel).
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
        from backend.ollama_client import OllamaClient
        from backend.otel.session import get_session_id
        from backend.otel import setup as otel

        original_generate = OllamaClient.generate

        @functools.wraps(original_generate)
        def instrumented_generate(self, model: str, prompt: str, **kwargs):
            session_id = get_session_id()

            # Labels shared across all metrics for this call
            attrs = {
                "model":      model,
                "session_id": session_id,
            }

            otel.ollama_active_requests.add(1, attrs)
            otel.ollama_request_count.add(1, attrs)

            wall_start = time.perf_counter()

            with otel.tracer.start_as_current_span("ollama.generate") as span:
                span.set_attribute("ollama.model",         model)
                span.set_attribute("session.id",           session_id)
                span.set_attribute("ollama.prompt.length", len(prompt))

                try:
                    # ── Call the ORIGINAL method ──────────────────────────────
                    # Re-implement one level below to intercept raw response
                    # before json.loads(result["response"]) discards token data.
                    import json, requests as _requests

                    payload = {
                        "model":  model,
                        "prompt": prompt,
                        "format": "json",
                        "stream": False,
                    }
                    resp = _requests.post(
                        self.base_url,
                        json=payload,
                        timeout=1600,
                    )
                    resp.raise_for_status()
                    raw = resp.json()

                    # ── Extract Ollama telemetry fields ───────────────────────
                    prompt_tokens  = raw.get("prompt_eval_count",  0) or 0
                    compl_tokens   = raw.get("eval_count",         0) or 0
                    total_tokens   = prompt_tokens + compl_tokens
                    total_ns       = raw.get("total_duration",     0) or 0
                    eval_ns        = raw.get("eval_duration",      1) or 1
                    load_ns        = raw.get("load_duration",      0) or 0
                    wall_ms        = round((time.perf_counter() - wall_start) * 1000, 2)
                    tokens_per_sec = (compl_tokens / (eval_ns / 1e9)) if eval_ns > 0 else 0

                    # ── Metrics ───────────────────────────────────────────────
                    otel.ollama_latency_ms.record(wall_ms,        attrs)
                    otel.ollama_prompt_tokens.add(prompt_tokens,  attrs)
                    otel.ollama_completion_tokens.add(compl_tokens, attrs)
                    otel.ollama_total_tokens.add(total_tokens,    attrs)
                    if tokens_per_sec > 0:
                        otel.ollama_tokens_per_sec.record(tokens_per_sec, attrs)

                    # ── Span attributes & event ───────────────────────────────
                    span.set_attribute("ollama.tokens.prompt",     prompt_tokens)
                    span.set_attribute("ollama.tokens.completion",  compl_tokens)
                    span.set_attribute("ollama.tokens.total",       total_tokens)
                    span.set_attribute("ollama.latency_ms",         wall_ms)
                    span.set_attribute("ollama.tokens_per_sec",     round(tokens_per_sec, 2))

                    span.add_event("ollama.timing", attributes={
                        "total_duration_ms":  round(total_ns  / 1e6, 2),
                        "eval_duration_ms":   round(eval_ns   / 1e6, 2),
                        "load_duration_ms":   round(load_ns   / 1e6, 2),
                        "wall_clock_ms":      wall_ms,
                        "prompt_tokens":      prompt_tokens,
                        "completion_tokens":  compl_tokens,
                        "tokens_per_sec":     round(tokens_per_sec, 2),
                    })

                    # ── Return EXACTLY what the original method returns ───────
                    result = json.loads(raw["response"])
                    return result

                except Exception as exc:
                    span.record_exception(exc)
                    try:
                        from opentelemetry.trace import StatusCode
                        span.set_status(StatusCode.ERROR, str(exc))
                    except Exception:
                        pass
                    raise

                finally:
                    otel.ollama_active_requests.add(-1, attrs)

        OllamaClient.generate = instrumented_generate
        _PATCHED = True
        logger.info("OllamaClient.generate patched with OTel instrumentation")

    except ImportError as e:
        logger.warning("Could not apply OllamaClient patch (%s). Instrumentation skipped.", e)
