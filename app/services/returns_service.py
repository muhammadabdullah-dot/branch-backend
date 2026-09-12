from datetime import datetime, timezone
from decimal import Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import (
    Location,
    OutboxEvent,
    Product,
    ReturnLine,
    ReturnRecord,
    SaleLine,
    SaleRecord,
    StockMovement,
    TillSession,
    User,
)
from app.schemas.sales import ReturnCreateRequest
from app.schemas.types import money_str

DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")


class ReturnError(Exception):
    def __init__(self, message: str):
        self.message = message


@atomic()
async def create_return(cashier: User, payload: ReturnCreateRequest) -> ReturnRecord:
    if not payload.lines:
        raise ReturnError("A return needs at least one line")
    if not await TillSession.get_or_none(status="open"):
        raise ReturnError("No Till is open — open one before processing a return")

    sale = await SaleRecord.get_or_none(invoice_number=payload.against.strip().upper())
    if not sale:
        raise ReturnError(f"Original invoice {payload.against} not found")

    products: dict[str, Product] = {}
    for line in payload.lines:
        product = await Product.get_or_none(id=line.productId)
        if not product:
            raise ReturnError(f"Unknown product {line.productId}")
        products[line.productId] = product

    # Cap each line at what's actually still returnable — found live 2026-09-12: nothing
    # previously checked this, so the same invoice could be refunded and re-stocked an
    # unlimited number of times, up to the original quantity, on every repeated attempt.
    for line in payload.lines:
        sold_lines = await SaleLine.filter(sale_id=sale.id, product_id=line.productId, is_return=False)
        sold_qty = sum((sl.qty for sl in sold_lines), ZERO)
        already_returned = await ReturnLine.filter(return_record__against_id=sale.id, product_id=line.productId)
        already_returned_qty = sum((rl.qty for rl in already_returned), ZERO)
        remaining = sold_qty - already_returned_qty
        if line.qty > remaining:
            raise ReturnError(
                f"Cannot return {money_str(line.qty)} of {products[line.productId].name} against "
                f"{sale.invoice_number} — only {money_str(remaining)} remains returnable "
                f"({money_str(already_returned_qty)} already returned of {money_str(sold_qty)} sold)"
            )

    refund_total = sum((l.qty * l.unitPrice for l in payload.lines), ZERO)

    record = await ReturnRecord.create(against=sale, cashier=cashier, refund_total=refund_total)

    location = await Location.get(id=DEFAULT_LOCATION_ID)
    for line in payload.lines:
        product = products[line.productId]
        await ReturnLine.create(return_record=record, product=product, qty=line.qty, unit_price=line.unitPrice)
        await StockMovement.create(
            product=product, location=location, kind="return", qty=line.qty,
            origin_user=cashier, at=datetime.now(timezone.utc),
        )

    await OutboxEvent.create(
        aggregate_type="ReturnRecord",
        aggregate_id=str(record.id),
        payload={"against": sale.invoice_number, "refundTotal": str(refund_total)},
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )

    await record.fetch_related("against", "cashier")
    return record
