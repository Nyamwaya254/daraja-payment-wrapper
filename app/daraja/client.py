"""
Daraja API HTTP client with circuit breaker and retry.

Circuit breaker states:
    Closed -> requests flow normally
    Open -> requests fail fast( no daraja calls)
    Half-Open -> small no of requests allowed,closes on success,opens on failure

RETRY POLICY(tenacitu)
-Retries only on transient network errors(timeout,connection reset)
-Does not retry Daraja 4xx- those are permanent failures
-Does not retry on certian Daraja ResultCode (Insufficient Funds)
-Exponential backoff with jitter prevents thundering-herd on recovery

We will use per-process circuit breaker not Redis-backed) because:
    - Simpler. No Redis round-trip on the hot path.
    - Trade-off: each worker has independent state — one worker may open its
    circuit while another hasn't. Acceptable for most scales.
    - For true cross-process circuit breaking, store failure_count in Redis
    and use a Lua script for atomic check-and-increment.
"""

from __future__ import annotations
from enum import Enum, auto
import time
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings
from app.daraja.auth import DarajaAuthManager
from app.exceptions import DarajaCircuitOpenError, DarajaError, DarajaTimeoutError

logger = structlog.get_logger(__name__)


# Daraja response codes that must NOT be retried(permanent business failures)
NON_RETRYABLE_RESPONSE_CODES = frozenset(
    {
        "400.002.02",  # Bad request — missing parameters
        "400.002.05",  # Invalid timestamp
        "400.002.10",  # Invalid shortcode
        "401.002.01",  # Unauthorized
    }
)


class CircuitState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class DarajaClient:
    """
    One instance per process - shared across all request handlers via DI
    The CB state and counters are instance variables (process local)

    Handles:
    -  token injection
    - Exponential backoff retry for transient errors (5xx, timeouts)
    - Circuit breaker to prevent cascade failures
    - Structured request/response logging
    """

    def __init__(
        self,
        settings: Settings,
        auth_manager: DarajaAuthManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._auth = auth_manager
        self._http = http_client

        self._failure_count: int = 0
        self._last_failure_time: float = (
            0.0  # last_failure_time refers to when the cicuit opened
        )
        self._state: CircuitState = CircuitState.CLOSED

    # Circuit breaker
    def _check_circuit(self) -> None:
        """'Raise DarajaCircuitOpenError if the circuit  is open

        Transitions OPEN -> HALF_OPEN when recovery_timeout has elapsed.

        Raises:
            DarajaCircuitOpenError: When circuit is OPEN  and timeout not elapsed
        """
        if self._state == CircuitState.CLOSED:
            return
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            timeout = self._settings.circuit_breaker_recovery_timeout
            if elapsed >= timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("circuit_half_open", elapsed=round(elapsed, 1))
            else:
                retry_after = int(timeout - elapsed)
                raise DarajaCircuitOpenError(
                    f"Daraja circuit is OPEN. Retry after {retry_after}s.",
                    retry_after=retry_after,
                )

        # HALF-OPEN : let the request through as a probe

    def _on_success(self) -> None:
        if self._state != CircuitState.CLOSED:
            logger.info("circuit_closed", previous_state=self._state.name)
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        threshold = self._settings.circuit_breaker_failure_threshold

        if self._failure_count >= threshold or self._state == CircuitState.HALF_OPEN:
            self._state == CircuitState.OPEN
            logger.warning(
                "circuit_opened",
                failure_count=self._failure_count,
                threshold=threshold,
            )

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send an authenticated POST REQUEST to daraja with retry + circuit breaker"""
        self._check_circuit()

        url = f"{self._settings.daraja_base_url}{path}"
        log = logger.bind(daraja_path=path, url=url)

        last_exception: Exception | None = None

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(
                    (httpx.TimeoutException, httpx.TransportError)
                ),
                stop=stop_after_attempt(self._settings.daraja_retry_attempts),
                wait=wait_exponential_jitter(
                    initial=self._settings.daraja_retry_initial_wait,
                    max=self._settings.daraja_retry_max_wait,
                ),
                reraise=False,  # when retries exhausted,dont auto reraise the last exception
            ):
                with attempt:
                    last_exception = None
                    try:
                        result = await self._execute_request(url, payload, log)
                        self._on_success()
                        return result
                    except (httpx.TimeoutException, httpx.TransportError) as e:
                        last_exception = e
                        self._on_failure()
                        attempt_number = attempt.retry_state.attempt_number
                        log.warning(
                            "daraja_transient_error",
                            attempt=attempt_number,
                            max_attempts=self._settings.daraja_retry_attempts,
                            error=str(e),
                        )
                        raise  # tenacity catches this and decided retry or stop

                    except DarajaError:
                        # non-retryable error -propagate immediately
                        raise

        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise DarajaTimeoutError(
                f"Daraja request to {path} timed out after {self._settings.daraja_retry_attempts} attempts",
                daraja_description=str(e),
            ) from e
        if last_exception:
            raise DarajaTimeoutError(
                f"Daraja request exhausted {self._settings.daraja_retry_attempts} retry attempts"
            )
        raise DarajaError(f"Unexpected state after retrying Daraja request to {path}.")

    async def _execute_request(
        self,
        url: str,
        payload: dict[str, Any],
        log: structlog.BoundLogger,
    ) -> dict[str, Any]:
        """Execute a single HTTP request to Daraja"""

        token = await self._auth.get_token()

        start = time.perf_counter()
        response = await self._http.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=self._settings.daraja_connect_timeout,
                read=self._settings.daraja_read_timeout,
            ),
        )
        latency_ms = (time.perf_counter() - start) * 1000

        log.info(
            "daraja_response",
            status=response.status_code,
            latency_ms=round(latency_ms, 2),
        )

        if response.status_code >= 500:
            # 5xx - transient let tenacity decide retry
            self._on_failure()
            raise httpx.TransportError(f"Daraja 5xx: {response.status_code}")

        if response.status_code == 401:
            # token expire mid-request(race with expiry)
            # invalidate cache and raise -- the retry will re-fetch
            try:
                from app.daraja.auth import TOKEN_CACHE_KEY

                await self._auth._redis.delete(TOKEN_CACHE_KEY)
            except Exception:
                pass
            raise DarajaError(
                "Daraja returned 401 - token invalidated, will retry with fresh token",
                http_status=401,
            )
        if response.status_code >= 400:
            # 4xx except 401 - Permanent failure, no retry
            try:
                body = response.json()
                error_code = body.get("errorCode", str(response.status_code))
                error_message = body.get("errorMessage", response.text[:200])
            except Exception:
                error_code = str(response.status_code)
                error_message = response.text[:200]

            log.warning(
                "daraja_client_error",
                status=response.status_code,
                error_code=error_code,
                error_message=error_message,
            )
            raise DarajaError(
                f"Daraja client error {response.status_code}: {error_message}",
                http_status=response.status_code,
                daraja_code=error_code,
                daraja_description=error_message,
            )

        return response.json()
