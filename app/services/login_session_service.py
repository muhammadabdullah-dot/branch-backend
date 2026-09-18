"""Logins: one row per sign-in, checked on every request.

The rules, in the words the screens use:

- Everyone: a login ends when they sign out, when someone with "End someone's login" ends it, or 12 hours after
  sign-in. Signing in again on the same device replaces that device's older login.
- Counter staff (`is_counter_staff`): one login at a time. While they are signed in on one device, a sign-in on
  any other is refused, naming where and since when. If devices are assigned to them, they can sign in only on
  those. Their login also ends once the app has not been heard from for 2 hours (an open app checks in every
  couple of minutes), so a browser closed without signing out doesn't hold their login all day.
- A counter person with no device assigned yet can sign in on any computer here, so the rule can be rolled out
  without locking anyone out on the first day. The Branch Manager's screens say who is still in that state.

Head office never signs in here: sync runs on the branch's own key, not on anyone's login.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from tortoise.expressions import F, Q
from tortoise.transactions import in_transaction

from app.core.abilities import BRANCH_MANAGER, COUNTER_ROLES, PHARMACIST
from app.core.pk_time import day_start, pk_day, pk_time
from app.core.security import TOKEN_TTL
from app.models import Device, LoginSession, User, UserDevice, UserPermission

# Counter staff: how long a login lasts with the app closed (no request at all from it).
IDLE_LIMIT = timedelta(hours=2)
# How often the last-seen time is written: at most once a minute per login, so Billing never waits on it.
TOUCH_EVERY = timedelta(seconds=60)
LOGIN_HOURS = int(TOKEN_TTL.total_seconds() // 3600)
IDLE_HOURS = int(IDLE_LIMIT.total_seconds() // 3600)

RULES_TEXT = (
    f"One login at a time, and only on their own devices. A login ends when they sign out, when the Branch Manager "
    f"ends it, {LOGIN_HOURS} hours after sign-in, or once the app has been closed for {IDLE_HOURS} hours. "
    f"With no device assigned yet, they can sign in on any computer here."
)
EVERYONE_TEXT = (
    f"Anyone can sign in again on the same device at any time. Every login ends {LOGIN_HOURS} hours after sign-in."
)

# Two sign-ins arriving together must not both find the person signed out.
_start_lock = asyncio.Lock()


class LoginError(Exception):
    def __init__(self, message: str, status: int = 400, note: str | None = None):
        self.message = message
        self.status = status
        # What the activity log records about it, when that says more than the message.
        self.note = note or message


class LoginEnded(Exception):
    """The token's login is over: the request gets a 401 carrying this message."""

    def __init__(self, message: str):
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def clock(at: datetime, now: datetime | None = None) -> str:
    """3:40 pm, Pakistan time. Another day's time also names the day: 17 Sep, 3:40 pm."""
    local = pk_time(at)
    text = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    if local.date() != pk_day(now or _now()):
        text = f"{local.day} {local.strftime('%b')}, {text}"
    return text


def _either(names: list[str]) -> str:
    names = [n for n in dict.fromkeys(names) if n]
    if len(names) <= 1:
        return names[0] if names else "their own device"
    return f"{', '.join(names[:-1])} or {names[-1]}"


def _live(now: datetime) -> Q:
    """Logins still good at `now`: not ended, token not run out, and (for counter staff) heard from lately."""
    return Q(ended_at__isnull=True, expires_at__gt=now) & (Q(one_login=False) | Q(last_seen_at__gt=now - IDLE_LIMIT))


def _counter_work(role_id: str) -> str:
    """What makes someone counter staff by default: ringing up sales for a Salesperson, making slips for a Pharmacist."""
    return "store.slips" if role_id == PHARMACIST else "store.billing"


async def _automatic(user: User) -> bool:
    if user.role_id not in COUNTER_ROLES:
        return False
    return await UserPermission.exists(user_id=user.id, resource=_counter_work(user.role_id), can_write=True)


async def is_counter_staff(user: User) -> bool:
    """Under the counter staff rule: as switched on the person, or by default a Salesperson who rings up sales or a
    Pharmacist who makes pharmacy slips."""
    if user.counter_login is not None:
        return bool(user.counter_login)
    return await _automatic(user)


