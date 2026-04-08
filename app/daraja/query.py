"""DAraja Transacton Status,Account Balance and Reversal APIs
Transactio Status:
 -Resolve PENDING payments after a crash or B2C queue timeout
 -Verify a payment the customer claims to have made
 - Used in the 5-minute cron job for stale PENDING records

Account Balance:
  - Monitor your Paybill float — alert if it drops below threshold
  - Pre-disbursement check before batch B2C payouts
  - Compliance reporting

Reversal:
  - Undo a completed B2C payout (within Safaricom's reversal window)
  - Use for duplicate payouts, fraudulent transactions
  - NOT for refunds — Safaricom treats reversals differently from refunds
  - Has daily limits — check Daraja portal for your tier's limits
"""

from __future__ import annotations
from dataclasses import dataclass

import structlog

from app.config import Settings
from app.daraja.client import DarajaClient
from app.exceptions import DarajaError


logger = structlog.get_logger(__name__)

_TRANSACTION_STATUS_PATH = "/mpesa/transactionstatus/v1/query"
_ACCOUNT_BALANCE_PATH = "/mpesa/accountbalance/v1/query"
_REVERSAL_PATH = "/mpesa/reversal/v1/request"


@dataclass(frozen=True)
class QueryAcknowledgement:
    """Daraja's immediate acknowledgement for async query requests."""

    conversation_id: str
    originator_conversation_id: str
    response_code: str
    response_description: str


class DarajaQueryClient:
    """Daraja Transaction Status, Balance and Reversal API calls"""

    def __init__(self, settings: Settings, client: DarajaClient) -> None:
        self._settings = settings
        self._client = client

    async def query_transaction_status(
        self,
        *,
        transaction_id: str,
        identifier_type: str = "1",
        remarks: str = "Transaction status query ",
        occasion: str = "",
    ) -> QueryAcknowledgement:
        """Query the status of a transaction by M-Pesa TransactionID
        identifier_type: "1" = MSISDN(phone number), "2" = Till, "4" = Shortcode.
                             Use "4" when querying against your shortcode.
        """
        base = self._settings.daraja_callback_base_url
        payload = {
            "Initiator": self._settings.daraja_b2c_initiator_name,
            "SecurityCredential": (
                self._settings.daraja_b2c_security_credential.get_secret_value()
            ),
            "TransactionID": transaction_id,
            "PartyA": self._settings.daraja_shortcode,
            "IdentifierType": identifier_type,
            "ResultURL": f"{base}/api/v1/callbacks/query/transaction-status/result",
            "QueueTimeOutURL": f"{base}/api/v1/callbacks/query/transaction-status/timeout",
            "Remarks": remarks[:100],
            "Occasion": occasion[:100],
        }

        logger.info("transaction_status_querying", transaction_id=transaction_id)
        return await self._send(payload, _TRANSACTION_STATUS_PATH, "transaction_status")

    async def query_account_balance(
        self,
        *,
        identifier_type: str = "4",
        remarks: str = "Account balance query",
    ) -> QueryAcknowledgement:
        """Query your business account balance"""
        base = self._settings.daraja_callback_base_url
        payload = {
            "Initiator": self._settings.daraja_b2c_initiator_name,
            "SecurityCredential": (
                self._settings.daraja_b2c_security_credential.get_secret_value()
            ),
            "CommandID": "AccountBalance",
            "PartyA": self._settings.daraja_shortcode,
            "IdentifierType": identifier_type,
            "ResultURL": f"{base}/api/v1/callbacks/query/account-balance/result",
            "QueueTimeOutURL": f"{base}/api/v1/callbacks/query/account-balance/timeout",
            "Remarks": remarks[:100],
        }
        logger.info("account_balance_querying")
        return await self._send(payload, _ACCOUNT_BALANCE_PATH, "account_balance")

    async def reverse_transaction(
        self,
        *,
        transaction_id: str,
        amount: int,
        receiver_shortcode: str,
        remarks: str,
        occasion: str = "",
    ) -> QueryAcknowledgement:
        """Request a reversal of a completed transaction
        Used to undo completed B2C payouts not STK Push refunds"""
        base = self._settings.daraja_callback_base_url
        payload = {
            "Initiator": self._settings.daraja_b2c_initiator_name,
            "SecurityCredential": (
                self._settings.daraja_b2c_security_credential.get_secret_value()
            ),
            "CommandID": "TransactionReversal",
            "TransactionID": transaction_id,
            "Amount": amount,
            "ReceiverParty": receiver_shortcode,
            "RecieverIdentifierType": "4",  # Daraja typo — sic
            "ResultURL": f"{base}/api/v1/callbacks/query/reversal/result",
            "QueueTimeOutURL": f"{base}/api/v1/callbacks/query/reversal/timeout",
            "Remarks": remarks[:100],
            "Occasion": occasion[:100],
        }
        logger.info("reversal_requesting", transaction_id=transaction_id, amount=amount)
        return await self._send(payload, _REVERSAL_PATH, "reversal")

    async def _send(
        self,
        payload: dict,
        path: str,
        operation: str,
    ) -> QueryAcknowledgement:
        """Handles sending any request  to Daraja's asyn query APIs(Transaction Status, Account Balance, Reversal)"""
        response = await self._client.post(path, payload)

        if response.get("ResponseCode") not in ("o", None, ""):
            desc = response.get("ResponseDescription", "unknown")
            raise DarajaError(
                f"Daraja {operation} rejected: {desc}",
                daraja_code=response.get("ResponseCode"),
                daraja_description=desc,
            )
        return QueryAcknowledgement(
            conversation_id=response.get("ConversationID", ""),
            originator_conversation_id=response.get("OriginatorConversationID", ""),
            response_code=response.get("ResponseCode", "0"),
            response_description=response.get("ResponseDescription", ""),
        )
