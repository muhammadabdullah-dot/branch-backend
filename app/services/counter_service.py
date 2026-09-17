"""Counters, who is on duty at them, and what each has taken today.

Two questions this answers that nothing else could: *which counter is a bill rung at*, and *who was
standing there*. Both used to be guesses — one till for the whole branch, and "staff on duty" counted
backwards from who happened to make a sale, which cannot see a person who was there all morning and
sold nothing.

Duty rows are opened and closed rather than edited, so the board is a live read of history rather than
a separate state anybody has to keep true.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.pk_time import PKT
from app.core.device_context import get_device_id
from app.models import CashMovement, CounterDuty, OutboxEvent, ReturnRecord, SaleRecord, SalesCounter, TillSession, User

ZERO = Decimal("0")
# The shop's own day is Pakistan's (core/pk_time.py). Sales are stamped in UTC; Pakistan keeps no daylight saving.


class CounterError(Exception):
    def __init__(self, message: str):
        self.message = message


def day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Midnight to midnight in branch time, as UTC instants."""
    local = (now or datetime.now(timezone.utc)).astimezone(PKT)
    start = datetime.combine(local.date(), time.min, tzinfo=PKT)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


async def ensure_default_counter() -> None:
    """A branch that has never set counters up still has to be able to open a till, so it gets one."""
    if await SalesCounter.all().limit(1).exists():
        return
    await SalesCounter.create(code="C1", name="Counter 1", sort_order=1)


async def list_counters(include_inactive: bool = True) -> list[SalesCounter]:
    qs = SalesCounter.all() if include_inactive else SalesCounter.filter(active=True)
    return await qs.order_by("sort_order", "code")


async def get_counter(counter_id: str) -> SalesCounter:
    counter = await SalesCounter.get_or_none(id=counter_id)
    if not counter:
        raise CounterError("That counter no longer exists.")
    return counter


@atomic()
async def create_counter(user: User, code: str, name: str, location: str | None) -> SalesCounter:
    code = (code or "").strip().upper()[:20]
    name = (name or "").strip()[:80]
    if not code or not name:
        raise CounterError("A counter needs a short code and a name.")
    if await SalesCounter.get_or_none(code=code):
        raise CounterError(f"Counter {code} already exists.")
    last = await SalesCounter.all().order_by("-sort_order").first()
    return await SalesCounter.create(
        code=code, name=name, location=(location or "").strip()[:120] or None,
        sort_order=(last.sort_order if last else 0) + 1,
    )


@atomic()
async def update_counter(user: User, counter_id: str, name: str | None, location: str | None, active: bool | None) -> SalesCounter:
    counter = await get_counter(counter_id)
    if name is not None:
        cleaned = name.strip()[:80]
        if not cleaned:
            raise CounterError("A counter needs a name.")
        counter.name = cleaned
    if location is not None:
        counter.location = location.strip()[:120] or None
    if active is not None and active != counter.active:
        if not active:
            # Taking a counter out of use must not leave a drawer or a person stranded on it.
            if await TillSession.get_or_none(counter=counter, status="open"):
                raise CounterError("Close this counter's till before taking it out of use.")
            for duty in await CounterDuty.filter(counter=counter, ended_at=None):
                duty.ended_at = datetime.now(timezone.utc)
                duty.ended_by = user
                await duty.save()
        counter.active = active
    await counter.save()
    return counter


async def current_duty(user_id: str) -> CounterDuty | None:
    return await CounterDuty.filter(user_id=user_id, ended_at=None).order_by("-started_at").first()


@atomic()
async def assign(actor: User, counter_id: str, user_id: str, note: str | None = None) -> CounterDuty:
    """Put somebody on a counter. One person stands at one counter, and one counter has one person on
    it, so an assignment ends whatever either of them was doing before."""
    counter = await get_counter(counter_id)
    if not counter.active:
        raise CounterError(f"{counter.name} is out of use.")
    person = await User.get_or_none(id=user_id)
    if not person or not person.active:
        raise CounterError("Pick someone whose account is in use.")
    now = datetime.now(timezone.utc)
    standing = await CounterDuty.filter(ended_at=None).filter(Q(user_id=str(person.id)) | Q(counter=counter))
    for duty in standing:
        if duty.user_id == person.id and duty.counter_id == counter.id:
            return duty
        if duty.counter_id == counter.id and await TillSession.get_or_none(counter=counter, status="open", opened_by_id=duty.user_id):
            raise CounterError("The person on this counter still has their till open. Close it first.")
        duty.ended_at = now
        duty.ended_by = actor
        await duty.save()
    duty = await CounterDuty.create(
        counter=counter, user=person, started_at=now, assigned_by=actor,
        note=(note or "").strip()[:200] or None, device_id=get_device_id(),
    )
    await OutboxEvent.create(
        aggregate_type="CounterDuty",
        aggregate_id=str(duty.id),
        payload={"event": "started", "counter": counter.code, "counterName": counter.name,
                 "user": person.name, "assignedBy": actor.name, "startedAt": now.isoformat()},
        origin_user_id=str(actor.id), origin_device_id=get_device_id(),
    )
    return duty


