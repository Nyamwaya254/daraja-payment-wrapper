from __future__ import annotations

from functools import lru_cache
import re
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir="/run/secrets",  # docker secrets mount
        case_sensitive=False,
        extra="ignore",
    )

    # daraja
    daraja_base_url: str = Field(
        default="https://sandbox.safaricom.co.ke",
        description="Switch to https://api.safaricom.co.ke in production.",
    )

    @field_validator("database_url")
    @classmethod
    def fix_asyncpg_scheme(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    daraja_consumer_key: SecretStr
    daraja_consumer_secret: SecretStr
    daraja_shortcode: str = Field(
        description="Business Short Code (Paybill or Till number).",
    )
    daraja_passkey: SecretStr = Field(
        description="Lipa Na M-Pesa Online passkey from Daraja portal.",
    )
    daraja_callback_base_url: str = Field(
        description=(
            "Public HTTPS base URL for Daraja callbacks. "
            "Must be HTTPS and reachable by Safaricom. "
            "Use ngrok in sandbox: https://xxxx.ngrok.io"
        ),
    )
    daraja_b2c_initiator_name: str
    daraja_b2c_security_credential: SecretStr

    # HTTP client
    daraja_connect_timeout: float = Field(default=10.0, gt=0)
    daraja_read_timeout: float = Field(default=30.0, gt=0)
    daraja_write_timeout: float = Field(default=10.0, gt=0)
    daraja_pool_timeout: float = Field(default=5.0, gt=0)
    daraja_max_connections: int = Field(default=20, gt=0)
    daraja_max_keepalive_connections: int = Field(default=10, gt=0)

    # circuit_breaker
    circuit_breaker_failure_threshold: int = Field(default=5, ge=1)
    circuit_breaker_recovery_timeout: int = Field(
        default=60,
        ge=1,
        description="Seconds the circuit stays OPEN before probing.",
    )

    # retry
    daraja_retry_attempts: int = Field(default=3, ge=1, le=5)
    daraja_retry_initial_wait: float = Field(default=1.0, gt=0)
    daraja_retry_max_wait: float = Field(default=10.0, gt=0)

    # postgreSQL fields
    postgres_user: str | None = None
    postgres_password: SecretStr
    postgres_server: str | None = None
    postgres_port: int = 5432
    postgres_db: str | None = None
    database_url: str | None = Field(
        default=None,
        description="asyncpg DSN: postgresql+asyncpg://user:pass@host:port/db",
    )

    @model_validator(mode="after")
    def build_database_url(self) -> "Settings":
        if not self.database_url and all(
            [
                self.postgres_user,
                self.postgres_server,
                self.postgres_db,
                self.postgres_password,
            ]
        ):
            pw = self.postgres_password.get_secret_value()
            self.database_url = f"postgresql+asyncpg://{self.postgres_user}:{pw}@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        return self

    database_pool_size: int = Field(default=10, ge=1, le=50)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout: float = Field(default=30.0, gt=0)
    database_pool_recycle: int = Field(
        default=1800,
        description="Recycle connections after 30 min to avoid stale connections.",
    )
    database_echo: bool = Field(
        default=False,
        description="Log all SQL — only for local debugging, never in production.",
    )

    # redis fields
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(default=20, ge=1)
    redis_socket_timeout: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout: float = Field(default=5.0, gt=0)

    # OAuth token cache
    # refreshh 5 min  before expiry to avoid using a token that expires mid-request
    token_refresh_margin_seconds: int = Field(default=300, ge=60)
    token_lock_ttl_ms: int = Field(
        default=10_000,
        description="TTL on the distributed lock preventing token thundering-herd.",
    )

    # idempotency fields
    idempotency_ttl_seconds: int = Field(
        default=86_400,
        description="24h — matches typical payment processing window.",
    )
    stk_lock_ttl_seconds: int = Field(
        default=30,
        description="Max time to hold the per-idempotency-key lock.",
    )

    # rate_limiting -token-bucket rate limiter
    # Daraja sandbox: ~30 req/min. Production: higher — confirm with Safaricom.
    stk_rate_limit_capacity: int = Field(default=20)
    stk_rate_limit_refill_rate: float = Field(
        default=0.5,
        description="Tokens per second = 30/min sustained.",
    )
    auth_rate_limit_capacity: int = Field(default=5)
    auth_rate_limit_refill_rate: float = Field(default=0.083)  # 5/min

    # safaricom callback IP allowlist
    safaricom_allowed_ips: list[str] = Field(
        default=[
            "196.201.214.200",
            "196.201.214.206",
            "196.201.213.114",
            "196.201.214.207",
            "196.201.214.208",
            "196.201.213.44",
            "196.201.212.127",
            "196.201.212.128",
            "196.201.212.129",
            "196.201.212.136",
            "196.201.212.74",
            "196.201.212.69",
        ],
    )
    trust_callback_ips_in_sandbox: bool = Field(
        default=True,
        description="Disable IP allowlist in sandbox — MUST be False in production.",
    )

    # observability fields
    log_level: str = Field(default="INFO")
    sentry_dsn: SecretStr | None = Field(default=None)
    environment: str = Field(default="development")
    service_name: str = Field(default="daraja-payment-service")

    # security
    # internal API authentication
    internal_api_keys: str = Field(
        description=(
            "Comma-separated list of valid API keys for internal service-to-service auth. "
            "Supports multiple keys for zero-downtime rotation. "
            "Example: 'key-abc123,key-xyz789'"
        )
    )

    @property
    def parsed_api_keys(self) -> set[str]:
        """Return the set of valid keys,stripping whitespace."""
        return {k.strip() for k in self.internal_api_keys.split(",") if k.strip()}

    # validators
    @field_validator("daraja_base_url", "daraja_callback_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("daraja_callback_base_url")
    @classmethod
    def must_be_https_in_prod(cls, v: str) -> str:
        """Allow http:// only for localhost/ngrok in dev"""
        if v.startswith("http://") and "localhost" not in v and "127.0.0.1" not in v:
            raise ValueError(
                "daraja_callback_base_url must use HTTPs"
                "Safaricom will not POST to plain HTTP endpoints"
            )
        return v

    @field_validator("daraja_shortcode")
    @classmethod
    def shortcode_must_be_numeric(cls, v: str) -> str:
        """Short-code are 5-7 digit numbers e.g 174379 for sandbox"""
        if not re.fullmatch(r"\d{5,7}", v):
            raise ValueError("daraja_shortcode must be 5-7 digits")
        return v

    @field_validator("database_url")
    @classmethod
    def must_use_asyncpg(cls, v: str) -> str:
        if v is None:
            return v
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "database_url must use the asyncpg driver:postgresql+asyncpg://"
            )
        return v

    @model_validator(mode="after")
    def prod_safety_checks(self) -> "Settings":
        is_daraja_production = "sandbox" not in self.daraja_base_url

        if is_daraja_production and self.trust_callback_ips_in_sandbox:
            raise ValueError(
                "trust_callback_ips_in_sandbox must be False when using "
                "Daraja production URL (api.safaricom.co.ke). "
                "Set DARAJA_BASE_URL to sandbox or set TRUST_CALLBACK_IPS_IN_SANDBOX=false."
            )

        if self.environment == "production" and self.database_echo:
            raise ValueError("database_echo must be False in production")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton settings instance"""
    """In tests , call get_settings.cache_clear() before overriding with a fixture"""
    return Settings()  # type: ignore[call-arg]
