"""STK Push Celery tasks- durable callback processing"""

from __future__ import annotations
from typing import Any

import structlog
from celery.exceptions import MaxRetriesExceededError


from app.config import get_settings
from app.daraja.auth import DarajaAuthManager
from app.daraja.client import DarajaClient
from app.daraja.stk import STKPushInitiator
from app.repository.payment_repo import PaymentRepository
from app.schemas.stk import STKCallbackBody, STKPushInitiateRequest
from app.services.stk import STKService
from app.tasks import celery_app
from app.tasks._infra import get_http_client, get_redis, get_session_factory
from app.tasks._worker_utils import run_async

logger = structlog.get_logger(__name__)

_BACKOFF_BASE = 30
_MAX_RETRIES = 5


@celery_app.task(
    name="app.tasks.stk_tasks.process_stk_callback",
    bind=True,
    max_retries=_MAX_RETRIES,
    default_retry_delay=_BACKOFF_BASE,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=55,
    time_limit=60,
)
def process_stk_callback(self, payload: dict[str, Any]):
    """Process an STK push callback payload durably"""

    log = logger.bind(
        task_id=self.request.id,
        retry_count=self.request.retries,
        checkout_request_id=payload.get("CheckoutRequestID"),
        result_code=payload.get("ResultCode"),
    )
    # parse payload -fail fast, no retry on malformed
    try:
        callback = STKCallbackBody.model_validate(payload)
    except Exception as exc:
        log.error("stk_task_callback_parse_failed", error=str(exc))
        return  # malformed no need to retry

    async def _process() -> None:
        settings = get_settings()
        session_factory = get_session_factory()
        redis_client = get_redis()
        http_client = get_http_client()

        # create a new db session for this transaction
        async with session_factory() as session:
            auth = DarajaAuthManager(settings, redis_client, http_client)
            client = DarajaClient(settings, auth, http_client)
            initiator = STKPushInitiator(settings, client)
            repo = PaymentRepository(session)
            service = STKService(
                settings=settings,
                initiator=initiator,
                payment_repo=repo,
                redis=redis_client,
                db=session,
            )
            await service.process_callback(callback)

    try:
        run_async(_process())
        log.info("stk_task_callback_processed")
    except Exception as exc:
        retry_in = _BACKOFF_BASE * (2**self.request.retries)
        log.warning("stk_task_retrying", error=str(exc), retry_in=retry_in)
        try:
            raise self.retry(exc=exc, countdown=retry_in)
        except MaxRetriesExceededError:
            log.error(
                "stk_task_max_retries_exceeded",
                checkout_request_id=payload.get("CheckoutRequestID"),
            )
            raise


@celery_app.task(
    name="app.tasks.stk_tasks.initiate_stk_push_async",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def initiate_stk_push_async(self, request_data: dict[str, Any]) -> dict[str, Any]:
    """Initiate an STK Push asynchronously for bulk/batch scenarios."""
    log = logger.bind(
        task_id=self.request.id,
        idempotency_key=request_data.get("idempotency_key"),
    )

    async def _initiate() -> dict:
        settings = get_settings()
        session_factory = get_session_factory()
        redis_client = get_redis()
        http_client = get_http_client()

        request = STKPushInitiateRequest.model_validate(request_data)
        async with session_factory() as session:
            auth = DarajaAuthManager(settings, redis_client, http_client)
            client = DarajaClient(settings, auth, http_client)
            initiator = STKPushInitiator(settings, client)
            repo = PaymentRepository(session)
            service = STKService(
                settings=settings,
                initiator=initiator,
                payment_repo=repo,
                redis=redis_client,
                db=session,
            )
            result = await service.initiate_stk_push(request)
            return result.model_dump()

    try:
        result = run_async(_initiate())
        log.info("stk_async_initiatiaton_complete", payment_id=result.get("payment_id"))
        return result
    except Exception as exc:
        log.warning("stk_async_initiation_failed", error=str(exc))
        try:
            raise self.retry(exc=exc, countdown=10 * (3**self.request.retries))
        except MaxRetriesExceededError:
            log.error("stk_async_initiation_max_retries")
            raise
