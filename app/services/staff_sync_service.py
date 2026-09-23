"""Branch staff accounts, kept in step with head office.

Up: every change to an account here — a new starter, a new password, switched off, access changed —
raises the account's revision and goes to head office as a `Staff` event carrying the whole account.

Down: head office sends `staff.upsert` (the whole account) and `staff.remove` (taken off this branch).
An incoming account is applied when its revision is at least this branch's; a lower one is ignored,
because this branch's own newer change is already on its way up. Applying never raises the revision or
sends an event back: an account arriving from head office is not a change made here. The one exception: a new
starter that an older head office gave every action on every Sales counter screen starts from this branch's own
starting point instead, and that correction goes back up so head office holds it too.
"""
import uuid

from tortoise.transactions import atomic

from app.core.abilities import (
    BRANCH_MANAGER, COUNTER_RESOURCES, COUNTER_ROLES, DROPPED_RESOURCES, LEGACY_JOBS, PHARMACIST, SALESPERSON, is_legacy,
    legacy_grants, never_for, preset_grants, translate_legacy, translate_legacy_resources,
)
from app.core.device_context import get_device_id
from app.core.resources import excluded_resources_for_role
from app.models import OutboxEvent, Role, RoleDefaultPermission, User, UserPermission

_ACTIONS = {"R": "can_read", "W": "can_write", "X": "can_execute"}


class StaffApplyError(Exception):
    def __init__(self, message: str):
        self.message = message


async def _state(user: User) -> dict:
    permissions = []
    for row in await UserPermission.filter(user=user).order_by("resource"):
        actions = [a for a, field in _ACTIONS.items() if getattr(row, field)]
        if actions:
            permissions.append({"resource": row.resource, "actions": actions})
    return {
        "id": str(user.id), "name": user.name, "email": user.email, "roleId": user.role_id, "active": user.active,
        "passwordHash": user.password_hash, "permissions": permissions, "rev": user.rev,
        "title": user.title, "discountLimit": str(user.discount_limit) if user.discount_limit is not None else None,
    }


async def emit(user_id, changed_by: User | None = None, *, bump: bool = True) -> None:
    """Record a change to an account for head office. `bump` raises the revision first."""
    user = await User.get_or_none(id=user_id)
    if not user:
        return
    if bump:
        user.rev += 1
        await user.save(update_fields=["rev", "updated_at"])
    state = await _state(user)
    state["changedBy"] = changed_by.name if changed_by else "branch"
    await OutboxEvent.create(
        aggregate_type="Staff", aggregate_id=str(user.id), payload={"user": state},
        origin_user_id=str(changed_by.id) if changed_by else None, origin_device_id=get_device_id(),
    )


async def backfill() -> int:
    """Tell head office about every account it hasn't heard of yet — the accounts that existed before
    staff sync did. Runs at startup; an account already reported is left alone."""
    reported = set(await OutboxEvent.filter(aggregate_type="Staff").values_list("aggregate_id", flat=True))
    count = 0
    for user in await User.all():
        if str(user.id) not in reported:
            await emit(user.id, bump=False)
            count += 1
    return count


