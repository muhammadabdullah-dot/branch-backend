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
    # Which drawer. Empty: the caller's own. Someone else's only for a person who oversees the tills (a Branch Manager, or
    # the Counter board); anyone else naming another person's till is refused (403).
    sessionId: str | None = None
    # What it was for (cash out) or where it came from (cash in). Empty: petty expenses / the safe.
    accountId: str | None = None
    payee: str | None = None


class TillCloseRequest(BaseModel):
    countedDenominations: dict[str, int]
    # As for CashMovementRequest: the caller's own till unless they oversee the tills.
    sessionId: str | None = None
    # The person confirmed the drawer is empty. Nothing else closes a till with nothing counted.
    drawerEmpty: bool = False


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
    openedById: str | None = None
    # Whose tills this person may work: every open one (a Branch Manager, or the Counter board), else their own only.
    overseesTills: bool = False
    sessionNumber: str | None = None
    openedAt: datetime | None = None
    openingFloat: Money | None = None
    openingDenominations: dict[str, int] | None = None
    openingNotes: str | None = None
    movements: list[CashMovementOut] = []


class TillCloseOut(BaseModel):
    sessionNumber: str
    counterName: str | None = None
    openedBy: str | None = None
    closedBy: str | None = None
    grossSale: Money
    totalDisc: Money
    gst: Money
    misc: Money
    # What rounding each bill to the rupee added (or took off): the cash that really changed hands.
    roundOff: Money = 0
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
    roundOff: Money = 0
    creditSale: Money
    # Card, Easypaisa / JazzCash, bank transfer, gift voucher and points: money that isn't in the drawer.
    nonCashSale: Money = 0
    cashSaleReturn: Money
    tillOpen: Money
    cashIn: Money
    cashOut: Money
    netCash: Money


class OpenTillOut(BaseModel):
    """An open till a till screen can work on: the caller's own, and every other one for someone who oversees them."""
    id: str
    sessionNumber: str
    counterName: str | None = None
    openedBy: str | None = None
    openedAt: datetime
    mine: bool


class LastCloseOut(BaseModel):
    """What a counter's last close counted, offered on Till Open as the next opening count."""
    sessionNumber: str
    closedAt: datetime
    closedBy: str | None = None
    countedCash: Money
    denominations: dict[str, int] = {}


class TillOpenCounterOut(BaseModel):
    id: str
    code: str
    name: str
    location: str | None = None
    deviceId: str | None = None
    # Who is on it now. A counter with a till open says whose, never what it has taken.
    person: str | None = None
    personId: str | None = None
    tillOpen: bool = False
    openedBy: str | None = None
    lastClose: LastCloseOut | None = None


class TillOpenOptionsOut(BaseModel):
    counters: list[TillOpenCounterOut]
