"""Counter board and Staff on Duty wire shapes."""
from datetime import datetime

from pydantic import BaseModel

from app.schemas.types import Money


class CounterCreateRequest(BaseModel):
    code: str
    name: str
    location: str | None = None


class CounterUpdateRequest(BaseModel):
    # The short code can be corrected only until the first till is opened at the counter.
    code: str | None = None
    name: str | None = None
    location: str | None = None
    active: bool | None = None


class AssignRequest(BaseModel):
    userId: str
    note: str | None = None


class CounterOut(BaseModel):
    id: str
    code: str
    # A till has been opened here at some time, so the code is fixed (bills and tills carry it from then on).
    codeLocked: bool = False
    name: str
    location: str | None = None
    active: bool
    deviceId: str | None = None
    # Who is standing here, since when, and who put them there.
    person: str | None = None
    personId: str | None = None
    onDutySince: datetime | None = None
    assignedBy: str | None = None
    # The drawer at this counter.
    tillOpen: bool = False
    # The open till's id, so the board can take a Branch Manager straight to closing it.
    sessionId: str | None = None
    sessionNumber: str | None = None
    sessionOpenedAt: datetime | None = None
    openedBy: str | None = None
    expectedCash: Money | None = None
    # What this counter has taken today.
    bills: int = 0
    netSales: Money = 0
    returns: int = 0
    refunds: Money = 0
    lastSaleAt: datetime | None = None


class LooseTillOut(BaseModel):
    """A drawer open without a counter — opened before the branch had counters, or on one since retired."""
    sessionId: str | None = None
    sessionNumber: str
    openedBy: str
    openedAt: datetime


class CounterBoardOut(BaseModel):
    counters: list[CounterOut]
    looseTills: list[LooseTillOut] = []
    day: str
    asOf: datetime
    openTills: int
    onDuty: int
    unattendedTills: int


class DutyPersonOut(BaseModel):
    userId: str
    name: str
    counter: str | None = None
    counterId: str | None = None
    onDuty: bool
    since: datetime | None = None
    until: datetime | None = None
    minutes: int = 0
    assignedBy: str | None = None
    note: str | None = None
    sessionNumber: str | None = None
    tillOpen: bool = False
    expectedCash: Money | None = None
    bills: int = 0
    netSales: Money = 0
    returns: int = 0
    refunds: Money = 0
    cashIn: Money = 0
    cashOut: Money = 0
    lastSaleAt: datetime | None = None


class StaffOnDutyOut(BaseModel):
    people: list[DutyPersonOut]
    day: str
    asOf: datetime
    onDuty: int
    traded: int
    # People who rang sales today without being on a counter: the board and the floor disagree.
    tradedOffDuty: int


class DutyHistoryRow(BaseModel):
    id: str
    counter: str
    counterCode: str
    person: str
    startedAt: datetime
    endedAt: datetime | None = None
    minutes: int
    assignedBy: str | None = None
    endedBy: str | None = None
    note: str | None = None


class DutyHistoryOut(BaseModel):
    items: list[DutyHistoryRow]
