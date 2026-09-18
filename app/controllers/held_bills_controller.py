from fastapi import HTTPException, status

from app.models import HeldBill
from app.schemas.held_bills import HeldBillCreate, HeldBillOut
from app.schemas.sales import SaleLineOut
from app.services import held_bills_service


def _to_out(b: HeldBill) -> HeldBillOut:
    return HeldBillOut(
        id=str(b.id), label=b.label,
        lines=[SaleLineOut(**line) for line in b.lines],
        partyId=str(b.party_id) if b.party_id else None,
        heldAt=b.held_at,
    )


async def list_all(user=None) -> list[HeldBillOut]:
    return [_to_out(b) for b in await held_bills_service.list_all(user)]


async def hold(data: HeldBillCreate, user=None) -> HeldBillOut:
    return _to_out(await held_bills_service.hold(data, user))


async def remove(bill_id: str, user=None) -> None:
    if not await held_bills_service.remove(bill_id, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Held bill not found")
