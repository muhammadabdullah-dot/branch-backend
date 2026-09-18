from app.models import HeldBill, Party, User
from app.schemas.held_bills import HeldBillCreate


async def list_all(user: User | None = None) -> list[HeldBill]:
    # Bills parked with F4 only. Pharmacy slips share the table and are listed on their own (services/slips_service.py).
    bills = await HeldBill.filter(kind="held").order_by("-held_at")
    if user is None or not hidden_from(user):
        return bills
    return [b for b in bills if not await _carries_pharmacy(b)]


def hidden_from(user: User) -> bool:
    """A Salesperson never sees Pharmacy Items, so a held bill carrying any (only a Branch Manager could have held one)
    is the Branch Manager's to recall, void and take payment for."""
    from app.services import pharmacy_service

    return pharmacy_service.hides_pharmacy(user)


async def _carries_pharmacy(bill: HeldBill) -> bool:
    from app.services import pharmacy_service

    return await pharmacy_service.carries_pharmacy(bill.lines or [])


async def get_for(user: User, bill_id: str) -> HeldBill | None:
    """The held bill, when this person may see it."""
    held = await HeldBill.get_or_none(id=bill_id, kind="held")
    if held and hidden_from(user) and await _carries_pharmacy(held):
        return None
    return held


async def hold(data: HeldBillCreate, user: User | None = None) -> HeldBill:
    party = await Party.get_or_none(id=data.partyId) if data.partyId else None
    lines = [line.model_dump() for line in data.lines]
    if user is not None:
        # The lines the holder may sell (or holds again under their pass) stay cleared for whoever takes payment, a
        # Salesperson finishing a Pharmacist's bill or the other way round.
        from app.services import pharmacy_service

        lines = await pharmacy_service.mark_held_lines(lines, user, data.pharmacyPass, data.billId)
    return await HeldBill.create(label=data.label, lines=lines, party=party)


async def remove(bill_id: str, user: User | None = None) -> bool:
    held = await (get_for(user, bill_id) if user is not None else HeldBill.get_or_none(id=bill_id, kind="held"))
    if not held:
        return False
    await held.delete()
    return True
