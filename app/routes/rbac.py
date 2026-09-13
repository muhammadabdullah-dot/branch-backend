from fastapi import APIRouter, Depends, status

from app.controllers import rbac_controller
from app.core.resources import RBAC_MANAGEMENT_RESOURCE
from app.middlewares.auth import get_current_user, require_permission
from app.models import User
from app.schemas.auth import PermissionOut
from app.schemas.rbac import (
    RoleOut,
    UpdatePermissionsRequest,
    UserCreateRequest,
    UserNameOut,
    UserSummaryOut,
    UserUpdateRequest,
)

router = APIRouter(prefix="/rbac", tags=["rbac"])
users_router = APIRouter(prefix="/users", tags=["rbac"])

_read = require_permission(RBAC_MANAGEMENT_RESOURCE, "R")
_write = require_permission(RBAC_MANAGEMENT_RESOURCE, "W")


@router.get("/resources", response_model=list[str])
async def resources(user: User = Depends(_read)) -> list[str]:
    return rbac_controller.resources()


@router.get("/roles", response_model=list[RoleOut])
async def roles(user: User = Depends(_read)) -> list[RoleOut]:
    """The roles a new starter can be given. Fixed set — adding one is a code change, not data."""
    return await rbac_controller.list_roles()


@users_router.post("", response_model=UserSummaryOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreateRequest, caller: User = Depends(_write)) -> UserSummaryOut:
    """Take on a new member of branch staff.

    Behind the same `branch-console.staff-access` resource that already governs who may change
    access — the Branch Manager holds it. There is deliberately no separate branch-admin role:
    the person who answers for the branch is the person who hires into it, and a second
    authority would just be someone else to find when a salesperson starts on a Monday.
    """
    return await rbac_controller.create_user(payload)


@users_router.patch("/{user_id}", response_model=UserSummaryOut)
async def update_user(
    user_id: str, payload: UserUpdateRequest, caller: User = Depends(_write)
) -> UserSummaryOut:
    """Rename someone, correct their email, reset their password, or deactivate them.

    Deactivate rather than delete: a user is on the other end of every sale, adjustment and
    till close they ever made, and removing the row would orphan that history."""
    return await rbac_controller.update_user(user_id, payload, caller)


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
