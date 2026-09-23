from datetime import datetime

from fastapi import HTTPException, status

from app.models import User
from app.schemas.counter import (
    AssignRequest,
    CounterBoardOut,
    CounterCreateRequest,
    CounterOut,
    CounterUpdateRequest,
    DutyHistoryOut,
    DutyHistoryRow,
    StaffOnDutyOut,
)
from app.services import counter_service


def _fail(exc: counter_service.CounterError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


async def board() -> CounterBoardOut:
    return CounterBoardOut(**await counter_service.board())


async def staff_on_duty() -> StaffOnDutyOut:
    return StaffOnDutyOut(**await counter_service.staff_on_duty())


async def create(user: User, payload: CounterCreateRequest) -> CounterBoardOut:
    try:
        await counter_service.create_counter(user, payload.code, payload.name, payload.location)
    except counter_service.CounterError as exc:
        raise _fail(exc)
    return await board()


async def update(user: User, counter_id: str, payload: CounterUpdateRequest) -> CounterBoardOut:
    try:
        await counter_service.update_counter(user, counter_id, payload.name, payload.location, payload.active, payload.code)
    except counter_service.CounterError as exc:
        raise _fail(exc)
    return await board()


async def assign(user: User, counter_id: str, payload: AssignRequest) -> CounterBoardOut:
    try:
        await counter_service.assign(user, counter_id, payload.userId, payload.note)
    except counter_service.CounterError as exc:
        raise _fail(exc)
    return await board()


async def end_duty(user: User, counter_id: str) -> CounterBoardOut:
    try:
        await counter_service.end_duty(user, counter_id)
    except counter_service.CounterError as exc:
        raise _fail(exc)
    return await board()


async def history(from_at: datetime | None, to_at: datetime | None, limit: int) -> DutyHistoryOut:
    rows = await counter_service.duty_history(from_at, to_at, min(max(limit, 1), 500))
    return DutyHistoryOut(items=[DutyHistoryRow(**r) for r in rows])


def counter_out(row: dict) -> CounterOut:
    return CounterOut(**row)
