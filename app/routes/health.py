"""Health check endpoints for kubernetes liveness and readiness probes"""

from sqlalchemy import text
import time
from typing import Any
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import structlog

from redis.asyncio import Redis

from app.dependencies import SessionDep, get_redis


logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Health"])

_start_time = time.time()


@router.get("/health", include_in_schema=False)
async def liveness() -> dict[str, str]:
    """Liveness probe — confirms the process is alive and event loop is running.

    Returns 200 always. If this fails, the process is dead or deadlocked.
    """

    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
async def readiness(
    db: SessionDep,
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    """Readiness probe - confirms the service can handle traffic
    Checks:
      - PostgreSQL connectivity (SELECT 1)
      - Redis connectivity (PING)

    Returns 200 if all checks pass, 503 if any fail.
    Kubernetes removes unhealthy pods from the load balancer on 503.
    """
    checks: dict[str, Any] = {}
    all_healthy = True

    # postgreSQL
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        logger.error("readiness_postgres_failed", error=str(e))
        checks["postgres"] = f"error: {type(e).__name__}"
        all_healthy = False

    # Redis
    try:
        pong = await redis.ping()
        checks["redis"] = "ok" if pong else "no_response"
        if not pong:
            all_healthy = False
    except Exception as e:
        logger.error("readiness_redis_failed", error=str(e))
        checks["redis"] = f"error: {type(e).__name__}"
        all_healthy = False
    status_code = 200 if all_healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_healthy else "not_ready",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "checks": checks,
        },
    )
