"""Till (drawer) session: contracts.md §6's Net Cash formula, computed server-side from the
sale/return/movement rows this backend already owns, rather than trusted from the client.

Whose drawer: a till is the person's who opened it, and everyone works their own and only their own: its close, cash
in and out, its X and Z, and every till list. A Branch Manager, and anyone given the Counter board, oversees the floor:
they see every till and can close any of them or cash in and out of it, always by naming it. Staff on duty sees every
till's figures and works their own. Nobody falls back to somebody else's drawer: that is how one person's count once
closed another person's till (dry run B1)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from tortoise.transactions import atomic

from app.core.abilities import BRANCH_MANAGER
from app.core.device_context import get_device_id
from app.models import CashMovement, Counter, CounterDuty, OutboxEvent, ReturnRecord, SaleRecord, SalesCounter, TillSession, User, next_value
from app.schemas.types import money_str

ZERO = Decimal("0")
# Bills started carrying the till session they were rung into when counters arrived; drawers opened
# before that are reconciled the old way, by the clock.
LINKED_FROM = datetime(2026, 9, 16, tzinfo=timezone.utc)

NO_TILL = "You have no till open."
NOT_YOURS = "That till is someone else's. You can only work on the till you opened."
NOT_YOURS_TO_SEE = "That till is someone else's. You can only see the tills you opened."


class TillError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        # 403 when the till is somebody else's; 400 for everything else.
        self.status = status


def denomination_total(denominations: dict[str, int]) -> Decimal:
    return sum((Decimal(note) * count for note, count in denominations.items()), ZERO)


def clean_counts(denominations: dict[str, int] | None) -> dict[str, int]:
    """The notes and coins actually counted. A count below zero, or a note that isn't an amount, is refused rather
    than quietly taken off the total."""
    out: dict[str, int] = {}
    for note, count in (denominations or {}).items():
        try:
            value = Decimal(str(note))
        except InvalidOperation:
            raise TillError(f"{note} isn't a note or a coin.")
        if value <= 0:
            raise TillError(f"{note} isn't a note or a coin.")
        if count < 0:
            raise TillError("A count can't be below zero.")
        if count:
            out[str(note)] = int(count)
    return out


# ── whose tills a person sees and works ─────────────────────────────────────────────────────────

async def oversees_tills(user: User) -> bool:
    """Works every till (closes it, cash in and out): a Branch Manager, or someone given the Counter board."""
    from app.services.rbac_service import has_permission

    if user.role_id == BRANCH_MANAGER:
        return True
    return await has_permission(user, "store.counters", "R")


async def sees_all_tills(user: User) -> bool:
    """Reads every till (lists, X and Z): those who oversee them, and Staff on duty, which shows each person's drawer."""
    from app.services.rbac_service import has_permission

    return await oversees_tills(user) or await has_permission(user, "store.staff-on-duty", "R")


async def _find(session_id: str) -> TillSession | None:
    try:
        uuid.UUID(str(session_id))
    except ValueError:
        return None
    return await TillSession.get_or_none(id=session_id)


async def get_current(user: User, session_id: str | None = None) -> TillSession | None:
    """The drawer a till screen shows: the caller's own open one. Someone who oversees the tills can name another open
    one; nobody else is ever shown somebody else's drawer, and nobody is shown one they didn't ask for."""
    till = await resolve_session(user, session_id) if session_id else await session_for(user)
    if till:
        await till.fetch_related("movements__account", "counter", "opened_by")
    return till


async def session_for(user: User) -> TillSession | None:
    """The drawer a sale rung by this person belongs in: their own, and only their own.

    Falling back to anybody else's would quietly put one person's takings in somebody else's drawer, and the first
    anybody would know of it is a variance at closing time.
    """
    return await TillSession.filter(status="open", opened_by_id=user.id).order_by("-opened_at").first()


