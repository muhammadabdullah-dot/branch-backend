from urllib.parse import quote

from fastapi import HTTPException, Response, status

from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, UserOut
from app.services import auth_service, login_session_service
from app.services.login_session_service import LoginError
from app.services.rbac_service import effective_permissions

NOTE_HEADER = "X-Activity-Note"


def note_header(note: str) -> dict[str, str]:
    """What the activity log should record about this request (middlewares/activity.py reads it)."""
    return {NOTE_HEADER: quote(note[:300], safe=" ,.:;()'")}


def _refuse(exc: LoginError) -> HTTPException:
    return HTTPException(exc.status, exc.message, headers=note_header(exc.note))


async def login(payload: LoginRequest, device_id: str | None, ip: str | None, response: Response) -> LoginResponse:
    user = await auth_service.authenticate(payload.email, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    try:
        session = await login_session_service.start(user, device_id, ip)
    except LoginError as exc:
        raise _refuse(exc) from exc
    where = session.device_name or ("a computer not registered here" if session.device_id else "a browser that didn't say which device it is")
    response.headers.update(note_header(f"On {where}{' (counter staff rule)' if session.one_login else ''}"))
    return await auth_service.build_login_response(user, session)


async def logout(user: User, session_id) -> dict:
    await login_session_service.sign_out(session_id, user)
    return {"detail": "Signed out"}


async def session(user: User, session_id, device_id: str | None) -> dict:
    """The app checks in with this every couple of minutes: it keeps a counter login alive while the app is open,
    and a login ended elsewhere is noticed within minutes even when nobody touches the screen."""
    rules = await login_session_service.person_rules(str(user.id), device_id)
    mine = next((s for s in rules["signedIn"] if s["id"] == str(session_id)), None)
    return {
        "sessionId": str(session_id), "counterStaff": rules["counterStaff"], "rules": rules["rules"] if rules["counterStaff"] else None,
        "deviceName": mine["deviceName"] if mine else None, "startedAt": mine["startedAt"] if mine else None,
        "expiresAt": mine["expiresAt"] if mine else None,
    }


async def this_device(device_id: str | None) -> dict:
    names = await login_session_service.registered_names([device_id] if device_id else [])
    return {"name": names.get(device_id or "")}


async def sessions(device_id: str | None) -> dict:
    return await login_session_service.overview(device_id)


async def end_login(session_id: str, by: User, reason: str, response: Response) -> dict:
    try:
        row = await login_session_service.end(session_id, by, reason)
    except LoginError as exc:
        raise _refuse(exc) from exc
    where = await login_session_service.where_label(row)
    since = login_session_service.clock(row.started_at)
    response.headers.update(note_header(f"Ended {row.user.name}'s login on {where} (signed in since {since})"))
    return {"detail": f"{row.user.name} is signed out of {where}.", "userName": row.user.name}


async def devices(device_id: str | None) -> dict:
    return await login_session_service.devices(device_id)


async def register_this_device(device_id: str | None, name: str, by: User, response: Response) -> dict:
    try:
        device = await login_session_service.register_this(device_id, name, by)
    except LoginError as exc:
        raise _refuse(exc) from exc
    response.headers.update(note_header(f"This device is now {device.name}"))
    return await login_session_service.devices(device_id)


async def rename_device(target_id: str, name: str, device_id: str | None) -> dict:
    try:
        await login_session_service.rename(target_id, name)
    except LoginError as exc:
        raise _refuse(exc) from exc
    return await login_session_service.devices(device_id)


async def remove_device(target_id: str, device_id: str | None, response: Response) -> dict:
    try:
        name = await login_session_service.unregister(target_id)
    except LoginError as exc:
        raise _refuse(exc) from exc
    response.headers.update(note_header(f"{name} is no longer a registered device"))
    return await login_session_service.devices(device_id)


async def sign_in_rules(user_id: str, device_id: str | None) -> dict:
    try:
        return await login_session_service.person_rules(user_id, device_id)
    except LoginError as exc:
        raise _refuse(exc) from exc


async def set_sign_in_rules(user_id: str, payload, by: User, response: Response) -> dict:
    try:
        rules = await login_session_service.set_person_rules(user_id, payload.counterStaff, payload.deviceIds, by)
    except LoginError as exc:
        raise _refuse(exc) from exc
    target = await User.get(id=user_id)
    devices = ", ".join(d["name"] or "unnamed" for d in rules["devices"]) or "any computer"
    response.headers.update(note_header(
        f"{target.name}: counter staff rule {'on' if rules['counterStaff'] else 'off'}"
        f"{' (automatic)' if payload.counterStaff is None else ''}; devices: {devices}"
    ))
    return rules


async def me(user: User) -> MeResponse:
    await user.fetch_related("role")
    permissions = await effective_permissions(user)
    return MeResponse(user=await auth_service.user_out(user, permissions), permissions=permissions)


async def change_password(user: User, payload) -> dict:
    try:
        await auth_service.change_own_password(user, payload.currentPassword, payload.newPassword, payload.confirmPassword)
    except auth_service.AccountError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    from app.services import staff_sync_service

    # Head office keeps every branch account; the new password works there after the next check-in.
    await staff_sync_service.emit(user.id, user)
    return {"detail": "Password changed"}


async def change_name(user: User, payload) -> MeResponse:
    try:
        await auth_service.change_own_name(user, payload.name)
    except auth_service.AccountError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    from app.services import staff_sync_service

    await staff_sync_service.emit(user.id, user)
    return await me(user)
