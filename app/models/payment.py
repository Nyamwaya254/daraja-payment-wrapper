from __future__ import annotations
from datetime import datetime
from decimal import Decimal
import enum
import uuid


from sqlalchemy import (
    DateTime,
    Integer,
    Numeric,
    Text,
    func,
    text,
    String,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class Base(DeclarativeBase):
    pass


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REVERSED = "reversed"


class PaymentType(str, enum.Enum):
    STK_PUSH = "stk_push"
    C2B = "c2b"
    B2C = "b2c"


class Payment(Base):
    """
    Payment record table
    Lifecycle:
        PENDING   — created before Daraja call (ensures traceability even on crash)
        COMPLETED — callback received with ResultCode == 0
        FAILED    — callback received with ResultCode != 0
        CANCELLED — user cancelled PIN prompt (ResultCode == 1032)
        REVERSED  — completed but subsequently reversed via Daraja Reversal API

        The idempotency_key UNIQUE constraint is the source-of-truth deduplication
        guarantee. The Redis cache is a fast-path shortcut.
    invariants enforced at Db level:
        - amount > 0
        - amount_paid > 0 when present
        - checkout_request_id UNIQUE (one Daraja request per payment)
        - idempotency_key UNIQUE (one payment per client dedup key)
        - mpesa_receipt UNIQUE (one receipt per confirmed transaction)


    """

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment=(
            "UUID V4 primary key,generated application-side for traceability before DB write."
        ),
    )
    payment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentType.STK_PUSH.value
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentStatus.PENDING.value,
    )

    # customer fields
    phone: Mapped[str] = mapped_column(
        String(15),
        nullable=False,
        comment="Normalised MSISDN: 2547XXXXXXXX. Never store raw user input.",
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    account_reference: Mapped[str | None] = mapped_column(
        String(12),
        nullable=False,
        comment="Appears on customer's M-Pesa confirmation SMS.",
    )
    transaction_desc: Mapped[str | None] = mapped_column(
        String(13),
        nullable=False,
        comment="Human-readable payment description sent to Daraja.",
    )
    # deduplication
    idempotency_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="Client-suplied dedup key. UNIQUE enforced at DB level",
    )

    # daraja identifiers - populated after successful STK Push initiation
    checkout_request_id: Mapped[str | str] = mapped_column(
        String(100),
        nullable=True,
        unique=True,
        comment="Daraja CheckoutRequestID. USed to match inbound callbacks",
    )
    merchant_request_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Outcome - populated when callback received
    mpesa_receipt: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="MPESA trans receipt(e.g QWRTTHGGBV). Unique per transaction",
    )
    amount_paid: Mapped[str | None] = mapped_column(
        Numeric(19, 4),
        nullable=True,
        comment="Actual amount confirmed by M-PESA.May differ form amount if customer modifeid value. NULL until COMPLETED status ",
    )
    mpesa_transaction_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Daraja TransactionDate: YYYYMMDDHHmmss (UTC+3 / Nairobi time). Stored as UTC. Parse with: datetime.strptime(raw, '%Y%m%d%H%M%S').",
    )
    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    result_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Daraja ResultCode from callback.0=sucess other indicate failure type",
    )

    # audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # callback matching
        Index("ix_payments_checkout_request_id", "checkout_request_id"),
        # status dashboard status
        Index("ix_payments_status", "status"),
        # Per-phone payments history
        Index("ix_payments_phone", "phone"),
        # Receipt lookup(reconcialiation,support querie)
        Index("ix_payments_mpesa_receipt", "mpesa_receipt"),
        # Time_range queries
        Index("ix_payments_created_at", "created_at"),
        # Composite: status + created_at for "pending payments older than x" queries
        Index("ix_payments_status_created_at", "status", "created_at"),
        # Domain constraints
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint(
            "amount_paid IS NULL OR amount_paid >0",
            name="ck_payments_amount_paid_positive",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Payment id={self.id!r} status={self.status!r}"
            f"amount={self.amount} phone=****{self.phone[-4]}"
        )