async def counter_staff_ids() -> set[str]:
    users = await User.filter(active=True).values("id", "role_id", "counter_login")
    workers = {
        (str(u), r) for u, r in await UserPermission.filter(resource__in=["store.billing", "store.slips"], can_write=True).values_list("user_id", "resource")
    }
    return {
        str(u["id"]) for u in users
        if (u["counter_login"] if u["counter_login"] is not None
            else (u["role_id"] in COUNTER_ROLES and (str(u["id"]), _counter_work(u["role_id"])) in workers))
    }


async def registered_names(ids) -> dict[str, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = await Device.filter(id__in=ids, registered_at__isnull=False).values("id", "name")
    return {r["id"]: r["name"] for r in rows if r["name"]}


def _where(session: LoginSession, names: dict[str, str]) -> str:
    return names.get(session.device_id or "") or session.device_name or "another computer"


async def close_lapsed(now: datetime | None = None) -> None:
    """Writes down the logins that ended on their own, so every list and check reads the same."""
    now = now or _now()
    await LoginSession.filter(ended_at__isnull=True, expires_at__lte=now).update(
        ended_at=F("expires_at"), end_kind="expired", end_reason=f"Ended {LOGIN_HOURS} hours after sign-in",
    )
    await LoginSession.filter(ended_at__isnull=True, one_login=True, last_seen_at__lte=now - IDLE_LIMIT).update(
        ended_at=F("last_seen_at"), end_kind="idle", end_reason=f"App closed for {IDLE_HOURS} hours",
    )
    off = await User.filter(active=False).values_list("id", flat=True)
    if off:
        await LoginSession.filter(ended_at__isnull=True, user_id__in=list(off)).update(
            ended_at=now, end_kind="switched-off", end_reason="Account switched off",
        )


# Signing in and out.


async def start(user: User, device_id: str | None, ip: str | None) -> LoginSession:
    """A new login, or a plain refusal (LoginError) under the counter staff rule."""
    device_id = (device_id or "").strip()[:80] or None
    async with _start_lock:
        now = _now()
        await close_lapsed(now)
        counter = await is_counter_staff(user)
        device = await Device.get_or_none(id=device_id) if device_id else None
        device_name = device.name if device and device.registered_at else None
        if counter:
            assigned = await UserDevice.filter(user_id=user.id, device__registered_at__isnull=False).prefetch_related("device")
            if assigned and device_id not in {a.device_id for a in assigned}:
                names = _either([a.device.name or "an unnamed device" for a in assigned])
                raise LoginError(
                    f"You can sign in only on {names}. This computer isn't one of them. Ask the Branch Manager if you need to use it.",
                    status=403, note=f"Refused: not one of their devices ({device_name or 'a device not registered here'})",
                )
            live = [
                s for s in await LoginSession.filter(_live(now), user_id=user.id).order_by("-started_at")
                if not device_id or s.device_id != device_id
            ]
            if live:
                other = live[0]
                where = _where(other, await registered_names([other.device_id]))
                raise LoginError(
                    f"Already signed in on {where} since {clock(other.started_at, now)}. Ask the Branch Manager to end that login.",
                    status=409, note=f"Refused: already signed in on {where} since {clock(other.started_at, now)}",
                )
        if device_id:
            await LoginSession.filter(ended_at__isnull=True, user_id=user.id, device_id=device_id).update(
                ended_at=now, end_kind="same-device", end_reason="Signed in again on this device",
            )
        return await LoginSession.create(
            id=uuid.uuid4(), user=user, device_id=device_id, device_name=device_name, ip=(ip or None),
            one_login=counter, started_at=now, last_seen_at=now, expires_at=now + TOKEN_TTL,
        )


async def _ended_message(row: LoginSession) -> str:
    kind = row.end_kind
    if kind == "ended":
        by = await User.get_or_none(id=row.ended_by_id) if row.ended_by_id else None
        who = f"the Branch Manager ({by.name})" if by and by.role_id == BRANCH_MANAGER else (by.name if by else "the Branch Manager")
        reason = (row.end_reason or "").strip().rstrip(".")
        when = clock(row.ended_at) if row.ended_at else ""
        return f"Your login was ended by {who}{f' at {when}' if when else ''}.{f' Reason: {reason}.' if reason else ''} Sign in again."
    if kind == "signed-out":
        return "You signed out. Sign in again."
    if kind == "same-device":
        return "You signed in again on this device, so this older login has ended. Sign in again."
    if kind == "expired":
        return f"Your login ended {LOGIN_HOURS} hours after sign-in. Sign in again."
    if kind == "idle":
        return f"Your login ended because the app was closed for over {IDLE_HOURS} hours. Sign in again."
    if kind == "switched-off":
        return "This account is switched off. Ask the Branch Manager."
    return "Your login has ended. Sign in again."


async def check(session_id, user_id) -> uuid.UUID:
    """Every signed-in request: is its login still on? One lookup by key, and a write at most once a minute."""
    try:
        sid = uuid.UUID(str(session_id))
    except (ValueError, TypeError) as exc:
        raise LoginEnded("Signing in has changed on this branch. Please sign in again.") from exc
    row = await LoginSession.get_or_none(id=sid)
    if row is None or str(row.user_id) != str(user_id):
        raise LoginEnded("Your login has ended. Sign in again.")
    if row.ended_at is not None:
        raise LoginEnded(await _ended_message(row))
    now = _now()
    last = _aware(row.last_seen_at)
    if row.one_login and now - last > IDLE_LIMIT:
        await LoginSession.filter(id=sid, ended_at__isnull=True).update(
            ended_at=last, end_kind="idle", end_reason=f"App closed for {IDLE_HOURS} hours",
        )
        raise LoginEnded(f"Your login ended because the app was closed for over {IDLE_HOURS} hours. Sign in again.")
    if now - last >= TOUCH_EVERY:
        await LoginSession.filter(id=sid).update(last_seen_at=now)
    return sid


async def sign_out(session_id, user: User) -> None:
    if not session_id:
        return
    await LoginSession.filter(id=session_id, user_id=user.id, ended_at__isnull=True).update(
        ended_at=_now(), ended_by_id=user.id, end_kind="signed-out", end_reason="Signed out",
    )


async def end(session_id: str, by: User, reason: str) -> LoginSession:
    """A manager signs someone out. Their very next request is refused with who ended it, when and why."""
    try:
        sid = uuid.UUID(str(session_id))
    except (ValueError, TypeError) as exc:
        raise LoginError("That login wasn't found.", 404) from exc
    now = _now()
    await close_lapsed(now)
    row = await LoginSession.get_or_none(id=sid).prefetch_related("user")
    if row is None:
        raise LoginError("That login wasn't found.", 404)
    if row.ended_at is not None:
        raise LoginError(f"{row.user.name}'s login has already ended.", 409)
    if row.user.role_id == BRANCH_MANAGER and by.role_id != BRANCH_MANAGER:
        raise LoginError("Only a Branch Manager can end a Branch Manager's login.", 403)
    reason = " ".join((reason or "").split())[:200]
    if not reason:
        raise LoginError("Say why you are ending this login.", 422)
    row.ended_at = now
    row.ended_by = by
    row.end_kind = "ended"
    row.end_reason = reason
    await row.save(update_fields=["ended_at", "ended_by_id", "end_kind", "end_reason"])
    return row


async def where_label(session: LoginSession) -> str:
    return _where(session, await registered_names([session.device_id]))


# What the Branch Manager sees.


def _session_out(s: LoginSession, names: dict[str, str], viewer_device: str | None) -> dict:
    user = s.user
    return {
        "id": str(s.id), "userId": str(s.user_id), "userName": user.name, "userTitle": user.title,
        "branchManager": user.role_id == BRANCH_MANAGER, "counterStaff": s.one_login,
        "deviceId": s.device_id, "deviceName": names.get(s.device_id or "") or s.device_name,
        "registered": (s.device_id or "") in names, "thisDevice": bool(viewer_device and s.device_id == viewer_device),
        "startedAt": _aware(s.started_at).isoformat(), "lastSeenAt": _aware(s.last_seen_at).isoformat(),
        "expiresAt": _aware(s.expires_at).isoformat(), "ip": s.ip,
        "endedAt": _aware(s.ended_at).isoformat() if s.ended_at else None,
        "endedBy": s.ended_by.name if s.ended_at and s.ended_by_id and s.ended_by else None,
        "endKind": s.end_kind, "endReason": s.end_reason,
    }


async def overview(viewer_device: str | None) -> dict:
    """Who is signed in now, and the logins that ended today."""
    now = _now()
    await close_lapsed(now)
    live = await LoginSession.filter(_live(now)).order_by("-started_at").prefetch_related("user")
    ended = (
        await LoginSession.filter(ended_at__gte=day_start(pk_day(now))).order_by("-ended_at").limit(200)
        .prefetch_related("user", "ended_by")
    )
    names = await registered_names({s.device_id for s in [*live, *ended]})
    counter = await counter_staff_ids()
    with_device = {str(u) for u in await UserDevice.filter(device__registered_at__isnull=False).values_list("user_id", flat=True)}
    waiting = await User.filter(id__in=list(counter - with_device)).order_by("name").values("id", "name", "title") if counter - with_device else []
    return {
        "live": [_session_out(s, names, viewer_device) for s in live],
        "ended": [_session_out(s, names, viewer_device) for s in ended],
        "noDevice": [{"id": str(u["id"]), "name": u["name"], "title": u["title"]} for u in waiting],
        "rules": RULES_TEXT, "everyone": EVERYONE_TEXT, "loginHours": LOGIN_HOURS, "idleHours": IDLE_HOURS,
    }


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())[:120]
    if not name:
        raise LoginError("Give the device a name, for example Counter 1 PC.", 422)
    return name


