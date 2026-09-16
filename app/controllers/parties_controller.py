from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.models import Party, PartyContact
from app.schemas.import_result import ImportSummary
from app.schemas.parties import PartyContactIn, PartyContactOut, PartyCreate, PartyDetailOut, PartyOut, PartyUpdate
from app.services import media_service, parties_service


def _fields(p: Party) -> dict:
    return dict(
        id=str(p.id), code=p.code, name=p.name, isWalkIn=p.is_walk_in,
        phone=p.phone, telephone=p.telephone, fax=p.fax, email=p.email,
        address=p.address, address2=p.address2, city=p.city, area=p.area, subArea=p.sub_area,
        category=p.category, contactPerson=p.contact_person, ntn=p.ntn, cnic=p.cnic, sTaxRegNo=p.s_tax_reg_no,
        loyaltyNo=p.loyalty_no, dueDays=p.due_days, creditAllowed=p.credit_allowed,
        creditLimit=p.credit_limit, creditBalance=p.credit_balance, tier=p.tier,
        hasPicture=bool(p.picture), active=p.active,
    )


def _to_out(p: Party) -> PartyOut:
    return PartyOut(**_fields(p))


def _contact_out(c: PartyContact) -> PartyContactOut:
    return PartyContactOut(
        id=str(c.id), cellNo=c.cell_no, contactPerson=c.contact_person, email=c.email,
        officeAddress=c.office_address, resAddress=c.res_address, remarks=c.remarks,
    )


def _raise(exc: parties_service.PartyError) -> None:
    raise HTTPException(exc.status, exc.message)


async def search(q: str | None, include_inactive: bool = False) -> list[PartyOut]:
    parties = (
        await parties_service.find_by_query(q, include_inactive) if q
        else await parties_service.list_all(include_inactive)
    )
    return [_to_out(p) for p in parties]


async def detail(party_id: str) -> PartyDetailOut:
    try:
        party, contacts = await parties_service.detail(party_id)
    except parties_service.PartyError as exc:
        _raise(exc)
    return PartyDetailOut(**_fields(party), contacts=[_contact_out(c) for c in contacts])


async def create(data: PartyCreate) -> PartyOut:
    return _to_out(await parties_service.create(data))


async def update(party_id: str, data: PartyUpdate) -> PartyOut:
    try:
        return _to_out(await parties_service.update(party_id, data))
    except parties_service.PartyError as exc:
        _raise(exc)


async def replace_contacts(party_id: str, contacts: list[PartyContactIn]) -> list[PartyContactOut]:
    try:
        return [_contact_out(c) for c in await parties_service.replace_contacts(party_id, contacts)]
    except parties_service.PartyError as exc:
        _raise(exc)


async def set_picture(party_id: str, content: bytes) -> PartyOut:
    try:
        return _to_out(await parties_service.set_picture(party_id, content))
    except parties_service.PartyError as exc:
        _raise(exc)


async def remove_picture(party_id: str) -> PartyOut:
    try:
        return _to_out(await parties_service.remove_picture(party_id))
    except parties_service.PartyError as exc:
        _raise(exc)


async def picture(party_id: str) -> FileResponse:
    party = await Party.get_or_none(id=party_id)
    found = media_service.picture_file(party.picture if party else None)
    if not found:
        raise HTTPException(404, "No picture for this Party")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})


async def import_file(filename: str, content: bytes) -> ImportSummary:
    return await parties_service.import_parties(filename, content)
