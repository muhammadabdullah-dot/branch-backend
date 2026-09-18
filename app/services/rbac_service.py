"""Services are where ORM calls happen: no repository layer. RBAC enforcement and management both live here."""
from tortoise.transactions import in_transaction

from decimal import Decimal

from app.core.abilities import (
    AREA_COLUMNS, AREAS, AREAS_GROUP, ABILITIES, BRANCH_MANAGER, COUNTER_RESOURCES, COUNTER_ROLES, GROUPS,
    NOT_FOR_PHARMACIST, PHARMACIST, PHARMACIST_NOTE, PRESETS, never_for, normalise, preset_grants, preset_limit,
    without_never,
)
from app.core.resources import RESOURCES, excluded_resources_for_role, resources_for_role
from app.core.security import hash_password
from app.models import Role, RoleDefaultPermission, User, UserPermission
from app.schemas.auth import PermissionOut

_ACTION_FIELDS = {"R": "can_read", "W": "can_write", "X": "can_execute"}


def _actions_from_flags(perm: UserPermission) -> list[str]:
    return [action for action, field in _ACTION_FIELDS.items() if getattr(perm, field)]


async def effective_permissions(user: User) -> list[PermissionOut]:
    """What the person can do: their ticks, less anything their starting point can never hold (a Pharmacist's money
    ticks, should head office or an old account still carry them)."""
    out = []
    for r in await UserPermission.filter(user=user):
        actions = [a for a in _actions_from_flags(r) if not never_for(user.role_id, r.resource, a)]
        if actions:
            out.append(PermissionOut(resource=r.resource, actions=actions))
    return out


async def has_permission(user: User, resource: str, action: str) -> bool:
    if never_for(user.role_id, resource, action):
        return False
    field = _ACTION_FIELDS[action]
    perm = await UserPermission.get_or_none(user=user, resource=resource)
    return bool(perm and getattr(perm, field))


def list_resources() -> list[str]:
    return RESOURCES


async def list_users() -> list[User]:
    return await User.all().order_by("name")


def discount_limit_of(user: User) -> Decimal:
    """The most bill discount this person may give, and approve for others, as a percent of the bill's margin: what it
    sells for after the Items' own discounts, less what those Items cost the shop with tax (services/sale_rules.py).
    100% lets them sell at cost; nothing lets anyone sell below it."""
    return user.discount_limit if user.discount_limit is not None else preset_limit(user.role_id)


async def grants_of(user: User) -> dict[str, set[str]]:
    return {p.resource: set(p.actions) for p in await effective_permissions(user)}


def abilities_catalog() -> dict:
    """Every tick, grouped as the access screen shows it. The account areas also come as a grid (an area a row, See and
    Use the columns), which is how they read best."""
    return {
        "groups": [
            {"key": key, "label": label, "abilities": [
                {"key": f"{resource}:{action}", "resource": resource, "action": action, "label": text, "hint": hint}
                for group, resource, action, text, hint in ABILITIES if group == key
            ], **({"grid": {
                "columns": [{"action": action, "label": text} for action, text in AREA_COLUMNS],
                "rows": [{"resource": f"accounts.area.{area}", "label": text, "hint": hint} for area, text, hint in AREAS],
            }} if key == AREAS_GROUP else {})}
            for key, label in GROUPS
        ],
        "presets": [
            {"roleId": role_id, "label": p["label"], "discountLimit": str(p["discountLimit"]),
             "abilities": sorted(f"{r}:{a}" for r, a in p["abilities"])}
            for role_id, p in PRESETS.items()
        ],
        # The ticks never offered to a Pharmacist, and the line said beside them.
        "notForPharmacist": NOT_FOR_PHARMACIST,
        "pharmacistNote": PHARMACIST_NOTE,
    }


async def _refuse_beyond_caller(caller: User | None, grants: dict[str, set[str]], limit: Decimal | None) -> None:
    """Nobody gives what they don't have: no ability they lack, no discount limit above their own."""
    if caller is None:
        return
    mine = await grants_of(caller)
    missing = sorted(f"{r}:{a}" for r, actions in grants.items() for a in actions if a not in mine.get(r, set()))
    if missing:
        labels = {f"{r}:{a}": text for _, r, a, text, _ in ABILITIES}
        # Name the abilities; a "see it" that only comes along with one of them isn't worth listing.
        named = [labels[m] for m in missing if m in labels] or missing
        names = ", ".join(named[:4])
        raise RbacError(f"You can't give access you don't have yourself: {names}{' …' if len(named) > 4 else ''}.", status=403)
    if limit is not None and limit > discount_limit_of(caller):
        raise RbacError(f"You can't set a discount limit above your own ({discount_limit_of(caller).normalize():f}%).", status=403)


class RbacError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


async def list_roles() -> list[Role]:
    return await Role.all().order_by("name")


