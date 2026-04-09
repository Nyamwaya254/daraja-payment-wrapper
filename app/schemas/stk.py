from __future__ import annotations
from decimal import Decimal
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Request
class STKPushInitiateRequest(BaseModel):
    """Validated inputs for stk push initiation"""

    model_config = ConfigDict(str_strip_whitespace=True)

    phone_number: Annotated[
        str,
        Field(
            description="Customer MSISDN. Accepted formats: 2547XXXXXXXX, +2547XXXXXXXX, 07XXXXXXXX.",
            examples=["254712345678", "+254712345678", "0712345678"],
        ),
    ]
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            le=300_000,
            description=(
                "Payment amount in KES. Must be a whole number — "
                "M-Pesa does not support fractional shillings via STK Push."
            ),
            examples=["500", "1000", "2500"],
        ),
    ]
    account_reference: Annotated[
        str,
        Field(
            min_length=1,
            max_length=12,
            description=(
                "Account identifier shown in the M-Pesa confirmation SMS. "
                "Typically your invoice/order number. Daraja hard limit: 12 chars."
            ),
            examples=["INV-001", "ORD-20240101"],
        ),
    ]
    transaction_desc: Annotated[
        str,
        Field(
            min_length=1,
            max_length=13,
            description=(
                "Short description shown in M-Pesa prompt. Daraja hard limit: 13 chars. "
            ),
            examples=["Order payment", "Subscription"],
        ),
    ]
    idempotency_key: Annotated[
        str,
        Field(
            min_length=8,
            max_length=64,
            description=(
                "Client-generated deduplication key. Submitting the same key twice "
                "returns the original response without re-initiating. "
            ),
            examples=["550e8400-e29b-41d4-a716-446655440000"],
        ),
    ]
    transaction_type: Annotated[
        str,
        Field(
            default="CustomerPayBillOnline",
            description=(
                "CustomerPayBillOnline for Paybill numbers. "
                "CustomerBuyGoodsOnline for Till (Buy Goods) numbers."
            ),
        ),
    ]

    @field_validator("phone_number")
    @classmethod
    def normalise_phone(cls, v: str) -> str:
        """Normalise any comman kenyaan MSISDN format to 2547XXXXXXXX"""
        raw = re.sub(r"[\s\-\(\)]", "", v)

        if raw.startswith("+"):
            raw = raw[1:]
        if raw.startswith("254"):
            normalised = raw
        elif raw.startswith("0") and len(raw) == 10:
            normalised = "254" + raw[1:]
        elif len(raw) == 9:
            normalised = "254" + raw
        else:
            raise ValueError(
                f"Cannot parse '{v} as a kenyan mobile number"
                "Expected formats: 2547XXXXXXXX, +2547XXXXXXXX, 07XXXXXXXX. "
            )

        # after normalisation number must be 254XXXXXXXX  or 2541XXXXXXXX(Airtel Kenya)
        if not re.fullmatch(r"2547\d{8}|2541\d{8}", normalised):
            raise ValueError(
                f"'{v} is not a valid Kenyan Safaricom or Airtel number (254{'7|1'}XXXXXXXX)"
            )
        return normalised

    @field_validator("amount")
    @classmethod
    def must_be_whole_shillings(cls, v: Decimal) -> Decimal:
        """Reject fractional amounts -M-pesa STK push requires integer KES"""
        if v != v.to_integral_value():
            raise ValueError(
                f"Amount {v} has fractional shillings"
                "Mpesa STK push requires whole KES amounts e.g 107,1999 not 107.50 "
            )
        return v

    @model_validator(mode="after")
    def account_reference_safe_for_daraja(self) -> "STKPushInitiateRequest":
        """Daraja rejects AccountReference containing special characters"""
        unsafe = re.search(r"[^a-zA-Z0-9\-_/]", self.account_reference)
        if unsafe:
            raise ValueError(
                f"account_reference contains invalid character {unsafe.group()}."
                "Only alphanumeric characters,hyphens,underscores and slashes are allowed"
            )
        return self


