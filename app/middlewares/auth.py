"""Per-request auth/authorization guard. Implemented as FastAPI dependencies (not ASGI middleware)
since a required resource/action is route-specific — Depends is the idiomatic way to parameterize that.
"""
import jwt
from fastapi import Depends, Header, HTTPException, status

from app.core.security import decode_access_token
from app.models import User
from app.services.rbac_service import has_permission


async def get_current_user(authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await User.get_or_none(id=payload["sub"], active=True)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_permission(resource: str, action: str):
    async def checker(user: User = Depends(get_current_user)) -> User:
        if not await has_permission(user, resource, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing '{action}' on '{resource}'")
        return user

    return checker


async def require_branch_manager(user: User = Depends(get_current_user)) -> User:
    """Staff and access: decided only by a Branch Manager account, whatever else anyone has been given."""
    if user.role_id != "branch-manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Branch Manager can manage staff and their access.")
    return user


def require_any_permission(*pairs: tuple[str, str]):
    """Passes if the caller holds ANY of the given (resource, action) pairs — for the rare screen
    genuinely reachable from two different modules (e.g. Parties, read from both Billing and
    Customer Registry)."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        for resource, action in pairs:
            if await has_permission(user, resource, action):
                return user
        wanted = ", ".join(f"{a} on {r}" for r, a in pairs)
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing any of: {wanted}")

    return checker
