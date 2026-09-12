from datetime import datetime

from pydantic import BaseModel

from app.schemas.types import Money


class TillOpenRequest(BaseModel):
    denominations: dict[str, int]
    notes: str = ""


class CashMovementRequest(BaseModel):
    denominations: dict[str, int]
    notes: str = ""


class TillCloseRequest(BaseModel):
    countedDenominations: dict[str, int]


class CashMovementOut(BaseModel):
    id: str
    kind: str
    amount: Money
    denominations: dict[str, int]
    notes: str | None
    at: datetime


class TillCurrentOut(BaseModel):
    isOpen: bool
    sessionNumber: str | None = None
    openedAt: datetime | None = None
    openingFloat: Money | None = None
    openingDenominations: dict[str, int] | None = None
    openingNotes: str | None = None
    movements: list[CashMovementOut] = []


class TillCloseOut(BaseModel):
    sessionNumber: str
    grossSale: Money
    totalDisc: Money
    gst: Money
    misc: Money
    creditSale: Money
    cashSaleReturn: Money
    tillOpen: Money
    cashIn: Money
    cashOut: Money
    netCash: Money
    countedCash: Money
    variance: Money


class TillSessionSummaryOut(BaseModel):
    sessionNumber: str
    cashierId: str
    openedAt: datetime
    closedAt: datetime
    openingFloat: Money
    netCash: Money
    countedCash: Money
    variance: Money


class TillSessionListOut(BaseModel):
    items: list[TillSessionSummaryOut]
    total: int


class TillClosePreviewOut(BaseModel):
    grossSale: Money
    totalDisc: Money
    gst: Money
    misc: Money
    creditSale: Money
    cashSaleReturn: Money
    tillOpen: Money
    cashIn: Money
    cashOut: Money
    netCash: Money