@atomic()
async def apply_upsert(data: dict) -> str:
    try:
        user_id = uuid.UUID(str(data.get("id")))
    except (ValueError, TypeError) as exc:
        raise StaffApplyError("The account from head office has no valid id.") from exc
    email = str(data.get("email") or "").strip().lower()
    role_id = data.get("roleId")
    if not email or not data.get("passwordHash"):
        raise StaffApplyError("The account from head office has no email or password.")
    # Head office may still send one of the old fixed jobs. Branches start everyone as Salesperson, Pharmacist or
    # Branch Manager now, so the job becomes the person's title and its usual limit, on the right start.
    legacy = LEGACY_JOBS.get(role_id) if role_id not in (SALESPERSON, PHARMACIST, BRANCH_MANAGER) else None
    if legacy:
        role_id = legacy["role"]
        data = {**data, "title": data.get("title") or legacy["title"]}
        if data.get("discountLimit") is None:
            data["discountLimit"] = str(legacy["limit"])
    if not await Role.exists(id=role_id):
        raise StaffApplyError(f"This branch has no role called {role_id}.")
    clash = await User.filter(email=email).exclude(id=user_id).first()
    if clash:
        raise StaffApplyError(f"{email} is already used by another account on this branch ({clash.name}).")

    incoming_rev = int(data.get("rev") or 1)
    user = await User.get_or_none(id=user_id)
    if user and user.rev > incoming_rev:
        return "kept-branch"
    if user is None:
        user = await User.create(
            id=user_id, name=data.get("name") or email, email=email, password_hash=data["passwordHash"],
            role_id=role_id, active=bool(data.get("active", True)), rev=incoming_rev, title=data.get("title"),
            discount_limit=data.get("discountLimit"),
        )
        outcome = "created"
    else:
        user.name = data.get("name") or user.name
        user.email = email
        user.password_hash = data["passwordHash"]
        user.role_id = role_id
        user.active = bool(data.get("active", True))
        user.rev = incoming_rev
        if "title" in data:
            user.title = data.get("title")
        if data.get("discountLimit") is not None:
            user.discount_limit = data["discountLimit"]
        await user.save()
        outcome = "updated"

    excluded = excluded_resources_for_role(role_id)
    await UserPermission.filter(user=user).delete()
    grants = data.get("permissions") or []
    if legacy and not grants:
        grants = [{"resource": r, "actions": sorted(a)} for r, a in legacy_grants(data.get("roleId") or "").items()]
    corrected = False
    if outcome == "created" and await _is_blanket_start(role_id, grants):
        # A head office from before it knew the branch's starting ticks gave a new starter every action on every Sales
        # counter screen of their role (dry run B4: a Salesperson who could put people on counters). They start from
        # this branch's own starting point instead, and head office is told, so both ends hold the same.
        grants = [{"resource": r, "actions": sorted(a)} for r, a in preset_grants(role_id).items()]
        corrected = True
    if is_legacy(g.get("resource") for g in grants):
        # Written before the books were split into a tick per screen and area: read as what it now stands for.
        held: dict[str, set[str]] = {}
        for grant in grants:
            held.setdefault(grant.get("resource") or "", set()).update(grant.get("actions") or [])
        grants = [{"resource": r, "actions": sorted(a)} for r, a in translate_legacy(held).items()]
    for grant in grants:
        resource = grant.get("resource")
        # What this starting point can never hold (a Pharmacist's money ticks) isn't taken from head office either.
        actions = {a for a in (grant.get("actions") or []) if not never_for(role_id, resource or "", a)}
        # A tick that now stands for nothing (Sell Pharmacy Items) isn't put back by a head office copy that still has it.
        if not resource or resource in excluded or resource in DROPPED_RESOURCES or not actions:
            continue
        await UserPermission.create(
            user=user, resource=resource,
            can_read="R" in actions, can_write="W" in actions, can_execute="X" in actions,
        )
    if corrected:
        await emit(user.id)
    return outcome


async def _is_blanket_start(role_id: str, grants: list[dict]) -> bool:
    """Head office's old way of starting someone: R, W and X on every Sales counter screen of their role, with nothing
    else. Only a Salesperson or Pharmacist this branch keeps its own starting point for; a starting point head office
    manages (Role access) is head office's to give."""
    if role_id not in COUNTER_ROLES or not grants:
        return False
    role = await Role.get_or_none(id=role_id)
    if role is None or role.managed_by_head_office:
        return False
    return all(
        g.get("resource") in COUNTER_RESOURCES and set(g.get("actions") or []) == {"R", "W", "X"} for g in grants
    )


async def apply_remove(data: dict) -> str:
    user = await User.get_or_none(id=str(data.get("userId") or ""))
    if not user:
        return "not-here"
    if user.active:
        user.active = False
        await user.save(update_fields=["active", "updated_at"])
    return "switched-off"


@atomic()
async def apply_role_template(data: dict) -> str:
    role = await Role.get_or_none(id=data.get("roleId"))
    if not role and data.get("roleId") in LEGACY_JOBS:
        raise StaffApplyError(
            f"Branches have no {LEGACY_JOBS[data['roleId']]['title']} role any more. What each person can do is set on the person."
        )
    if not role:
        raise StaffApplyError(f"This branch has no role called {data.get('roleId')}.")
    excluded = excluded_resources_for_role(role.id)
    resources = set(data.get("resources") or [])
    if is_legacy(resources):
        resources = translate_legacy_resources(resources)
    await RoleDefaultPermission.filter(role=role).delete()
    for resource in sorted(resources - excluded - DROPPED_RESOURCES):
        await RoleDefaultPermission.create(role=role, resource=resource, can_read=True, can_write=True, can_execute=True)
    role.managed_by_head_office = True
    await role.save(update_fields=["managed_by_head_office"])
    return "template-set"
