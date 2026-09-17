from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

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
    # Rupees off the whole line, and extra charges on it (freight, loading).
    flatDisc: Decimal = Decimal("0")
    misc: Decimal = Decimal("0")
    expiry: datetime | None = None
    taxRate: Decimal = Decimal("0")
    # A new sale / retail price that arrived with this delivery. Omit to leave the Item's price alone.
    newSalePrice: Decimal | None = None
    newRetailPrice: Decimal | None = None


class GRNCreateRequest(BaseModel):
    supplierId: str
    partyInvNo: str | None = None
    locationId: str
    gstMode: str = "normal"
    advanceTax: Decimal = Decimal("0")
    # Receive against an approved purchase order: its lines are ticked off by what arrives.
    purchaseOrderId: str | None = None
    lines: list[GRNLineIn]


class GRNParsedLine(BaseModel):
    """A line read from a supplier's spreadsheet, resolved to an Item but not yet received."""
    row: int
    productId: str
    productName: str
    productSku: str
    qty: Qty
    bonusQty: Qty
    unitPrice: Money
    discPercent: Decimal
    flatDisc: Money
    misc: Money
    expiry: datetime | None = None
    taxRate: Decimal
    newSalePrice: Money | None = None
    newRetailPrice: Money | None = None


class GRNParseOut(BaseModel):
    lines: list[GRNParsedLine]
    errors: list[dict]


class GRNLineOut(BaseModel):
    productId: str
    productName: str | None = None
    productSku: str | None = None
    qty: Qty
    bonusQty: Qty
    unitPrice: Money
    discPercent: Decimal
    flatDisc: Money = 0
    misc: Money = 0
    expiry: datetime | None = None
    taxRate: Decimal
    newSalePrice: Money | None = None
    newRetailPrice: Money | None = None


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
    purchaseOrderId: str | None = None
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
    # What the reason is called on the branch's list today.
    reasonLabel: str | None = None
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
    reasonLabel: str | None = None
    # Whether approving it adds stock (a "found" kind of reason) or takes it off.
    adds: bool = False
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
    unit: str | None = None
    qtySent: Qty
    qtyReceived: Qty | None = None


class TransferOut(BaseModel):
    id: str
    number: str | None = None
    # inbound: coming here · outbound: sent from here to another branch
    direction: str = "inbound"
    # cloud: sent by head office · branch: dispatched here · local: seeded demo row
    origin: str = "local"
    # The other end: where it comes from (inbound) or where it's going (outbound).
    fromWarehouse: str
    counterpartyCode: str | None = None
    status: str
    lines: list[TransferLineOut]
    vehicle: str | None = None
    driver: str | None = None
    notes: str | None = None
    locationId: str | None = None
    locationName: str | None = None
    dispatchedBy: str | None = None
    receivedBy: str | None = None
    requestedAt: datetime
    dispatchedAt: datetime | None = None
    receivedAt: datetime | None = None
    disputeOpen: bool
    disputeNote: str | None = None
    requestedBy: str | None = None
    approvedBy: str | None = None
    holdReason: str | None = None
    heldBy: str | None = None
    heldAt: datetime | None = None
    # The receiving branch's answer before sending: awaiting · acknowledged · declined · skipped · overridden.
    ackStatus: str | None = None
    ackRequestedAt: datetime | None = None
    ackAt: datetime | None = None
    ackBy: str | None = None
    ackNote: str | None = None
    overrideReason: str | None = None
    overrideBy: str | None = None
    overrideAt: datetime | None = None


class TransferStepRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class TransferReceiveLine(BaseModel):
    productId: str
    qtyReceived: Decimal = Field(ge=0)


class TransferReceiveRequest(BaseModel):
    locationId: str
    lines: list[TransferReceiveLine]
    note: str | None = Field(default=None, max_length=500)
    # Something's wrong beyond the counts (damage, wrong Items): open a dispute even if everything arrived.
    dispute: bool = False


class TransferDispatchRequest(BaseModel):
    vehicle: str | None = Field(default=None, max_length=40)
    driver: str | None = Field(default=None, max_length=80)


class TransferSendLine(BaseModel):
    productId: str
    qty: Decimal = Field(gt=0)


class TransferSendRequest(BaseModel):
    destinationCode: str
    locationId: str
    lines: list[TransferSendLine] = Field(min_length=1)
    vehicle: str | None = Field(default=None, max_length=40)
    driver: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=255)


class KnownBranchOut(BaseModel):
    code: str
    name: str
    city: str | None = None


class DisputeRequest(BaseModel):
    note: str
