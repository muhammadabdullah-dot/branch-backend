"""One till session's report (X while open, Z once closed), and the sessions X/Z opens it from."""
from datetime import datetime

from pydantic import BaseModel

from app.schemas.types import Money


class TillReportSessionOut(BaseModel):
    id: str
    sessionNumber: str
    status: str
    counterName: str | None = None
    openedBy: str | None = None
    closedBy: str | None = None
    openedAt: datetime
    closedAt: datetime | None = None
    openingFloat: Money
    netCash: Money | None = None
    countedCash: Money | None = None
    variance: Money | None = None


class TillReportTenderOut(BaseModel):
    code: str
    name: str
    amount: Money
    bills: int


class TillReportRefundOut(BaseModel):
    code: str
    name: str
    amount: Money
    count: int


class TillReportPersonOut(BaseModel):
    name: str
    bills: int
    amount: Money


class TillReportMovementOut(BaseModel):
    kind: str
    amount: Money
    at: datetime
    notes: str | None = None
    payee: str | None = None
    accountName: str | None = None
    by: str | None = None


class TillReportOut(BaseModel):
    sessionId: str
    sessionNumber: str
    status: str
    # X: still open, figures so far. Z: closed, with the count.
    kind: str
    counterName: str | None = None
    counterCode: str | None = None
    openedBy: str | None = None
    closedBy: str | None = None
    openedAt: datetime
    closedAt: datetime | None = None
    openingNotes: str | None = None
    openingDenominations: dict[str, int] = {}
    closingDenominations: dict[str, int] | None = None
    invoices: int
    netSale: Money
    tenders: list[TillReportTenderOut]
    returnsCount: int
    returnsTotal: Money
    refunds: list[TillReportRefundOut]
    people: list[TillReportPersonOut]
    movements: list[TillReportMovementOut]
    grossSale: Money
    totalDisc: Money
    gst: Money
    misc: Money
    # What rounding each bill to the rupee added (or took off).
    roundOff: Money = 0
    creditSale: Money
    nonCashSale: Money
    cashSaleReturn: Money
    tillOpen: Money
    cashIn: Money
    cashOut: Money
    netCash: Money
    # What should be in the drawer: at close, what was worked out then; while open, now.
    expectedCash: Money
    countedCash: Money | None = None
    # Counted less expected: below zero is short, above is over.
    variance: Money | None = None
    generatedAt: datetime