@atomic()
async def end_duty(actor: User, counter_id: str) -> CounterDuty | None:
    counter = await get_counter(counter_id)
    duty = await CounterDuty.filter(counter=counter, ended_at=None).order_by("-started_at").first()
    if not duty:
        return None
    if await TillSession.get_or_none(counter=counter, status="open"):
        raise CounterError("This counter's till is still open. Close the till first.")
    duty.ended_at = datetime.now(timezone.utc)
    duty.ended_by = actor
    await duty.save()
    await OutboxEvent.create(
        aggregate_type="CounterDuty",
        aggregate_id=str(duty.id),
        payload={"event": "ended", "counter": counter.code, "endedBy": actor.name,
                 "endedAt": duty.ended_at.isoformat()},
        origin_user_id=str(actor.id), origin_device_id=get_device_id(),
    )
    return duty


async def _takings(sales: list[SaleRecord], returns: list[ReturnRecord]) -> dict:
    net = sum((s.net_value for s in sales), ZERO)
    refunds = sum((r.refund_total for r in returns), ZERO)
    return {"bills": len(sales), "netSales": net, "returns": len(returns), "refunds": refunds,
            "lastSaleAt": max((s.at for s in sales), default=None)}


async def board() -> dict:
    """Every counter, who is on it, whether its drawer is open, and what it has taken today."""
    from app.services import till_service

    start, end = day_window()
    counters = await list_counters()
    duties = await CounterDuty.filter(ended_at=None).prefetch_related("user", "assigned_by")
    duty_by_counter = {str(d.counter_id): d for d in duties}
    # Open drawers whenever they were opened (a till left open overnight is still this counter's), plus
    # everything opened today, so a counter that has already closed up still shows its takings.
    sessions = await TillSession.filter(Q(status="open") | Q(opened_at__gte=start)).prefetch_related("opened_by")
    open_by_counter = {str(s.counter_id): s for s in sessions if s.counter_id and s.status == "open"}
    sessions_by_counter: dict[str, set[str]] = {}
    for s in sessions:
        sessions_by_counter.setdefault(str(s.counter_id), set()).add(str(s.id))
    today_sales = await SaleRecord.filter(at__gte=start, at__lt=end).prefetch_related("cashier")
    today_returns = await ReturnRecord.filter(at__gte=start, at__lt=end)

    rows = []
    for counter in counters:
        cid = str(counter.id)
        duty = duty_by_counter.get(cid)
        session = open_by_counter.get(cid)
        mine = sessions_by_counter.get(cid, set())
        sales = [s for s in today_sales if str(s.till_session_id) in mine]
        sale_ids = {str(s.id) for s in sales}
        returns = [r for r in today_returns if str(r.against_id) in sale_ids]
        takings = await _takings(sales, returns)
        drawer = None
        if session:
            drawer = (await till_service.compute_breakdown(session))["netCash"]
        rows.append({
            "id": cid, "code": counter.code, "name": counter.name, "location": counter.location,
            "active": counter.active, "deviceId": counter.device_id,
            "person": duty.user.name if duty else None,
            "personId": str(duty.user_id) if duty else None,
            "onDutySince": duty.started_at if duty else None,
            "assignedBy": duty.assigned_by.name if duty and duty.assigned_by else None,
            "tillOpen": session is not None,
            "sessionNumber": session.session_number if session else None,
            "sessionOpenedAt": session.opened_at if session else None,
            "openedBy": session.opened_by.name if session else None,
            "expectedCash": drawer,
            **takings,
        })
    unassigned = [r for r in rows if r["tillOpen"] and not r["person"]]
    return {
        "counters": rows,
        "asOf": datetime.now(timezone.utc),
        "day": start.astimezone(PKT).date().isoformat(),
        "openTills": sum(1 for r in rows if r["tillOpen"]),
        "onDuty": sum(1 for r in rows if r["person"]),
        "unattendedTills": len(unassigned),
        # A till session opened before this branch had counters, or on a counter later retired.
        "looseTills": [
            {"sessionNumber": s.session_number, "openedBy": s.opened_by.name, "openedAt": s.opened_at}
            for s in sessions if not s.counter_id and s.status == "open"
        ],
    }


