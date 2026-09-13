"""The centerpiece of I3 (contracts.md §6). POST /sales must be one atomic transaction covering
the sale record, line items, stock movement posting, voucher redemption, and outbox evidence —
@atomic() rolls back all of it on any failure, so a partial sale can never exist.
"""
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import (
    Location,
    OutboxEvent,
    Party,
    Product,
    SaleLine,
    SaleRecord,
    SaleTender,
    StockMovement,
    TillSession,
    User,
    next_value,
)
from app.schemas.sales import SaleCreateRequest
from app.schemas.types import money_str
from app.services import gift_voucher_service
from app.services.rbac_service import has_permission

DISCOUNT_LIMIT_PERCENT = Decimal("5")
DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")


class SaleError(Exception):
    def __init__(self, message: str):
        self.message = message


# Until this branch server has been verified it has no identity, and an invoice has to be numbered
# something. `BR` is deliberately not a real branch's code — a bill printed before setup is a bill
# that should be obvious as such, rather than one quietly filed under whichever branch the software
# was written in.
UNVERIFIED_PREFIX = "BR"


async def invoice_prefix() -> str:
    """This branch's own code, from the identity head office issued it.

    It used to be the literal string "HO". That was the head office assumption baked into the
    numbering: every branch in the chain would have printed bills numbered as if they were Head
    Office, and two branches' invoice numbers would have collided the moment a second one opened.
    A branch is a branch — its bills carry its own code.
    """
    from app.services import registration_service

    identity = await registration_service.current()
    return identity.code if identity else UNVERIFIED_PREFIX


async def peek_next_invoice_number() -> str:
    from app.models import Counter

    counter = await Counter.get_or_none(id="invoice")
    seq = counter.value if counter else 143
    return f"{await invoice_prefix()}-{_invoice_year()}-{seq:06d}"


def _invoice_year() -> int:
    """The branch's own trading year, not the server's UTC one — Pakistan is UTC+5, so a sale rung
    at half past nine in the evening on 31 December is still last year's bill."""
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=5))).year


async def _resolve_party(party_id: str | None) -> Party:
    if party_id:
        party = await Party.get_or_none(id=party_id)
        if not party:
            raise SaleError("Party not found")
        return party
    party = await Party.get_or_none(is_walk_in=True)
    if not party:
        raise SaleError("Walk-in party not seeded")
    return party


def _line_gross(line, unit_price: Decimal) -> Decimal:
    sign = Decimal("-1") if line.isReturn else Decimal("1")
    return sign * line.qty * unit_price