# Responses


class STKPushInitiateResponse(BaseModel):
    """
    Successful STK Push initiation response
    the customer has received a PIN prompt .Poll GET /payments/{payment_id}/status
    for the outcome — the payment is not complete until the callback confirms it
    """

    payment_id: str = Field(description="Internal payment UUID for status polling")
    checkout_request_id: str = Field(
        description="Daraja CheckoutRequestID — unique per STK request.",
        examples=["ws_CO_191220191020363925"],
    )
    merchant_request_id: str = Field(
        description="Daraja MerchantRequestID.",
        examples=["29115-34620561-1"],
    )
    response_code: str = Field(description="Daraja ResponseCode . '0'=accepted")
    customer_message: str = Field(
        description="Message to display to the customer while they wait.",
        examples=["Success. Request accepted for processing"],
    )
    status: str = Field(
        default="pending",
        description="Payment status. Will be 'pending' until callback received.",
    )


class PaymentStatusResponse(BaseModel):
    """Payment status for polling endpoint"""

    payment_id: str
    status: str
    amount: str
    phone: str
    account_reference: str | None
    mpesa_receipt: str | None = None
    failure_reason: str | None = None
    result_code: int | None = None
    created_at: str
    updated_at: str


# callback schemas
class CallbackMetadataItem(BaseModel):
    """Single item from Daraja callbackMetadata.Item array"""

    Name: str
    Value: Any = None


class CallbackMetadata(BaseModel):
    """Container for successful payment metadata"""

    Item: list[CallbackMetadataItem] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Converts item list to {Name:Value} dict for easy access"""
        return {item.Name: item.Value for item in self.Item}


class STKCallbackBody(BaseModel):
    """
    Inner stkcallback object from Daraja callback payload
    ResultCode == 0 means payment succeeded.

    """

    MerchantRequestID: str
    CheckoutRequestID: str
    ResultCode: int
    ResultDesc: str
    callback_metadata: CallbackMetadata | None = Field(
        default=None, alias="CallbackMetadata"
    )

    @property
    def is_success(self) -> bool:
        return self.ResultCode == 0

    def extract_payment_details(self) -> dict[str, Any]:
        """Extract structured payment details from CallbackMetadata"""
        if not self.is_success:
            raise ValueError(
                f"Cannot extract payment details from a failed callback"
                f"(ResultCode{self.ResultCode}: {self.ResultDesc})"
            )
        if not self.callback_metadata:
            return {}
        return self.callback_metadata.to_dict()


class STKCallbackEnvelope(BaseModel):
    """
    Top-level Daraja STK callback payload wrapper

    Daraja wraps everything in {"Body": {"stkCallback": {...}}}.
    This schema peels that envelope and expose the inner callback
    """

    Body: dict[str, Any]

    def extract(self) -> STKCallbackBody:
        """Extract and validates the inner STK callback"""
        stk_callback_raw = self.Body.get("stkCallback")
        if not stk_callback_raw:
            raise ValueError(
                "Callback Body missing 'stkCallback' key."
                f"Received keys: {list(self.Body.keys())}"
            )
        return STKCallbackBody.model_validate(stk_callback_raw)


# Daraja ACK
class DarajaCallbackAck(BaseModel):
    """
    Response body Daraja expects from all callback endpoints
    Daraja interprets any response with ResultCode != '0' as a rejection and may retry
    """

    ResultCode: str = "0"
    ResultDesc: str = "Accepted"


class DarajaSTKResponse(BaseModel):
    """Represents the successful response from Daraja STK Push API"""

    model_config = ConfigDict(extra="forbid")

    checkout_request_id: str = Field(..., alias="CheckoutRequestID")
    merchant_request_id: str = Field(..., alias="MerchantRequestID")
    response_code: str = Field(..., alias="ResponseCode")
    response_description: str = Field(..., alias="ResponseDescription")
    customer_message: str = Field(..., alias="CustomerMessage")
