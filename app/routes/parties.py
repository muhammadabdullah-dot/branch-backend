from fastapi import APIRouter, Depends

from app.controllers import parties_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.parties import PartyCreate, PartyOut, PartyUpdate

router = APIRouter(prefix="/parties", tags=["parties"])

_read = require_any_permission(("store.billing", "R"), ("branch-console.customers", "R"))
_write = require_permission("branch-console.customers", "W")


@router.get("", response_model=list[PartyOut])
async def search(q: str | None = None, user: User = Depends(_read)) -> list[PartyOut]:
    return await parties_controller.search(q)


@router.post("", response_model=PartyOut)
async def create(payload: PartyCreate, user: User = Depends(_write)) -> PartyOut:
    return await parties_controller.create(payload)


@router.patch("/{party_id}", response_model=PartyOut)
async def update(party_id: str, payload: PartyUpdate, user: User = Depends(_write)) -> PartyOut:
    return await parties_controller.update(party_id, payload)