async def _refuse_clash(name: str, device_id: str) -> None:
    clash = await Device.filter(registered_at__isnull=False, name__iexact=name).exclude(id=device_id).first()
    if clash:
        raise LoginError(f"Another device is already called {name}. Pick a different name.", 409)


async def devices(viewer_device: str | None) -> dict:
    now = _now()
    await close_lapsed(now)
    rows = await Device.filter(registered_at__isnull=False).order_by("name").prefetch_related("registered_by")
    ids = [d.id for d in rows]
    people: dict[str, list[dict]] = {}
    for link in await UserDevice.filter(device_id__in=ids).prefetch_related("user"):
        people.setdefault(link.device_id, []).append({"id": str(link.user_id), "name": link.user.name})
    on_now: dict[str, list[dict]] = {}
    for s in await LoginSession.filter(_live(now), device_id__in=ids).order_by("started_at").prefetch_related("user"):
        on_now.setdefault(s.device_id, []).append({"name": s.user.name, "since": _aware(s.started_at).isoformat()})
    this = await Device.get_or_none(id=viewer_device) if viewer_device else None
    return {
        "devices": [
            {
                "id": d.id, "name": d.name, "registeredAt": _aware(d.registered_at).isoformat(),
                "registeredBy": d.registered_by.name if d.registered_by_id and d.registered_by else None,
                "lastSeenAt": _aware(d.last_seen_at).isoformat() if d.last_seen_at else None,
                "people": sorted(people.get(d.id, []), key=lambda p: p["name"].lower()),
                "signedIn": on_now.get(d.id, []), "thisDevice": d.id == viewer_device,
            }
            for d in rows
        ],
        "thisDevice": {
            "id": viewer_device, "registered": bool(this and this.registered_at),
            "name": this.name if this and this.registered_at else None,
        },
    }


