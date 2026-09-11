from app.models import HeldBill, Party
from app.schemas.held_bills import HeldBillCreate


async def list_all() -> list[HeldBill]:
    return await HeldBill.all().order_by("-held_at")


async def hold(data: HeldBillCreate) -> HeldBill:
    party = await Party.get_or_none(id=data.partyId) if data.partyId else None
    return await HeldBill.create(
        label=data.label, lines=[line.model_dump() for line in data.lines], party=party
    )


async def remove(bill_id: str) -> bool:
    deleted = await HeldBill.filter(id=bill_id).delete()
    return deleted > 0
