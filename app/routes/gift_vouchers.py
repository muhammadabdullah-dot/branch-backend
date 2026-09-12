from fastapi import APIRouter, Depends

from app.controllers import gift_vouchers_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.gift_vouchers import GiftVoucherIssueRequest, GiftVoucherListOut, GiftVoucherOut, GiftVoucherRedeemRequest

router = APIRouter(prefix="/gift-vouchers", tags=["gift-vouchers"])

_read = require_permission("store.gift-vouchers", "R")
_issue = require_permission("store.gift-vouchers", "X")
_redeem = require_permission("store.gift-vouchers", "W")


@router.get("", response_model=GiftVoucherListOut)
async def list_vouchers(limit: int = 200, offset: int = 0, user: User = Depends(_read)) -> GiftVoucherListOut:
    return await gift_vouchers_controller.list_all(limit, offset)


@router.get("/{code}", response_model=GiftVoucherOut)
async def find(code: str, user: User = Depends(_read)) -> GiftVoucherOut:
    return await gift_vouchers_controller.find(code)


@router.post("", response_model=GiftVoucherOut)
async def issue(payload: GiftVoucherIssueRequest, user: User = Depends(_issue)) -> GiftVoucherOut:
    return await gift_vouchers_controller.issue(payload)


@router.post("/{code}/redeem", response_model=GiftVoucherOut)
async def redeem(code: str, payload: GiftVoucherRedeemRequest, user: User = Depends(_redeem)) -> GiftVoucherOut:
    return await gift_vouchers_controller.redeem(code, payload)
