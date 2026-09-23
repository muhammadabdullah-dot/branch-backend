"""Pharmacy slips (services/slips_service.py): a Pharmacist prints one, the cash counter takes its payment on its own,
and an open one can be cancelled with a reason.

Included by routes/sales.py, so it needs no line in app/main.py.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.middlewares.auth import get_current_user, require_any_permission, require_permission
from app.models import User
from app.schemas.held_bills import SlipCancelIn, SlipCreateIn, SlipOut, SlipPaidOut, SlipPayIn
from app.services import pharmacy_service, slips_service
from app.services.rbac_service import has_permission

router = APIRouter(prefix="/slips", tags=["pharmacy slips"])

_make = require_permission("store.slips", "W")
# Whoever takes payment pays a slip: never a Pharmacist, whatever is ticked (core/abilities.py PHARMACIST_NEVER_ACTIONS).
_take = require_permission("store.billing", "W")
_list = require_any_permission(("store.slips", "W"), ("store.billing", "W"), ("store.hold-recall", "R"))


def _raise(exc: slips_service.SlipError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _out(slip, user: User) -> SlipOut:
    # Someone who doesn't sell Pharmacy Items is told how many Items a slip has, never which.
    return slips_service.to_out(slip, show_lines=not pharmacy_service.hides_pharmacy(user))


@router.post("", response_model=SlipOut)
async def make_pharmacy_slip(payload: SlipCreateIn, user: User = Depends(_make)) -> SlipOut:
    """The Pharmacist's bill, stored as a slip with its own number, for the customer to pay at the cash counter. Printing
    the same bill again hands back the same slip."""
    try:
        return _out(await slips_service.make(user, payload), user)
    except slips_service.SlipError as exc:
        raise _raise(exc) from exc


@router.get("", response_model=list[SlipOut])
async def list_slips(
    status: str = Query("open", pattern="^(open|paid|cancelled|all)$"), day: date | None = None, user: User = Depends(_list),
) -> list[SlipOut]:
    """Newest first: open slips whatever day they were printed, paid and cancelled ones by the day it happened (today
    when no day is given). The cash counter and the held bills screen see every one; a Pharmacist sees only their own."""
    from app.core.pk_time import today_pk

    everyone = await has_permission(user, "store.billing", "W") or await has_permission(user, "store.hold-recall", "R")
    slips = await slips_service.list_slips(user, not everyone, status, day or today_pk())
    return [_out(s, user) for s in slips]


@router.get("/number/{code}", response_model=SlipOut)
async def find_slip(code: str, user: User = Depends(_take)) -> SlipOut:
    """A slip by the number printed on it (typed or scanned), for the payment dialog. One that can't be paid says why."""
    try:
        return _out(await slips_service.lookup(code), user)
    except slips_service.SlipError as exc:
        raise _raise(exc) from exc


@router.post("/{code}/pay", response_model=SlipPaidOut)
async def pay_pharmacy_slip(code: str, payload: SlipPayIn, user: User = Depends(_take)) -> SlipPaidOut:
    """The slip's payment, on its own: never onto a bill. Cash, card or online, into this person's till. Sending the
    same payment again hands back the one already taken."""
    from app.services import fbr_service

    try:
        slip, sale = await slips_service.pay(user, code, payload)
    except slips_service.SlipError as exc:
        raise _raise(exc) from exc
    # The payment's bill has committed: one short try to get its FBR number onto the paid note.
    await fbr_service.send_after_commit(sales=[sale])
    out = slips_service.paid_out(slip, sale)
    out.fbr = await fbr_service.stamp_for_sale(sale)
    return out


@router.post("/{slip_id}/cancel", response_model=SlipOut)
async def cancel_pharmacy_slip(slip_id: str, payload: SlipCancelIn, user: User = Depends(get_current_user)) -> SlipOut:
    """An open slip the customer won't pay for: by the Pharmacist who made it, or a Branch Manager, with a reason."""
    try:
        return _out(await slips_service.cancel(user, slip_id, payload.reason), user)
    except slips_service.SlipError as exc:
        raise _raise(exc) from exc
