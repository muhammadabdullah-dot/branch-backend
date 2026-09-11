from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money, Qty


class BalanceOut(BaseModel):
    productId: str
    locationId: str | None = None
    balance: Qty


class StockMovementOut(BaseModel):
    id: str
    productId: str
    locationId: str
    kind: str
    qty: Qty
    reason: str | None = None
    originUserId: str | None = None
    at: datetime


class GRNLineIn(BaseModel):
    productId: str
    qty: Decimal
    bonusQty: Decimal = Decimal("0")
    unitPrice: Decimal
    discPercent: Decimal = Decimal("0")
    expiry: datetime | None = None
    taxRate: Decimal = Decimal("0")


class GRNCreateRequest(BaseModel):
    supplierId: str
    partyInvNo: str | None = None
    locationId: str
    gstMode: str = "normal"
    advanceTax: Decimal = Decimal("0")
    lines: list[GRNLineIn]


class GRNLineOut(BaseModel):
    productId: str
    qty: Qty
    bonusQty: Qty
    unitPrice: Money
    discPercent: Decimal
    expiry: datetime | None = None
    taxRate: Decimal


class GRNOut(BaseModel):
    id: str
    grnNumber: str
    supplierId: str
    partyInvNo: str | None = None
    locationId: str
    gstMode: str
    advanceTax: Money
    approved: bool
    receivedByUserId: str
    at: datetime
    lines: list[GRNLineOut]


class BatchOut(BaseModel):
    id: str
    productId: str
    lotNumber: str | None = None
    expiry: datetime | None = None
    receivedQty: Qty


class CountSubmitRequest(BaseModel):
    productId: str
    locationId: str
    countedQty: Decimal


class CountOut(BaseModel):
    id: str
    productId: str
    locationId: str
    systemQty: Qty
    countedQty: Qty
    status: str
    countedByUserId: str
    approvedByUserId: str | None = None
    at: datetime


class AdjustmentSubmitRequest(BaseModel):
    productId: str
    locationId: str
    reason: str
    magnitude: Decimal
    notes: str = ""


class AdjustmentOut(BaseModel):
    id: str
    productId: str
    locationId: str
    reason: str
    magnitude: Qty
    notes: str | None = None
    status: str
    submittedByUserId: str
    decidedByUserId: str | None = None
    at: datetime


class TransferLineOut(BaseModel):
    productId: str
    qtySent: Qty
    qtyReceived: Qty | None = None


class TransferOut(BaseModel):
    id: str
    fromWarehouse: str
    status: str
    lines: list[TransferLineOut]
    vehicle: str | None = None
    driver: str | None = None
    requestedAt: datetime
    dispatchedAt: datetime | None = None
    receivedAt: datetime | None = None
    disputeOpen: bool
    disputeNote: str | None = None


class DisputeRequest(BaseModel):
    note: str