async def create_user(
    name: str, email: str, password: str, role_id: str, *, title: str | None = None,
    discount_limit: Decimal | None = None, caller: User | None = None,
) -> User:
    """Take on a new member of branch staff.

    The account is created with its role's standard access materialized into real permission rows,
    the same way the seeded accounts are. That matters: `RoleDefaultPermission` is a *template*, not
    a live link: nothing reads it after creation: so an account created without this step can
    sign in and see nothing at all.
    """
    email = (email or "").strip().lower()
    if not await Role.exists(id=role_id):
        raise RbacError(f"No such role: {role_id}", status=422)
    if await User.filter(email=email).exists():
        raise RbacError(f"Somebody already uses {email} on this branch.", status=409)

    role = await Role.get(id=role_id)
    # Once head office manages a starting point, its list is the standard access; otherwise the software's own.
    if role.managed_by_head_office:
        standard = {
            row.resource: {a for a, f in _ACTION_FIELDS.items() if getattr(row, f)}
            for row in await RoleDefaultPermission.filter(role=role)
        }
    else:
        standard = preset_grants(role_id)
    standard = {r: a for r, a in without_never(role_id, standard).items() if r not in excluded_resources_for_role(role_id)}
    limit = discount_limit if discount_limit is not None else preset_limit(role_id)
    await _refuse_beyond_caller(caller, standard, limit)
    async with in_transaction():
        user = await User.create(
            name=name.strip(), email=email, password_hash=hash_password(password),
            role_id=role_id, active=True, title=(title or "").strip() or PRESETS.get(role_id, {}).get("label"),
            discount_limit=limit,
        )
        await UserPermission.bulk_create([
            UserPermission(
                user=user, resource=resource,
                can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions, granted_by=caller,
            ) for resource, actions in sorted(standard.items())
        ])
    from app.services import staff_sync_service
    await staff_sync_service.emit(user.id, bump=False)
    return user


async def update_user(
    user_id: str, *, name=None, email=None, active=None, password=None, title=None, discount_limit=None,
    caller: User | None = None, fields_set: set[str] | None = None,
) -> User | None:
    user = await User.get_or_none(id=user_id)
    if not user:
        return None
    fields_set = fields_set or set()
    if "title" in fields_set:
        user.title = (title or "").strip() or None
    if "discountLimit" in fields_set and discount_limit is not None:
        new_limit = Decimal(str(discount_limit))
        # Keeping or lowering someone's limit is always fine; only raising it is held to the editor's own.
        if new_limit > discount_limit_of(user):
            await _refuse_beyond_caller(caller, {}, new_limit)
        user.discount_limit = new_limit
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
    from app.services import staff_sync_service
    await staff_sync_service.emit(user.id)
    await user.refresh_from_db()
    return user


async def switch_works_as(user_id: str, role_id: str, caller: User) -> tuple[User, str | None] | None:
    """Moves someone between Salesperson and Pharmacist, which decides which Items they sell at the till and whether they
    take payment. Their Sales counter ticks start again from the new role's (a Salesperson gets the till back, a
    Pharmacist gets the slips); every other tick, and their title, stay. Their discount limit stays too, except that a
    Pharmacist's becomes 0. Nobody is made a Branch Manager this way, and a Branch Manager isn't switched. Returns the
    person and what they were before (None when nothing changed)."""
    if role_id not in COUNTER_ROLES:
        raise RbacError("Someone can be switched to Salesperson or Pharmacist only.", status=422)
    user = await User.get_or_none(id=user_id)
    if not user:
        return None
    if user.role_id == BRANCH_MANAGER:
        raise RbacError(f"{user.name} is a Branch Manager, who sells every Item. A Branch Manager isn't switched.", status=403)
    if user.role_id not in COUNTER_ROLES:
        raise RbacError(f"{user.name} can't be switched from how they started.", status=403)
    if user.role_id == role_id:
        return user, None
    if not await Role.exists(id=role_id):
        raise RbacError("This branch doesn't have that role yet. Restart the branch server and try again.", status=409)
    rows = await UserPermission.filter(user=user)
    held = {r.resource: set(_actions_from_flags(r)) for r in rows if _actions_from_flags(r)}
    kept = {r: a for r, a in held.items() if r not in COUNTER_RESOURCES}
    counter = {r: a for r, a in preset_grants(role_id).items() if r in COUNTER_RESOURCES}
    wanted = without_never(role_id, normalise({**kept, **counter}))
    # Nobody gives what they don't have: only the counter ticks the new role brings are held to the caller's own.
    await _refuse_beyond_caller(caller, {r: a - held.get(r, set()) for r, a in counter.items() if a - held.get(r, set())}, None)
    before = user.role_id
    async with in_transaction():
        await UserPermission.filter(user=user).delete()
        for resource, actions in sorted(wanted.items()):
            await UserPermission.create(
                user=user, resource=resource, can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions,
                granted_by=caller,
            )
        user.role_id = role_id
        fields = ["role_id", "updated_at"]
        if role_id == PHARMACIST:
            user.discount_limit = preset_limit(PHARMACIST)
            fields.append("discount_limit")
        await user.save(update_fields=fields)
    from app.services import staff_sync_service
    await staff_sync_service.emit(user.id, caller)
    await user.refresh_from_db()
    return user, before


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
    excluded = excluded_resources_for_role(target.role_id)
    # What their starting point can never hold (a Pharmacist's money ticks) is left out, whatever was sent.
    wanted = without_never(target.role_id, normalise({
        g.resource: {a for a in g.actions if a in _ACTION_FIELDS}
        for g in grants if g.resource in RESOURCES and g.resource not in excluded
    }))
    # Only what changes has to be within the caller's own access: someone keeps abilities they already had.
    current = await grants_of(target)
    added = {r: actions - current.get(r, set()) for r, actions in wanted.items()}
    await _refuse_beyond_caller(granted_by, {r: a for r, a in added.items() if a}, None)
    await UserPermission.filter(user=target).delete()
    for resource, actions in sorted(wanted.items()):
        await UserPermission.create(
            user=target,
            resource=resource,
            can_read="R" in actions,
            can_write="W" in actions,
            can_execute="X" in actions,
            granted_by=granted_by,
        )
    from app.services import staff_sync_service
    await staff_sync_service.emit(target.id, granted_by)
    return await effective_permissions(target)
