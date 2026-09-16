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
