from datetime import datetime

from pydantic import BaseModel

from app.schemas.types import Money


class TillOpenRequest(BaseModel):
    denominations: dict[str, int]
    notes: str = ""
    # Which counter this drawer is at. Left out: the counter the person is already on, or the only one there is.
    counterId: str | None = None


class CashMovementRequest(BaseModel):
    denominations: dict[str, int]
    notes: str = ""
    # Which drawer, when the person acting has more than their own in front of them. Empty: their own.
    sessionId: str | None = None
    # What it was for (cash out) or where it came from (cash in). Empty: petty expenses / the safe.
    accountId: str | None = None
    payee: str | None = None


class TillCloseRequest(BaseModel):
    countedDenominations: dict[str, int]
    sessionId: str | None = None


class CashMovementOut(BaseModel):
    id: str
    kind: str
    amount: Money
    denominations: dict[str, int]
    notes: str | None
    at: datetime
    accountId: str | None = None
    accountName: str | None = None
    payee: str | None = None


class TillCurrentOut(BaseModel):
    isOpen: bool
    sessionId: str | None = None
    counterId: str | None = None
    counterName: str | None = None
    openedBy: str | None = None
    sessionNumber: str | None = None
    openedAt: datetime | None = None
    openingFloat: Money | None = None
    openingDenominations: dict[str, int] | None = None
    openingNotes: str | None = None
    movements: list[CashMovementOut] = []


class TillCloseOut(BaseModel):
    sessionNumber: str
    counterName: str | None = None
    grossSale: Money
    totalDisc: Money
    gst: Money
    misc: Money
    creditSale: Money
    # Card, Easypaisa / JazzCash, bank transfer, gift voucher and points: money that isn't in the drawer.
    nonCashSale: Money = 0
    cashSaleReturn: Money
    tillOpen: Money
    cashIn: Money
    cashOut: Money
    netCash: Money
    countedCash: Money
    variance: Money


class TillSessionSummaryOut(BaseModel):
    sessionNumber: str
    counterName: str | None = None
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
    # Card, Easypaisa / JazzCash, bank transfer, gift voucher and points: money that isn't in the drawer.
    nonCashSale: Money = 0
    cashSaleReturn: Money
    tillOpen: Money
    cashIn: Money
    cashOut: Money
    netCash: Money
