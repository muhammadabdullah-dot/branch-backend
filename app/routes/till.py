from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.controllers import till_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.till import (
    CashMovementOut,
    CashMovementRequest,
    OpenTillOut,
    TillCloseOut,
    TillClosePreviewOut,
    TillCloseRequest,
    TillCurrentOut,
    TillOpenOptionsOut,
    TillOpenRequest,
    TillSessionListOut,
)
from app.schemas.till_report import TillReportOut, TillReportSessionOut

router = APIRouter(prefix="/till", tags=["till"])

_read = require_permission("store.till", "R")
_write = require_permission("store.till", "W")
# Whether a Till is open, and which, is a branch-oversight fact as much as a counter one —
# the Branch Dashboard leads with it. Read-only; opening/closing still needs store.till:W.
_current_read = require_any_permission(("store.till", "R"), ("branch-console.dashboard", "R"))
# X/Z's Day Close tab and Reports' Staff & Work — neither implies store.till itself (a Sales
# Manager reading X/Z, or an Inventory Manager reading Reports, may legitimately lack it).
_sessions_read = require_any_permission(("store.xz", "R"), ("reports", "R"))


@router.get("/current", response_model=TillCurrentOut)
async def current(sessionId: str | None = None, user: User = Depends(_current_read)) -> TillCurrentOut:
    """The caller's own open till. `sessionId` names another open one, for someone who oversees the tills (a Branch
    Manager, or the Counter board); anyone else gets 403 for a till that isn't theirs."""
    return await till_controller.current(user, sessionId)


@router.get("/open-tills", response_model=list[OpenTillOut])
async def open_tills(user: User = Depends(_write)) -> list[OpenTillOut]:
    """The open tills the caller can work on: every one for someone who oversees them, else only their own."""
    return await till_controller.open_tills(user)


@router.get("/open-options", response_model=TillOpenOptionsOut)
async def open_options(user: User = Depends(_write)) -> TillOpenOptionsOut:
    """Till Open's counters (free or taken, and who is on each) and what each counter's last close counted."""
    return await till_controller.open_options(user)


@router.post("/open", response_model=TillCurrentOut)
async def open_till(payload: TillOpenRequest, user: User = Depends(_write)) -> TillCurrentOut:
    return await till_controller.open_till(user, payload)


@router.post("/cash-in", response_model=CashMovementOut)
async def cash_in(payload: CashMovementRequest, user: User = Depends(_write)) -> CashMovementOut:
    return await till_controller.cash_in(user, payload)


@router.post("/cash-out", response_model=CashMovementOut)
async def cash_out(payload: CashMovementRequest, user: User = Depends(_write)) -> CashMovementOut:
    return await till_controller.cash_out(user, payload)


@router.get("/close/preview", response_model=TillClosePreviewOut)
async def preview_close(sessionId: str | None = None, user: User = Depends(_read)) -> TillClosePreviewOut:
    return await till_controller.preview_close(user, sessionId)


@router.post("/close", response_model=TillCloseOut)
async def close(payload: TillCloseRequest, user: User = Depends(_write)) -> TillCloseOut:
    return await till_controller.close(user, payload)


@router.get("/sessions", response_model=TillSessionListOut)
async def list_sessions(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
    user: User = Depends(_sessions_read),
) -> TillSessionListOut:
    return await till_controller.list_sessions(user, from_, to, limit, offset)


@router.get("/reports", response_model=list[TillReportSessionOut])
async def report_sessions(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    user: User = Depends(_sessions_read),
) -> list[TillReportSessionOut]:
    """Every till open now and the ones closed in the window, with their ids, to open one session's report."""
    return await till_controller.report_sessions(user, from_, to)


@router.get("/reports/{session_id}", response_model=TillReportOut)
async def session_report(session_id: str, user: User = Depends(_sessions_read)) -> TillReportOut:
    """One session's report: X while it's open (figures so far), Z once closed (counted, short or over)."""
    return await till_controller.session_report(user, session_id)
