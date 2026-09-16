from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.controllers import gift_vouchers_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.gift_vouchers import GiftVoucherCheckOut, GiftVoucherIssueRequest, GiftVoucherListOut, GiftVoucherOut, GiftVoucherRedeemRequest

router = APIRouter(prefix="/gift-vouchers", tags=["gift-vouchers"])

_read = require_permission("store.gift-vouchers", "R")
_issue = require_permission("store.gift-vouchers", "X")
_redeem = require_permission("store.gift-vouchers", "W")


@router.get("", response_model=GiftVoucherListOut)
async def list_vouchers(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
    limit: int = 200, offset: int = 0, user: User = Depends(_read),
) -> GiftVoucherListOut:
    """Vouchers issued in the window, newest first."""
    return await gift_vouchers_controller.list_all(limit, offset, from_, to)


@router.get("/{code}", response_model=GiftVoucherOut)
async def find(code: str, user: User = Depends(_read)) -> GiftVoucherOut:
    return await gift_vouchers_controller.find(code)


@router.get("/{code}/check", response_model=GiftVoucherCheckOut)
async def check(code: str, partyId: str | None = None, user: User = Depends(_read)) -> GiftVoucherCheckOut:
    """Whether the voucher can pay on a bill for `partyId` (omit for a walk-in bill). `refusal` is
    null when it can, and otherwise the exact reason the sale would be refused."""
    return await gift_vouchers_controller.check(code, partyId)


@router.post("", response_model=GiftVoucherOut)
async def issue(payload: GiftVoucherIssueRequest, user: User = Depends(_issue)) -> GiftVoucherOut:
    return await gift_vouchers_controller.issue(payload, user)


@router.post("/{code}/redeem", response_model=GiftVoucherOut)
async def redeem(code: str, payload: GiftVoucherRedeemRequest, user: User = Depends(_redeem)) -> GiftVoucherOut:
    return await gift_vouchers_controller.redeem(code, payload)
