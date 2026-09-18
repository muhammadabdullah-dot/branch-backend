"""What the Billing extras send: Sold without stock, Priced below cost, the pharmacy setting, scan history."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.types import Money, Qty


class PastZeroSaleOut(BaseModel):
    by: str
    at: datetime | None = None
    qty: Qty


class SoldWithoutStockOut(BaseModel):
    productId: str
    sku: str
    name: str
    unit: str | None = None
    # How many Main Store is short now.
    short: Qty
    # The same in plain words: "5 tablets" for an Item sold loose, "3 pc" otherwise.
    shortText: str | None = None
    # When it went below zero this time.
    since: datetime | None = None
    # What the branch's other switched-on locations hold, which could be moved to Main Store.
    elsewhere: Qty
    sales: list[PastZeroSaleOut]


class LastReceivedOut(BaseModel):
    at: datetime | None = None
    grnNumber: str | None = None
    supplier: str | None = None
    unitPrice: Money | None = None


class PriceCheckRowOut(BaseModel):
    productId: str
    sku: str
    name: str
    department: str | None = None
    unit: str | None = None
    price: Money
    wholesalePrice: Money | None = None
    costWithTax: Money
    priceWithTax: Money
    onHand: Qty
    # Which of its own prices are at or below cost, in words: "Sale price", "Sale price and Box price".
    which: str | None = None
    # How far below cost with tax the lowest of them is, per stocked unit.
    gap: Money | None = None
    lastReceived: LastReceivedOut | None = None


class PricedBelowCostOut(BaseModel):
    belowCost: list[PriceCheckRowOut]
    noCost: list[PriceCheckRowOut]


class PharmacySettingIn(BaseModel):
    # The departments whose Items only a Pharmacist (or a Branch Manager) puts on a bill, and a Pharmacist only those.
    departments: list[str] = Field(default_factory=list, max_length=20)


class PharmacySettingOut(PharmacySettingIn):
    updatedAt: datetime | None = None
    updatedBy: str | None = None


class ScanEventIn(BaseModel):
    # The till's own id for the event, so one sent twice is kept once.
    id: str = Field(min_length=1, max_length=40)
    # The bill (its POST /sales clientRequestId) and the line's number on it.
    billId: str = Field(min_length=1, max_length=80)
    lineKey: int
    productId: str = Field(min_length=1, max_length=40)
    # "added", "more", "less", "level", "removed" or "held".
    action: str = Field(max_length=12)
    # Pieces on the line after it, and "pack" or "box" when sold that way.
    qty: Decimal
    level: str | None = Field(default=None, max_length=8)
    # How it went on: "scan", "search", "weight", "recall" or "slip". Only on "added".
    how: str | None = Field(default=None, max_length=12)
    # How long ago it happened, by the till's clock, when the batch was sent.
    ageMs: int = Field(default=0, ge=0)


class ScanEventsIn(BaseModel):
    events: list[ScanEventIn] = Field(max_length=500)


class ScanEventOut(BaseModel):
    at: datetime
    action: str
    qty: Qty
    level: str | None = None


class ScanLineOut(BaseModel):
    id: str
    billId: str
    firstAt: datetime
    endedAt: datetime | None = None
    userId: str
    userName: str
    counter: str | None = None
    till: str | None = None
    deviceId: str | None = None
    device: str | None = None
    productId: str
    sku: str
    name: str
    qty: Qty
    level: str | None = None
    how: str
    # "removed", "held", "sold", "unpaid" (never paid) or "open" (still on a bill).
    outcome: str
    invoiceNumber: str | None = None
    events: list[ScanEventOut]


class PersonOut(BaseModel):
    id: str
    name: str


class ScanHistoryOut(BaseModel):
    rows: list[ScanLineOut]
    # Everyone who scanned that day, for the person filter.
    people: list[PersonOut]
    counts: dict[str, int]
