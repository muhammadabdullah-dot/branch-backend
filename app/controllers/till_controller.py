from datetime import datetime

from fastapi import HTTPException

from app.models import User
from app.schemas.till import (
    CashMovementOut,
    CashMovementRequest,
    OpenTillOut,
    TillCloseOut,
    TillClosePreviewOut,
    TillCloseRequest,
    TillCurrentOut,
    TillOpenOptionsOut,
    TillOpenRequest,
    TillSessionListOut,
    TillSessionSummaryOut,
)
from app.schemas.till_report import TillReportOut, TillReportSessionOut
from app.services import till_service


def _fail(exc: till_service.TillError) -> HTTPException:
    """Somebody else's till is a 403, with the same plain words the screen shows; anything else a 400 (or 404)."""
    return HTTPException(exc.status, exc.message)


def _movement_out(m) -> CashMovementOut:
    from app.models import Account

    account = m.__dict__.get("_account")
    account = account if isinstance(account, Account) else None
    return CashMovementOut(
        id=str(m.id), kind=m.kind, amount=m.amount, denominations=m.denominations, notes=m.notes, at=m.at,
        accountId=str(m.account_id) if m.account_id else None, accountName=account.name if account else None, payee=m.payee,
    )


async def current(user: User, session_id: str | None = None) -> TillCurrentOut:
    """The caller's own open till, or (someone who oversees the tills) the open one they name."""
    try:
        till = await till_service.get_current(user, session_id)
    except till_service.TillError as exc:
        raise _fail(exc)
    oversees = await till_service.oversees_tills(user)
    if not till:
        return TillCurrentOut(isOpen=False, overseesTills=oversees)
    counter = till.__dict__.get("_counter")
    opened_by = till.__dict__.get("_opened_by")
    return TillCurrentOut(
        isOpen=True, sessionId=str(till.id), sessionNumber=till.session_number, openedAt=till.opened_at,
        counterId=str(till.counter_id) if till.counter_id else None,
        counterName=counter.name if counter else None, openedBy=opened_by.name if opened_by else None,
        openedById=str(till.opened_by_id), overseesTills=oversees,
        openingFloat=till.opening_float, openingDenominations=till.opening_denominations,
        openingNotes=till.opening_notes, movements=[_movement_out(m) for m in till.movements],
    )


async def open_tills(user: User) -> list[OpenTillOut]:
    return [
        OpenTillOut(
            id=str(t.id), sessionNumber=t.session_number, counterName=t.counter.name if t.counter_id and t.counter else None,
            openedBy=t.opened_by.name if t.opened_by else None, openedAt=t.opened_at, mine=str(t.opened_by_id) == str(user.id),
        )
        for t in await till_service.open_sessions(user)
    ]


async def open_options(user: User) -> TillOpenOptionsOut:
    return TillOpenOptionsOut(**await till_service.open_options(user))


async def open_till(user: User, payload: TillOpenRequest) -> TillCurrentOut:
    try:
        await till_service.open_till(user, payload.denominations, payload.notes, payload.counterId)
    except till_service.TillError as exc:
        raise _fail(exc)
    return await current(user)


async def cash_in(user: User, payload: CashMovementRequest) -> CashMovementOut:
    try:
        movement = await till_service.record_movement(user, "in", payload.denominations, payload.notes, payload.accountId, payload.payee, payload.sessionId)
        await movement.fetch_related("account")
    except till_service.TillError as exc:
        raise _fail(exc)
    return _movement_out(movement)


async def cash_out(user: User, payload: CashMovementRequest) -> CashMovementOut:
    try:
        movement = await till_service.record_movement(user, "out", payload.denominations, payload.notes, payload.accountId, payload.payee, payload.sessionId)
        await movement.fetch_related("account")
    except till_service.TillError as exc:
        raise _fail(exc)
    return _movement_out(movement)


async def preview_close(user: User, session_id: str | None = None) -> TillClosePreviewOut:
    try:
        breakdown = await till_service.preview_close(user, session_id)
    except till_service.TillError as exc:
        raise _fail(exc)
    return TillClosePreviewOut(**breakdown)


async def close(user: User, payload: TillCloseRequest) -> TillCloseOut:
    try:
        result = await till_service.close_till(user, payload.countedDenominations, payload.sessionId, payload.drawerEmpty)
    except till_service.TillError as exc:
        raise _fail(exc)
    return TillCloseOut(**result)


async def list_sessions(user: User, from_at: datetime | None, to_at: datetime | None, limit: int, offset: int) -> TillSessionListOut:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    sessions, total = await till_service.list_closed_sessions(user, from_at, to_at, limit, offset)
    items = [
        TillSessionSummaryOut(
            sessionNumber=s.session_number, counterName=s.counter.name if s.counter_id else None,
            cashierId=str(s.opened_by_id), openedAt=s.opened_at, closedAt=s.closed_at,
            openingFloat=s.opening_float, netCash=s.net_cash, countedCash=s.counted_cash, variance=s.variance,
        )
        for s in sessions
    ]
    return TillSessionListOut(items=items, total=total)


async def report_sessions(user: User, from_at: datetime | None, to_at: datetime | None) -> list[TillReportSessionOut]:
    sessions = await till_service.list_report_sessions(user, from_at, to_at)
    return [
        TillReportSessionOut(
            id=str(s.id), sessionNumber=s.session_number, status=s.status, counterName=s.counter.name if s.counter_id and s.counter else None,
            openedBy=s.opened_by.name if s.opened_by else None, closedBy=s.closed_by.name if s.closed_by_id and s.closed_by else None,
            openedAt=s.opened_at, closedAt=s.closed_at, openingFloat=s.opening_float, netCash=s.net_cash,
            countedCash=s.counted_cash, variance=s.variance,
        )
        for s in sessions
    ]


async def session_report(user: User, session_id: str) -> TillReportOut:
    try:
        return TillReportOut(**await till_service.session_report(user, session_id))
    except till_service.TillError as exc:
        raise _fail(exc)
