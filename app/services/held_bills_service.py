from app.models import HeldBill, Party, User
from app.schemas.held_bills import HeldBillCreate


async def list_all() -> list[HeldBill]:
    return await HeldBill.all().order_by("-held_at")


async def hold(data: HeldBillCreate, user: User | None = None) -> HeldBill:
    party = await Party.get_or_none(id=data.partyId) if data.partyId else None
    lines = [line.model_dump() for line in data.lines]
    if user is not None:
        # Pharmacy lines held by pharmacy staff (or held again under their pass) stay cleared for whoever takes payment.
        from app.services import pharmacy_service

        lines = await pharmacy_service.mark_held_lines(lines, user, data.pharmacyPass, data.billId)
    return await HeldBill.create(label=data.label, lines=lines, party=party)


async def remove(bill_id: str) -> bool:
    deleted = await HeldBill.filter(id=bill_id).delete()
    return deleted > 0