async def resolve_session(user: User, session_id: str | None) -> TillSession:
    """Which drawer an action is about: the caller's own, or, for someone who oversees the tills, the one they named.
    Never the branch's only open till, and never anybody else's for anyone else."""
    if session_id:
        till = await _find(session_id)
        if till and str(till.opened_by_id) != str(user.id) and not await oversees_tills(user):
            raise TillError(NOT_YOURS, status=403)
        if not till or till.status != "open":
            raise TillError("That till is not open.")
        await till.fetch_related("counter")
        return till
    till = await session_for(user)
    if not till:
        raise TillError(NO_TILL)
    await till.fetch_related("counter")
    return till


async def open_sessions(user: User) -> list[TillSession]:
    """The open tills a till screen can offer: every one for someone who oversees them, else only the caller's own."""
    qs = TillSession.filter(status="open")
    if not await oversees_tills(user):
        qs = qs.filter(opened_by_id=user.id)
    return await qs.order_by("opened_at").prefetch_related("opened_by", "counter")


def _last_close(till: TillSession) -> dict:
    """What a counter's last close counted: offered on Till Open as the next opening count, never used on its own."""
    by = till.closed_by or till.opened_by
    return {
        "sessionNumber": till.session_number, "closedAt": till.closed_at, "closedBy": by.name if by else None,
        "countedCash": till.counted_cash if till.counted_cash is not None else ZERO,
        "denominations": clean_counts(till.closing_denominations) if till.closing_denominations else {},
    }


async def open_options(user: User) -> dict:
    """What Till Open needs, for anyone who opens a till, without the Counter board: the counters in use, whether each
    already has a till open and who is on it, the counter this person is on, and what the last close at each counted
    (a suggested opening count). A counter someone else holds says who holds it, never what their drawer has taken."""
    counters = await SalesCounter.filter(active=True).order_by("sort_order", "code")
    open_tills = {
        str(t.counter_id): t
        for t in await TillSession.filter(status="open", counter_id__not_isnull=True).prefetch_related("opened_by")
    }
    duties = {str(d.counter_id): d for d in await CounterDuty.filter(ended_at=None).prefetch_related("user")}
    rows = []
    for counter in counters:
        till = open_tills.get(str(counter.id))
        duty = duties.get(str(counter.id))
        last = (
            await TillSession.filter(counter_id=counter.id, status="closed", closed_at__not_isnull=True)
            .order_by("-closed_at").prefetch_related("closed_by", "opened_by").first()
        )
        rows.append({
            "id": str(counter.id), "code": counter.code, "name": counter.name, "location": counter.location,
            "deviceId": counter.device_id,
            "person": duty.user.name if duty else None, "personId": str(duty.user_id) if duty else None,
            "tillOpen": till is not None, "openedBy": till.opened_by.name if till and till.opened_by else None,
            "lastClose": _last_close(last) if last else None,
        })
    return {"counters": rows}


async def _session_number() -> str:
    """TS-0001 on a new branch. A branch that already has tills goes on from its highest number, and no number a till
    already has is ever given again."""
    seed = 1
    if not await Counter.exists(id="till_session"):
        for number in await TillSession.all().values_list("session_number", flat=True):
            digits = str(number).rsplit("-", 1)[-1]
            if digits.isdigit():
                seed = max(seed, int(digits) + 1)
    while True:
        number = f"TS-{await next_value('till_session', seed):04d}"
        if not await TillSession.exists(session_number=number):
            return number


