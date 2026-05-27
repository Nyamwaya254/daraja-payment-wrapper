"""
HTTP mapping lives in the exception handlers registered on the FastAPI app,
not in the exception classes themselves — keeping domain logic free of HTTP concerns.

RFC 7807 Problem Details: https://datatracker.ietf.org/doc/html/rfc7807
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppError(Exception):
    """Root of the application expection hierarchy"""

    message: str = ""
    code: str = "INTERNAL_ERROR"
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# Domain Errors (4xx)
@dataclass
class DomainError(AppError):
    """Base for business-rule violations .Maps to 4xx HTTP responses"""


@dataclass
class ValidationError(DomainError):
    """Input failed domain validation (beyond Pydantic schema checks)"""

    code: str = "VALIDATION_ERROR"


@dataclass
class DuplicatePaymentError(DomainError):
    """Idempotency key collision with different parameters"""

    code: str = "DUPLICATE_PAYMENT"


@dataclass
class PaymentNotFoundError(DomainError):
    """Payment record does not exist"""

    code: str = "PAYMENT_NOT_FOUND"


@dataclass
class RateLimitError(DomainError):
    """Client exceeded allowed request rate"""

    code: str = "RATE_LIMITER"
    retry_after: int = 60


# Infrastructure Errors (5xx)
@dataclass
class InfraError(AppError):
    """Base for infrastructure failures.Maps to 5xx responses"""


@dataclass
class DarajaError(InfraError):
    """Generic Daraja API failure"""

    code: str = "DARAJA_ERROR"
    daraja_code: str | None = None
    daraja_description: str | None = None
    http_status: int | None = None


@dataclass
class DarajaAuthError(DarajaError):
    """OAUTH token acquisition or refresh failed"""

    code: str = "DARAJA_AUTH_ERROR"


@dataclass
class DarajaCircuitOpenError(DarajaError):
    """Circuit breaker is open ;Daraja is currently unreachable"""

    code: str = "DARAJA_CIRCUIT_OPEN"
    retry_after: int = 60


@dataclass
class DarajaTimeoutError(DarajaError):
    """Daraja api did not respond within the configured timeout"""

    code: str = "DARAJA_TIMEOUT"


@dataclass
class DatabaseError(InfraError):
    """DB operation failed"""

    code: str = "DATABASE_ERROR"


@dataclass
class CacheError(InfraError):
    """Redis operation failed,Non-fatal - callers should degrade gracefully"""

    code: str = "CACHE_ERROR"


# Daraja ResultCode -> AppError mapping

# Map Daraja STK Resultcode intergers to (exception_class,is_retryable)
DARAJA_RESULT_CODE_MAP: dict[int, tuple[type[DarajaError], bool]] = {
    0: (DarajaError, False),  # success - not an error
    1: (DarajaError, False),  # Insufficient funds - permanent
    17: (DarajaError, False),  # Risk management - permanent
    1001: (DarajaError, True),  # Unable to lock subscriber - transient
    1019: (DarajaError, False),  # Transaction expired
    1025: (DarajaError, True),  # Unable to complete transaction - transient
    1032: (DarajaError, False),  # Cancelled by user — permanent
    1037: (DarajaError, True),  # DS timeout, subscriber unreachable — transient
    2001: (DarajaError, False),  # Wrong PIN — permanent
    9999: (DarajaError, True),  # Internal system error — transient
}


DARAJA_RESPONSE_CODE_MAP: dict[str, str] = {
    "0": "success",
    "400.002.02": "Bad request — missing parameters",
    "400.002.05": "Invalid timestamp",
    "400.002.10": "Bad request — invalid shortcode",
    "401.002.01": "Unauthorized — invalid consumer key",
    "500.001.1001": "Unable to lock subscriber",
    "500.002.1001": "Duplicate originator conversation ID",
}


def daraja_result_code_to_exception(
    result_code: int,
    result_desc: str,
) -> DarajaError | None:
    """Converts a daraja callback ResultCode to an AppError, or None for success"""

    if result_code == 0:
        return None

    exc_class, _ = DARAJA_RESULT_CODE_MAP.get(result_code, (DarajaError, True))
    return exc_class(
        message=result_desc,
        daraja_code=str(result_code),
        daraja_description=result_desc,
    )
