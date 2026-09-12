from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.types import Money, Qty


class BalanceOut(BaseModel):
    productId: str
    locationId: str | None = None
    balance: Qty


class StockValueOut(BaseModel):
    """Whole-branch stock valuation — the client can't fold this itself without the whole catalog."""
    lines: int
    totalQty: Qty
    valueAtSale: Money
    valueAtCost: Money


class StockMovementOut(BaseModel):
    id: str
    productId: str
    productName: str | None = None
    productSku: str | None = None
    locationId: str
    kind: str
    qty: Qty
    reason: str | None = None
    originUserId: str | None = None
    at: datetime


class StockMovementListOut(BaseModel):
    items: list[StockMovementOut]
    total: int


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
    productName: str | None = None
    productSku: str | None = None
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


class GRNListOut(BaseModel):
    items: list[GRNOut]
    total: int


class PurchaseReturnLineIn(BaseModel):
    productId: str
    qty: Decimal
    unitPrice: Decimal


class PurchaseReturnCreateRequest(BaseModel):
    supplierId: str
    locationId: str
    # Optional — a return can reference the GRN it came from, but isn't required to (e.g. old
    # or expired stock being returned long after receiving, or stock never tied to one GRN).
    grnId: str | None = None
    reason: str = "other"
    notes: str = ""
    lines: list[PurchaseReturnLineIn]


class PurchaseReturnLineOut(BaseModel):
    productId: str
    productName: str | None = None
    productSku: str | None = None
    qty: Qty
    unitPrice: Money


class PurchaseReturnOut(BaseModel):
    id: str
    returnNumber: str
    supplierId: str
    locationId: str
    grnId: str | None = None
    reason: str
    notes: str | None = None
    submittedByUserId: str
    at: datetime
    lines: list[PurchaseReturnLineOut]


class PurchaseReturnListOut(BaseModel):
    items: list[PurchaseReturnOut]
    total: int


class BatchOut(BaseModel):
    id: str
    productId: str
    productName: str | None = None
    productSku: str | None = None
    lotNumber: str | None = None
    expiry: datetime | None = None
    receivedQty: Qty


class BatchListOut(BaseModel):
    items: list[BatchOut]
    total: int


class CountSubmitRequest(BaseModel):
    productId: str
    locationId: str
    countedQty: Decimal


class CountOut(BaseModel):
    id: str
    productId: str
    productName: str | None = None
    productSku: str | None = None
    locationId: str
    systemQty: Qty
    countedQty: Qty
    status: str
    countedByUserId: str
    approvedByUserId: str | None = None
    at: datetime


class CountListOut(BaseModel):
    items: list[CountOut]
    total: int


class AdjustmentSubmitRequest(BaseModel):
    productId: str
    locationId: str
    reason: str
    magnitude: Decimal
    notes: str = ""


class AdjustmentOut(BaseModel):
    id: str
    productId: str
    productName: str | None = None
    productSku: str | None = None
    locationId: str
    reason: str
    magnitude: Qty
    notes: str | None = None
    status: str
    submittedByUserId: str
    decidedByUserId: str | None = None
    at: datetime


class AdjustmentListOut(BaseModel):
    items: list[AdjustmentOut]
    total: int


class TransferLineOut(BaseModel):
    productId: str
    productName: str | None = None
    productSku: str | None = None
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
