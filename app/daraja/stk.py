"""
STK PUSH initiation
Initiates an STK Push (Lipa Na M-Pesa Online) transaction. It constructs the Daraja payload, sends the request,translating Daraja's response into domain types.
Logging  with PII-safe field masking
"""

from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
import structlog

from app.config import Settings
from app.daraja.auth import DarajaAuthManager
from app.daraja.client import DarajaClient
from app.exceptions import DarajaError
from app.schemas.stk import DarajaSTKResponse


logger = structlog.get_logger(__name__)

STK_PUSH_PATH = "/mpesa/stkpush/v1/processrequest"

TRANSACTION_TYPE_PAYBILL = "CustomerPayBillOnline"
TRANSACTION_TYPE_TILL = "CustomerBuyGoodsOnline"

ACCOUNT_REFERENCE_MAX_LEN = 12
TRANSACTION_DESC_MAX_LEN = 13


@dataclass(frozen=True)
class STKInitiationResult:
    """Typed result of a successful STK Push initiation"""

    checkout_request_id: str
    merchant_request_id: str
    response_code: str
    response_description: str
    customer_message: str


class STKPushInitiator:
    """Initiates STK Push requests the Daraja API"""

    def __init__(self, settings: Settings, client: DarajaClient) -> None:
        self._settings = settings
        self._client = client

    async def initiate(
        self,
        *,
        phone: str,
        amount: Decimal,
        account_reference: str,
        transaction_desc: str,
        transaction_type: str = TRANSACTION_TYPE_PAYBILL,
    ) -> STKInitiationResult:
        """Build and dispatch an STK Push request to Daraja
        The password and timestamp are generated atomically - they must refer to the same instant
        """

        passkey = self._settings.daraja_passkey.get_secret_value()
        password, timestamp = DarajaAuthManager.generate_stk_password(
            self._settings.daraja_shortcode,
            passkey,
        )
        callback_url = f"{self._settings.daraja_callback_base_url}/api/v1/callback/stk"

        payload = {
            "BusinessShortCode": self._settings.daraja_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": transaction_type,
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": self._settings.daraja_shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": account_reference[:ACCOUNT_REFERENCE_MAX_LEN],
            "TransactionDesc": transaction_desc[:TRANSACTION_DESC_MAX_LEN],
        }
        # PII-safe logging: only log last 4 digits of MSISDN
        log = logger.bind(
            phone_suffix=phone[-4:],
            amount=int(amount),
            account_reference=account_reference,
            transaction_type=transaction_type,
        )
        log.info("stk_push_initiating")
        # make request
        raw_response = await self._client.post(STK_PUSH_PATH, payload)
        # parse and validate response
        try:
            validated = DarajaSTKResponse.model_validate(raw_response)
        except ValidationError as e:
            log.error(
                "stk_push_invalid_response",
                errors=e.errors(),
                raw_response=raw_response,
            )
            raise DarajaError(
                f"Invalid Daraja STK response structure : {e}",
                daraja_code=None,
            ) from e
        # check business success( ResponseCode == "0")
        if validated.response_code != "0":
            log.error(
                "stk_push_rejected_by_daraja",
                response_code=validated.response_code,
                description=validated.response_description,
            )
            raise DarajaError(
                f"STK Push rejected: {validated.response_description}",
                daraja_code=validated.response_code,
                daraja_description=validated.response_description,
            )
        # construct result from validated data
        result = STKInitiationResult(
            checkout_request_id=validated.checkout_request_id,
            merchant_request_id=validated.merchant_request_id,
            response_code=validated.response_code,
            response_description=validated.response_description,
            customer_message=validated.customer_message,
        )
        log.info(
            """stk_push_accepted_by_daraja""",
            checkout_request_id=result.checkout_request_id,
            merchant_request_id=result.merchant_request_id,
        )
        return result

    """Opted to use pydantic validation"""
    # @staticmethod
    # def _assert_response_accepted(
    #     response: dict,
    #     log: structlog.BoundLogger,
    # ) -> None:
    #     """
    #     Validate that Daraja accepted the STK request
    #     Daraja can return HTTP 200 with ResponseCode != '0', which still
    #     means the request was rejected. We treat this as a hard error.
    #     """
    #     response_code = response.get("ResponseCode")
    #     if response_code != "0":
    #         description = response.get("ResponseDescription", "unknown")
    #         log.error(
    #             "stk_push_rejected_by_daraja",
    #             response_code=response_code,
    #             description=description,
    #         )
    #         raise DarajaError(
    #             f"STK Push rejected: {description}",
    #             daraja_code=response_code,
    #             daraja_description=description,
    #         )
    #     # validate required fields are present in accepted response
    #     required_fields = {
    #         "CheckoutRequestID",
    #         "MerchantRequestID",
    #         "ResponseCode",
    #         "ResponseDescription",
    #         "CustomerMessage",
    #     }
    #     missing = required_fields - set(response.keys())
    #     if missing:
    #         log.error("stk_push_response_fields", missing=list(missing))
    #         raise DarajaError(
    #             f"Daraja STK response missing required fields: {missing}",
    #             daraja_code=response_code,
    #         )
