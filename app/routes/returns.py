"""Returns beyond a plain refund (which stays at POST /returns in routes/sales.py): replace and exchange, the return
receipt and printing it again, the day's returns, and the return window per department."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.controllers import returns_controller
from app.middlewares.auth import get_current_user, refusal, require_any_permission, require_permission
from app.models import User
from app.schemas.returns import ExchangeOut, ExchangeRequest, ReturnListItemOut, ReturnReceiptOut, ReturnWindowsIn, ReturnWindowsOut
from app.services.rbac_service import has_permission

router = APIRouter(tags=["returns"])


async def _return_and_sell(user: User = Depends(get_current_user)) -> User:
    """A replace or an exchange takes goods back and rings a new bill, so it needs both."""
    needed = [("store.returns", "W"), ("store.billing", "W")]
    missing = [pair for pair in needed if not await has_permission(user, *pair)]
    if missing:
        raise HTTPException(status.HTTP_403_FORBIDDEN, refusal(missing, user))
    return user


_list_read = require_any_permission(("store.returns", "R"), ("store.xz", "R"), ("reports", "R"))
_receipt_read = require_any_permission(("store.returns", "R"), ("store.reprint", "X"))
# Printing a return receipt again: the same tick as reprinting a bill.
_reprint = require_permission("store.reprint", "X")
_windows_read = require_any_permission(("store.returns", "R"), ("store.billing", "R"), ("branch-console.shop-settings", "R"))
_windows_write = require_permission("branch-console.shop-settings", "W")


@router.post("/returns/exchange/quote")
async def quote_exchange(payload: ExchangeRequest, user: User = Depends(_return_and_sell)) -> dict:
    return await returns_controller.quote_exchange(user, payload)


@router.post("/returns/exchange", response_model=ExchangeOut)
async def exchange_items(payload: ExchangeRequest, user: User = Depends(_return_and_sell)) -> ExchangeOut:
    return await returns_controller.exchange(user, payload)


@router.get("/returns", response_model=list[ReturnListItemOut])
async def list_returns(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, limit: int = 200,
    user: User = Depends(_list_read),
) -> list[ReturnListItemOut]:
    return await returns_controller.list_returns(from_, to, limit)


@router.get("/returns/windows", response_model=ReturnWindowsOut)
async def return_windows(user: User = Depends(_windows_read)) -> ReturnWindowsOut:
    return await returns_controller.windows()


@router.put("/returns/windows", response_model=ReturnWindowsOut)
async def save_return_windows(payload: ReturnWindowsIn, user: User = Depends(_windows_write)) -> ReturnWindowsOut:
    return await returns_controller.save_windows(payload, user)


@router.get("/returns/{return_id}/receipt", response_model=ReturnReceiptOut)
async def return_receipt(return_id: str, user: User = Depends(_receipt_read)) -> ReturnReceiptOut:
    return await returns_controller.receipt(return_id, user)


# Looking isn't a reprint; this POST is, and the activity log keeps who and when, which is how the copy is numbered.
@router.post("/returns/{return_id}/reprint", response_model=ReturnReceiptOut)
async def reprint_return_receipt(return_id: str, user: User = Depends(_reprint)) -> ReturnReceiptOut:
    return await returns_controller.reprint(return_id, user)
