from datetime import datetime

from pydantic import BaseModel, Field


class VerifyIn(BaseModel):
    """The three things printed on the sheet main office sends with a new branch server."""

    cloudUrl: str
    code: str
    pairingKey: str


class BranchIdentityOut(BaseModel):
    """This branch, as head office defined it. Every field here is read-only by design — there is
    no PATCH for any of it, because these values are also the sync credentials."""

    branchId: str
    code: str
    name: str
    address: str | None = None
    city: str | None = None
    phone: str | None = None
    timezone: str
    cloudUrl: str
    verifiedAt: datetime


class RegistrationStatusOut(BaseModel):
    """Answered without a token, because the app has to ask it before anyone can sign in — on a
    fresh branch server there is nobody to sign in as yet. It carries no secret: whether a branch
    is set up, and what it is called, is the same thing written on the shop front."""

    initialized: bool
    branch: BranchIdentityOut | None = None
    serverTime: datetime


class ResetIn(BaseModel):
    """Typing the branch code back is the confirmation. A yes/no dialog is too easy to click
    through for something that costs a trip to main office to undo."""

    confirmCode: str = Field(min_length=1)


class SyncStatusOut(BaseModel):
    """What an operator standing in the branch needs to answer "is our data reaching head office?"
    without ringing anyone."""

    initialized: bool
    branch: BranchIdentityOut | None = None
    pendingEvents: int
    sentEvents: int
    lastAttemptAt: datetime | None = None
    lastSuccessAt: datetime | None = None
    lastError: str | None = None
    consecutiveFailures: int
    running: bool
    schedulerActive: bool
    scheduleHint: str
    serverTime: datetime
    # Downstream: what head office sent here.
    pullCursor: int = 0
    lastPullAt: datetime | None = None
    lastPullError: str | None = None


class SyncCollectOut(BaseModel):
    """A quick exchange with head office: collect what it sent, send what's waiting here. No figures."""

    ok: bool
    applied: int
    failed: int
    sent: int
    pendingAfter: int
    # Items whose stock went to head office because they moved since it was last told.
    stockItems: int = 0
    error: str | None = None
    skippedReason: str | None = None


class SyncRunOut(BaseModel):
    ok: bool
    sent: int
    duplicates: int
    pendingBefore: int
    pendingAfter: int
    batches: int
    error: str | None = None
    skippedReason: str | None = None
    # The reporting half. "Events are up to date" and "head office's figures are up to date" are
    # different claims, so they are reported separately rather than collapsed into one tick.
    snapshotOk: bool = False
    tradingDays: int = 0
    productDays: int = 0
    stockRows: int = 0
    # Updates collected from head office in the same run.
    pulledApplied: int = 0
    pulledFailed: int = 0
    pullError: str | None = None
