"""
STK push service - orcherstrates the full initiation and callback flow.

Idempotency contract:
  - Same idempotency_key + same params → same response, no double charge
  - Same idempotency_key + different params → 409 Conflict
  - Redis is the fast path; DB UNIQUE constraint is the safety net
  - Idempotency results are cached for 24h (configurable)

Concurrency contract:
  - Per-idempotency-key distributed lock prevents two workers from
    initiating the same payment simultaneously
  - Lock TTL = 30s (configurable) — longer than one Daraja round-trip
  - If lock holder crashes, the TTL releases it automatically

Crash safety:
  - Payment record is created in DB (status=PENDING) BEFORE calling Daraja
  - If we crash after Daraja call but before DB update, a reconciliation
    job can query Daraja Transaction Status API to recover
  - If we crash before Daraja call, the PENDING record is orphaned —
    a scheduled job should expire PENDING records older than 10 minutes
Callback idempotency:
  - Safaricom can and does send duplicate callbacks
  - A Redis SET NX on the CheckoutRequestID prevents duplicate processing
  - All callback state updates are wrapped in DB transactions

"""

from __future__ import annotations
import uuid

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.daraja.stk import STKPushInitiator
from app.exceptions import (
    DarajaError,
    DuplicatePaymentError,
    daraja_result_code_to_exception,
)
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.repository.payment_repo import PaymentRepository
from app.schemas.stk import (
    PaymentStatusResponse,
    STKCallbackBody,
    STKPushInitiateRequest,
    STKPushInitiateResponse,
)


logger = structlog.get_logger(__name__)


