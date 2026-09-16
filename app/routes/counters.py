from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.controllers import counter_controller
from app.middlewares.auth import require_any_permission, require_permission
from app.models import User
from app.schemas.counter import (
    AssignRequest,
    CounterBoardOut,
    CounterCreateRequest,
    CounterUpdateRequest,
    DutyHistoryOut,
    StaffOnDutyOut,
)

router = APIRouter(prefix="/counters", tags=["counters"])

_read = require_permission("store.counters", "R")
_write = require_permission("store.counters", "W")
# Who is on the floor is a thing the Branch Dashboard and Reports ask as much as the counter board does.
_duty_read = require_any_permission(("store.staff-on-duty", "R"), ("store.counters", "R"),
                                    ("branch-console.dashboard", "R"), ("reports", "R"))


@router.get("/board", response_model=CounterBoardOut)
async def board(user: User = Depends(_read)) -> CounterBoardOut:
    return await counter_controller.board()


@router.post("", response_model=CounterBoardOut)
async def create(payload: CounterCreateRequest, user: User = Depends(_write)) -> CounterBoardOut:
    return await counter_controller.create(user, payload)


@router.patch("/{counter_id}", response_model=CounterBoardOut)
async def update(counter_id: str, payload: CounterUpdateRequest, user: User = Depends(_write)) -> CounterBoardOut:
    return await counter_controller.update(user, counter_id, payload)


@router.post("/{counter_id}/assign", response_model=CounterBoardOut)
async def assign(counter_id: str, payload: AssignRequest, user: User = Depends(_write)) -> CounterBoardOut:
    return await counter_controller.assign(user, counter_id, payload)


@router.post("/{counter_id}/end-duty", response_model=CounterBoardOut)
async def end_duty(counter_id: str, user: User = Depends(_write)) -> CounterBoardOut:
    return await counter_controller.end_duty(user, counter_id)


@router.get("/staff-on-duty", response_model=StaffOnDutyOut)
async def staff_on_duty(user: User = Depends(_duty_read)) -> StaffOnDutyOut:
    return await counter_controller.staff_on_duty()


@router.get("/duty-history", response_model=DutyHistoryOut)
async def duty_history(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = 200,
    user: User = Depends(_duty_read),
) -> DutyHistoryOut:
    return await counter_controller.history(from_, to, limit)