@atomic()
async def open_till(user: User, denominations: dict[str, int], notes: str, counter_id: str | None = None) -> TillSession:
    """One drawer per person and one drawer per counter — anything else and two people would be
    counting the same cash at the end of the day."""
    from app.services import counter_service

    if await TillSession.get_or_none(status="open", opened_by_id=user.id):
        raise TillError("You already have a till open. Close it before opening another.")
    denominations = clean_counts(denominations)
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
            # Then the counter this computer is registered to, else the only one there is.
            device = get_device_id()
            counter = await SalesCounter.get_or_none(active=True, device_id=device) if device else None
            if counter is None:
                active = await SalesCounter.filter(active=True)
                counter = active[0] if len(active) == 1 else None
    if counter is None:
        # Say what is really in the way: no counters at all, every counter taken, or simply none picked.
        active = await SalesCounter.filter(active=True)
        if not active:
            raise TillError("No counter is set up yet. Add one on the Counter Board, then open the till.")
        open_tills = await TillSession.filter(status="open", counter_id__in=[c.id for c in active]).prefetch_related("opened_by")
        if len(open_tills) >= len(active):
            names = {str(c.id): c.name for c in active}
            held = ", ".join(f"{names.get(str(t.counter_id), 'a counter')} ({t.opened_by.name if t.opened_by else 'someone'})" for t in open_tills)
            raise TillError(f"Every counter already has a till open: {held}. Close one of them, or add another counter on the Counter Board.")
        raise TillError("Pick which counter this till is at.")
    if await TillSession.get_or_none(status="open", counter=counter):
        raise TillError(f"{counter.name} already has a till open.")
    standing = await CounterDuty.filter(counter=counter, ended_at=None).first()
    if standing and str(standing.user_id) != str(user.id):
        person = await User.get_or_none(id=standing.user_id)
        raise TillError(f"{person.name if person else 'Somebody else'} is on {counter.name}. A manager can reassign the counter.")
    if not standing:
        # Opening a till at a counter puts you on it, recorded as plainly as if a manager had.
        await counter_service.assign(user, str(counter.id), str(user.id), note="opened the till here")
    session_number = await _session_number()
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
    denominations = clean_counts(denominations)
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


async def _session_bills(till: TillSession) -> tuple[list[SaleRecord], list[ReturnRecord]]:
    """The bills and returns rung into this drawer. For a drawer from before bills carried their session, those
    between its opening and its closing (still open: up to now)."""
    linked = await SaleRecord.filter(till_session=till).prefetch_related("tenders")
    if linked or till.opened_at >= LINKED_FROM:
        return linked, await ReturnRecord.filter(till_session=till)
    sales = SaleRecord.filter(at__gte=till.opened_at)
    returns = ReturnRecord.filter(at__gte=till.opened_at)
    if till.closed_at:
        # Read again after it closed, a later day's bills must not land in it.
        sales, returns = sales.filter(at__lte=till.closed_at), returns.filter(at__lte=till.closed_at)
    return await sales.prefetch_related("tenders"), await returns


