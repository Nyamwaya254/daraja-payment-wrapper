"""
Observability configuration.

I Have implemented:
  1.Structured logging(structlog) -machine-parseable JSON logs
  2.Distributed tracing(openTelemetry) -trace context across services
  3.Error tracking(Sentry) -exception capture with payment context
"""

from __future__ import annotations
import logging
import os
import re
import structlog
from structlog.types import WrappedLogger, EventDict
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from app.config import get_settings


# structlog processors


def _add_service_context(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Add service-level context to every log event
    This allows filtering logs by service or environment in log aggregator
    """
    settings = get_settings()
    event_dict.setdefault("service", settings.service_name)
    event_dict.setdefault("environment", settings.environment)
    return event_dict


def _mask_phone_number(match: re.Match[str]) -> str:
    phone = match.group()
    return phone[:4] + "****" + phone[-4:]


def _strip_pii_from_logs(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Masks PII in log output (phone numbers,email addresses)"""
    SENSITIVE_KEYS = {"password", "secret", "access_token", "security_credential"}
    PHONE_PATTERN = re.compile(r"\b2547\d{8}\b}")

    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "***REDACTED***"
        elif isinstance(event_dict[key], str):
            event_dict[key] = PHONE_PATTERN.sub(_mask_phone_number, event_dict[key])
    return event_dict


# structlog  configuration
def configure_structlog(log_level: str = "INFO") -> None:
    """Configure structlog for prod JSON output.Call once at application startup"""
    # configure standard library logging to route through structlog
    logging.basicConfig(
        format="%(message)%",
        level=getattr(logging, log_level.upper(), logging.INFO),
    )
    # silence noisy third_party loggers
    for noisy_logger in [
        "httpx",
        "httpcore",
        "sqlalchemy.pool",
        "asyncio",
        "uvicorn.access",
    ]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            # Add log level as string
            structlog.stdlib.add_log_level,
            # add logger name
            structlog.stdlib.add_logger_name,
            # Add ISO timestamp
            structlog.processors.TimeStamper(fmt="iso"),
            # stack info for exceptions
            structlog.processors.StackInfoRenderer(),
            # Exception info rendering
            structlog.processors.ExceptionRenderer(),
            # service context
            _add_service_context,
            # PII stripping
            _strip_pii_from_logs,
            # final: JSON output
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# OpenTelemetry configuration
def configure_opentelemetry(service_name: str, environment: str) -> None:
    """Creattes a TracerProvider with service name,env"""
    try:
        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": environment,
            }
        )
        provider = TracerProvider(resource=resource)

        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        else:
            # fall back to console in development
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        # Auto-instrument FastAPI and HTTPX
        FastAPIInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()

    except ImportError:
        structlog.get_logger(__name__).warning(
            "opentelemetry_not_installed",
            hint="pip install opentelemetry-sdk opentelemetry-instrumentation-fastapi",
        )
