"""
Payment repository- sits between service layer and DB for payment entities
Rules:
    -Never SELECT * - always enumerate columns to reduce data transfer and for effiecient queries
"""

from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal


import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentStatus

logger = structlog.get_logger(__name__)
# Columns needed for the status polling endpoint — avoid SELECT *
_STATUS_COLUMNS = (
    Payment.id,
    Payment.status,
    Payment.amount,
    Payment.phone,
    Payment.account_reference,
    Payment.mpesa_receipt,
    Payment.failure_reason,
    Payment.result_code,
    Payment.created_at,
    Payment.updated_at,
)


class PaymentStatusRow:
    """Typed projection returned by get_by_id
    Wraps the raw SQLAlchemy Row in a stable typed object so callers
    get predictable attribute access regardless of SA version internals.
    """

    __slots__ = (
        "id",
        "status",
        "amount",
        "phone",
        "account_reference",
        "mpesa_receipt",
        "failure_reason",
        "result_code",
        "created_at",
        "updated_at",
    )

    def __init__(self, row) -> None:
        self.id = row.id
        self.status = row.status
        self.amount = row.amount
        self.phone = row.phone
        self.account_reference = row.account_reference
        self.mpesa_receipt = row.mpesa_receipt
        self.failure_reason = row.failure_reason
        self.result_code = row.result_code
        self.created_at = row.created_at
        self.updated_at = row.updated_at


class PaymentRepository:
    """Async repository for payment persistence"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, payment: Payment) -> Payment:
        """Persist a new payment record"""

        self._session.add(payment)
        await self._session.flush()
        logger.debug("Payment created", payment_id=payment.id, status=payment.status)
        return payment

    async def get_by_id(self, payment_id: str) -> "PaymentStatusRow | None":
        """Fetch a payment by PK with only status-relevent columns.All attributes are safe to access by name"""
        result = await self._session.execute(
            select(*_STATUS_COLUMNS).where(Payment.id == payment_id)
        )
        row = result.first()
        return PaymentStatusRow(row) if row is not None else None

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        """Look up a payment by the client-supplied idempotency key"""
        result = await self._session.execute(
            select(Payment).where(Payment.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def get_by_checkout_request_id(
        self, checkout_request_id: str
    ) -> Payment | None:
        """Fetch payment by Daraja CheckoutRequestID for callback matching.
        Hot path for callback processing
        """
        result = await self._session.execute(
            select(Payment).where(Payment.checkout_request_id == checkout_request_id)
        )
        return result.scalar_one_or_none()

    async def update_daraja_ids(
        self,
        payment_id: str,
        *,
        checkout_request_id: str,
        merchant_request_id: str,
    ) -> None:
        """Store Daraja-assigned identifiers after successful initiation"""
        await self._session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                checkout_request_id=checkout_request_id,
                merchant_request_id=merchant_request_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        logger.debug(
            "payment_daraja_ids_updated",
            payment_id=payment_id,
            checkout_request_id=checkout_request_id,
        )

    async def mark_completed(
        self,
        payment_id: str,
        *,
        mpesa_receipt: str | None,
        amount_paid: Decimal | float | None,
        transaction_date: str | None,
    ) -> None:
        """Mark payment as COMPLETED with M-PESA confirmation details."""
        await self._session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                status=PaymentStatus.COMPLETED.value,
                mpesa_receipt=mpesa_receipt,
                amount_paid=Decimal(str(amount_paid)) if amount_paid else None,
                mpesa_transaction_date=transaction_date,
                updated_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "payment_marked_completed",
            payment_id=payment_id,
            mpesa_receipt=mpesa_receipt,
        )

    async def mark_failed(
        self,
        payment_id: str,
        *,
        failure_reason: str,
        result_code: int,
    ) -> None:
        """Mark payment as failed with the Daraja result details"""
        status = (
            PaymentStatus.CANCELLED.value
            if result_code == 1032  # user cancelled
            else PaymentStatus.FAILED.value
        )

        await self._session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                status=status,
                failure_reason=failure_reason,
                result_code=result_code,
                updated_at=datetime.now(timezone.utc),
            )
        )
        logger.info(
            "payment_marked_failed",
            payment_id=payment_id,
            result_code=result_code,
            status=status,
        )
