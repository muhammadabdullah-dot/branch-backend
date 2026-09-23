"""FBR invoices (services/fbr_service.py): the Branch Manager's settings card, and the invoices waiting for FBR.

Included by routes/sales.py, so it needs no line in app/main.py; the loop that sends waiting invoices starts and stops
with the server through this router.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response

from app.middlewares.auth import get_current_user, require_permission
from app.models import User
from app.schemas.fbr import FbrQueueOut, FbrSettingsIn, FbrSettingsOut
from app.services import fbr_service


async def _start_loop() -> None:
    fbr_service.start()


async def _stop_loop() -> None:
    await fbr_service.stop()


router = APIRouter(prefix="/fbr", tags=["fbr invoices"], on_startup=[_start_loop], on_shutdown=[_stop_loop])

# Anyone who sees the receipt settings sees these (never the token); only a Branch Manager changes them, which the
# service checks, whatever else a person has been given.
_read = require_permission("branch-console.shop-settings", "R")


def _raise(exc: fbr_service.FbrError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/settings", response_model=FbrSettingsOut)
async def fbr_settings(user: User = Depends(_read)) -> FbrSettingsOut:
    return FbrSettingsOut(**await fbr_service.settings_out(user))


# Named for the activity trail, which reads the route's name: "Save FBR settings".
@router.put("/settings", response_model=FbrSettingsOut, name="save_FBR_settings")
async def save_fbr_settings(payload: FbrSettingsIn, response: Response, user: User = Depends(get_current_user)) -> FbrSettingsOut:
    """The activity trail keeps who saved them and what changed (the note below), never the token: the request's token
    fields are hidden by middlewares/activity.py."""
    try:
        out, note = await fbr_service.save_settings(user, payload)
    except fbr_service.FbrError as exc:
        raise _raise(exc) from exc
    response.headers["X-Activity-Note"] = quote(note)
    return FbrSettingsOut(**out)


@router.get("/queue", response_model=FbrQueueOut)
async def fbr_waiting_invoices(user: User = Depends(_read)) -> FbrQueueOut:
    """Bills and returns FBR hasn't taken yet: waiting (sent again on their own) and refused (FBR answered with an error)."""
    return FbrQueueOut(**await fbr_service.queue_out())


@router.post("/queue/send", response_model=FbrQueueOut, name="send_FBR_invoices_now")
async def send_fbr_invoices_now(user: User = Depends(get_current_user)) -> FbrQueueOut:
    try:
        return FbrQueueOut(**await fbr_service.send_again(user))
    except fbr_service.FbrError as exc:
        raise _raise(exc) from exc
