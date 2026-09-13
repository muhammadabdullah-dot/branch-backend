"""First-run verification, and the sync status behind it.

`/registration/status` and `/registration/verify` are the only two endpoints on this server that
take no token, and the reason is structural: on a branch server that has just been installed there
is nobody to be. Users are seeded, roles exist, but the sequence the business asked for is
*verify the branch, then set up its people* — so requiring a signed-in user to verify the branch
would invert it.

What stops that being a hole is that verification is one-shot and self-closing. `verify` refuses
the moment an identity exists, so the window is open exactly once per branch database, and the
only thing that can walk through it is somebody holding a key minted by head office for this
specific branch code. After that first success the door is closed for good — re-opening it takes
an authenticated reset here *and* a revoke at head office.

`/sync/*` is ordinary authenticated territory, gated on `branch-console.sync`.
"""
from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.controllers import registration_controller
from app.middlewares.auth import require_permission
from app.models import User
from app.schemas.registration import (
    RegistrationStatusOut,
    ResetIn,
    SyncRunOut,
    SyncStatusOut,
    VerifyIn,
)

router = APIRouter(prefix="/registration", tags=["registration"])
sync_router = APIRouter(prefix="/sync", tags=["sync"])

_read = require_permission("branch-console.sync", "R")
_write = require_permission("branch-console.sync", "W")


@router.get("/status", response_model=RegistrationStatusOut)
async def registration_status() -> RegistrationStatusOut:
    """Has this branch server been set up, and as what? The branch app asks this before it renders
    anything at all."""
    return await registration_controller.status()


@router.post("/verify", response_model=RegistrationStatusOut)
async def verify(
    payload: VerifyIn,
    request: Request,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
) -> RegistrationStatusOut:
    """Spend the key from main office. Succeeds once; every later attempt is refused.

    On success this writes exactly one row — the branch's identity. No existing data in this
    database is read, changed or removed by verifying.
    """
    # Recorded at head office as "who claimed this branch". Not a security control — it is so an
    # unexpected claim is something an admin can see rather than guess at.
    client_host = request.client.host if request.client else None
    claimed_from = x_device_id or client_host
    return await registration_controller.verify(payload, claimed_from)


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset(payload: ResetIn, user: User = Depends(_write)) -> Response:
    """Forget this branch's identity so it can be verified again — after head office has revoked
    the pairing. Removes the identity and the sync bookkeeping, and nothing else: no sale, no
    stock movement, no user, no history is touched."""
    await registration_controller.reset(payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@sync_router.get("/status", response_model=SyncStatusOut)
async def sync_status(user: User = Depends(_read)) -> SyncStatusOut:
    """Is our data reaching head office? How far behind are we, and why."""
    return await registration_controller.sync_status()


@sync_router.post("/run", response_model=SyncRunOut)
async def run_sync(user: User = Depends(_write)) -> SyncRunOut:
    """Push now instead of waiting for the next scheduled run. Same code path as the scheduler —
    there is no separate 'manual' behaviour that could work when the automatic one doesn't."""
    return await registration_controller.run_sync()