async def compute_breakdown(till: TillSession) -> dict:
    """The drawer's own bills, not the branch's. Sessions opened before bills carried a session fall
    back to the clock, which was exactly right while only one till could be open at a time.

    Each bill counts at what the customer actually paid: its Net Value, rounded to the rupee, not the Grand Total
    before rounding. The difference is the bill's round off, shown on its own line, so a drawer counted exactly
    balances to the paisa, and what is expected here is what the books take out of the cash counter at close
    (accounts_posting_service._tills: each bill's cash less its change)."""
    sales, returns = await _session_bills(till)
    gross_sale = sum((s.gross for s in sales), ZERO)
    total_disc = sum((s.disc_total for s in sales), ZERO)
    gst_total = sum((s.gst for s in sales), ZERO)
    misc = sum((s.fare for s in sales), ZERO)
    # Net Value less the bill's parts as they are stored: exactly what rounding added or took off each bill.
    round_off = sum((s.net_value - (s.gross - s.disc_total + s.gst + s.fare) for s in sales), ZERO)
    credit_sale = ZERO
    # Paid by card, Easypaisa / JazzCash, bank transfer, gift voucher or points: none of it is in the drawer.
    non_cash_sale = ZERO
    for s in sales:
        for t in s.tenders:
            if t.code == "CREDIT":
                credit_sale += t.amount
            elif t.code != "CASH":
                non_cash_sale += t.amount
        if s.cash_back and not any(t.code == "CASH" for t in s.tenders):
            # Paid more than the bill without any cash (a card for a few paisa over): no change comes out of the drawer,
            # so only what paid the bill counts as paid by card.
            non_cash_sale -= s.cash_back
    # Only cash refunds leave the drawer; a refund back onto a gift voucher doesn't.
    cash_sale_return = sum((r.refund_total for r in returns if r.refund_method == "CASH"), ZERO)
    movements = await CashMovement.filter(till_session=till)
    cash_in = sum((m.amount for m in movements if m.kind == "in"), ZERO)
    cash_out = sum((m.amount for m in movements if m.kind == "out"), ZERO)
    till_open = till.opening_float
    net_cash = (gross_sale - total_disc + gst_total + misc + round_off - credit_sale - non_cash_sale - cash_sale_return
                + till_open + cash_in - cash_out)
    return {
        "grossSale": gross_sale,
        "totalDisc": total_disc,
        "gst": gst_total,
        "misc": misc,
        "roundOff": round_off,
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
async def close_till(
    user: User, counted_denominations: dict[str, int], session_id: str | None = None, drawer_empty: bool = False,
) -> dict:
    """Closes a drawer against what was counted in it. Nothing closes at zero by accident: an empty count is refused,
    and a drawer that really is empty is closed only when the person says so (`drawer_empty`)."""
    till = await resolve_session(user, session_id)
    counted_denominations = clean_counts(counted_denominations)
    counted_cash = denomination_total(counted_denominations)
    if counted_cash <= 0 and not drawer_empty:
        raise TillError('Count the cash in the drawer before closing the till. If the drawer really is empty, '
                        'confirm "The drawer is empty" instead.')
    if counted_cash > 0 and drawer_empty:
        raise TillError(f'You counted Rs {counted_cash:,.0f} but also said the drawer is empty. Clear the count, or untick '
                        '"The drawer is empty".')
    breakdown = await compute_breakdown(till)
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
                 "countedCash": str(counted_cash), "variance": str(variance), "closedBy": user.name,
                 "drawerEmpty": bool(drawer_empty)},
        origin_user_id=str(user.id), origin_device_id=get_device_id(),
    )
    counter = await till.counter
    opened_by = await till.opened_by
    return {**breakdown, "sessionNumber": till.session_number, "counterName": counter.name if counter else None,
            "openedBy": opened_by.name if opened_by else None, "closedBy": user.name,
            "countedCash": counted_cash, "variance": variance}


async def list_closed_sessions(
    user: User, from_at: datetime | None, to_at: datetime | None, limit: int, offset: int
) -> tuple[list[TillSession], int]:
    """Closed sessions, from the server rather than each browser's own journal: what Reports' Staff & Work and Staff &
    Roles read. Every till's for someone who sees them all, else only the caller's own."""
    qs = TillSession.filter(status="closed")
    if not await sees_all_tills(user):
        qs = qs.filter(opened_by_id=user.id)
    if from_at:
        qs = qs.filter(closed_at__gte=from_at)
    if to_at:
        qs = qs.filter(closed_at__lte=to_at)
    total = await qs.count()
    sessions = await qs.order_by("-closed_at").offset(offset).limit(limit).prefetch_related("opened_by", "counter")
    return sessions, total


# ── one session's report: X while the drawer is open, Z once it's closed ────────────────────────────

async def list_report_sessions(
    user: User, from_at: datetime | None, to_at: datetime | None, limit: int = 200,
) -> list[TillSession]:
    """Every drawer open now, and the ones that closed in the window: what X/Z lists to open a report from. Only the
    caller's own, unless they see every till."""
    mine = {} if await sees_all_tills(user) else {"opened_by_id": user.id}
    open_now = await TillSession.filter(status="open", **mine).order_by("-opened_at").prefetch_related("opened_by", "closed_by", "counter")
    closed = TillSession.filter(status="closed", **mine)
    if from_at:
        closed = closed.filter(closed_at__gte=from_at)
    if to_at:
        closed = closed.filter(closed_at__lte=to_at)
    closed_rows = await closed.order_by("-closed_at").limit(max(1, min(limit, 500))).prefetch_related("opened_by", "closed_by", "counter")
    return [*open_now, *closed_rows]


