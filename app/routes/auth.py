from fastapi import APIRouter, Depends, Header, Request, Response

from app.controllers import auth_controller
from app.middlewares.auth import CurrentLogin, get_current_login, get_current_user, require_branch_manager, require_permission
from app.models import User
from app.schemas.auth import (
    ChangePasswordRequest,
    DeviceNameRequest,
    EndLoginRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MyNameRequest,
    SignInRulesRequest,
)

router = APIRouter(tags=["auth"])

# Who is signed in, and ending someone's login: a tick of its own (core/abilities.py), Branch Managers start with it.
SIGN_INS = "branch-console.sign-ins"


def _device(x_device_id: str | None) -> str | None:
    return (x_device_id or "").strip()[:80] or None


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, x_device_id: str | None = Header(default=None),
) -> LoginResponse:
    """Signs in and starts a login on this device. Counter staff get one login at a time, only on their own devices."""
    ip = request.client.host if request.client else None
    return await auth_controller.login(payload, _device(x_device_id), ip, response)


@router.post("/auth/logout")
async def logout(current: CurrentLogin = Depends(get_current_login)) -> dict:
    """Ends this login, so it can't be used again and counter staff can sign in somewhere else straight away."""
    return await auth_controller.logout(current.user, current.session_id)


@router.get("/auth/session")
async def session(current: CurrentLogin = Depends(get_current_login), x_device_id: str | None = Header(default=None)) -> dict:
    """The app checks in here every couple of minutes while it is open."""
    return await auth_controller.session(current.user, current.session_id, _device(x_device_id))


@router.get("/auth/device")
async def this_device(x_device_id: str | None = Header(default=None)) -> dict:
    """What this browser is called here, shown on the sign-in page. Only ever this browser's own name."""
    return await auth_controller.this_device(_device(x_device_id))


@router.get("/auth/sessions")
async def sessions(
    user: User = Depends(require_permission(SIGN_INS, "R")), x_device_id: str | None = Header(default=None),
) -> dict:
    """Who is signed in now, on which device and since when, and the logins that ended today."""
    return await auth_controller.sessions(_device(x_device_id))


@router.post("/auth/sessions/{session_id}/end")
async def end_login(
    session_id: str, payload: EndLoginRequest, response: Response, user: User = Depends(require_permission(SIGN_INS, "X")),
) -> dict:
    """Signs someone out straight away. Their next request is refused, saying who ended it and why."""
    return await auth_controller.end_login(session_id, user, payload.reason, response)


@router.get("/auth/devices")
async def devices(user: User = Depends(require_branch_manager), x_device_id: str | None = Header(default=None)) -> dict:
    """The devices registered here by name, who may sign in on each, and whether this browser is one of them."""
    return await auth_controller.devices(_device(x_device_id))


@router.put("/auth/devices/this")
async def register_this_device(
    payload: DeviceNameRequest, response: Response, user: User = Depends(require_branch_manager),
    x_device_id: str | None = Header(default=None),
) -> dict:
    """A Branch Manager, signed in on the device itself, gives this browser a name so it can be assigned to people."""
    return await auth_controller.register_this_device(_device(x_device_id), payload.name, user, response)


@router.patch("/auth/devices/{device_id}")
async def rename_device(
    device_id: str, payload: DeviceNameRequest, user: User = Depends(require_branch_manager),
    x_device_id: str | None = Header(default=None),
) -> dict:
    return await auth_controller.rename_device(device_id, payload.name, _device(x_device_id))


@router.delete("/auth/devices/{device_id}")
async def remove_device(
    device_id: str, response: Response, user: User = Depends(require_branch_manager),
    x_device_id: str | None = Header(default=None),
) -> dict:
    """No longer a registered device: nobody is assigned to it any more."""
    return await auth_controller.remove_device(device_id, _device(x_device_id), response)


@router.get("/auth/people/{user_id}/sign-in")
async def sign_in_rules(
    user_id: str, user: User = Depends(require_branch_manager), x_device_id: str | None = Header(default=None),
) -> dict:
    """How one person signs in: the counter staff rule, their devices, and where they are signed in now."""
    return await auth_controller.sign_in_rules(user_id, _device(x_device_id))


@router.put("/auth/people/{user_id}/sign-in")
async def set_sign_in_rules(
    user_id: str, payload: SignInRulesRequest, response: Response, user: User = Depends(require_branch_manager),
) -> dict:
    return await auth_controller.set_sign_in_rules(user_id, payload, user, response)


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)) -> MeResponse:
    return await auth_controller.me(user)


@router.post("/auth/change-password")
async def change_password(payload: ChangePasswordRequest, user: User = Depends(get_current_user)) -> dict:
    """Anyone changes their own password: with the current one, and the new one typed twice."""
    return await auth_controller.change_password(user, payload)


@router.patch("/me", response_model=MeResponse)
async def change_my_name(payload: MyNameRequest, user: User = Depends(get_current_user)) -> MeResponse:
    """A Branch Manager corrects their own name. Everyone else asks the Branch Manager."""
    return await auth_controller.change_name(user, payload)
