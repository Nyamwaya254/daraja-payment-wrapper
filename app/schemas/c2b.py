"""
C2B validation url -
"""

from __future__ import annotations

from pydantic import BaseModel


class C2BPayload(BaseModel):
    """Common fields in both C2B validation and confirmation payloads
    Daraja sends all fields as strings including TransAmt.We parse and validate her be4 service layer sees the data
    """
