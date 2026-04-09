"""Modele-level infrastructure singletons for celery workers
These are created once per worker process and reused across all tasks.
"""

from __future__ import annotations
import asyncio
import atexit
from typing import Optional
import httpx
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
    AsyncSession,
)
from celery.signals import worker_process_shutdown

from app.config import get_settings


_settings = get_settings()

# DB engine
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker] = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(
            _settings.database_url,
            pool_size=_settings.database_pool_size,
            max_overflow=_settings.database_max_overflow,
            pool_pre_ping=True,
            echo=_settings.database_echo,
        )
        _session_factory = async_sessionmaker(
            bind=_engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the singleton async session factory."""
    global _session_factory
    if _session_factory is None:
        get_engine()
        # Ensure engine is created
    return _session_factory


# Redis client
_redis: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    """Get or create the singleton Redis client."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            _settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
    return _redis


# http client
_http: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """GEt or create the singleton HTTP client (with connection pooling)."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=_settings.daraja_max_keepalive_connections,
                max_connections=_settings.daraja_max_connections,
            ),
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
    return _http


# cleanup functions
async def _close_resources() -> None:
    """Close all singleton resources"""
    global _engine, _redis, _http
    if _redis:
        await _redis.aclose()
    if _http:
        await _http.aclose()
    if _engine:
        await _engine.dispose()


def _sync_close_resources() -> None:
    """Synchronous wrapper for celery signals (which are not async)"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_close_resources())
        else:
            loop.run_until_complete(_close_resources())
    except RuntimeError:
        # no event loop- create a temporary one
        asyncio.run(_close_resources())


# Register cleanup on worker shutdown(celery signal)
@worker_process_shutdown.connect
def on_worker_shutdown(**kwargs) -> None:
    """Clean up infrastructure when the worker process exits."""
    _sync_close_resources()


# register atexit as fallback
atexit.register(_sync_close_resources)
