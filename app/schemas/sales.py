from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money, Qty


class SaleLineIn(BaseModel):
    productId: str
    qty: Decimal
    unitPrice: Decimal
    isReturn: bool = False


class SaleCreateRequest(BaseModel):
    partyId: str | None = None
    lines: list[SaleLineIn]
    discPercent: Decimal = Decimal("0")
    flatDisc: Decimal = Decimal("0")
    fare: Decimal = Decimal("0")
    tenders: dict[str, Decimal]
    voucherCode: str | None = None
    discountOverrideByUserId: str | None = None
    # Optional client-generated idempotency key — a retried request with the same key returns
    # the already-committed sale instead of creating a second one. Omit for the old behavior.
    clientRequestId: str | None = None


class SaleLineOut(BaseModel):
    productId: str
    name: str
    sku: str
    qty: Qty
    unitPrice: Money
    isWeighed: bool
    isReturn: bool


class SaleTenderOut(BaseModel):
    code: str
    name: str
    amount: Money


class SaleRecordOut(BaseModel):
    id: str
    invoiceNumber: str
    at: datetime
    cashierId: str
    partyId: str
    partyName: str
    lines: list[SaleLineOut]
    gross: Money
    discTotal: Money
    fare: Money
    gst: Money
    grandTotal: Money
    netValue: Money
    discountOverrideBy: str | None = None
    earnedPoints: int
    tenders: list[SaleTenderOut]
    received: Money
    cashBack: Money
    isCreditSale: bool
    fbrInvoiceNumber: str


class ReturnLineIn(BaseModel):
    productId: str
    qty: Decimal
    unitPrice: Decimal


class ReturnCreateRequest(BaseModel):
    against: str
    lines: list[ReturnLineIn]


class ReturnRecordOut(BaseModel):
    id: str
    against: str
    at: datetime
    cashierId: str
    refundTotal: Money


class NextInvoiceNumberOut(BaseModel):
    invoiceNumber: str


class SaleListOut(BaseModel):
    items: list[SaleRecordOut]
    total: int
