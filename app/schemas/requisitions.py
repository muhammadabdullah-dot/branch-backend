from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.types import Qty


class StockRequestLineIn(BaseModel):
    # An Item this branch carries, by its id; or one only head office's list has, by its code, name and unit.
    productId: str | None = None
    sku: str | None = Field(default=None, max_length=60)
    name: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=30)
    qty: Decimal = Field(gt=0)


class StockRequestIn(BaseModel):
    # Empty for head office's godown; otherwise the code of the branch asked to send it.
    sourceCode: str | None = Field(default=None, max_length=20)
    reason: str | None = Field(default=None, max_length=255)
    neededBy: date | None = None
    lines: list[StockRequestLineIn] = Field(min_length=1, max_length=300)


class WithdrawIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class StockRequestLineOut(BaseModel):
    # Empty for an Item this branch doesn't carry yet.
    productId: str | None = None
    productName: str | None = None
    productSku: str | None = None
    unit: str | None = None
    qtyRequested: Qty
    qtyApproved: Qty | None = None
    onHand: Qty | None = None
    dailySales: Qty | None = None


class StockRequestOut(BaseModel):
    id: str
    number: str
    status: str
    origin: str
    sourceCode: str | None = None
    sourceName: str | None = None
    reason: str | None = None
    neededBy: date | None = None
    createdBy: str | None = None
    createdAt: datetime
    updatedAt: datetime
    sentAt: datetime | None = None
    sentBy: str | None = None
    headOfficeNumber: str | None = None
    receivedAtHeadOffice: datetime | None = None
    decidedAt: datetime | None = None
    decidedBy: str | None = None
    decisionNote: str | None = None
    transferId: str | None = None
    transferNumber: str | None = None
    # The shipment it became, as it stands here now.
    transferStatus: str | None = None
    transferDisputeOpen: bool = False
    lines: list[StockRequestLineOut]


class CoverOut(BaseModel):
    productId: str
    onHand: Qty
    dailySales: Qty
    # Days the stock lasts at the last 30 days' sales; empty when it hasn't sold.
    daysCover: Qty | None = None


class CompanyItemHint(BaseModel):
    branchCode: str
    branchName: str
    qty: Qty


class CompanyItemOut(BaseModel):
    """An Item from head office's list, for a stock request. `productId` is set when this branch carries it too."""
    sku: str
    name: str
    unit: str | None = None
    brand: str | None = None
    category: str | None = None
    department: str | None = None
    # godown: in head office's Item master · branch: only other branches carry it
    source: str
    productId: str | None = None
    onHandHere: Qty | None = None
    godownQty: Qty | None = None
    branches: list[CompanyItemHint] = []


class SuggestionOut(BaseModel):
    productId: str
    productName: str
    productSku: str
    unit: str | None = None
    onHand: Qty
    dailySales: Qty
    daysCover: Qty
    suggestedQty: Qty


class SourceOut(BaseModel):
    code: str
    name: str
    city: str | None = None


class TransferDispatchFromIn(BaseModel):
    locationId: str | None = None
    vehicle: str | None = Field(default=None, max_length=40)
    driver: str | None = Field(default=None, max_length=80)
