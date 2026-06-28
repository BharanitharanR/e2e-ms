# e2e-marqeta-simulator/backend/otel/setup.py
"""
OpenTelemetry SDK initialisation.

Sets up a single TracerProvider and MeterProvider backed by OTLP exporters
pointing at the OTel Collector. Called once at process startup via
backend/instrumentation.py. Idempotent — safe to import multiple times.

All instruments (counters, histograms, gauges) are defined here so every
other module just does `from backend.otel.setup import meter, tracer` and
records — no SDK boilerplate scattered across the codebase.

Environment variables:
  OTEL_EXPORTER_OTLP_ENDPOINT  gRPC endpoint of the collector
                                default: http://otel-collector:4317
  OTEL_SERVICE_NAME             service name tag on all telemetry
                                default: marqeta-e2e-backend
"""
import os
import logging

logger = logging.getLogger(__name__)

# ── Lazy guards ───────────────────────────────────────────────────────────────
_initialised = False
tracer = None
meter  = None

# ── Instruments (populated after init) ───────────────────────────────────────
# Counters
ollama_request_count   = None
ollama_prompt_tokens   = None
ollama_completion_tokens = None
ollama_total_tokens    = None
heal_outcome_count     = None

# Histograms
ollama_latency_ms      = None
ollama_tokens_per_sec  = None
heal_iterations        = None

# Updowns (gauge-like via UpDownCounter)
ollama_active_requests = None


def init(
    endpoint: str = None,
    service_name: str = None,
) -> None:
    global _initialised, tracer, meter
    global ollama_request_count, ollama_prompt_tokens, ollama_completion_tokens
    global ollama_total_tokens, heal_outcome_count, ollama_latency_ms
    global ollama_tokens_per_sec, heal_iterations, ollama_active_requests

    if _initialised:
        return

    endpoint     = endpoint     or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "marqeta-e2e-backend")

    try:
        from opentelemetry import trace as otel_trace, metrics as otel_metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        resource = Resource.create({"service.name": service_name})

        # ── Traces ────────────────────────────────────────────────────────────
        span_exporter    = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        tracer_provider  = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        otel_trace.set_tracer_provider(tracer_provider)
        tracer = otel_trace.get_tracer(service_name)

        # ── Metrics ───────────────────────────────────────────────────────────
        metric_exporter  = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        metric_reader    = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5_000)
        meter_provider   = MeterProvider(resource=resource, metric_readers=[metric_reader])
        otel_metrics.set_meter_provider(meter_provider)
        meter = otel_metrics.get_meter(service_name)

        # ── Define all instruments ────────────────────────────────────────────
        ollama_request_count = meter.create_counter(
            "ollama.requests.total",
            description="Total Ollama generate() calls",
            unit="1",
        )
        ollama_prompt_tokens = meter.create_counter(
            "ollama.tokens.prompt.total",
            description="Total prompt (input) tokens sent to Ollama",
            unit="tokens",
        )
        ollama_completion_tokens = meter.create_counter(
            "ollama.tokens.completion.total",
            description="Total completion (output) tokens received from Ollama",
            unit="tokens",
        )
        ollama_total_tokens = meter.create_counter(
            "ollama.tokens.total",
            description="Total tokens (prompt + completion) consumed",
            unit="tokens",
        )
        ollama_latency_ms = meter.create_histogram(
            "ollama.request.duration_ms",
            description="Wall-clock latency of Ollama generate() calls",
            unit="ms",
        )
        ollama_tokens_per_sec = meter.create_histogram(
            "ollama.tokens.per_second",
            description="Completion token generation rate (tokens/sec) from Ollama eval_duration",
            unit="tokens/s",
        )
        ollama_active_requests = meter.create_up_down_counter(
            "ollama.requests.active",
            description="Number of Ollama requests currently in-flight",
            unit="1",
        )
        heal_outcome_count = meter.create_counter(
            "heal.outcome.total",
            description="Healing loop outcome counts by status and root_cause_category",
            unit="1",
        )
        heal_iterations = meter.create_histogram(
            "heal.iterations",
            description="Number of LLM iterations per healing run",
            unit="1",
        )

        _initialised = True
        logger.info("OTel initialised — endpoint=%s service=%s", endpoint, service_name)

    except ImportError as e:
        logger.warning(
            "opentelemetry packages not installed (%s). "
            "Telemetry disabled — add otel deps to requirements.txt to enable.", e
        )
        # Set stub no-op objects so callers never crash on attribute access
        _install_noop_stubs()
    except Exception as e:
        logger.warning("OTel init failed (%s). Telemetry disabled.", e)
        _install_noop_stubs()


# ── No-op stubs — called if OTel SDK is unavailable ──────────────────────────

class _NoopCounter:
    def add(self, *a, **kw): pass

class _NoopHistogram:
    def record(self, *a, **kw): pass

class _NoopSpan:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def set_attribute(self, *a): pass
    def add_event(self, *a, **kw): pass
    def record_exception(self, *a): pass
    def set_status(self, *a): pass

class _NoopTracer:
    def start_as_current_span(self, *a, **kw):
        return _NoopSpan()

def _install_noop_stubs():
    global _initialised, tracer, meter
    global ollama_request_count, ollama_prompt_tokens, ollama_completion_tokens
    global ollama_total_tokens, heal_outcome_count, ollama_latency_ms
    global ollama_tokens_per_sec, heal_iterations, ollama_active_requests

    tracer = _NoopTracer()
    meter  = None
    ollama_request_count     = _NoopCounter()
    ollama_prompt_tokens     = _NoopCounter()
    ollama_completion_tokens = _NoopCounter()
    ollama_total_tokens      = _NoopCounter()
    heal_outcome_count       = _NoopCounter()
    ollama_latency_ms        = _NoopHistogram()
    ollama_tokens_per_sec    = _NoopHistogram()
    ollama_active_requests   = _NoopCounter()
    heal_iterations          = _NoopHistogram()
    _initialised = True
