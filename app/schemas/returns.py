"""Returns beyond a plain refund: replace and exchange, the return receipt, the day's returns, and the return window
per department."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.sales import MemberIn, ReturnLineIn, SaleRecordOut, TenderDetailIn
from app.schemas.types import Money, Qty


class ExchangeLineIn(BaseModel):
    productId: str
    qty: Decimal


class ExchangeRequest(BaseModel):
    against: str
    # replace: the same Items go back to the customer. exchange: other Items go out.
    mode: Literal["replace", "exchange"]
    # What comes back, against the original bill.
    lines: list[ReturnLineIn]
    # What goes out on an exchange. A replace sends out what came back, so this is ignored.
    newLines: list[ExchangeLineIn] = []
    reason: str | None = Field(default=None, max_length=40)
    remark: str | None = Field(default=None, max_length=60)
    # CASH or CREDIT: how the return's value pays for the new Items, and how any money due back goes back.
    refundMethod: str | None = None
    # What the customer pays when the new Items cost more, by payment method (Cash, Card, Easypaisa, JazzCash,
    # Credit). Cash may be more than is due: the rest is change.
    tenders: dict[str, Decimal] = {}
    tenderDetails: dict[str, TenderDetailIn] = {}
    # Card and online payments need the customer's name and mobile number, like any bill.
    member: MemberIn | None = None
    # A retried request with the same key gives back the exchange already made instead of making another.
    clientRequestId: str | None = Field(default=None, max_length=80)


class ReturnReceiptLineOut(BaseModel):
    productId: str
    name: str
    sku: str
    qty: Qty
    unitPrice: Money
    value: Money
    isWeighed: bool = False


class ReturnReceiptTenderOut(BaseModel):
    code: str
    name: str
    amount: Money
    reference: str | None = None


class ReprintMarkOut(BaseModel):
    at: datetime
    by: str
    # 1 for the first reprint of this return receipt.
    copyNumber: int


class ReturnReceiptOut(BaseModel):
    id: str
    number: str
    at: datetime
    against: str
    againstAt: datetime | None = None
    partyName: str
    walkIn: bool
    # refund, replace or exchange
    kind: str
    lines: list[ReturnReceiptLineOut]
    refundTotal: Money
    taxTotal: Money | None = None
    rounding: Money | None = None
    refundMethod: str
    refundMethodName: str
    refundReference: str | None = None
    reason: str | None = None
    reasonLabel: str | None = None
    remark: str | None = None
    cashierName: str | None = None
    tillLabel: str | None = None
    # Replace and exchange: the bill the Items going out were rung on.
    exchangeInvoice: str | None = None
    exchangeFbrInvoice: str | None = None
    exchangeLines: list[ReturnReceiptLineOut] = []
    exchangeGst: Money | None = None
    exchangeDiscount: Money | None = None
    exchangeTotal: Money | None = None
    # exchangeTotal less refundTotal: more than zero, the customer paid it; less, it went back to them.
    difference: Money | None = None
    # How the customer paid a difference (the part the returned goods covered is not listed).
    differenceTenders: list[ReturnReceiptTenderOut] = []
    change: Money | None = None
    reprint: ReprintMarkOut | None = None


class ExchangeOut(BaseModel):
    # The return receipt (with the new Items and the difference) and the new bill itself.
    receipt: ReturnReceiptOut
    sale: SaleRecordOut


class ReturnListItemOut(BaseModel):
    id: str
    number: str
    at: datetime
    against: str
    partyName: str
    kind: str
    refundTotal: Money
    refundMethod: str
    refundMethodName: str
    reasonLabel: str | None = None
    remark: str | None = None
    cashierName: str | None = None
    exchangeInvoice: str | None = None
    exchangeTotal: Money | None = None
    items: int


class ReturnWindowRowOut(BaseModel):
    id: str
    name: str
    active: bool
    days: int | None = None


class ReturnWindowsOut(BaseModel):
    departments: list[ReturnWindowRowOut]
    updatedAt: datetime | None = None
    updatedBy: str | None = None


class ReturnWindowsIn(BaseModel):
    # Department entry id -> days (0: no returns). Left out or null: no limit.
    days: dict[str, int | None] = {}
