from fastapi import APIRouter, Depends, File, UploadFile

from app.controllers import parties_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.import_result import ImportSummary
from app.schemas.parties import PartyContactIn, PartyContactOut, PartyCreate, PartyDetailOut, PartyOut, PartyUpdate

router = APIRouter(prefix="/parties", tags=["parties"])

_read = require_any_permission(("store.billing", "R"), ("branch-console.customers", "R"))
_write = require_permission("branch-console.customers", "W")


@router.get("", response_model=list[PartyOut])
async def search(q: str | None = None, includeInactive: bool = False, user: User = Depends(_read)) -> list[PartyOut]:
    """Active Parties only unless `includeInactive` — Billing must never attach a switched-off customer."""
    return await parties_controller.search(q, includeInactive)


@router.post("", response_model=PartyOut)
async def create(payload: PartyCreate, user: User = Depends(_write)) -> PartyOut:
    return await parties_controller.create(payload)


@router.post("/import", response_model=ImportSummary)
async def import_parties(file: UploadFile = File(...), user: User = Depends(_write)) -> ImportSummary:
    content = await file.read()
    return await parties_controller.import_file(file.filename, content)


@router.get("/{party_id}", response_model=PartyDetailOut)
async def detail(party_id: str, user: User = Depends(_read)) -> PartyDetailOut:
    """One Party with its other contacts — what the edit form opens with."""
    return await parties_controller.detail(party_id)


@router.patch("/{party_id}", response_model=PartyOut)
async def update(party_id: str, payload: PartyUpdate, user: User = Depends(_write)) -> PartyOut:
    return await parties_controller.update(party_id, payload)


@router.put("/{party_id}/contacts", response_model=list[PartyContactOut])
async def replace_contacts(party_id: str, payload: list[PartyContactIn], user: User = Depends(_write)) -> list[PartyContactOut]:
    """Saves the whole Other Contacts grid: the list sent replaces what was there."""
    return await parties_controller.replace_contacts(party_id, payload)


@router.get("/{party_id}/picture")
async def picture(party_id: str, user: User = Depends(_read)):
    return await parties_controller.picture(party_id)


@router.put("/{party_id}/picture", response_model=PartyOut)
async def upload_picture(party_id: str, file: UploadFile = File(...), user: User = Depends(_write)) -> PartyOut:
    """JPEG, PNG or WebP under 3 MB. Replaces any existing picture."""
    return await parties_controller.set_picture(party_id, await file.read())


@router.delete("/{party_id}/picture", response_model=PartyOut)
async def delete_picture(party_id: str, user: User = Depends(_write)) -> PartyOut:
    return await parties_controller.remove_picture(party_id)
