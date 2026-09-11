from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money


class GiftVoucherOut(BaseModel):
    id: str
    code: str
    faceValue: Money
    balance: Money
    issuedToName: str | None = None
    issuedAt: datetime
    expiresAt: datetime
    status: str


class GiftVoucherIssueRequest(BaseModel):
    faceValue: Decimal
    issuedToName: str | None = None


class GiftVoucherRedeemRequest(BaseModel):
    amount: Decimal
    invoiceNumber: str
