"""The centerpiece of I3 (contracts.md §6). POST /sales must be one atomic transaction covering
the sale record, line items, stock movement posting, voucher redemption, and outbox evidence —
@atomic() rolls back all of it on any failure, so a partial sale can never exist.
"""
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise.transactions import atomic

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
from app.services import gift_voucher_service
from app.services.rbac_service import has_permission

DISCOUNT_LIMIT_PERCENT = Decimal("5")
DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")


class SaleError(Exception):
    def __init__(self, message: str):
        self.message = message


async def peek_next_invoice_number() -> str:
    from app.models import Counter

    counter = await Counter.get_or_none(id="invoice")
    seq = counter.value if counter else 143
    return f"HO-2026-{seq:06d}"


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
        raise SaleError(f"Payment not covered — remaining {net_value - received}")
    cash_back = max(ZERO, received - net_value)

    earned_points = 0
    if party.loyalty_no:
        earned_points = max(0, int((gross - disc_total) // Decimal("50")))

    invoice_seq = await next_value("invoice", 143)
    invoice_number = f"HO-2026-{invoice_seq:06d}"
    fbr_invoice_number = f"7000-{invoice_number[-8:]}"
    is_credit_sale = payload.tenders.get("CREDIT", ZERO) > 0

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
        origin_user_id=str(cashier.id),
    )

    await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by")
    return sale


async def find_by_invoice(invoice_number: str) -> SaleRecord | None:
    sale = await SaleRecord.get_or_none(invoice_number=invoice_number.strip().upper())
    if sale:
        await sale.fetch_related("lines__product", "tenders", "party", "cashier", "discount_override_by")
    return sale
