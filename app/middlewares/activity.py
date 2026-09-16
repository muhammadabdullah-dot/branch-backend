"""Records every action on this branch server against the person who did it.

An ASGI layer around the whole API: every request that changes something (POST, PUT, PATCH, DELETE) and
every export is written to `activity_logs` once it has been answered — who (from their token, or the email
on a sign-in), what (the route and what was sent, with passwords and secrets removed), and how it went. The
same row goes to head office as an `Activity` event. Nothing here can fail a request: a record that can't be
written is reported to the console and the response the person got is unaffected.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import logs
from app.core.security import decode_access_token

_WRITES = {"POST", "PUT", "PATCH", "DELETE"}
# Head office's own calls and the app's plumbing aren't a person's activity.
_SKIP = ("/health", "/sync/", "/registration/status", "/docs", "/openapi.json", "/redoc", "/alerts", "/client-errors")
_SECRET_KEYS = re.compile(r"pass(word)?|secret|token|pairing|hash|key$", re.IGNORECASE)
_MAX_BODY = 16_000
_MAX_TEXT = 300


def _redact(value, depth: int = 0):
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: ("[hidden]" if _SECRET_KEYS.search(str(k)) else _redact(v, depth + 1)) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        items = [_redact(v, depth + 1) for v in value[:50]]
        return items + [f"… {len(value) - 50} more"] if len(value) > 50 else items
    if isinstance(value, str) and len(value) > _MAX_TEXT:
        return value[:_MAX_TEXT] + "…"
    return value


def _label(name: str | None, method: str, path: str) -> str:
    if not name:
        return f"{method} {path}"[:120]
    words = name.replace("_", " ").strip()
    return (words[:1].upper() + words[1:])[:120]


class ActivityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        path: str = scope["path"]
        wanted = (method in _WRITES or path.startswith("/exports")) and not path.startswith(_SKIP)
        if not wanted:
            await self.app(scope, receive, send)
            return

        body = bytearray()
        status = {"code": 500}

        async def receive_wrapper() -> Message:
            message = await receive()
            if message["type"] == "http.request" and len(body) <= _MAX_BODY:
                body.extend(message.get("body", b""))
            return message

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            try:
                await _record(scope, bytes(body), status["code"])
            except Exception as exc:  # noqa: BLE001 — the record must never cost the person their response
                logs.log.warning("activity: couldn't record %s %s", method, path, exc_info=exc)


async def _record(scope: Scope, body: bytes, status_code: int) -> None:
    from app.core.device_context import get_device_id  # noqa: F401 — device id comes from the header here
    from app.models import ActivityLog, OutboxEvent, User

    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    method, path = scope["method"], scope["path"]
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    name = getattr(route, "name", None)

    detail = None
    content_type = headers.get("content-type", "")
    if body and "application/json" in content_type and len(body) <= _MAX_BODY:
        try:
            detail = _redact(json.loads(body))
        except ValueError:
            detail = None
    elif body and "multipart/form-data" in content_type:
        names = re.findall(rb'filename="([^"]{1,120})"', body[:4000])
        detail = {"files": [n.decode("utf-8", "replace") for n in names]}

    user = None
    auth = headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            user = await User.get_or_none(id=decode_access_token(auth.removeprefix("Bearer ").strip())["sub"])
        except Exception:  # noqa: BLE001 — an expired or bad token is still an attempt worth recording
            user = None
    action = _label(name, method, path)
    if path == "/auth/login":
        email = (detail or {}).get("email") if isinstance(detail, dict) else None
        if status_code < 400 and email:
            user = await User.get_or_none(email=str(email).strip().lower())
        action = "Sign in" if status_code < 400 else "Failed sign-in"
    elif path == "/auth/logout":
        action = "Sign out"

    now = datetime.now(timezone.utc)
    row = await ActivityLog.create(
        id=uuid.uuid4(), at=now, user=user, user_name=user.name if user else ((detail or {}).get("email") if isinstance(detail, dict) and path == "/auth/login" else None),
        user_title=user.title if user else None, action=action, method=method, route=route_path, path=path[:255],
        params=scope.get("path_params") or None, status_code=status_code, detail=detail,
        device_id=headers.get("x-device-id"), ip=(scope.get("client") or [None])[0],
    )
    await OutboxEvent.create(
        aggregate_type="Activity", aggregate_id=str(row.id),
        payload={"activity": {
            "id": str(row.id), "at": now.isoformat(), "userId": str(user.id) if user else None, "userName": row.user_name,
            "userTitle": row.user_title, "action": action, "method": method, "route": route_path, "path": row.path,
            "params": row.params, "status": status_code, "detail": detail, "deviceId": row.device_id, "ip": row.ip,
        }},
        origin_user_id=str(user.id) if user else None, origin_device_id=row.device_id,
    )
