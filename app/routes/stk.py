"""STK Push API routes
ACK pattern for callbacks:
  Daraja has a 5-second callback timeout. Exceed it and Daraja marks the
  callback as failed and may retry. We ACK immediately (HTTP 200) and
  process asynchronously via BackgroundTasks.

  Risk: if the process dies after ACK but before BackgroundTask completes,
  the callback is lost. Mitigate: for critical payments, implement a
  Celery task that queries Daraja Transaction Status API as a fallback.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks
import structlog

from app.dependencies import STKServiceDep
from app.exceptions import PaymentNotFoundError
from app.schemas.stk import (
    DarajaCallbackAck,
    PaymentStatusResponse,
    STKCallbackBody,
    STKCallbackEnvelope,
    STKPushInitiateRequest,
    STKPushInitiateResponse,
)
from app.services.stk import STKService


logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["STK Push"])


async def _safe_process_callback(
    service: STKService,
    callback: STKCallbackBody,
) -> None:
    try:
        await service.process_callback(callback)
    except Exception:
        logger.exception(
            "callback_background_task_failed",
            checkout_id=callback.CheckoutRequestID,
        )


@router.post(
    "/payments/stk",
    response_model=STKPushInitiateResponse,
    status_code=202,
    summary="Initiate STK Push",
    description=(
        "Send an M-Pesa payment prompt to the customer's phone. "
        "The customer enters their PIN to authorise. "
        "Payment outcome is delivered asynchronously via Daraja callback. "
        "Poll GET /payments/{payment_id}/status while waiting."
    ),
    responses={
        202: {"description": "STK Push accepted by Daraja. Awaiting customer PIN."},
        409: {
            "description": "Idempotency key collision — concurrent request in progress."
        },
        422: {"description": "Input validation failed."},
        429: {"description": "Rate limit exceeded. See Retry-After header."},
        503: {"description": "Daraja API unavailable (circuit open)."},
    },
)
async def initiate_stk_push(
    body: STKPushInitiateRequest,
    service: STKServiceDep,
) -> STKPushInitiateResponse:
    """initiate an STK Push
    The response arrives immediately but the payment is NOT complete.
    The customer has received a PIN prompt and has up to 60 seconds to respond.

    Use the returned `payment_id` to poll `/payments/{payment_id}/status`
    """
    return await service.initiate_stk_push(body)


@router.get(
    "/payments/{payment_id}/status",
    response_model=PaymentStatusResponse,
    summary="Get Payment status",
    description="Poll payment status. PENDING → COMPLETED or FAILED after callback.",
    responses={
        200: {"description": "Payment found."},
        404: {"description": "No payment with this ID."},
    },
)
async def get_payment_status(
    payment_id: str,
    service: STKServiceDep,
) -> PaymentStatusResponse:
    """Poll payment status by internal ID
    polling strategy:
      - Poll immediately after initiation
      - Then poll at 5s, 10s, 20s, 40s, 80s intervals
      - Stop polling after 5 minutes — consider PENDING beyond that as timed out
    """
    result = await service.get_payment_status(payment_id)
    if not result:
        raise PaymentNotFoundError(
            f"No payment with id '{payment_id}'.",
            details={"payment_id": payment_id},
        )
    return result


@router.post(
    "/callback/stk",
    response_model=DarajaCallbackAck,
    status_code=200,
    summary="STK Push callback (Daraja -> my server)",
    description=(
        "Daraja posts payment outcomes here. "
        "This endpoint must be publicly reachable via HTTPS. "
        "ACKs immediately; processes payment outcome asynchronously."
    ),
    include_in_schema=True,
)
async def stk_push_callback(
    body: STKCallbackEnvelope,
    background_tasks: BackgroundTasks,
    service: STKServiceDep,
) -> DarajaCallbackAck:
    """Receive and ACK an STK Push callback from Daraja
    Critical constraints:
      - Must respond HTTP 200 within 5 seconds (Daraja timeout)
      - Must return {"ResultCode": "0", "ResultDesc": "Accepted"}
      - Must be idempotent (Daraja sends duplicates)
      - Must be on a public HTTPS URL (not localhost)

    The actual payment processing (DB writes, downstream events) runs
    in a BackgroundTask after the 200 ACK is sent.
    """
    try:
        callback = body.extract()
    except ValueError as e:
        logger.error(
            "stk_callback_parse_failed", error=str(e), body_keys=list(body.Body.keys())
        )
        # still ACk - malformed callbacks should not trigger Daraja retries indefinitely
        return DarajaCallbackAck(ResultCode="0", ResultDesc="Accepted")
    logger.info(
        "stk_callback_received",
        checkout_request_id=callback.CheckoutRequestID,
        merchant_request_id=callback.MerchantRequestID,
        result_code=callback.ResultCode,
        is_success=callback.is_success,
    )

    # Queue async processing - weve already ACKed at this point
    background_tasks.add_task(_safe_process_callback, service, callback)

    return DarajaCallbackAck(ResultCode="0", ResultDesc="Accepted")