async def register_this(device_id: str | None, name: str, by: User) -> Device:
    device_id = (device_id or "").strip()[:80]
    if not device_id:
        raise LoginError("This browser didn't say which device it is. Reload the page and try again.", 422)
    name = _clean_name(name)
    await _refuse_clash(name, device_id)
    device, _ = await Device.get_or_create(id=device_id)
    device.name = name
    if device.registered_at is None:
        device.registered_at = _now()
        device.registered_by = by
    await device.save()
    return device


async def rename(device_id: str, name: str) -> Device:
    device = await Device.get_or_none(id=device_id, registered_at__isnull=False)
    if not device:
        raise LoginError("That device isn't registered here.", 404)
    name = _clean_name(name)
    await _refuse_clash(name, device_id)
    device.name = name
    await device.save(update_fields=["name"])
    return device


async def unregister(device_id: str) -> str:
    device = await Device.get_or_none(id=device_id, registered_at__isnull=False)
    if not device:
        raise LoginError("That device isn't registered here.", 404)
    name = device.name or "The device"
    async with in_transaction():
        await UserDevice.filter(device_id=device.id).delete()
        device.registered_at = None
        device.registered_by = None
        device.name = None
        await device.save(update_fields=["registered_at", "registered_by_id", "name"])
    return name


