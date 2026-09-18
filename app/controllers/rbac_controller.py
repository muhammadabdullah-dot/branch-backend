from fastapi import HTTPException, status

from app.models import User
from app.core.abilities import PRESETS
from app.schemas.rbac import (
    RoleOut,
    UpdatePermissionsRequest,
    UserCreateRequest,
    UserNameOut,
    UserSummaryOut,
    UserUpdateRequest,
    WorksAsRequest,
)
from app.services import rbac_service


def resources() -> list[str]:
    return rbac_service.list_resources()


def _out(u: User) -> UserSummaryOut:
    return UserSummaryOut(
        id=str(u.id), name=u.name, email=u.email, roleId=u.role_id, active=u.active, title=u.title,
        discountLimit=format(rbac_service.discount_limit_of(u).normalize(), "f"),
    )


def abilities():
    return rbac_service.abilities_catalog()


async def list_roles() -> list[RoleOut]:
    return [RoleOut(id=r.id, name=r.name) for r in await rbac_service.list_roles()]


async def create_user(payload: UserCreateRequest, caller: User | None = None) -> UserSummaryOut:
    try:
        user = await rbac_service.create_user(
            payload.name, str(payload.email), payload.password, payload.roleId,
            title=payload.title, discount_limit=payload.discountLimit, caller=caller,
        )
    except rbac_service.RbacError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return _out(user)


async def update_user(user_id: str, payload: UserUpdateRequest, caller: User) -> UserSummaryOut:
    if user_id == str(caller.id) and payload.active is False:
        # Locking yourself out of the only account that can unlock people is a support call
        # somebody has to drive to the branch to fix.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't deactivate your own account.")
    data = payload.model_dump(exclude_unset=True)
    try:
        if user_id == str(caller.id) and "discountLimit" in data:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change your own discount limit.")
        if user_id == str(caller.id) and data.get("password"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Change your own password from My account, because it needs your current password.")
        user = await rbac_service.update_user(
            user_id, name=data.get("name"), email=(str(data["email"]) if data.get("email") else None),
            active=data.get("active"), password=data.get("password"), title=data.get("title"),
            discount_limit=data.get("discountLimit"), caller=caller, fields_set=set(data),
        )
    except rbac_service.RbacError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _out(user)


async def switch_works_as(user_id: str, payload: WorksAsRequest, caller: User) -> tuple[UserSummaryOut, str | None]:
    """The person as they now are, and what the activity trail notes about it ("Salesperson to Pharmacist"), None when
    they already worked as that."""
    if user_id == str(caller.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change your own access. Ask another manager.")
    try:
        result = await rbac_service.switch_works_as(user_id, payload.roleId, caller)
    except rbac_service.RbacError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user, before = result

    def label(role_id: str) -> str:
        return PRESETS.get(role_id, {}).get("label", role_id)

    note = f"{user.name}: {label(before)} to {label(user.role_id)}" if before else None
    return _out(user), note


async def list_users() -> list[UserSummaryOut]:
    return [_out(u) for u in await rbac_service.list_users()]


async def list_user_names() -> list[UserNameOut]:
    return [UserNameOut(id=str(u.id), name=u.name) for u in await rbac_service.list_users()]


async def get_permissions(user_id: str):
    permissions = await rbac_service.get_user_permissions(user_id)
    if permissions is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return permissions


async def update_permissions(user_id: str, payload: UpdatePermissionsRequest, caller: User):
    if user_id == str(caller.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change your own access. Ask another manager.")
    try:
        permissions = await rbac_service.replace_user_permissions(user_id, payload.permissions, caller)
    except rbac_service.RbacError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    if permissions is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return permissions
