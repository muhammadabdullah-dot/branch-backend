from fastapi import HTTPException, status

from app.models import User
from app.schemas.till import (
    CashMovementOut,
    CashMovementRequest,
    TillCloseOut,
    TillClosePreviewOut,
    TillCloseRequest,
    TillCurrentOut,
    TillOpenRequest,
)
from app.services import till_service


def _movement_out(m) -> CashMovementOut:
    return CashMovementOut(id=str(m.id), kind=m.kind, amount=m.amount, denominations=m.denominations, notes=m.notes, at=m.at)


async def current() -> TillCurrentOut:
    till = await till_service.get_current()
    if not till:
        return TillCurrentOut(isOpen=False)
    return TillCurrentOut(
        isOpen=True, sessionNumber=till.session_number, openedAt=till.opened_at,
        openingFloat=till.opening_float, openingDenominations=till.opening_denominations,
        openingNotes=till.opening_notes, movements=[_movement_out(m) for m in till.movements],
    )


async def open_till(user: User, payload: TillOpenRequest) -> TillCurrentOut:
    try:
        await till_service.open_till(user, payload.denominations, payload.notes)
    except till_service.TillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return await current()


async def cash_in(user: User, payload: CashMovementRequest) -> CashMovementOut:
    try:
        movement = await till_service.record_movement(user, "in", payload.denominations, payload.notes)
    except till_service.TillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _movement_out(movement)


async def cash_out(user: User, payload: CashMovementRequest) -> CashMovementOut:
    try:
        movement = await till_service.record_movement(user, "out", payload.denominations, payload.notes)
    except till_service.TillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return _movement_out(movement)


async def preview_close() -> TillClosePreviewOut:
    try:
        breakdown = await till_service.preview_close()
    except till_service.TillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return TillClosePreviewOut(**breakdown)


async def close(user: User, payload: TillCloseRequest) -> TillCloseOut:
    try:
        result = await till_service.close_till(user, payload.countedDenominations)
    except till_service.TillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return TillCloseOut(**result)
