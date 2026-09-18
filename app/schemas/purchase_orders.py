from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.types import Money, Qty


class PurchaseOrderLineIn(BaseModel):
    productId: str
    qty: Decimal = Field(gt=0)
    unitPrice: Decimal = Field(ge=0)
    discPercent: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class PurchaseOrderCreate(BaseModel):
    supplierId: str
    locationId: str
    expectedAt: datetime | None = None
    notes: str | None = Field(default=None, max_length=255)
    lines: list[PurchaseOrderLineIn]


class PurchaseOrderUpdate(BaseModel):
    """Drafts only. Lines, when sent, replace the order's lines."""
    supplierId: str | None = None
    locationId: str | None = None
    expectedAt: datetime | None = None
    notes: str | None = Field(default=None, max_length=255)
    lines: list[PurchaseOrderLineIn] | None = None


class PurchaseOrderLineOut(BaseModel):
    productId: str
    productName: str | None = None
    productSku: str | None = None
    qty: Qty
    unitPrice: Money
    discPercent: Decimal
    receivedQty: Qty
    remainingQty: Qty
    # Written in by hand and still waiting for its details on the Item form.
    needsDetails: bool = False


class PurchaseOrderOut(BaseModel):
    id: str
    poNumber: str
    supplierId: str
    supplierName: str
    locationId: str
    status: str
    expectedAt: datetime | None = None
    notes: str | None = None
    createdByUserId: str
    createdByName: str | None = None
    approvedByUserId: str | None = None
    approvedByName: str | None = None
    createdAt: datetime
    approvedAt: datetime | None = None
    closedAt: datetime | None = None
    orderValue: Money
    lines: list[PurchaseOrderLineOut]
    grnNumbers: list[str] = []


class PurchaseOrderListOut(BaseModel):
    items: list[PurchaseOrderOut]
    total: int


class SuggestionLineOut(BaseModel):
    productId: str
    sku: str
    name: str
    unit: str | None = None
    packSize: int | None = None
    ratePerDay: Decimal
    held: Qty
    onOrder: Qty
    reorderLevel: Qty | None = None
    suggestedQty: Qty
    unitCost: Money
    # The working in words: "sells 4 a day, holds 12, 30 days of cover needs 120, so order 108".
    reason: str
    # Why it's listed: "received from them", "ordered from them", "on the Item's supplier list", "running low".
    why: str
    needsDetails: bool = False


class SuggestionsOut(BaseModel):
    supplierId: str | None = None
    supplierName: str | None = None
    coverDays: int
    salesDays: int
    # How many Items the rules found; at most 300 are sent.
    count: int
    rule: str
    lines: list[SuggestionLineOut]


class ByHandIn(BaseModel):
    """An Item the catalog doesn't have, written in on an order."""
    name: str = Field(min_length=1, max_length=160)
    unit: str | None = Field(default=None, max_length=20)
    # What the supplier charges for one.
    cost: Decimal = Field(ge=0)
    # What it sells for, when known.
    price: Decimal | None = Field(default=None, ge=0)
    sku: str | None = Field(default=None, max_length=40)
