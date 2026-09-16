"""Till (drawer) session — contracts.md §6's Net Cash formula, computed server-side from the
sale/return/movement rows this backend already owns, rather than trusted from the client."""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import CashMovement, CounterDuty, OutboxEvent, ReturnRecord, SaleRecord, SalesCounter, TillSession, User, next_value

ZERO = Decimal("0")
# Bills started carrying the till session they were rung into when counters arrived; drawers opened
# before that are reconciled the old way, by the clock.
LINKED_FROM = datetime(2026, 9, 16, tzinfo=timezone.utc)


class TillError(Exception):
    def __init__(self, message: str):
        self.message = message


def denomination_total(denominations: dict[str, int]) -> Decimal:
    return sum((Decimal(note) * count for note, count in denominations.items()), ZERO)


async def get_current(user: User | None = None) -> TillSession | None:
    """The drawer the caller is working out of: their own open session. A person who has none — a
    manager looking in, or a branch that opened its till before counters existed — sees the branch's
    open session when there is exactly one, which is how this behaved when only one could be open."""
    till = None
    if user is not None:
        till = await TillSession.filter(status="open", opened_by_id=user.id).order_by("-opened_at").first()
    if till is None:
        open_tills = await TillSession.filter(status="open")
        till = open_tills[0] if len(open_tills) == 1 else None
    if till:
        await till.fetch_related("movements__account", "counter")
    return till


async def session_for(user: User) -> TillSession | None:
    """The session a sale rung by this person belongs in."""
    return await get_current(user)


async def resolve_session(user: User, session_id: str | None) -> TillSession:
    """Which drawer an action is about: the one named, else the caller's own, else the only open one."""
    if session_id:
        till = await TillSession.get_or_none(id=session_id, status="open")
        if not till:
            raise TillError("That till session is not open.")
        await till.fetch_related("counter")
        return till
    till = await get_current(user)
    if not till:
        raise TillError("No till session is open")
    return till


