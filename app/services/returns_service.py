from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise.transactions import atomic

from app.core.device_context import get_device_id
from app.models import (
    GiftVoucher,
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
    VoucherRedemption,
    balance_for,
)
from app.schemas.sales import ReturnCreateRequest
from app.schemas.types import money_str

DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")
CENT = Decimal("0.01")


class ReturnError(Exception):
    def __init__(self, message: str):
        self.message = message


REFUND_METHODS = ("CASH", "CREDIT", "CARD", "BANK", "EASYPAISA", "JAZZCASH")
REFUND_LABELS = {
    "CASH": "cash from the till", "CREDIT": "off the customer's balance", "CARD": "back to the card",
    "BANK": "by bank transfer", "EASYPAISA": "to Easypaisa", "JAZZCASH": "to JazzCash",
}


async def quote(against: str, lines: list[tuple[str, Decimal]]) -> dict:
    """What a return refunds, worked out from the bill itself: each unit at what the customer actually paid for it —
    its price, less its share of the bill's discounts, plus its GST — rounded to the rupee like the bill was."""
    sale = await SaleRecord.get_or_none(invoice_number=against.strip().upper()).prefetch_related("party", "tenders")
    if not sale:
        raise ReturnError(f"Original invoice {against} not found")
    out_lines = []
    total = ZERO
    tax_total = ZERO
    for product_id, qty in lines:
        if qty <= 0:
            continue
        product = await Product.get_or_none(id=product_id)
        if not product:
            raise ReturnError(f"Unknown product {product_id}")
        sold_lines = await SaleLine.filter(sale_id=sale.id, product_id=product_id, is_return=False)
        sold_qty = sum((sl.qty for sl in sold_lines), ZERO)
        if sold_qty <= 0:
            raise ReturnError(f"{product.name} isn't on {sale.invoice_number}.")
        already = sum((rl.qty for rl in await ReturnLine.filter(return_record__against_id=sale.id, product_id=product_id)), ZERO)
        remaining = sold_qty - already
        if qty > remaining:
            raise ReturnError(
                f"Cannot return {money_str(qty)} of {product.name} against {sale.invoice_number} — only {money_str(remaining)} "
                f"remains returnable ({money_str(already)} already returned of {money_str(sold_qty)} sold)"
            )
        gross = sum((sl.qty * sl.unit_price for sl in sold_lines), ZERO)
        disc = sum(((sl.disc_amount or ZERO) for sl in sold_lines), ZERO)
        tax = sum(((sl.tax_amount if sl.tax_amount is not None else (sl.qty * sl.unit_price - (sl.disc_amount or ZERO)) * product.tax_rate / Decimal("100"))
                   for sl in sold_lines), ZERO)
        unit_tax = tax / sold_qty
        unit_paid = ((gross - disc) / sold_qty + unit_tax).quantize(CENT, rounding=ROUND_HALF_UP)
        line_tax = (qty * unit_tax).quantize(CENT, rounding=ROUND_HALF_UP)
        value = (qty * unit_paid).quantize(CENT, rounding=ROUND_HALF_UP)
        unit_cost = next((sl.unit_cost for sl in sold_lines if sl.unit_cost is not None), product.avg_cost)
        out_lines.append({"product": product, "qty": qty, "unitPrice": unit_paid, "taxAmount": line_tax, "value": value, "unitCost": unit_cost})
        total += value
        tax_total += line_tax
    refund = total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    credit_paid = sum((t.amount for t in sale.tenders if t.code == "CREDIT"), ZERO)
    return {
        "sale": sale, "lines": out_lines, "value": total, "taxTotal": tax_total, "refundTotal": refund, "rounding": refund - total,
        "suggestedMethod": "CREDIT" if credit_paid > 0 and not sale.party.is_walk_in else "CASH",
    }


@atomic()
async def create_return(cashier: User, payload: ReturnCreateRequest) -> ReturnRecord:
    if not payload.lines:
        raise ReturnError("A return needs at least one line")
    from app.services import till_service

    till = await till_service.session_for(cashier)
    if not till:
        raise ReturnError("No Till is open — open one at your counter before processing a return")
    merged: dict[str, Decimal] = {}
    for line in payload.lines:
        merged[line.productId] = merged.get(line.productId, ZERO) + line.qty
    priced = await quote(payload.against, list(merged.items()))
    if not priced["lines"]:
        raise ReturnError("Enter how many of something is coming back.")
    sale: SaleRecord = priced["sale"]
    method = (payload.refundMethod or priced["suggestedMethod"]).upper()
    if method not in REFUND_METHODS:
        raise ReturnError("Pick how the money goes back: cash, off the customer's balance, card, bank, Easypaisa or JazzCash.")
    refund_total = priced["refundTotal"]
    if method == "CREDIT":
        party = sale.party
        if party.is_walk_in:
            raise ReturnError("A walk-in customer has no balance to take a return off. Refund it another way.")
        party.credit_balance = party.credit_balance - refund_total
        await party.save(update_fields=["credit_balance"])

    record = await ReturnRecord.create(
        against=sale, cashier=cashier, till_session=till, refund_total=refund_total, refund_method=method,
        refund_reference=(payload.refundReference or "").strip()[:40] or None,
        tax_total=priced["taxTotal"], rounding=priced["rounding"],
    )

    location = await Location.get(id=DEFAULT_LOCATION_ID)
    now = datetime.now(timezone.utc)
    for line in priced["lines"]:
        product = line["product"]
        await ReturnLine.create(
            return_record=record, product=product, qty=line["qty"], unit_price=line["unitPrice"],
            tax_amount=line["taxAmount"], unit_cost=line["unitCost"],
        )
        # Goods back on the shelf at what they cost when sold, which moves the average like any stock coming in.
        if line["unitCost"] is not None:
            on_hand = await balance_for(product.id)
            if on_hand > 0 and product.avg_cost is not None:
                product.avg_cost = (product.avg_cost * on_hand + line["qty"] * line["unitCost"]) / (on_hand + line["qty"])
                await product.save(update_fields=["avg_cost"])
        await StockMovement.create(
            product=product, location=location, kind="return", qty=line["qty"], reason=sale.invoice_number[-20:],
            origin_user=cashier, at=now, unit_cost=line["unitCost"],
        )

    await _reverse_points(sale, refund_total, cashier)

    await OutboxEvent.create(
        aggregate_type="ReturnRecord",
        aggregate_id=str(record.id),
        payload={"against": sale.invoice_number, "refundTotal": str(refund_total), "refundMethod": method},
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )

    await record.fetch_related("against", "cashier")
    return record


