"""Per-request auth/authorization guard. Implemented as FastAPI dependencies (not ASGI middleware)
since a required resource/action is route-specific: Depends is the idiomatic way to parameterize that.
"""
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.core.security import TOKEN_TTL, decode_access_token
from app.models import User
from app.services import login_session_service
from app.services.rbac_service import has_permission


@dataclass
class CurrentLogin:
    user: User
    session_id: UUID


async def get_current_login(authorization: str | None = Header(default=None)) -> CurrentLogin:
    """The signed-in person and the login their token belongs to. A login that has ended (signed out, ended by a
    manager, left unused) is refused here, so ending one takes effect on the very next request."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        hours = int(TOKEN_TTL.total_seconds() // 3600)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Your login ended {hours} hours after sign-in. Sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again.")
    try:
        session_id = await login_session_service.check(payload.get("sid"), payload.get("sub"))
    except login_session_service.LoginEnded as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.message)
    user = await User.get_or_none(id=payload["sub"], active=True)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account is switched off. Ask the Branch Manager.")
    return CurrentLogin(user=user, session_id=session_id)


async def get_current_user(login: CurrentLogin = Depends(get_current_login)) -> User:
    return login.user


def access_name(resource: str, action: str) -> str | None:
    """What the access is called on Staff & Roles, so a refusal says what to ask for rather than naming a resource.
    Seeing something comes with changing it, so a refused read can name the tick that brings it."""
    from app.core.abilities import ABILITIES

    exact = next((label for _, r, a, label, _ in ABILITIES if r == resource and a == action), None)
    if exact or action != "R":
        return exact
    return next((label for _, r, _a, label, _ in ABILITIES if r == resource), None)


def refusal(pairs: list[tuple[str, str]]) -> str:
    from app.core.abilities import MANAGER_ONLY_RESOURCES

    if all(resource in MANAGER_ONLY_RESOURCES for resource, _ in pairs):
        return "Only a Branch Manager account can do this."
    names = list(dict.fromkeys(name for name in (access_name(r, a) for r, a in pairs) if name))
    if not names:
        return "You don't have access to this. Ask your Branch Manager."
    if len(names) == 1:
        return f"This needs {names[0]}. Ask your Branch Manager for it."
    return f"This needs one of: {', '.join(names[:4])}. Ask your Branch Manager."


def require_permission(resource: str, action: str):
    async def checker(user: User = Depends(get_current_user)) -> User:
        if not await has_permission(user, resource, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, refusal([(resource, action)]))
        return user

    return checker


async def require_branch_manager(user: User = Depends(get_current_user)) -> User:
    """Staff and access: decided only by a Branch Manager account, whatever else anyone has been given."""
    if user.role_id != "branch-manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Branch Manager can manage staff and their access.")
    return user


def require_any_permission(*pairs: tuple[str, str]):
    """Passes if the caller holds ANY of the given (resource, action) pairs, for the rare screen
    genuinely reachable from two different modules (e.g. Parties, read from both Billing and
    Customer Registry)."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        for resource, action in pairs:
            if await has_permission(user, resource, action):
                return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, refusal(list(pairs)))

    return checker