async def person_rules(user_id: str, viewer_device: str | None = None) -> dict:
    try:
        user = await User.get_or_none(id=uuid.UUID(str(user_id)))
    except ValueError:
        user = None
    if not user:
        raise LoginError("That person wasn't found.", 404)
    now = _now()
    automatic = await _automatic(user)
    assigned = await UserDevice.filter(user_id=user.id, device__registered_at__isnull=False).prefetch_related("device")
    live = await LoginSession.filter(_live(now), user_id=user.id).order_by("-started_at").prefetch_related("user")
    names = await registered_names({s.device_id for s in live})
    return {
        "userId": str(user.id),
        "counterStaff": bool(user.counter_login) if user.counter_login is not None else automatic,
        "setting": user.counter_login, "automatic": automatic,
        "devices": sorted(({"id": a.device_id, "name": a.device.name} for a in assigned), key=lambda d: (d["name"] or "").lower()),
        "signedIn": [_session_out(s, names, viewer_device) for s in live],
        "rules": RULES_TEXT,
    }


async def set_person_rules(user_id: str, counter_staff: bool | None, device_ids: list[str], by: User) -> dict:
    try:
        user = await User.get_or_none(id=uuid.UUID(str(user_id)))
    except ValueError:
        user = None
    if not user:
        raise LoginError("That person wasn't found.", 404)
    if str(user.id) == str(by.id):
        raise LoginError("You can't change how you sign in yourself. Ask another manager.", 403)
    ids = [i for i in dict.fromkeys(d.strip() for d in device_ids) if i]
    registered = set(await Device.filter(id__in=ids, registered_at__isnull=False).values_list("id", flat=True)) if ids else set()
    if len(registered) != len(ids):
        raise LoginError("One of those devices isn't registered any more. Refresh and try again.", 409)
    async with in_transaction():
        user.counter_login = counter_staff
        await user.save(update_fields=["counter_login"])
        await UserDevice.filter(user_id=user.id).exclude(device_id__in=ids or [""]).delete()
        have = set(await UserDevice.filter(user_id=user.id).values_list("device_id", flat=True))
        for device_id in ids:
            if device_id not in have:
                await UserDevice.create(id=uuid.uuid4(), user=user, device_id=device_id, assigned_by=by)
    return await person_rules(str(user.id))


async def give_managers_the_sign_ins() -> int:
    """Once: every Branch Manager gets "Who is signed in" and "End someone's login", including where head office
    manages the starting point and the usual startup backfill leaves new ticks alone. After that it is a tick
    like any other."""
    from app.models import Counter
    from app.services import staff_sync_service

    if await Counter.exists(id="rollout:sign-ins"):
        return 0
    changed = 0
    for user in await User.filter(role_id=BRANCH_MANAGER):
        perm = await UserPermission.get_or_none(user=user, resource="branch-console.sign-ins")
        if perm is None:
            await UserPermission.create(user=user, resource="branch-console.sign-ins", can_read=True, can_write=False,
                                        can_execute=True, granted_by=None)
        elif not (perm.can_read and perm.can_execute):
            perm.can_read = True
            perm.can_execute = True
            await perm.save()
        else:
            continue
        changed += 1
        await staff_sync_service.emit(user.id, None)
    await Counter.create(id="rollout:sign-ins", value=1)
    return changed