async def _reverse_points(sale: SaleRecord, refund_total: Decimal, cashier: User) -> None:
    """Goods brought back take back the points they earned, in proportion to what's refunded. A member
    who already spent those points goes below zero until they earn them again."""
    from app.models import LoyaltyEntry, Member, SaleTender
    from app.services import members_service

    if not sale.member_id or sale.earned_points <= 0:
        return
    member = await Member.get_or_none(id=sale.member_id)
    if not member:
        return
    points_paid = sum(await SaleTender.filter(sale=sale, code="POINTS").values_list("amount", flat=True), ZERO)
    paid = sale.net_value - points_paid
    if paid <= 0:
        return
    taken_back = -sum(await LoyaltyEntry.filter(member=member, kind="reverse", invoice_number=sale.invoice_number).values_list("points", flat=True))
    due = int((Decimal(sale.earned_points) * min(refund_total, paid) / paid).to_integral_value(rounding="ROUND_DOWN"))
    points = min(sale.earned_points - taken_back, due)
    if points > 0:
        await members_service.add_entry(member, "reverse", -points, invoice_number=sale.invoice_number, note="Goods returned", user=cashier)


@atomic()
async def reverse_voucher_sale(invoice_number: str, processed_by: User, reason: str) -> ReturnRecord:
    """Undo a whole sale that was paid by a gift voucher it had no right to use — spent on another
    customer's bill before the ownership check existed. Everything goes back where it came from: the
    stock onto the shelf and the amount onto the voucher. No cash changed hands, so the return is
    refunded to the voucher, not the drawer, and the till and cash figures don't move."""
    sale = await SaleRecord.get_or_none(invoice_number=invoice_number.strip().upper()).prefetch_related("lines__product", "tenders")
    if not sale:
        raise ReturnError(f"Invoice {invoice_number} not found")
    if await ReturnRecord.exists(against=sale):
        raise ReturnError(f"{sale.invoice_number} already has a return against it — sort that out by hand, not with this.")
    tenders = list(sale.tenders)
    if len(tenders) != 1 or tenders[0].code != "VOUCHER":
        raise ReturnError(f"{sale.invoice_number} wasn't paid entirely by one gift voucher.")
    if any(line.is_return for line in sale.lines):
        raise ReturnError(f"{sale.invoice_number} has return lines on it — reverse it by hand.")
    redemptions = await VoucherRedemption.filter(invoice_number=sale.invoice_number).prefetch_related("voucher")
    amount = tenders[0].amount
    if len(redemptions) != 1 or redemptions[0].amount != amount:
        raise ReturnError(f"The voucher record for {sale.invoice_number} doesn't match its payment.")
    voucher: GiftVoucher = redemptions[0].voucher

    from app.services import till_service

    now = datetime.now(timezone.utc)
    record = await ReturnRecord.create(
        against=sale, cashier=processed_by, till_session=await till_service.session_for(processed_by),
        refund_total=amount, refund_method="VOUCHER",
        refund_reference=voucher.code, note=reason,
    )
    location = await Location.get(id=DEFAULT_LOCATION_ID)
    for line in sale.lines:
        # What each unit was actually paid, after its share of any discount.
        paid = line.qty * line.unit_price - (line.disc_amount or ZERO)
        await ReturnLine.create(return_record=record, product=line.product, qty=line.qty, unit_price=(paid / line.qty).quantize(Decimal("0.01")))
        await StockMovement.create(
            product=line.product, location=location, kind="return", qty=line.qty,
            reason=sale.invoice_number, origin_user=processed_by, at=now,
        )

    voucher.balance += amount
    if voucher.status == "redeemed" and voucher.balance > 0:
        voucher.status = "active"
    await voucher.save()
    # A negative redemption keeps the voucher's history honest: spent on this invoice, then given back.
    await VoucherRedemption.create(voucher=voucher, invoice_number=sale.invoice_number, amount=-amount)

    await OutboxEvent.create(
        aggregate_type="ReturnRecord", aggregate_id=str(record.id),
        payload={
            "against": sale.invoice_number, "refundTotal": str(amount), "refundMethod": "VOUCHER",
            "voucherCode": voucher.code, "note": reason,
        },
        origin_user_id=str(processed_by.id), origin_device_id=get_device_id(),
    )
    await record.fetch_related("against", "cashier")
    return record
