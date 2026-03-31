"""Critical security layer.THis ensures that only requests coming from Safaricoms Known callback IPs caan hit the callback endpoints
Only Safaricom official callback IP addresses can trigger state change like mark paymeny complete
IP source:https://developer.safaricom.co.ke/Documentation#callback-urls-whitelisting
Bypass in sandbox:
    Safaricoms sandbox might not have fixed IPS,trust_callback_ips_in_sandbox=true skips the check
Proxy header handlings
    -Since most deployements sit behind a load balancer or reverse proxy i.e NGINX
    -The real client id is passed in the X-Forwarded-For header.The middleware extracts the first IP from the header
"""

from __future__ import annotations

from ipaddress import AddressValueError, ip_address, IPv4Address, IPv6Address
from typing import Callable

from fastapi import Request
from starlette.responses import JSONResponse
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import get_settings


logger = structlog.get_logger(__name__)
_CALLBACK_PREFIX = "/api/v1/callback/"


class SafaricomIPAllowlistMiddleware(BaseHTTPMiddleware):
    """Enforce Safaricom IP allowlist on all /api/v1/callbacks/* endpoints.
    All other routes pass through without inspection.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self._settings = get_settings()
        # pre-parse IPS at startup so no parsing overhead per request
        self._allowed: frozenset[IPv4Address | IPv6Address] = self._parse_ips(
            self._settings.safaricom_allowed_ips
        )

    @staticmethod
    def _parse_ips(raw_ips: list[str]) -> frozenset:
        """iterate each string to convert to IPv4address or IPv6address and return frozenset if the string is valid else return warning"""
        parsed = set()
        for ip_str in raw_ips:
            try:
                parsed.add(ip_address(ip_str))
            except AddressValueError:
                logger.warning("invalid_allowlist_ip", ip=ip_str)
        return frozenset

    async def dispatch(self, request: Request, call_next: Callable):
        # only inspect callback routes else skip all checks and pass through
        if not request.url.path.startswith(_CALLBACK_PREFIX):
            return await call_next(request)
        # sandbox bypass
        if self._settings.trust_callback_ips_in_sandbox:
            return await call_next(request)

        # extract client ip to get real ip handling proxies
        client_ip = self._extract_client_ip(request)

        if not self._is_allowed(client_ip):
            logger.warning(
                "callback_ip_rejected",
                client_ip=client_ip,
                path=request.url.path,
                allowlist_size=len(self._allowed),
            )
            # return 403 Forbidden response,response does not reveal why access is denied :security through obscurity
            return JSONResponse(
                status_code=403,
                content={
                    "type": "https://errors.mpesa.example.com/forbidden",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": "Access denied.",
                },
            )
        logger.debug("callback_ip_allowed", client_ip=client_ip)
        return await call_next(request)

    def _extract_client_ip(self, request: Request) -> str:
        """Extract originating IP with XFF support
        X-Forwarded-For: client_ip, proxy1_ip, proxy2_ip
        The first IP is the originating client (what we want to check).
        """
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip
        # if no xff only happens if youre not behind a proxy(my case in the meantime)
        if request.client:
            return request.client.host
        return ""

    def _is_allowed(self, ip_str: str) -> bool:
        """Convert string to an IPv4Address or IPv6Address and checks membership in the pre-parsed set"""
        try:
            return ip_address(ip_str) in self._allowed
        except AddressValueError:
            return False