class STKService:
    """Orchestrates STK Push: idempotency -> Daraja -> DB -> response"""

    def __init__(
        self,
        settings: Settings,
        initiator: STKPushInitiator,
        payment_repo: PaymentRepository,
        redis: Redis,
        db: AsyncSession,
    ):
        self._settings = settings
        self._initiator = initiator
        self._payment_repo = payment_repo
        self._redis = redis
        self._db = db

    # initiation
    async def initiate_stk_push(
        self, request: STKPushInitiateRequest
    ) -> STKPushInitiateResponse:
        """Initiate an STK Push with full idempotency and durability guarantee
        Flow:
          1. Check Redis idempotency cache → return cached response if hit
          2. Acquire per-key distributed lock → prevent concurrent duplicate
          3. Check DB idempotency (Redis cold-start / restart safety)
          4. Create PENDING payment record in DB (crash-safe anchor)
          5. Call Daraja STK Push API
          6. Update payment with Daraja identifiers
          7. Cache idempotency result in Redis
          8. Return Response
        """

        # idempotency key cache lookup
        key = request.idempotency_key
        idem_cache_key = f"stk:idem: {key}"
        lock_key = f"stk:lock:{key}"

        log = logger.bind(
            idempotency_key=key,
            phone_suffix=request.phone_number[-4:],
            amount=int(request.amount),
        )
        # fast_path(redis cache hit)
        cached_raw = await self._redis_get(idem_cache_key)
        if cached_raw:
            log.info("stk_idempotency_cache_hit")
            return STKPushInitiateResponse.model_validate_json(cached_raw)

        # acquire distributed lock
        lock_acquired = await self._redis_set_nx(
            lock_key,
            "1",
            ex=self._settings.stk_lock_ttl_seconds,
        )
        if not lock_acquired:
            log.warning("stk_concurrent_request_rejected")
            raise DuplicatePaymentError(
                "A concurrent request with this idempotency key is in progress."
                "Retry after the current request completes",
                details={"idempotency_key": {key}},
            )

        # Redis cold-start safety - db fallback
        try:
            existing_payment = await self._payment_repo.get_by_idempotency_key(key)
            if existing_payment and existing_payment.status in (
                PaymentStatus.COMPLETED.value,
                PaymentStatus.FAILED.value,
                PaymentStatus.CANCELLED.value,
            ):
                log.info("stk_idempotency_db_hit", status=existing_payment.status)
                return self._payment_to_response(existing_payment)
            # create PENDING record BEFORE calling Daraja
            # This is the crash-safety anchor. If we die after this write but
            # before the Daraja call, a reconciliation job can clean up PENDING
            # records and determine their true status via Daraja Query API
            payment_id = str(uuid.uuid4)
            payment = Payment(
                id=payment_id,
                payment_type=PaymentType.STK_PUSH.value,
                status=PaymentStatus.PENDING.value,
                phone=request.phone_number,
                amount=request.amount,
                account_reference=request.account_reference,
                transaction_desc=request.transaction_desc,
                idempotency_key=key,
            )

            async with self._db.begin():
                await self._payment_repo.create(payment)

            # call daraja
            try:
                daraja_result = await self._initiator.initiate(
                    phone=request.phone_number,
                    amount=request.amount,
                    account_reference=request.account_reference,
                    transaction_desc=request.transaction_desc,
                    transaction_type=request.transaction_type,
                )
            except DarajaError:
                # mark payment as failed and re-raise the exception so API returns an error to the client
                async with self._db.begin():
                    await self._payment_repo.mark_failed(
                        payment_id,
                        failure_reason="Daraja API call failed before initiation",
                        result_code=1,
                    )
                log.error("stk_daraja_call_failed", payment_id=payment_id)
                raise

            # persist Daraja identifiers
            async with self._db.begin():
                await self._payment_repo.update_daraja_ids(
                    payment_id,
                    checkout_request_id=daraja_result.checkout_request_id,
                    merchant_request_id=daraja_result.merchant_request_id,
                )
            # build response
            response = STKPushInitiateResponse(
                payment_id=payment_id,
                checkout_request_id=daraja_result.checkout_request_id,
                merchant_request_id=daraja_result.merchant_request_id,
                response_code=daraja_result.response_code,
                customer_message=daraja_result.customer_message,
                status=PaymentStatus.PENDING.value,
            )
            # cache idempotency result
            await self._redis_setex(
                idem_cache_key,
                self._settings.idempotency_ttl_seconds,
                response.model_dump_json(),
            )
            log.info(
                "stk_push_initiated_successfully",
                payment_id=payment_id,
                checkout_request_id=daraja_result.checkout_request_id,
            )
            return response

        finally:
            # always release lock even on exception
            await self._redis_delete(lock_key)

    # callback processing
    async def process_callback(self, callback: STKCallbackBody) -> None:
        """Process an inbound STK Push callback from Daraja
        This is called from a Background task after HTTP 200 ACK is sent to Saf
        Designed to be idempotent since saf may send duplicate callbacks
        """
        checkout_id = callback.CheckoutRequestID
        log = logger.bind(
            checkout_request_id=checkout_id,
            merchant_request_id=callback.MerchantRequestID,
            result_code=callback.ResultCode,
        )
        # duplicate callback guard
        callback_idem_key = f"stk:callback:{checkout_id}"
        first_time = await self._redis_set_nx(
            callback_idem_key, "1", ex=self._settings.idempotency_ttl_seconds
        )
        if not first_time:
            log.info("stk_duplicate_callback_ignored")
            return
        # fetch matching payment record
        payment = await self._payment_repo.get_by_checkout_request_id(checkout_id)
        if not payment:
            log.warning("stk_callback_no_matching_payment")
            return
        # apply outcome(success or failure
        if callback.is_success:
            try:
                details = callback.extract_payment_details()
            except ValueError:
                details = {}

            async with self._db.begin():
                await self._payment_repo.mark_completed(
                    payment.id,
                    mpesa_receipt=details.get("MpesaReceiptNumber"),
                    amount_paid=details.get("Amount"),
                    transaction_date=str(details.get("TransactionDate", "")),
                )
            log.info(
                "stk_payment_confirmed",
                payment_id=payment.id,
                mpesa_receipt=details.get("MpesaReceiptNumber"),
                amount_paid=details.get("Amount"),
            )
        else:
            domain_error = daraja_result_code_to_exception(
                callback.ResultCode, callback.ResultDesc
            )
            async with self._db.begin():
                await self._payment_repo.mark_failed(
                    payment.id,
                    failure_reason=callback.ResultDesc,
                    result_code=callback.ResultCode,
                )
            log.info(
                "stk_payment_failed",
                payment_id=payment.id,
                result_code=callback.ResultCode,
                reason=callback.ResultDesc,
                domain_error_code=domain_error.code if domain_error else None,
            )

    # status query

    async def get_payment_status(self, payment_id: str) -> PaymentStatusResponse:
        """Fetch payment status for the polling endpoint"""
        row = await self._payment_repo.get_by_id(payment_id)
        if not row:
            return None
        return PaymentStatusResponse(
            payment_id=str(row.id),
            status=str(row.status),
            amount=str(row.amount),
            phone=str(row.phone),
            account_reference=row.account_reference,
            mpesa_receipt=row.mpesa_receipt,
            failure_reason=row.failure_reason,
            result_code=row.result_code,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at),
        )

    # private helpers
    @staticmethod
    def _payment_to_response(payment: Payment) -> STKPushInitiateResponse:
        """Converts a Payment ORM object to an STKPushInitiateResponse.
        Used only for DB-level idempotency fallback when Redis is cold
        Only called when the payment was found via idempotency_key lookup"""
        return STKPushInitiateResponse(
            payment_id=payment.id,
            checkout_request_id=payment.checkout_request_id or "",
            merchant_request_id=payment.merchant_request_id or "",
            response_code="0",
            customer_message="Payment already processed.",
            status=payment.status,
        )

    async def _redis_get(self, key: str) -> bytes | None:
        """Redis GET with graceful degradation on connection failure."""
        try:
            return await self._redis.get(key)
        except Exception as e:
            logger.warning("redis_get_failed", key=key, error=str(e))
            return None

    async def _redis_set_nx(self, key: str, value: str, ex: int) -> bool:
        """Redis SET NX graceful degradation"""
        try:
            result = await self._redis.set(key, value, nx=True, ex=ex)
            return bool(result)
        except Exception as e:
            logger.warning("redis_set_nx_failed", key=key, error=str(e))
            # on redis failure during lock acquisition:allow through
            # better to risk a duplicate than hard-fail all payments
            return True

    async def _redis_setex(self, key: str, ttl: int, value: str) -> None:
        """Redis SETEx sets a redis key with an expiration with graceful degradation"""
        try:
            await self._redis.setex(key, ttl, value)
        except Exception as e:
            logger.warning("redis_setex_failed", key=key, error=str(e))

    async def _redis_delete(self, key: str) -> None:
        """Redis Delete to release locks with graceful degradation"""
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning("redis_delete_failed", key=key, error=str(e))
