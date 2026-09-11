"""Till (drawer) session — contracts.md §6's Net Cash formula, computed server-side from the
sale/return/movement rows this backend already owns, rather than trusted from the client."""
from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.models import CashMovement, OutboxEvent, ReturnRecord, SaleRecord, TillSession, User, next_value

ZERO = Decimal("0")


class TillError(Exception):
    def __init__(self, message: str):
        self.message = message


def denomination_total(denominations: dict[str, int]) -> Decimal:
    return sum((Decimal(note) * count for note, count in denominations.items()), ZERO)


async def get_current() -> TillSession | None:
    till = await TillSession.get_or_none(status="open")
    if till:
        await till.fetch_related("movements")
    return till


@atomic()
async def open_till(user: User, denominations: dict[str, int], notes: str) -> TillSession:
    if await TillSession.get_or_none(status="open"):
        raise TillError("A till session is already open")
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
        status="open",
    )
    await OutboxEvent.create(
        aggregate_type="TillSession",
        aggregate_id=str(till.id),
        payload={"sessionNumber": session_number, "event": "opened", "openingFloat": str(total)},
        origin_user_id=str(user.id),
    )
    return till


@atomic()
async def record_movement(user: User, kind: str, denominations: dict[str, int], notes: str) -> CashMovement:
    till = await TillSession.get_or_none(status="open")
    if not till:
        raise TillError("No till session is open")
    amount = denomination_total(denominations)
    movement = await CashMovement.create(
        till_session=till, kind=kind, amount=amount, denominations=denominations, notes=notes or None, user=user
    )
    await OutboxEvent.create(
        aggregate_type="CashMovement",
        aggregate_id=str(movement.id),
        payload={"tillSessionId": str(till.id), "kind": kind, "amount": str(amount)},
        origin_user_id=str(user.id),
    )
    return movement


async def compute_breakdown(till: TillSession) -> dict:
    sales = await SaleRecord.filter(at__gte=till.opened_at).prefetch_related("tenders")
    returns = await ReturnRecord.filter(at__gte=till.opened_at)
    gross_sale = sum((s.gross for s in sales), ZERO)
    total_disc = sum((s.disc_total for s in sales), ZERO)
    gst_total = sum((s.gst for s in sales), ZERO)
    misc = sum((s.fare for s in sales), ZERO)
    credit_sale = ZERO
    for s in sales:
        for t in s.tenders:
            if t.code == "CREDIT":
                credit_sale += t.amount
    cash_sale_return = sum((r.refund_total for r in returns), ZERO)
    movements = await CashMovement.filter(till_session=till)
    cash_in = sum((m.amount for m in movements if m.kind == "in"), ZERO)
    cash_out = sum((m.amount for m in movements if m.kind == "out"), ZERO)
    till_open = till.opening_float
    net_cash = gross_sale - total_disc + gst_total + misc - credit_sale - cash_sale_return + till_open + cash_in - cash_out
    return {
        "grossSale": gross_sale,
        "totalDisc": total_disc,
        "gst": gst_total,
        "misc": misc,
        "creditSale": credit_sale,
        "cashSaleReturn": cash_sale_return,
        "tillOpen": till_open,
        "cashIn": cash_in,
        "cashOut": cash_out,
        "netCash": net_cash,
    }


async def preview_close() -> dict:
    till = await TillSession.get_or_none(status="open")
    if not till:
        raise TillError("No till session is open")
    return await compute_breakdown(till)


@atomic()
async def close_till(user: User, counted_denominations: dict[str, int]) -> dict:
    till = await TillSession.get_or_none(status="open")
    if not till:
        raise TillError("No till session is open")
    breakdown = await compute_breakdown(till)
    counted_cash = denomination_total(counted_denominations)
    variance = counted_cash - breakdown["netCash"]
    till.status = "closed"
    till.closed_at = datetime.now(timezone.utc)
    till.net_cash = breakdown["netCash"]
    till.counted_cash = counted_cash
    till.variance = variance
    await till.save()
    await OutboxEvent.create(
        aggregate_type="TillSession",
        aggregate_id=str(till.id),
        payload={"sessionNumber": till.session_number, "event": "closed", "netCash": str(breakdown["netCash"])},
        origin_user_id=str(user.id),
    )
    return {**breakdown, "sessionNumber": till.session_number, "countedCash": counted_cash, "variance": variance}
