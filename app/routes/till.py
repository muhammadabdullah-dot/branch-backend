from fastapi import APIRouter, Depends

from app.controllers import till_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.till import (
    CashMovementOut,
    CashMovementRequest,
    TillCloseOut,
    TillClosePreviewOut,
    TillCloseRequest,
    TillCurrentOut,
    TillOpenRequest,
)

router = APIRouter(prefix="/till", tags=["till"])

_read = require_permission("store.till", "R")
_write = require_permission("store.till", "W")


@router.get("/current", response_model=TillCurrentOut)
async def current(user: User = Depends(_read)) -> TillCurrentOut:
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