async def staff_on_duty() -> dict:
    """Who is on the floor now — and, because a name on a board is only half the answer, what each of
    them has actually done today, including anyone who traded without being put on a counter."""
    from app.services import till_service

    start, end = day_window()
    duties = await CounterDuty.filter(started_at__gte=start).prefetch_related("user", "counter", "assigned_by", "ended_by")
    sessions = await TillSession.filter(opened_at__gte=start).prefetch_related("opened_by", "counter")
    sales = await SaleRecord.filter(at__gte=start, at__lt=end).prefetch_related("cashier")
    returns = await ReturnRecord.filter(at__gte=start, at__lt=end).prefetch_related("cashier")
    movements = await CashMovement.filter(at__gte=start, at__lt=end)
    now = datetime.now(timezone.utc)

    people: dict[str, dict] = {}

    def row_for(user_id: str, name: str) -> dict:
        return people.setdefault(user_id, {
            "userId": user_id, "name": name, "counter": None, "counterId": None, "onDuty": False,
            "since": None, "until": None, "minutes": 0, "assignedBy": None, "sessionNumber": None,
            "tillOpen": False, "expectedCash": None, "bills": 0, "netSales": ZERO, "returns": 0,
            "refunds": ZERO, "cashIn": ZERO, "cashOut": ZERO, "lastSaleAt": None, "note": None,
        })

    for duty in sorted(duties, key=lambda d: d.started_at):
        row = row_for(str(duty.user_id), duty.user.name)
        ended = duty.ended_at
        row["minutes"] += int(((ended or now) - duty.started_at).total_seconds() // 60)
        row["counter"] = duty.counter.name
        row["counterId"] = str(duty.counter_id)
        row["since"] = duty.started_at
        row["until"] = ended
        row["onDuty"] = ended is None
        row["assignedBy"] = duty.assigned_by.name if duty.assigned_by else None
        row["note"] = duty.note

    for session in sessions:
        row = row_for(str(session.opened_by_id), session.opened_by.name)
        row["sessionNumber"] = session.session_number
        row["tillOpen"] = session.status == "open"
        if session.status == "open":
            row["expectedCash"] = (await till_service.compute_breakdown(session))["netCash"]
            if not row["counter"] and session.counter:
                row["counter"] = session.counter.name
                row["counterId"] = str(session.counter_id)

    for sale in sales:
        row = row_for(str(sale.cashier_id), sale.cashier.name)
        row["bills"] += 1
        row["netSales"] += sale.net_value
        row["lastSaleAt"] = max(row["lastSaleAt"] or sale.at, sale.at)
    for ret in returns:
        row = row_for(str(ret.cashier_id), ret.cashier.name)
        row["returns"] += 1
        row["refunds"] += ret.refund_total
    for movement in movements:
        row = row_for(str(movement.user_id), "")
        row["cashIn" if movement.kind == "in" else "cashOut"] += movement.amount
        if not row["name"]:
            user = await User.get_or_none(id=movement.user_id)
            row["name"] = user.name if user else "-"

    rows = sorted(people.values(), key=lambda r: (not r["onDuty"], -r["netSales"], r["name"]))
    return {
        "people": rows,
        "asOf": now,
        "day": start.astimezone(PKT).date().isoformat(),
        "onDuty": sum(1 for r in rows if r["onDuty"]),
        "traded": sum(1 for r in rows if r["bills"]),
        # Somebody selling without being put on a counter is the thing a manager wants to see, not a
        # row to hide: it means the board does not match the floor.
        "tradedOffDuty": sum(1 for r in rows if r["bills"] and not r["counterId"]),
    }


async def duty_history(from_at: datetime | None, to_at: datetime | None, limit: int = 200) -> list[dict]:
    qs = CounterDuty.all()
    if from_at:
        qs = qs.filter(started_at__gte=from_at)
    if to_at:
        qs = qs.filter(started_at__lte=to_at)
    duties = await qs.order_by("-started_at").limit(limit).prefetch_related("user", "counter", "assigned_by", "ended_by")
    now = datetime.now(timezone.utc)
    return [{
        "id": str(d.id), "counter": d.counter.name, "counterCode": d.counter.code, "person": d.user.name,
        "startedAt": d.started_at, "endedAt": d.ended_at,
        "minutes": int(((d.ended_at or now) - d.started_at).total_seconds() // 60),
        "assignedBy": d.assigned_by.name if d.assigned_by else None,
        "endedBy": d.ended_by.name if d.ended_by else None,
        "note": d.note,
    } for d in duties]
