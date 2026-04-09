"""Security headers middleware

Applies defence-in-depth HTTP security headers to every response.
This middleware runs on for every request,so all responses benefit from the same security posture
Reference: OWASP Secure Headers Project
https://owasp.org/www-project-secure-headers/

Headers applied:
  Strict-Transport-Security  — force HTTPS for 1 year
  X-Content-Type-Options     — prevent MIME sniffing
  X-Frame-Options            — prevent clickjacking
  Referrer-Policy            — limit referer leakage
  Permissions-Policy         — deny unnecessary browser APIs
  Content-Security-Policy    — restrict resource loading (API-only; no HTML)
  Cache-Control              — prevent caching of API responses
  X-Request-ID               — propagate or generate request trace ID
Removed headers:
  Server
  X-powered-By
"""

from __future__ import annotations
from typing import Callable
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Paths that need relaxed security headers (Swagger UI, ReDoc)
_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    # API-only: no inline scripts, no external resources, no framing
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    # API responses should never be cached — payment state can change between polls
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
}

# Headers that must not appear in API responses
_HEADERS_TO_REMOVE = {"Server", "X-Powered-By"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers and propagate request IDs"""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # generate a request ID for distributed tracing
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        # store the request state so route handlers can log it
        request.state.request_id = request_id
        response = await call_next(request)

        # skip strict CSP for documentation endpoints
        if any(request.url.path.startswith(p) for p in _DOCS_PATHS):
            # Apply only essential headers
            response.headers["X-Request-id"] = request_id
            return response

        # Apply security headers
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value

        # roemove info-leaking headers
        for header in _HEADERS_TO_REMOVE:
            if header in response.headers:
                del response.headers[header]

        # echo the request ID for client-side correlation
        response.headers["X-Request-ID"] = request_id
        return response
