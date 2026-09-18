from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.controllers import rbac_controller
from app.controllers.auth_controller import note_header
from app.middlewares.auth import get_current_user, require_branch_manager
from app.models import User
from app.schemas.auth import PermissionOut
from app.schemas.rbac import (
    AbilitiesOut,
    AbilityGroupOut,
    RoleOut,
    UpdatePermissionsRequest,
    UserCreateRequest,
    UserNameOut,
    UserSummaryOut,
    UserUpdateRequest,
    WorksAsRequest,
)

router = APIRouter(prefix="/rbac", tags=["rbac"])
users_router = APIRouter(prefix="/users", tags=["rbac"])

# Staff and access are the Branch Manager's alone — not a tick anyone else can be given.
_read = require_branch_manager
_write = require_branch_manager


class GridColumnOut(BaseModel):
    action: str
    label: str


class GridRowOut(BaseModel):
    resource: str
    label: str
    hint: str = ""


class GridOut(BaseModel):
    columns: list[GridColumnOut]
    rows: list[GridRowOut]


class GroupWithGridOut(AbilityGroupOut):
    # A group that reads best as rows and columns (the account areas: See and Use).
    grid: GridOut | None = None


class CatalogOut(AbilitiesOut):
    groups: list[GroupWithGridOut]
    # The ticks the access screen doesn't offer a Pharmacist (they never take money), and the line it says about them.
    notForPharmacist: list[str] = []
    pharmacistNote: str | None = None


@router.get("/resources", response_model=list[str])
async def resources(user: User = Depends(_read)) -> list[str]:
    return rbac_controller.resources()


@router.get("/abilities", response_model=CatalogOut)
async def abilities(user: User = Depends(_read)) -> CatalogOut:
    """Everything a person at the branch can be given, grouped and in plain words, with the three starting points."""
    return CatalogOut(**rbac_controller.abilities())


@router.get("/roles", response_model=list[RoleOut])
async def roles(user: User = Depends(_read)) -> list[RoleOut]:
    """The roles a new starter can be given. Fixed set — adding one is a code change, not data."""
    return await rbac_controller.list_roles()


@users_router.post("", response_model=UserSummaryOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreateRequest, caller: User = Depends(_write)) -> UserSummaryOut:
    """Take on a new member of branch staff.

    Only a Branch Manager account can do this, the same as changing access. There is deliberately no
    separate branch-admin role:
    the person who answers for the branch is the person who hires into it, and a second
    authority would just be someone else to find when a salesperson starts on a Monday.
    """
    return await rbac_controller.create_user(payload, caller)


@users_router.patch("/{user_id}", response_model=UserSummaryOut)
async def update_user(
    user_id: str, payload: UserUpdateRequest, caller: User = Depends(_write)
) -> UserSummaryOut:
    """Rename someone, correct their email, reset their password, or deactivate them.

    Deactivate rather than delete: a user is on the other end of every sale, adjustment and
    till close they ever made, and removing the row would orphan that history."""
    return await rbac_controller.update_user(user_id, payload, caller)


@users_router.put("/{user_id}/works-as", response_model=UserSummaryOut)
async def switch_between_salesperson_and_pharmacist(
    user_id: str, payload: WorksAsRequest, response: Response, caller: User = Depends(_write)
) -> UserSummaryOut:
    """Which Items someone sells at the till, and whether they take payment. Their Sales counter ticks start again from
    the new role's; other ticks, title and limit stay (a Pharmacist's limit becomes 0). A Branch Manager isn't switched and
    nobody is made one here. Goes to head office like any other change to the account; the activity trail
    records it under this function's name, noting who moved from what to what."""
    out, note = await rbac_controller.switch_works_as(user_id, payload, caller)
    if note:
        response.headers.update(note_header(note))
    return out


@users_router.get("", response_model=list[UserSummaryOut])
async def list_users(user: User = Depends(_read)) -> list[UserSummaryOut]:
    return await rbac_controller.list_users()


@users_router.get("/names", response_model=list[UserNameOut])
async def list_user_names(user: User = Depends(get_current_user)) -> list[UserNameOut]:
    """Id → display name for every ledger row that records who did something.

    Any signed-in user, deliberately: the full `GET /users` above is gated on staff-access
    because it carries emails and roles, but *names* are what every Movements, Counts,
    Adjustments and Till row needs to be readable at all. Gating those behind an admin
    permission is why a Stock Keeper's own ledger showed rows filed under "User d400a6f3".
    """
    return await rbac_controller.list_user_names()


@users_router.get("/{user_id}/permissions", response_model=list[PermissionOut])
async def get_permissions(user_id: str, user: User = Depends(_read)) -> list[PermissionOut]:
    return await rbac_controller.get_permissions(user_id)


@users_router.put("/{user_id}/permissions", response_model=list[PermissionOut])
async def update_permissions(
    user_id: str, payload: UpdatePermissionsRequest, caller: User = Depends(_write)
) -> list[PermissionOut]:
    return await rbac_controller.update_permissions(user_id, payload, caller)
