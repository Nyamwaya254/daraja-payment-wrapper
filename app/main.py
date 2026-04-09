"""FastAPI application factory"""

from __future__ import annotations
import functools
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import structlog

from app.config import Settings, get_settings
from app.dependencies import lifespan
from app.exceptions import (
    AppError,
    DarajaCircuitOpenError,
    DarajaError,
    DomainError,
    DuplicatePaymentError,
    PaymentNotFoundError,
    RateLimitError,
)
from app.middleware.api_key import APIKeyMiddleware
from app.middleware.ip_allowlist import SafaricomIPAllowlistMiddleware
from app.middleware.rate_limit import TokenBucketRateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.routes import health, stk

logger = structlog.get_logger(__name__)

_ERROR_BASE = "https://errors.mpesa.example.com"


def _problem(status, title, detail, error_code="error", extra=None):
    """Constructs an RFC 7807 problem detail JSON object."""
    body = {
        "type": f"{_ERROR_BASE}/{error_code}",
        "title": title,
        "status": status,
        "detail": detail,
    }
    if extra:
        body.update(extra)
    return body


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is not None:
        # Clear the lru_cache so get_settings() returns fresh settings
        # We patch the underlying function directly via functools — safe
        # because lru_cache wraps the original with __wrapped__.
        get_settings.cache_clear()
        # Temporarily replace the cached callable for the test process.
        # This works reliably because lru_cache stores the result after
        # the first call — clearing the cache then patching means the
        # next call to get_settings() returns our test settings instance.
        original_func = get_settings.__wrapped__  # the unwrapped function
        # Re-wrap so lru_cache will call our lambda on next invocation
        patched = functools.lru_cache(maxsize=1)(lambda: settings)
        # Swap function globals so all module-level callers get the patched version
        import app.config as _cfg

        _cfg.get_settings = patched
        # Also patch the local import in this module
        import sys

        sys.modules["app.config"].get_settings = patched

    effective = settings or get_settings()

    app = FastAPI(
        title="M-Pesa Daraja Integration Service",
        description="Daraja: STK Push, C2B, B2C, Reconciliation.",
        version="1.0.0",
        docs_url="/docs" if effective.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    import redis.asyncio as aioredis

    _mw_redis = aioredis.from_url(
        effective.redis_url,
        max_connections=5,
        socket_timeout=effective.redis_socket_timeout,
        socket_connect_timeout=effective.redis_socket_connect_timeout,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SafaricomIPAllowlistMiddleware)
    app.add_middleware(APIKeyMiddleware, valid_keys=effective.parsed_api_keys)
    app.add_middleware(TokenBucketRateLimitMiddleware, redis=_mw_redis)

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            request_id=getattr(request.state, "request_id", "-"),
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        for e in errors:
            e.pop("url", None)
        return JSONResponse(
            422,
            content=_problem(
                422,
                "Validation Error",
                f"{len(errors)} field(s) failed",
                "validation-error",
                {"errors": errors},
            ),
        )

    @app.exception_handler(PaymentNotFoundError)
    async def not_found(request: Request, exc: PaymentNotFoundError):
        return JSONResponse(
            404,
            content=_problem(
                404,
                "Payment Not Found",
                exc.message,
                exc.code.lower().replace("_", "-"),
                exc.details or {},
            ),
        )

    @app.exception_handler(DuplicatePaymentError)
    async def duplicate(request: Request, exc: DuplicatePaymentError):
        return JSONResponse(
            409,
            content=_problem(
                409,
                "Duplicate Payment Request",
                exc.message,
                exc.code.lower().replace("_", "-"),
                exc.details or {},
            ),
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit(request: Request, exc: RateLimitError):
        return JSONResponse(
            429,
            headers={"Retry-After": str(exc.retry_after)},
            content=_problem(
                429,
                "Too Many Requests",
                exc.message,
                "rate-limited",
                {"retry_after": exc.retry_after},
            ),
        )

    @app.exception_handler(DarajaCircuitOpenError)
    async def circuit_open(request: Request, exc: DarajaCircuitOpenError):
        return JSONResponse(
            503,
            headers={"Retry-After": str(exc.retry_after)},
            content=_problem(
                503,
                "Service Temporarily Unavailable",
                "M-Pesa is temporarily unavailable.",
                "daraja-circuit-open",
                {"retry_after": exc.retry_after},
            ),
        )

    @app.exception_handler(DarajaError)
    async def daraja_error(request: Request, exc: DarajaError):
        logger.error(
            "daraja_api_error", message=exc.message, daraja_code=exc.daraja_code
        )
        detail = (
            exc.message if effective.environment != "production" else "M-Pesa failed."
        )
        return JSONResponse(
            502, content=_problem(502, "M-Pesa API Error", detail, "daraja-error")
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(
            400,
            content=_problem(
                400,
                "Request Error",
                exc.message,
                exc.code.lower().replace("_", "-"),
                exc.details or {},
            ),
        )

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        logger.error("unhandled_app_error", code=exc.code, message=exc.message)
        return JSONResponse(
            500,
            content=_problem(
                500,
                "Internal Server Error",
                "An unexpected error occurred.",
                "internal-error",
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.error(
            "unhandled_exception",
            exc_type=type(exc).__name__,
            path=request.url.path,
            traceback=traceback.format_exc(),
        )
        return JSONResponse(
            500,
            content=_problem(
                500,
                "Internal Server Error",
                "An unexpected error occurred.",
                "internal-error",
            ),
        )

    app.include_router(health.router)
    app.include_router(stk.router)

    return app


app = create_app()
