from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.controllers import till_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.till import (
    CashMovementOut,
    CashMovementRequest,
    TillCloseOut,
    TillClosePreviewOut,
    TillCloseRequest,
    TillCurrentOut,
    TillOpenRequest,
    TillSessionListOut,
)

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
async def current(user: User = Depends(_current_read)) -> TillCurrentOut:
    return await till_controller.current()


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
async def preview_close(user: User = Depends(_read)) -> TillClosePreviewOut:
    return await till_controller.preview_close()


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
    return await till_controller.list_sessions(from_, to, limit, offset)
