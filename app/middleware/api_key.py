"""API Key authentication middleware
Protects all routes except:
  - /api/v1/callbacks/* — Safaricom does not send your key
  - /health, /ready     — internal probes must not require auth
  - /docs               — development only, blocked in production by main.py

Keys are comma-separated in the env var, allowing zero-downtime rotation:
  INTERNAL_API_KEYS="key-v1,key-v2"
  → deploy new key, update all callers, remove old key, redeploy
"""

from __future__ import annotations
import secrets
from typing import Callable

from fastapi import Request, Response
import structlog

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

_EXEMPT_PREFIXES = (
    "/api/v1/callback/",  # Safaricom callback IPs — protected by ip_allowlist.py
    "/docs",
    "/openapi.json",
    "/redoc",
    "/health",
    "/",
    "/favicon.ico",
    "/ready",
)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Enforce X_API_Key header on all non-exempt routes"""

    def __init__(self, app: ASGIApp, valid_keys: set[str]) -> None:
        super().__init__(app)
        if not valid_keys:
            raise ValueError(
                "APIKeyMiddleware initialised with no valid keys. ",
            )
        self._valid_keys = valid_keys

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # exempt routes pass through without a key
        if any(request.url.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key", "")

        # constant-time comparison against every valid key
        is_valid = any(
            secrets.compare_digest(provided_key, valid) for valid in self._valid_keys
        )

        if not is_valid:
            logger.warning(
                "api_key_rejected",
                path=request.url.path,
                method=request.method,
                client_ip=(
                    request.headers.get("X-Forwaded-For", "").split(",")[0].strip()
                    or (request.client.host if request.client else "-")
                ),
            )
            return JSONResponse(
                status_code=401,
                content={
                    "type": "https://errors.mpesa.example.com/unauthorized",
                    "title": "Unauthorized",
                    "status": 401,
                    "detail": "Missing or invalid X-API-Key header.",
                },
            )
        return await call_next(request)
