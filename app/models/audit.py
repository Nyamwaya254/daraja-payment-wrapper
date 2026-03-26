from __future__ import annotations
from datetime import datetime
import hashlib
import json
from typing import Any
import uuid

from sqlalchemy import DateTime, String, Text, func, select, text, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.payment import Base


class PaymentAuditLog(Base):
    """
    Append only audit trail for all payment lifecycle events
    -NEVER UPDATE any row in this table
    -Never DELETE  any row in this table
    -Always compute checksum be4 insert
    -The previous_checksum chain enabes tamper detection
    """

    __tablename__ = "payment_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment=(
            "Logical reference to payments.id."
            "NO FK constraint by design - audit rows must survive payment deletion "
        ),
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="eg INITIATED, DARAJA_ACCEPTED, CALLBACK_RECEIVED, COMPLETED, FAILED",
    )
    actor: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Who triggered the event : 'api_user', 'daraja_callback', 'celery_worker'",
    )
    payload_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Sanitised, JSON-encoded event context. "
            "PII stripped before storage. "
            "NULL only for events with no meaningful context."
        ),
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="X-Request-id/trace ID for cross-service trace correlation",
    )
    checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment=(
            "SHA-256 of (payment_id|event_type|payload_json|previous_checksum)."
            "Recompute and compare to detect row tampering."
        ),
    )
    previous_checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Checksum of the previous audit row for this payment_id. "
            "NULL only for the first event of a payment. "
            "Forms a linked hash chain — breaking any link indicates tampering."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    __table_args__ = (
        Index("ix_audit_payment_id", "payment_id"),
        Index("ix_audit_event_type", "event_type"),
        Index("ix_audit_created_at", "created_at"),
    )

    @staticmethod
    def compute_checksum(
        payment_id: str,
        event_type: str,
        payload_json: str,
        previous_checksum: str | None,
    ) -> str:
        """Computes SHA-256 checksum for tamper detection"""
        data = f"{payment_id}|{event_type}|{payload_json}|{previous_checksum or ''}"
        return hashlib.sha256(data.encode()).hexdigest()

    def __repr__(self) -> str:
        return (
            f"<PaymentAuditLog payment_id={self.payment_id!r}"
            f"event={self.event_type!r} at={self.created_at}>"
        )


# Event type constraints
class AuditEventType:
    INITIATED = "INITIATED"
    DARAJA_ACCEPTED = "DARAJA_ACCEPTED"
    DARAJA_REJECTED = "DARAJA_REJECTED"
    CALLBACK_RECEIVED = "CALLBACK_RECEIVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT_RECEIVED = "TIMEOUT_RECEIVED"
    RECONCILIATION_QUERIED = "RECONCILIATION_QUERIED"
    REVERSED = "REVERSED"
    DUPLICATE_CALLBACK_IGNORED = "DUPLICATE_CALLBACK_IGNORED"


# Repository
class AuditLogRepository:
    """Repo for payment audit events"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        payment_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        ip_address: str | None = None,
        request_id: str | None = None,
    ) -> PaymentAuditLog:
        """
        Append an audit event for a payment
        Computes the checksum chain automatically - the caller only provides the event details
        """

        # Get previous checksum for this payment(chain continuity)
        result = await self._session.execute(
            select(PaymentAuditLog.checksum)
            .where(PaymentAuditLog.payment_id == payment_id)
            .order_by(PaymentAuditLog.created_at.desc())
            .limit(1)
        )
        previous_checksum = result.scalar_one_or_none()

        # sanitise payload - strip raw phone numbers and sensitive fields
        safe_payload = self._sanitise(payload or {})
        payload_json = json.dumps(safe_payload, default=str, sort_keys=True)

        checksum = PaymentAuditLog.compute_checksum(
            payment_id=payment_id,
            event_type=event_type,
            payload_json=payload_json,
            previous_checksum=previous_checksum,
        )
        entry = PaymentAuditLog(
            payment_id=payment_id,
            event_type=event_type,
            actor=actor,
            payload_json=payload_json,
            ip_address=ip_address,
            request_id=request_id,
            checksum=checksum,
            previous_checksum=previous_checksum,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get_payment_history(self, payment_id: str) -> list[PaymentAuditLog]:
        """Fetch all audit events for a payment in a chronological order"""
        result = await self._session.execute(
            select(PaymentAuditLog)
            .where(PaymentAuditLog.payment_id == payment_id)
            .order_by(PaymentAuditLog.created_at.asc())
        )
        return list(result.scalars().all())

    async def verify_chain_intergrity(self, payment_id: str) -> bool:
        """Verify a checksum chain for a payment has not been tampered with.Returns True if chain is intact"""
        entries = await self.get_payment_history(payment_id)
        if not entries:
            return True
        previous = None
        for entry in entries:
            expected = PaymentAuditLog.compute_checksum(
                payment_id=entry.payment_id,
                event_type=entry.event_type,
                payload_json=entry.payload_json or "",
                previous_checksum=previous,
            )
            if entry.checksum != expected:
                return False
            previous = entry.checksum

        return True

    @staticmethod
    def _sanitise(payload: dict) -> dict:
        """
        Strip PII and sensitive values from audit payload
        Masks phone numbers , removes raw amounts in some contexts.

        """
        SENSITIVE_KEYS = {
            "SecurityCredential",
            "Password",
            "password",
            "access_token",
            "token",
            "secret",
        }
        result = {}
        for k, v in payload.items():
            if k in SENSITIVE_KEYS:
                result[k] = "***REDACTED***"
            elif isinstance(v, str) and len(v) == 12 and v.startswith("254"):
                # mask phone numbers: 2547XXXXXXXX -> 2547****XXXX
                result[k] = v[:4] + "****" + v[-4:]
            else:
                result[k] = v
        return result
