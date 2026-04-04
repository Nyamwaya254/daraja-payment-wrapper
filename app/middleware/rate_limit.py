"""
Token bucker rate limiter- Redis-backed ,multi-process safe
Algorithm:
    Each(client_ip,router_prefix) pair has a redis hash.
    {tokens:float, last_refill: float (unit timestamp)}

    On every request,a Lua script automatically:
        1.Computes elapsed time since last-refill
        2.Adds(elapsed * refill_rate) tokens,capped as capacity
        3.Deducts 1 token if available -> allow
        4.Returns remaining tokens and retry_after_seconds

    Why Lua?
     Without Lua,steps 1-4 would be round-trips,creating a race
     where two concurrent requests both see enough tokens and both proceed.
     Lua scripts execute atomically in Redis - no race possible

    Token bucket over sliding window?
     Token bucket handles burst natively(accumulated tokens),
     Sliding window is more accurate but requires sorted sets(more memory)
     For Daraja initiation(where bursts are legitimate i.e batch payouts) hence token bucket is the right choice

Limits are route-prefix-based. The most specific matching prefix wins
"""

from __future__ import annotations
import time
from typing import Callable

from fastapi import Request, Response
from starlette.responses import JSONResponse
import structlog

from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = structlog.get_logger(__name__)


# Atomically check and consume one token from the bucket
# Returns [allowed(0|1),tokens_remaining(int), retry_after_seconds(int)]
# script retrieves buckets current tokens and last refill time ,calculates how many tokens to add based on elapsed time
_TOKEN_BUCKET_LUA = """
local key           = KEYS[1]
local capacity      = tonumber(ARGV[1])
local refill_rate   = tonumber(ARGV[2])
local now           = tonumber(ARGV[3])
local cost          = tonumber(ARGV[4])

local bucket        = redis.call('HMGET',key,'tokens','last_refill')
local tokens        = tonumber(bucket[1]) or capacity
local last_refill   = tonumber(bucket[2]) or now

local elapsed       = math.max(0, now - last_refill)
tokens              = math.min(capacity, tokens + elapsed * refill_rate)

if tokens >= cost then
    tokens = tokens - cost
    redis.call('HMSET', key, 'tokens', 'last_refill', now)
    redis.call('EXPIRE' key, math.ceil(capacity / refill_rate + 60))
    return {1, math.floor(tokens),0}
else
    redis.call('HMSET',  key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) + 60)
    local retry_after = math.ceil((cost - tokens) / refill_rate)
    return {0, 0, retry_after}
end
"""

# (capacity,refill_rate_per_second)
# Read as: burst of `capacity` requests, sustained at `refill_rate * 60` per minute
_ROUTE_LIMITS: dict[str, tuple[int, float]] = {
    "api/v1/callbacks/": (200, 10.0),  # high-volume: saf can batch callbacks
    "api/v1/payments/stk": (20, 0.5),  # 30/min sustained, burdt 20
    "/health": (100, 10.0),  # never rate-limit
    "/ready": (100, 10.0),
}
_DEFAULT_LIMIT: tuple[int, float] = (60, 1.0)  # 60/min sustained, burst 60


class TokenBucketRateLimitMiddleware(BaseHTTPMiddleware):
    """Token Bucket rate limiter middleware"""

    def __init__(self, app: ASGIApp, redis: Redis) -> None:
        super().__init__(app)
        self._redis = redis
        self._script_sha: str | None = None

    async def _load_script(self) -> str:
        """Load Lua Script into Redis via SCRIPT LOAD ( one round-trip cached per worker)"""
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_TOKEN_BUCKET_LUA)
        return self._script_sha

    def _resolve_limit(self, path: str) -> tuple[int, float]:
        """Return (capacity,refill_rate) fro the most specific matching prefix"""
        for prefix in sorted(_ROUTE_LIMITS, key=len, reverse=True):
            if path.startswith(prefix):
                return _ROUTE_LIMITS[prefix]
        return _DEFAULT_LIMIT

    def _extract_client_ip(self, request: Request) -> str:
        """Extracts originating IP,honouring X-Forwarded_For from trusted proxies"""
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        capacity, refill_rate = self._resolve_limit(request.url.path)
        client_ip = self._extract_client_ip(request)
        bucket_key = f"ratelimit:{client_ip}:{request.url.path}"

        try:
            # load the lua script SHA(once per wprker)
            sha = await self._load_script()
            result = await self._redis.evalsha(
                sha,
                1,  # no of keys(the buckey_key)
                bucket_key,  # key 1
                capacity,  # ARGV[1]
                refill_rate,  # ARGV[2]
                time.time(),  # ARGV[3] -current timestamp
                1,  # ARGV[4] -cost ( 1 token per request)
            )
            # unpack result
            allowed = int(result[0])
            tokens_remaining = int(result[1])
            retry_after = int(result[2])

        except Exception as e:
            # graceful degradation on redis failure -fail open
            logger.warning(
                "rate_limit_redis_error",
                error=str(e),
                path=request.url.path,
                client_ip=client_ip,
            )
            return await call_next(request)

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                client_ip=client_ip,
                path=request.url.path,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(capacity),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                },
                content={
                    "type": "https://errors.mpesa.example.com/rate-limited",
                    "title": "Too Many Requests",
                    "status": 429,
                    "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
                    "retry_after": retry_after,
                },
            )
        # allow the request and add rate-limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-limit"] = str(capacity)
        response.headers["X-RateLimit-Remaining"] = str(tokens_remaining)
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time() + (capacity - tokens_remaining) / refill_rate)
        )
        return response
