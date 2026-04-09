"""
Queue topology
  default       — general async work, normal priority
  callbacks     — STK/C2B callbacks from Daraja (high priority)
  reconciliation — scheduled Celery Beat jobs (low priority)
  dead_letter   — tasks that exhausted all retries (for ops inspection)
Dead letter strategy
  On final retry exhaustion, tasks are re-queued to `dead_letter` via
  task_failure signal. The dead_letter queue is NOT auto-consumed — ops
  inspects and replays via `celery -A app.tasks.celery_app call`.
Retry policy:
  Default: 3 retries, exponential backoff (30s → 150s → 450s)
  Callback tasks: 5 retries (Daraja can be slow to propagate state)
"""

from __future__ import annotations
from celery import Celery
from kombu import Exchange, Queue
from celery.signals import task_failure
import structlog

from app.config import get_settings


_settings = get_settings()

_default_exchange = Exchange("default", type="direct")
_callback_exchange = Exchange("callbacks", type="direct")
_dlq_exchange = Exchange("dead_letter", type="direct")

# Queue definitions
# Each queue binds to an exchange with a specific routing key
QUEUES = (
    Queue("default", _default_exchange, routing_key="default"),
    Queue("callbacks", _callback_exchange, routing_key="callbacks"),
    Queue("reconciliation", _default_exchange, routing_key="reconciliation"),
    Queue("dead_letter", _dlq_exchange, routing_key="dead_letter"),
)


# app factory
celery_app = Celery(
    "mpesa_stk",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=[
        "app.tasks.stk_tasks",
        # "app.tasks.c2b_tasks",
        "app.tasks._infra",
        "app.tasks._worker_utils",
        "app.tasks.reconciliation_tasks",
    ],
)

# configuration update
celery_app.conf.update(
    # serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # reliability
    tasks_acks_late=True,  # ACK only after task completes
    task_reject_on_worker_lost=True,  # Requeue if worker dies mid-task
    worker_prefetch_multiplier=1,  # One task at a time per worker
    # Queue routing
    task_queues=QUEUES,
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    task_routes={
        "app.tasks.stk_tasks.*": {"queue": "callbacks"},
        "app.tasks.c2b_tasks.*": {"queue": "callbacks"},
        "app.tasks.reconciliation_tasks.*": {"queue": "reconciliation"},
    },
    # backend result
    result_expires=3600,
    task_ignore_results=True,
    # rate limiting
    task_annotations={
        "app.tasks.stk_tasks.process_stk_callback": {"rate_limit": "30/m"},
        "app.tasks.c2b_tasks.process_c2b_confirmation": {"rate_limit": "30/m"},
    },
    # Retries
    task_soft_time_limit=55,  # Raise SoftTimeLimitExceeded at 55s
    task_time_limit=60,  # SIGKILL at 60s
    # celry beat schedule
    beat_schedule={
        "expire-stale-pending-payments": {
            "task": "app.tasks.reconciliation_tasks.expire_stale_pending_payments",
            "schedule": 300.0,  # every 5 mins
            "options": {"queue": "reconciliation"},
        },
        "check-daraja-account-balance": {
            "task": "app.tasks.reconciliation_tasks.check_account_balance",
            "schedule": 3600.0,  # every hour
            "options": {"queue": "reconciliation"},
        },
    },
    beat_schedule_filename="/tmp/celerybeat-schedule",
    timezone="Africa/Nairobi",
)


# dead letter queue signal
@task_failure.connect
def handle_task_falure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    eiinfo=None,
    **_kwargs,
) -> None:
    """On final retry exhaustion, push to dead letter queue for ops inspection"""
    logger = structlog.get_logger(__name__)
    # check if this is the final attempt
    request = getattr(sender, "request", None)
    retries = getattr(request, "retries", 0) if request else 0
    max_retries = getattr(sender, "max_retries", 3) if sender else 3

    if retries >= max_retries:
        logger.error(
            "task_moved_to_dead_letter",
            task_id=task_id,
            task_name=getattr(sender, "name", "unknown"),
            exception=str(exception),
            args=args,
            kwargs=kwargs,
        )
        # re-publish to dlq for ops inspection
        celery_app.send_task(
            "app.tasks.reconciliation_tasks.dead_letter_inspect",
            kwargs
            - {
                "original_task_name": getattr(sender, "name", "unknown"),
                "original_task_id": task_id,
                "exception_str": str(exception),
                "original_args": list(args or []),
                "original_kwargs": dict(kwargs or {}),
            },
            queue="dead_letter",
            ignore_result=True,
        )
