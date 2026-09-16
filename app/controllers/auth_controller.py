from fastapi import HTTPException, status

from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, UserOut
from app.services import auth_service
from app.services.rbac_service import effective_permissions


async def login(payload: LoginRequest) -> LoginResponse:
    user = await auth_service.authenticate(payload.email, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return await auth_service.build_login_response(user)


async def logout() -> dict:
    return {"detail": "logged out"}


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