@atomic()
async def open_till(user: User, denominations: dict[str, int], notes: str, counter_id: str | None = None) -> TillSession:
    """One drawer per person and one drawer per counter — anything else and two people would be
    counting the same cash at the end of the day."""
    from app.services import counter_service

    if await TillSession.get_or_none(status="open", opened_by_id=user.id):
        raise TillError("You already have a till open. Close it before opening another.")
    counter = None
    if counter_id:
        counter = await SalesCounter.get_or_none(id=counter_id)
        if not counter or not counter.active:
            raise TillError("Pick a counter that's in use.")
    else:
        # Not asked for a counter: the one they are already standing at, else the only one there is.
        duty = await counter_service.current_duty(str(user.id))
        if duty:
            counter = await SalesCounter.get_or_none(id=duty.counter_id)
        else:
            active = await SalesCounter.filter(active=True)
            counter = active[0] if len(active) == 1 else None
    if counter is None:
        raise TillError("Say which counter this till is at.")
    if await TillSession.get_or_none(status="open", counter=counter):
        raise TillError(f"{counter.name} already has a till open.")
    standing = await CounterDuty.filter(counter=counter, ended_at=None).first()
    if standing and str(standing.user_id) != str(user.id):
        person = await User.get_or_none(id=standing.user_id)
        raise TillError(f"{person.name if person else 'Somebody else'} is on {counter.name}. A manager can reassign the counter.")
    if not standing:
        # Opening a till at a counter puts you on it, recorded as plainly as if a manager had.
        await counter_service.assign(user, str(counter.id), str(user.id), note="opened the till here")
    seq = await next_value("till_session", 41)
    session_number = f"TS-{seq:04d}"
    total = denomination_total(denominations)
    till = await TillSession.create(
        session_number=session_number,
        opened_at=datetime.now(timezone.utc),
        opened_by=user,
        opening_float=total,
        opening_denominations=denominations,
        opening_notes=notes or None,
        counter=counter,
        status="open",
    )
    await OutboxEvent.create(
        aggregate_type="TillSession",
        aggregate_id=str(till.id),
        payload={"sessionNumber": session_number, "event": "opened", "openingFloat": str(total),
                 "counter": counter.code if counter else None, "counterName": counter.name if counter else None},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return till


@atomic()
async def record_movement(
    user: User, kind: str, denominations: dict[str, int], notes: str, account_id: str | None = None, payee: str | None = None,
    session_id: str | None = None,
) -> CashMovement:
    """A Cash Out says what the money was for — an expense head, a supplier paid from the counter, cash sent to the
    safe — and a Cash In where it came from. That's what puts it in the right place in the books."""
    from app.models import Account

    till = await resolve_session(user, session_id)
    amount = denomination_total(denominations)
    if amount <= 0:
        raise TillError("Count at least one note or coin.")
    account = None
    if account_id:
        account = await Account.get_or_none(id=account_id)
        if not account or not account.active:
            raise TillError("Pick an account that's in use.")
        if account.restricted:
            raise TillError(f"{account.name} is kept for the software's own entries.")
        if account.system_key == "cash.counter":
            raise TillError("The till's own cash can't be the other side of a cash in or out.")
    movement = await CashMovement.create(
        till_session=till, kind=kind, amount=amount, denominations=denominations, notes=notes or None, user=user,
        account=account, payee=(payee or "").strip()[:120] or None,
    )
    await OutboxEvent.create(
        aggregate_type="CashMovement",
        aggregate_id=str(movement.id),
        payload={"tillSessionId": str(till.id), "kind": kind, "amount": str(amount), "accountCode": account.code if account else None},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    return movement


async def compute_breakdown(till: TillSession) -> dict:
    """The drawer's own bills, not the branch's. Sessions opened before bills carried a session fall
    back to the clock, which was exactly right while only one till could be open at a time."""
    linked = await SaleRecord.filter(till_session=till).prefetch_related("tenders")
    if linked or till.opened_at >= LINKED_FROM:
        sales = linked
        returns = await ReturnRecord.filter(till_session=till)
    else:
        sales = await SaleRecord.filter(at__gte=till.opened_at).prefetch_related("tenders")
        returns = await ReturnRecord.filter(at__gte=till.opened_at)
    gross_sale = sum((s.gross for s in sales), ZERO)
    total_disc = sum((s.disc_total for s in sales), ZERO)
    gst_total = sum((s.gst for s in sales), ZERO)
    misc = sum((s.fare for s in sales), ZERO)
    credit_sale = ZERO
    # Paid by card, Easypaisa / JazzCash, bank transfer, gift voucher or points: none of it is in the drawer.
    non_cash_sale = ZERO
    for s in sales:
        for t in s.tenders:
            if t.code == "CREDIT":
                credit_sale += t.amount
            elif t.code != "CASH":
                non_cash_sale += t.amount
    # Only cash refunds leave the drawer; a refund back onto a gift voucher doesn't.
    cash_sale_return = sum((r.refund_total for r in returns if r.refund_method == "CASH"), ZERO)
    movements = await CashMovement.filter(till_session=till)
    cash_in = sum((m.amount for m in movements if m.kind == "in"), ZERO)
    cash_out = sum((m.amount for m in movements if m.kind == "out"), ZERO)
    till_open = till.opening_float
    net_cash = gross_sale - total_disc + gst_total + misc - credit_sale - non_cash_sale - cash_sale_return + till_open + cash_in - cash_out
    return {
        "grossSale": gross_sale,
        "totalDisc": total_disc,
        "gst": gst_total,
        "misc": misc,
        "creditSale": credit_sale,
        "nonCashSale": non_cash_sale,
        "cashSaleReturn": cash_sale_return,
        "tillOpen": till_open,
        "cashIn": cash_in,
        "cashOut": cash_out,
        "netCash": net_cash,
    }


async def preview_close(user: User, session_id: str | None = None) -> dict:
    till = await resolve_session(user, session_id)
    return await compute_breakdown(till)


@atomic()
async def close_till(user: User, counted_denominations: dict[str, int], session_id: str | None = None) -> dict:
    till = await resolve_session(user, session_id)
    breakdown = await compute_breakdown(till)
    counted_cash = denomination_total(counted_denominations)
    variance = counted_cash - breakdown["netCash"]
    till.status = "closed"
    till.closed_at = datetime.now(timezone.utc)
    till.net_cash = breakdown["netCash"]
    till.counted_cash = counted_cash
    till.variance = variance
    till.closing_denominations = counted_denominations
    till.closed_by = user
    await till.save()
    # The drawer is counted and shut, so whoever was on that counter is off duty unless put back on.
    if till.counter_id:
        from app.services import counter_service

        await counter_service.end_duty(user, str(till.counter_id))
    await OutboxEvent.create(
        aggregate_type="TillSession",
        aggregate_id=str(till.id),
        payload={"sessionNumber": till.session_number, "event": "closed", "netCash": str(breakdown["netCash"]),
                 "countedCash": str(counted_cash), "variance": str(variance)},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    counter = await till.counter
    return {**breakdown, "sessionNumber": till.session_number, "counterName": counter.name if counter else None,
            "countedCash": counted_cash, "variance": variance}


async def list_closed_sessions(
    from_at: datetime | None, to_at: datetime | None, limit: int, offset: int
) -> tuple[list[TillSession], int]:
    """Branch-wide closed sessions — the read path X/Z's Day Close and Reports' Staff & Work
    need instead of each browser's own local terminal journal."""
    qs = TillSession.filter(status="closed")
    if from_at:
        qs = qs.filter(closed_at__gte=from_at)
    if to_at:
        qs = qs.filter(closed_at__lte=to_at)
    total = await qs.count()
    sessions = await qs.order_by("-closed_at").offset(offset).limit(limit).prefetch_related("opened_by", "counter")
    return sessions, total
