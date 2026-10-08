from __future__ import annotations
import logging
from typing import Optional

log = logging.getLogger("agentos.tracing")
_tracer = None


def setup_tracing(service_name: str = "agentos", otlp_endpoint: Optional[str] = None):
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        else:
            exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        log.info("Tracing initialized for %s", service_name)
        return _tracer
    except Exception as e:
        log.warning("Tracing unavailable: %s", e)
        return None


def get_tracer():
    return _tracer
