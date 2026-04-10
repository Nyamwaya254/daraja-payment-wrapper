"""FastAPi dependency injection - complete wiring for all sessions"""

from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator
from fastapi import Depends
import httpx
from redis.asyncio import ConnectionPool, Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
    AsyncSession,
)

import structlog

from app.config import Settings, get_settings
from app.daraja.auth import DarajaAuthManager
from app.daraja.client import DarajaClient
from app.daraja.query import DarajaQueryClient
from app.daraja.stk import STKPushInitiator
from app.models.audit import AuditLogRepository
from app.observability import configure_observability
from app.repository.payment_repo import PaymentRepository
from app.services.stk import STKService


logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None
_redis_pool: ConnectionPool | None = None
_http_client: httpx.AsyncClient | None = None
_daraja_client: DarajaClient | None = None


@asynccontextmanager
async def lifespan(app):
    global _engine, _session_factory, _redis_pool, _http_client, _daraja_client
    configure_observability()

    settings = get_settings()
    log = logger.bind(service=settings.service_name, env=settings.environment)
    log.info("Startup_initialising")

    _engine = create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_recycle=settings.database_pool_recycle,
        pool_pre_ping=True,
        echo=settings.database_echo,
    )

    _session_factory = async_sessionmaker(
        bind=_engine, expire_on_commit=False, autocommit=False, autoflush=False
    )
    _redis_pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        decode_responses=False,
    )

    _http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=settings.daraja_max_connections,
            max_keepalive_connections=settings.daraja_max_keepalive_connections,
        ),
        follow_redirects=False,
    )

    redis_for_auth = Redis(connection_pool=_redis_pool)
    auth_manager = DarajaAuthManager(settings, redis_for_auth, _http_client)
    _daraja_client = DarajaClient(settings, auth_manager, _http_client)
    log.info("Startup_complete")
    yield

    log.info("Shutdown_initiated")
    if _http_client:
        await _http_client.aclose()
    if _engine:
        await _engine.dispose()
    if _redis_pool:
        await _redis_pool.aclose()
    log.info("Shutdown_complete")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Session factory is not initialised")
    async with _session_factory() as session:
        yield session


# db dep annotation
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def get_redis() -> AsyncGenerator[Redis, None]:
    if _redis_pool is None:
        raise RuntimeError("Redis pool not initialised")
    client = Redis(connection_pool=_redis_pool)
    try:
        yield client
    finally:
        await client.aclose()


async def get_settings_dep() -> Settings:
    return get_settings()


def get_daraja_client() -> DarajaClient:
    if _daraja_client is None:
        raise RuntimeError("Daraja client not initialised")
    return _daraja_client


# daraja dep annotation
DarajaDep = Annotated[DarajaClient, Depends(get_daraja_client)]


async def get_payment_repo(db: SessionDep) -> PaymentRepository:
    return PaymentRepository(db)


# payment repo dep annotation
PaymentRepoDep = Annotated[PaymentRepository, Depends(get_payment_repo)]


async def get_audit_repo(db: SessionDep) -> AuditLogRepository:
    return AuditLogRepository(db)


# audit repo dep annotation
AuditRepoDep = Annotated[AuditLogRepository, Depends(get_audit_repo)]


async def get_stk_service(
    db: SessionDep,
    payment_repo: PaymentRepoDep,
    settings: Settings = Depends(get_settings_dep),
    daraja_client: DarajaClient = Depends(get_daraja_client),
    redis: Redis = Depends(get_redis),
) -> STKService:
    initiator = STKPushInitiator(settings=settings, client=daraja_client)
    return STKService(
        settings=settings,
        initiator=initiator,
        payment_repo=payment_repo,
        redis=redis,
        db=db,
    )


# stk service dep annotation
STKServiceDep = Annotated[STKService, Depends(get_stk_service)]


async def get_query_client(
    daraja_client: DarajaDep,
    settings: Settings = Depends(get_settings_dep),
) -> DarajaQueryClient:
    return DarajaQueryClient(settings=settings, client=daraja_client)
