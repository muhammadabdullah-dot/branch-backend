from fastapi import HTTPException, status

from app.models import Party
from app.schemas.import_result import ImportSummary
from app.schemas.parties import PartyCreate, PartyOut, PartyUpdate
from app.services import parties_service


def _to_out(p: Party) -> PartyOut:
    return PartyOut(
        id=str(p.id), code=p.code, name=p.name, isWalkIn=p.is_walk_in,
        phone=p.phone, email=p.email, address=p.address, area=p.area,
        contactPerson=p.contact_person, ntn=p.ntn, cnic=p.cnic, sTaxRegNo=p.s_tax_reg_no,
        loyaltyNo=p.loyalty_no, dueDays=p.due_days, creditAllowed=p.credit_allowed,
        creditLimit=p.credit_limit, creditBalance=p.credit_balance, tier=p.tier, active=p.active,
    )


async def search(q: str | None) -> list[PartyOut]:
    parties = await parties_service.find_by_query(q) if q else await parties_service.list_all()
    return [_to_out(p) for p in parties]


async def create(data: PartyCreate) -> PartyOut:
    return _to_out(await parties_service.create(data))


async def update(party_id: str, data: PartyUpdate) -> PartyOut:
    party = await parties_service.update(party_id, data)
    if not party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    return _to_out(party)


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await parties_service.import_parties(filename, content)