async def session_report(user: User, session_id: str) -> dict:
    """Everything one drawer took and paid out, for printing: X (still open, figures so far) or Z (closed, with
    what was counted and the short or over). The caller's own drawer, unless they see every till."""
    from app.models import PaymentMethod

    till = await _find(session_id)
    if not till:
        raise TillError("That till session doesn't exist.", status=404)
    if str(till.opened_by_id) != str(user.id) and not await sees_all_tills(user):
        raise TillError(NOT_YOURS_TO_SEE, status=403)
    await till.fetch_related("opened_by", "closed_by", "counter")
    breakdown = await compute_breakdown(till)
    sales, returns = await _session_bills(till)
    method_names = {m.code: m.name for m in await PaymentMethod.all()}
    tenders: dict[str, dict] = {}
    for sale in sales:
        # What each payment put towards the bill: cash less the change handed back, so the payments add up to what the
        # bills came to (their Net Value), as the drawer and the books count them.
        change = sale.cash_back or ZERO
        paid_cash = any(t.code == "CASH" for t in sale.tenders)
        for tender in sale.tenders:
            row = tenders.setdefault(tender.code, {"code": tender.code, "name": method_names.get(tender.code, tender.code), "amount": ZERO, "bills": 0})
            amount = tender.amount
            if change and (tender.code == "CASH" or (not paid_cash and tender.code != "CREDIT")):
                amount -= change
                change = ZERO
            row["amount"] += amount
            row["bills"] += 1
    refunds: dict[str, dict] = {}
    for record in returns:
        code = record.refund_method or "CASH"
        row = refunds.setdefault(code, {"code": code, "name": method_names.get(code, code), "amount": ZERO, "count": 0})
        row["amount"] += record.refund_total
        row["count"] += 1
    people: dict[str, dict] = {}
    cashier_ids = {str(s.cashier_id) for s in sales} | {str(r.cashier_id) for r in returns}
    names = {str(u.id): u.name for u in await User.filter(id__in=list(cashier_ids))} if cashier_ids else {}
    for sale in sales:
        row = people.setdefault(str(sale.cashier_id), {"name": names.get(str(sale.cashier_id), "-"), "bills": 0, "amount": ZERO})
        row["bills"] += 1
        row["amount"] += sale.net_value
    movements = await CashMovement.filter(till_session=till).order_by("at").prefetch_related("account", "user")
    closed = till.status == "closed"
    expected = till.net_cash if closed and till.net_cash is not None else breakdown["netCash"]
    counter = till.counter
    return {
        "sessionId": str(till.id), "sessionNumber": till.session_number, "status": till.status, "kind": "Z" if closed else "X",
        "counterName": counter.name if counter else None, "counterCode": counter.code if counter else None,
        "openedBy": till.opened_by.name if till.opened_by else None, "closedBy": till.closed_by.name if till.closed_by else None,
        "openedAt": till.opened_at, "closedAt": till.closed_at, "openingNotes": till.opening_notes,
        "openingDenominations": till.opening_denominations or {}, "closingDenominations": till.closing_denominations,
        "invoices": len(sales), "netSale": sum((s.net_value for s in sales), ZERO),
        "tenders": sorted(tenders.values(), key=lambda r: (r["code"] != "CASH", -r["amount"])),
        "returnsCount": len(returns), "returnsTotal": sum((r.refund_total for r in returns), ZERO),
        "refunds": sorted(refunds.values(), key=lambda r: (r["code"] != "CASH", -r["amount"])),
        "people": sorted(people.values(), key=lambda r: -r["amount"]),
        "movements": [
            {
                "kind": m.kind, "amount": m.amount, "at": m.at, "notes": m.notes, "payee": m.payee,
                "accountName": m.account.name if m.account else None, "by": m.user.name if m.user else None,
            }
            for m in movements
        ],
        **breakdown,
        "expectedCash": expected,
        "countedCash": till.counted_cash if closed else None,
        "variance": till.variance if closed else None,
        "generatedAt": datetime.now(timezone.utc),
    }
