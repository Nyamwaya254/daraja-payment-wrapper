"""
Daraja OAuth 2.0 token manager

Token lifecycle:
1.Check Redis cache -> return if valid
2.Acquire Redlock-style distributed lock  -> prevents thundering herd
3.Fetch new token from Daraja/oauth/v1/generate
4.Store in Redis with TTL = (expires_in - safety_margin) seconds

Process safety:
Multiple fastapi workers and celery workers can run concurrently.
The redis NX lock ensures only one process fetches a new token at a time.
Other processes poll untill the token appears in cache ,then proceed / later on when scaling i will add redis+in memory storage to avoid many api calls /overfetching


STK Push password generation:
Daraja requires a time-based password for every STK initiation:
  pasword = base64(shortcode + passkey + timestamp)
  timestamp =YYYYMMDDHmmss(UTC)
this is a uniqueness/replay-prevention mechanism not a securty credential .The passkey is the shared secret
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import httpx
from redis.asyncio import Redis
import structlog

from app.config import Settings
from app.exceptions import DarajaAuthError


logger = structlog.get_logger(__name__)

TOKEN_CACHE_KEY = "daraja:oauth:token"
TOKEN_LOCK_KEY = "daraja:oauth:lock"


class DarajaAuthManager:
    """Manages daraja oauth token acquistion and caching"""

    def __init__(
        self, settings: Settings, redis: Redis, http_client=httpx.AsyncClient
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._http = http_client

    async def get_token(self) -> str:
        """Return a valid bearer token, fetching a new on if expired"""
        try:
            cached = await self._redis.get(TOKEN_CACHE_KEY)
        except Exception as e:
            # redis unavailble- fall through to direct fetch
            logger.warning("token_cache_unavailable", error=str(e))
            return (
                await self._fetch_new_token()
            )  # degraded mode path,every request hits daraja

        if cached:
            return cached.decode()  # cache hit -fastest path

        return await self._acquire_lock_and_refresh()  # cache miss

    async def _acquire_lock_and_refresh(self) -> str:
        """Acquire the distributed lock ,then refresh the token
        uses redis SET NX PX(atomic conditional set) as a distributed lock
        """
        lock_ttl_ms = self._settings.token_lock_ttl_ms
        lock_acquired = await self._redis.set(
            TOKEN_LOCK_KEY, "1", nx=True, px=lock_ttl_ms
        )

        if lock_acquired:
            try:
                return await self._fetch_new_token()
            finally:
                await self._redis.delete(TOKEN_CACHE_KEY)

        # another  worker holds the lock - poll until token appears
        poll_interval = 0.25  # secs
        max_polls = int((lock_ttl_ms / 1000) / poll_interval)

        for _ in range(max_polls):
            # Loop asks has lock holder finished and written the token yet? every 250ms for 10secs
            await asyncio.sleep(poll_interval)  # non_blocking
            try:
                cached = await self._redis.get(TOKEN_CACHE_KEY)
                if cached:
                    return cached.decode()
            except Exception:
                pass  # redis blip keep polling

        # lock holder took too long -fetch directly as last resort
        logger.warning("token_lock_timeout_fallback")
        return await self._fetch_new_token()

    async def _fetch_new_token(self) -> str:
        """Call Daraja /oauth/v1/generate and cache the result,returns a fresh access token string"""
        # build basic auth credentials
        key = self._settings.daraja_consumer_key.get_secret_value()
        secret = self._settings.daraja_consumer_secret.get_secret_value()
        credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()

        log = logger.bind(operation="daraja_oauth_fetch")
        url = f"{self._settings.daraja_base_url}/oauth/v1/generate"

        # make the http request
        try:
            response = await self._http.get(
                url,
                params={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(
                    connect=self._settings.daraja_connect_timeout,  # 10s to establish tcp connection
                    read=self._settings.daraja_read_timeout,  # 30s for the response body
                    write=self._settings.daraja_write_timeout,
                    pool=self._settings.daraja_pool_timeout,
                ),
            )
            response.raise_for_status()

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            log.error("daraja_oauth_http_error", status=status)

            if status == 401:
                raise DarajaAuthError(
                    "Daraja OAuth 401 - check consumer key and secret.",
                    daraja_code=str(status),
                )
            raise DarajaAuthError(
                f"Daraja OAuth failed with HTTP {status}.",
                http_status=status,
            ) from e

        except httpx.TimeoutException as e:
            log.error("daraja_oauth_timeout")
            raise DarajaAuthError("Daraja OAuth request timed out.") from e

        except httpx.RequestError as e:
            log.error("daraja_oauth_request_error", error=str(e))
            raise DarajaAuthError(f"Daraja OAuth network error: {e}") from e

        data = response.json()

        if "access_token" not in data:
            log.error("daraja_oauth_missing_token", response_keys=list(data.keys()))
            raise DarajaAuthError(
                "Daraja OAuth response missing 'access_token'.",
                details={"response_keys": list(data.keys())},
            )
        token: str = data["access_token"]
        expire_in = int(data.get("expires_in", 3600))
        cache_ttl = max(60, expire_in - self._settings.token_refresh_margin_seconds)

        try:
            await self._redis.setex(TOKEN_CACHE_KEY, cache_ttl, token)
        except Exception as e:
            # cache write failure is non_fatal - token still valid for this request
            log.warning("token_cache_write_failed", error=str(e))

        log.info("daraja_token_refreshed", cache_ttl_seconds=cache_ttl)
        return token

    @staticmethod
    def generate_stk_password(shortcode: str, passkey: str) -> tuple[str, str]:
        """Generate the stk push request password and timestamp

        Daraja formula:
         timestamp  = datetime.utcnow().strftime("%Y%m%d%H%M%S")
         raw_string = shortcode + passkey + timestamp
         password   = base64.b64encode(raw_string.encode()).decode()

        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        raw = f"{shortcode}{passkey}{timestamp}"
        password = base64.b64encode(raw.encode()).decode()
        return password, timestamp
