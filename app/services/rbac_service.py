"""Services are where ORM calls happen — no repository layer. RBAC enforcement and management both live here."""
from tortoise.transactions import in_transaction

from app.core.resources import RESOURCES, resources_for_role
from app.core.security import hash_password
from app.models import Role, User, UserPermission
from app.schemas.auth import PermissionOut

_ACTION_FIELDS = {"R": "can_read", "W": "can_write", "X": "can_execute"}


def _actions_from_flags(perm: UserPermission) -> list[str]:
    return [action for action, field in _ACTION_FIELDS.items() if getattr(perm, field)]


async def effective_permissions(user: User) -> list[PermissionOut]:
    rows = await UserPermission.filter(user=user)
    return [PermissionOut(resource=r.resource, actions=_actions_from_flags(r)) for r in rows if _actions_from_flags(r)]


async def has_permission(user: User, resource: str, action: str) -> bool:
    field = _ACTION_FIELDS[action]
    perm = await UserPermission.get_or_none(user=user, resource=resource)
    return bool(perm and getattr(perm, field))


def list_resources() -> list[str]:
    return RESOURCES


async def list_users() -> list[User]:
    return await User.all()


class RbacError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


async def list_roles() -> list[Role]:
    return await Role.all().order_by("name")


async def create_user(name: str, email: str, password: str, role_id: str) -> User:
    """Take on a new member of branch staff.

    The account is created with its role's standard access materialized into real permission rows,
    the same way the seeded accounts are. That matters: `RoleDefaultPermission` is a *template*, not
    a live link — nothing reads it after creation — so an account created without this step can
    sign in and see nothing at all.
    """
    email = (email or "").strip().lower()
    if not await Role.exists(id=role_id):
        raise RbacError(f"No such role: {role_id}", status=422)
    if await User.filter(email=email).exists():
        raise RbacError(f"Somebody already uses {email} on this branch.", status=409)

    async with in_transaction():
        user = await User.create(
            name=name.strip(), email=email, password_hash=hash_password(password),
            role_id=role_id, active=True,
        )
        await UserPermission.bulk_create([
            UserPermission(
                user=user, resource=resource,
                can_read=True, can_write=True, can_execute=True,
            ) for resource in sorted(resources_for_role(role_id))
        ])
    return user


async def update_user(user_id: str, *, name=None, email=None, active=None, password=None) -> User | None:
    user = await User.get_or_none(id=user_id)
    if not user:
        return None
    if email is not None:
        email = email.strip().lower()
        if await User.filter(email=email).exclude(id=user.id).exists():
            raise RbacError(f"Somebody already uses {email} on this branch.", status=409)
        user.email = email
    if name is not None:
        user.name = name.strip()
    if active is not None:
        user.active = active
    if password is not None:
        user.password_hash = hash_password(password)
    await user.save()
    return user


async def get_user_permissions(user_id: str) -> list[PermissionOut] | None:
    user = await User.get_or_none(id=user_id)
    if not user:
        return None
    return await effective_permissions(user)


async def replace_user_permissions(
    target_user_id: str, grants: list[PermissionOut], granted_by: User
) -> list[PermissionOut] | None:
    target = await User.get_or_none(id=target_user_id)
    if not target:
        return None
    await UserPermission.filter(user=target).delete()
    for grant in grants:
        await UserPermission.create(
            user=target,
            resource=grant.resource,
            can_read="R" in grant.actions,
            can_write="W" in grant.actions,
            can_execute="X" in grant.actions,
            granted_by=granted_by,
        )
    return await effective_permissions(target)
