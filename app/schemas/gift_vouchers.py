from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.types import Money


class GiftVoucherOut(BaseModel):
    id: str
    code: str
    faceValue: Money
    balance: Money
    # What was written on the voucher when it was issued. Display only — never an ownership test.
    issuedToName: str | None = None
    # The registry link. Null does NOT by itself mean open: see `ownership`.
    partyId: str | None = None
    partyCode: str | None = None
    # Who can spend it, exactly as the till enforces it:
    #   open        anyone holding the code
    #   customer    only ownerName's bill
    #   unresolved  issued by name to someone the registry can't identify — nobody can spend it
    ownership: Literal["open", "customer", "unresolved"] = "open"
    ownerName: str | None = None
    ownerCode: str | None = None
    issuedAt: datetime
    expiresAt: datetime
    status: str
    paidBy: str | None = None
    issuedBy: str | None = None


class GiftVoucherListOut(BaseModel):
    items: list[GiftVoucherOut]
    total: int


class GiftVoucherCheckOut(BaseModel):
    """Can this voucher go on this bill — the same answer the sale gives when it commits, asked
    before the customer's money is taken."""
    voucher: GiftVoucherOut
    refusal: str | None = None


class GiftVoucherIssueRequest(BaseModel):
    faceValue: Decimal
    # A customer from the registry, or omitted for an open voucher. There is no free-text name any
    # more: a typed name could never be checked at the till.
    partyId: str | None = None
    # CASH (into the open till) · CARD · BANK · EASYPAISA · JAZZCASH · COMPLIMENTARY
    paidBy: str | None = None
    paymentReference: str | None = None


class GiftVoucherRedeemRequest(BaseModel):
    amount: Decimal
    invoiceNumber: str
    partyId: str | None = None
