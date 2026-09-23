"""FBR invoices: the settings card, the list of invoices waiting for FBR, and the FBR block a receipt prints
(services/fbr_service.py)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FbrStampOut(BaseModel):
    """What a receipt prints in its FBR block."""
    # off: no FBR block. dummy: a test number, not registered with FBR. waiting: issued offline, not yet with FBR.
    # posted: FBR's own number. refused: FBR answered with an error; the receipt says it is waiting for FBR.
    status: Literal["off", "dummy", "waiting", "posted", "refused"]
    number: str | None = None
    # The POS ID FBR registered for the counter it was rung at, the POS's own number for it (the bill or return number),
    # and whether FBR's number came from FBR's sandbox (a test system: not a real invoice).
    posId: str | None = None
    usin: str | None = None
    sandbox: bool = False
    # The tax office the business is registered at, as SRO 1006(I)/2021 asks every receipt to print.
    taxOffice: str | None = None


class FbrCounterOut(BaseModel):
    id: str
    code: str
    name: str
    active: bool
    posId: str | None = None


class FbrQueueSummaryOut(BaseModel):
    waiting: int
    refused: int
    oldestAt: datetime | None = None


class FbrSettingsOut(BaseModel):
    mode: Literal["off", "dummy", "sandbox", "production"]
    # Never the token: whether one is saved, and its last four characters.
    tokenSet: bool
    tokenHint: str | None = None
    counters: list[FbrCounterOut]
    taxOffice: str | None = None
    defaultPctCode: str | None = None
    sandboxUrl: str
    productionUrl: str
    updatedAt: datetime | None = None
    updatedBy: str | None = None
    # Only a Branch Manager changes these; everyone else who can see the settings sees them read-only.
    canChange: bool
    queue: FbrQueueSummaryOut


class FbrSettingsIn(BaseModel):
    mode: str = Field(max_length=12)
    # Counter id -> the POS ID FBR gave that counter. Blank: none.
    posIds: dict[str, str | None] = {}
    # A new token replaces the saved one; left out or blank keeps it. clearToken removes it. Both are kept out of the
    # activity trail (middlewares/activity.py hides any field named like a token).
    accessToken: str | None = Field(default=None, max_length=400)
    clearToken: bool = False
    taxOffice: str | None = Field(default=None, max_length=120)
    defaultPctCode: str | None = Field(default=None, max_length=12)


class FbrQueueItemOut(BaseModel):
    id: str
    # sale or return, its number (USIN), and for a return the bill it is against.
    kind: str
    usin: str
    refUsin: str | None = None
    counter: str | None = None
    mode: str
    status: Literal["waiting", "refused"]
    attempts: int
    lastError: str | None = None
    nextTryAt: datetime | None = None
    createdAt: datetime


class FbrQueueOut(FbrQueueSummaryOut):
    mode: str
    items: list[FbrQueueItemOut]