@atomic()
async def create_sale(cashier: User, payload: SaleCreateRequest) -> SaleRecord:
    # Idempotent replay: checked FIRST, before any other validation or side effect, so a
    # retried request (lost response, double-click) returns the already-committed sale instead
    # of re-running credit-limit checks, re-posting stock movements, or re-redeeming a voucher.
    # Note: this closes the common case (a delayed/lost-response retry after the first request
    # already committed) but not a true simultaneous double-fire — two requests with the same
    # key landing in the same instant could both pass this check before either commits, and the
    # second would then fail on the column's unique constraint rather than returning the first
    # sale gracefully. Acceptable for now (a single till/cashier submitting sequentially can't
    # produce that race; only a client bug firing the exact same request twice in parallel could).
    if payload.clientRequestId:
        existing = await SaleRecord.get_or_none(client_request_id=payload.clientRequestId)
        if existing:
            await existing.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by")
            return existing

    if not payload.lines:
        raise SaleError("A sale needs at least one line")
    if not await TillSession.get_or_none(status="open"):
        raise SaleError("No Till is open — open one before taking payment")

    party = await _resolve_party(payload.partyId)
    products: dict[str, Product] = {}
    for line in payload.lines:
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise SaleError(f"Unknown product {line.productId}")
        products[line.productId] = product

    gross = sum((_line_gross(l, l.unitPrice) for l in payload.lines), ZERO)
    percent_disc = gross * payload.discPercent / Decimal("100")
    disc_total = percent_disc + payload.flatDisc

    def line_disc(line) -> Decimal:
        lg = _line_gross(line, line.unitPrice)
        share = (payload.flatDisc * (lg / gross)) if gross != 0 else ZERO
        return lg * payload.discPercent / Decimal("100") + share

    gst = sum(
        ((_line_gross(l, l.unitPrice) - line_disc(l)) * products[l.productId].tax_rate / Decimal("100"))
        for l in payload.lines
    )
    grand_total = gross - disc_total + payload.fare + gst
    net_value = grand_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    effective_pct = (disc_total / gross * Decimal("100")) if gross > 0 else ZERO
    override_user = None
    if effective_pct > DISCOUNT_LIMIT_PERCENT:
        if not payload.discountOverrideByUserId:
            raise SaleError(
                f"Discount {effective_pct:.1f}% exceeds {DISCOUNT_LIMIT_PERCENT}% — manager override required"
            )
        override_user = await User.get_or_none(id=payload.discountOverrideByUserId, active=True)
        if not override_user or not await has_permission(override_user, "store.discount-override", "X"):
            raise SaleError("Override user not found or lacks discount-override authority")

    received = sum(payload.tenders.values(), ZERO)
    if received < net_value:
        raise SaleError(f"Payment not covered — remaining {money_str(net_value - received)}")
    cash_back = max(ZERO, received - net_value)

    earned_points = 0
    if party.loyalty_no:
        earned_points = max(0, int((gross - disc_total) // Decimal("50")))

    invoice_seq = await next_value("invoice", 143)
    invoice_number = f"{await invoice_prefix()}-{_invoice_year()}-{invoice_seq:06d}"
    fbr_invoice_number = f"7000-{invoice_number[-8:]}"
    credit_amount = payload.tenders.get("CREDIT", ZERO)
    is_credit_sale = credit_amount > 0

    # Credit-limit enforcement (previously: creditBalance was seeded but never checked or
    # updated anywhere — legacy's "Check Balance Limit" had no equivalent at all).
    if is_credit_sale:
        if not party.credit_allowed:
            raise SaleError(f"{party.name} is not allowed credit sales")
        new_balance = party.credit_balance + credit_amount
        if new_balance > party.credit_limit:
            headroom = party.credit_limit - party.credit_balance
            raise SaleError(
                f"Credit sale of {money_str(credit_amount)} exceeds {party.name}'s "
                f"remaining headroom of {money_str(headroom)}"
            )
        party.credit_balance = new_balance
        await party.save(update_fields=["credit_balance"])

    sale = await SaleRecord.create(
        invoice_number=invoice_number,
        cashier=cashier,
        party=party,
        gross=gross,
        disc_total=disc_total,
        fare=payload.fare,
        gst=gst,
        grand_total=grand_total,
        net_value=net_value,
        discount_override_by=override_user,
        earned_points=earned_points,
        received=received,
        cash_back=cash_back,
        is_credit_sale=is_credit_sale,
        fbr_invoice_number=fbr_invoice_number,
        client_request_id=payload.clientRequestId,
    )

    location = await Location.get(id=DEFAULT_LOCATION_ID)
    for line in payload.lines:
        product = products[line.productId]
        await SaleLine.create(
            sale=sale, product=product, qty=line.qty, unit_price=line.unitPrice, is_return=line.isReturn
        )
        await StockMovement.create(
            product=product,
            location=location,
            kind="return" if line.isReturn else "sell",
            qty=line.qty if line.isReturn else -line.qty,
            origin_user=cashier,
            at=datetime.now(timezone.utc),
        )

    for code, amount in payload.tenders.items():
        if amount > 0:
            await SaleTender.create(sale=sale, code=code, amount=amount)

    voucher_amount = payload.tenders.get("VOUCHER", ZERO)
    if voucher_amount > 0:
        if not payload.voucherCode:
            raise SaleError("Voucher code required for a VOUCHER tender")
        try:
            await gift_voucher_service.redeem(payload.voucherCode, voucher_amount, invoice_number)
        except gift_voucher_service.VoucherError as exc:
            raise SaleError(exc.message) from exc

    await OutboxEvent.create(
        aggregate_type="SaleRecord",
        aggregate_id=str(sale.id),
        payload={"invoiceNumber": invoice_number, "netValue": str(net_value), "partyId": str(party.id)},
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )

    await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by")
    return sale


async def find_by_invoice(invoice_number: str) -> SaleRecord | None:
    sale = await SaleRecord.get_or_none(invoice_number=invoice_number.strip().upper())
    if sale:
        await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by")
    return sale


async def list_sales(
    from_at: datetime | None, to_at: datetime | None, limit: int, offset: int
) -> tuple[list[SaleRecord], int]:
    """Branch-wide, not terminal-scoped — the read path X/Z, the Dashboard and Reports need
    instead of each browser's own local sales journal."""
    qs = SaleRecord.all()
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lte=to_at)
    total = await qs.count()
    sales = (
        await qs.order_by("-at")
        .offset(offset)
        .limit(limit)
        .prefetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by")
    )
    return sales, total
