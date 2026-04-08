"""Reconciliation and Dead Letter Queues tasks - scheduled viaa celery Beat"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, select, update

import structlog

from app.config import get_settings
from app.daraja.auth import DarajaAuthManager
from app.daraja.client import DarajaClient
from app.daraja.query import DarajaQueryClient
from app.models.payment import Payment, PaymentStatus
from app.tasks import celery_app
from app.tasks._infra import get_http_client, get_redis, get_session_factory
from app.tasks._worker_utils import run_async


logger = structlog.get_logger(__name__)

_MIN_BALANCE_THRESHOLD_KES = 50_000


@celery_app.task(
    name="app.tasks.reconciliation_tasks.expire_stale_pending_payments",
    bind=True,
    max_retries=1,
    ignore_result=True,
)
def expire_stale_pending_payments(self) -> None:
    """Find PENDING payments older than 10 min and resolve via Transaction Status
    Runs every 5 minutes via Celery Beat
    """
    log = logger.bind(task="expire_stale_pending_payments")

    async def _run() -> None:
        settings = get_settings()
        stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)

        session_factory = get_session_factory()
        redis_client = get_redis()
        http_client = get_http_client()

        # create a lightweight db session
        async with session_factory() as session:
            # fetch stale pending payments (limit 50 per batch)

            result = await session.execute(
                select(
                    Payment.id,
                    Payment.mpesa_receipt,
                    Payment.checkout_request_id,
                    Payment.payment_type,
                )
                .where(
                    and_(
                        Payment.status == PaymentStatus.PENDING.value,
                        Payment.created_at < stale_cutoff,
                    )
                )
                .limit(50)
            )
            stale_payments = result.all()

        if not stale_payments:
            log.debug("no_stale_payments")
            return
        log.info("stale_payments_found", count=len(stale_payments))

        auth = DarajaAuthManager(settings, redis_client, http_client)
        daraja = DarajaClient(settings, auth, http_client)
        query_client = DarajaQueryClient(settings, daraja)

        for row in stale_payments:
            try:
                if row.mpesa_receipt:
                    await query_client.query_transaction_status(
                        transaction_id=row.mpesa_receipt,
                        remarks=f"Stale PENDING reconciliation: {row.id[:8]}",
                    )
                    log.info("reconciliation_status_query_sent", payment_id=row.id)
                else:
                    log.warning(
                        "stale_pending_no_receipt_marking_failed", payment_id=row.id
                    )
                    # seperate session to update payment
                    async with session_factory() as session:
                        async with session.begin():
                            await session.execute(
                                update(Payment)
                                .where(Payment.id == row.id)
                                .values(
                                    status=PaymentStatus.FAILED.value,
                                    failure_reason=(
                                        "No callback received within 10 mins"
                                    ),
                                    result_code=-99,
                                )
                            )
            except Exception as exc:
                log.error(
                    "reconciliation_query_failed", payment_id=row.id, error=str(exc)
                )

    # run async coroutine from sync celery tasks
    try:
        run_async(_run())
    except Exception as exc:
        log.error("expire_stale_payments_task_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
