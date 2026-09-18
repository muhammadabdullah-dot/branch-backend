from fastapi import APIRouter, Depends

from app.controllers import held_bills_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.held_bills import HeldBillCreate, HeldBillOut

router = APIRouter(prefix="/held-bills", tags=["held-bills"])

_read = require_permission("store.hold-recall", "R")
_write = require_permission("store.hold-recall", "W")


@router.get("", response_model=list[HeldBillOut])
async def list_all(user: User = Depends(_read)) -> list[HeldBillOut]:
    return await held_bills_controller.list_all()


@router.post("", response_model=HeldBillOut)
async def hold(payload: HeldBillCreate, user: User = Depends(_write)) -> HeldBillOut:
    return await held_bills_controller.hold(payload, user)


@router.delete("/{bill_id}")
async def remove(bill_id: str, user: User = Depends(_write)) -> dict:
    await held_bills_controller.remove(bill_id)
    return {"detail": "removed"}
