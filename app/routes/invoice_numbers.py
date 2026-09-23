"""Invoice numbers (services/invoice_numbers_service.py): the card on Receipt and Vouchers where a Branch Manager sets the
bill prefix, the year, the digits, the next bill number and the return prefix.

Included by routes/sales.py, so it needs no line in app/main.py. Anyone who sees the receipt settings sees it; only a
Branch Manager changes it, which the service checks, whatever else a person has been given (as the FBR card does). A
save is kept in the activity trail by middlewares/activity.py, with a note of what changed.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.middlewares.auth import get_current_user, require_permission
from app.models import User
from app.schemas.invoice_numbers import InvoiceNumbersIn, InvoiceNumbersOut, InvoiceNumbersPreviewOut
from app.services import invoice_numbers_service

router = APIRouter(prefix="/masters/settings/invoice-numbers", tags=["invoice numbers"])

_read = require_permission("branch-console.shop-settings", "R")


@router.get("", response_model=InvoiceNumbersOut)
async def invoice_numbers(user: User = Depends(_read)) -> InvoiceNumbersOut:
    return InvoiceNumbersOut(**await invoice_numbers_service.settings_out(user))


@router.get("/preview", response_model=InvoiceNumbersPreviewOut)
async def invoice_numbers_preview(
    billPrefix: str = Query("", max_length=40),
    yearInNumber: bool = True,
    digits: int | None = None,
    returnPrefix: str = Query("", max_length=40),
    nextNumber: int | None = None,
    user: User = Depends(_read),
) -> InvoiceNumbersPreviewOut:
    """The card's live preview: a GET, so trying things out doesn't fill the activity trail."""
    return InvoiceNumbersPreviewOut(**await invoice_numbers_service.assess(billPrefix, yearInNumber, digits, returnPrefix, nextNumber))


# Named for the activity trail, which reads the route's name: "Save invoice numbers".
@router.put("", response_model=InvoiceNumbersOut, name="save_invoice_numbers")
async def save_invoice_numbers(payload: InvoiceNumbersIn, response: Response, user: User = Depends(get_current_user)) -> InvoiceNumbersOut:
    try:
        out, note = await invoice_numbers_service.save(user, payload)
    except invoice_numbers_service.InvoiceNumbersError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    response.headers["X-Activity-Note"] = quote(note)
    return InvoiceNumbersOut(**out)
