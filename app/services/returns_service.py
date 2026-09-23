"""Customer returns: goods coming back against a bill.

Three ways a return ends:
- refund: the money goes back (cash from the till, off the customer's balance, to a card, bank or wallet);
- replace: the same Items go back to the customer (a faulty one swapped);
- exchange: other Items go out.

A replace or an exchange is a return plus a bill, linked (ReturnRecord.exchange_sale). The return is refunded the
usual way, cash or off the customer's balance, and the new bill is paid the same way for as much as the return was
worth, so every report, the till and the books see a return and a sale while the drawer and the customer's balance
only move by the difference. Anything more the customer pays is taken on the new bill like any other payment. The new
bill is made by the sales service itself, so everything that applies to a bill (stock, discount floor, selling rules,
payment details) applies to it too.

Every return is checked against the department's return window (return_window_service) and carries a number for its
printed receipt.
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise.transactions import atomic, in_transaction

from app.core.device_context import get_device_id
from app.core.pk_time import today_pk
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
from app.schemas.sales import MemberIn, ReturnCreateRequest, SaleCreateRequest, SaleLineIn
from app.schemas.types import money_str

DEFAULT_LOCATION_ID = "loc-1"
ZERO = Decimal("0")
CENT = Decimal("0.01")


class ReturnError(Exception):
    def __init__(self, message: str):
        self.message = message


class _Rollback(Exception):
    """Raised to undo a trial run (an exchange priced before it is made) while keeping what it worked out."""

    def __init__(self, result):
        self.result = result


REFUND_METHODS = ("CASH", "CREDIT", "CARD", "BANK", "EASYPAISA", "JAZZCASH")
REFUND_LABELS = {
    "CASH": "cash from the till", "CREDIT": "off the customer's balance", "CARD": "back to the card",
    "BANK": "by bank transfer", "EASYPAISA": "to Easypaisa", "JAZZCASH": "to JazzCash",
}
# A replace or exchange settles through the till or the customer's balance: the new bill is paid the same way the
# return was refunded, and only those two can do that for any customer.
EXCHANGE_METHODS = ("CASH", "CREDIT")
# What a customer can pay a difference with. Bank transfers need a screenshot, taken on Billing.
DIFFERENCE_TENDERS = ("CASH", "CARD", "EASYPAISA", "JAZZCASH", "BANK", "CREDIT")
KINDS = ("refund", "replace", "exchange")
KIND_LABELS = {"refund": "Refund", "replace": "Replace", "exchange": "Exchange"}
# The owner's rule for the reason Other: a remark of 5 to 20 characters.
REMARK_MIN, REMARK_MAX = 5, 20
# A trial bill is paid with this much cash so its own payment check passes; it is never kept.
_TRIAL_CASH = Decimal("99999999")


# Reason and remark:

def needs_remark(code: str | None, name: str | None) -> bool:
    """The reason Other asks for a remark: the starter reason, or any reason the branch has called Other."""
    return (code or "").strip().lower() == "other" or (name or "").strip().lower() == "other"


def clean_remark(text: str | None) -> str | None:
    value = " ".join((text or "").split())
    return value or None


async def check_reason(code: str | None, remark: str | None) -> tuple[str | None, str | None]:
    """The reason's code as the record keeps it, and the remark when the reason needs one (else None)."""
    from app.services import masters_service

    reason = (code or "").strip() or None
    if not reason:
        return None, None
    try:
        entry = await masters_service.require_reason("sale-return", reason)
    except masters_service.MastersError as exc:
        raise ReturnError(exc.message) from exc
    if not needs_remark(entry.code, entry.name):
        return entry.code, None
    text = clean_remark(remark)
    if not text:
        raise ReturnError(f"The reason is {entry.name}, so write a short remark on what happened ({REMARK_MIN} to {REMARK_MAX} characters).")
    if len(text) < REMARK_MIN:
        raise ReturnError(f"The remark is too short. Write {REMARK_MIN} to {REMARK_MAX} characters on what happened.")
    if len(text) > REMARK_MAX:
        raise ReturnError(f"The remark is too long. Keep it to {REMARK_MAX} characters (it has {len(text)}).")
    return entry.code, text


# Pharmacy Items:

SLIP_RETURN = "Returns of pharmacy slips are taken by the Branch Manager."
PHARMACY_RETURN = "Returns of pharmacy Items are taken by the Branch Manager."


async def refuse_pharmacy_for(person: User, against: str, product_ids) -> None:
    """A Salesperson never sees Pharmacy Items (services/pharmacy_service.py), so they don't take them back either: the
    Branch Manager does. Refused before anything is priced, in words that name no Item."""
    from app.services import pharmacy_service

    if not pharmacy_service.hides_pharmacy(person):
        return
    ids = {str(pid) for pid in product_ids}
    if pharmacy_service.FOLDED_ID not in ids and not await pharmacy_service.pharmacy_ids(ids):
        return
    sale = await SaleRecord.get_or_none(invoice_number=(against or "").strip().upper())
    raise ReturnError(SLIP_RETURN if sale and pharmacy_service.slip_numbers(sale.slips) else PHARMACY_RETURN)


# Pricing what comes back:

async def quote(against: str, lines: list[tuple[str, Decimal]]) -> dict:
    """What a return refunds, worked out from the bill itself: each unit at what the customer actually paid for it
    (its price, less its share of the bill's discounts, plus its GST), rounded to the rupee like the bill was.
    Refused for an Item past its department's return window."""
    from app.services import return_window_service

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
        try:
            await return_window_service.refuse_outside_window(sale.at, [product], sale.invoice_number)
        except return_window_service.ReturnWindowError as exc:
            raise ReturnError(exc.message) from exc
        from app.services import sales_service

        already = sum((rl.qty for rl in await ReturnLine.filter(return_record__against_id=sale.id, product_id=product_id)), ZERO)
        # What came back as return lines on a Billing bill naming this one counts too.
        already += await sales_service.returned_on_bills(sale, product_id)
        remaining = sold_qty - already
        if qty > remaining:
            raise ReturnError(
                f"Cannot return {money_str(qty)} of {product.name} against {sale.invoice_number}. Only {money_str(remaining)} "
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


def _merge(lines) -> list[tuple[str, Decimal]]:
    merged: dict[str, Decimal] = {}
    for line in lines:
        merged[line.productId] = merged.get(line.productId, ZERO) + Decimal(line.qty)
    return list(merged.items())


async def _next_number() -> str:
    """MT-RT-2026-000001: the return prefix (the branch's code and RT unless its Branch Manager set another under Invoice
    numbers), the Pakistan year and the running number, with the bill's year and digits. From 1 on a new branch, and on
    from the highest return number on one that has returns; never a number already used, nor one a bill carries
    (services/invoice_numbers_service.py)."""
    from app.services import invoice_numbers_service

    return await invoice_numbers_service.next_return_number()


def shown_number(record: ReturnRecord) -> str:
    """Returns taken before receipts had no number; their record's own id stands in."""
    return record.number or f"RT-{str(record.id)[:8].upper()}"


async def _record_return(
    cashier: User, till: TillSession | None, priced: dict, method: str, reference: str | None,
    reason: str | None, remark: str | None, kind: str,
) -> ReturnRecord:
    """Writes a priced return: the customer's balance, the record and its lines, the stock back on the shelf, points
    taken back, and the event for head office. Runs inside the caller's transaction."""
    sale: SaleRecord = priced["sale"]
    refund_total = priced["refundTotal"]
    if method == "CREDIT":
        party = sale.party
        if party.is_walk_in:
            raise ReturnError("A walk-in customer has no balance to take a return off. Refund it another way.")
        party.credit_balance = party.credit_balance - refund_total
        await party.save(update_fields=["credit_balance"])

    record = await ReturnRecord.create(
        against=sale, cashier=cashier, till_session=till, refund_total=refund_total, refund_method=method,
        refund_reference=(reference or "").strip()[:40] or None,
        tax_total=priced["taxTotal"], rounding=priced["rounding"], reason=reason, note=remark,
        number=await _next_number(), kind=kind,
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
                from app.services import price_history_service

                cost_before = price_history_service.snapshot(product)
                product.avg_cost = (product.avg_cost * on_hand + line["qty"] * line["unitCost"]) / (on_hand + line["qty"])
                await product.save(update_fields=["avg_cost"])
                # The new average cost goes into the Item's price history against this return.
                await price_history_service.record(product, cost_before, "return", shown_number(record), cashier)
        await StockMovement.create(
            product=product, location=location, kind="return", qty=line["qty"], reason=sale.invoice_number[-20:],
            origin_user=cashier, at=now, unit_cost=line["unitCost"],
        )

    # The return's own FBR invoice, a credit note against the bill (services/fbr_service.py), in this transaction.
    from app.services import fbr_service

    await fbr_service.issue_for_return(record, sale)
    await _reverse_points(sale, refund_total, cashier)
    return record


async def _outbox(record: ReturnRecord, sale: SaleRecord, cashier: User, exchange: SaleRecord | None = None) -> None:
    payload = {"against": sale.invoice_number, "refundTotal": str(record.refund_total), "refundMethod": record.refund_method,
               "number": record.number, "kind": record.kind}
    if exchange is not None:
        payload["exchangeInvoice"] = exchange.invoice_number
    await OutboxEvent.create(
        aggregate_type="ReturnRecord", aggregate_id=str(record.id), payload=payload,
        origin_user_id=str(cashier.id), origin_device_id=get_device_id(),
    )


# A refund:

async def _lists_ready() -> None:
    """The reason list fills itself on first use. That has to happen outside a return's transaction: a return refused
    after it (a missing remark, say) would undo the new rows while the list still counts itself as filled."""
    from app.services import masters_service

    await masters_service.ensure_reasons()


async def create_return(cashier: User, payload: ReturnCreateRequest) -> ReturnRecord:
    await _lists_ready()
    return await _create_return(cashier, payload)


@atomic()
async def _create_return(cashier: User, payload: ReturnCreateRequest) -> ReturnRecord:
    if not payload.lines:
        raise ReturnError("A return needs at least one line")
    from app.services import masters_service, till_service

    till = await till_service.session_for(cashier)
    if not till:
        raise ReturnError("No Till is open. Open one at your counter before processing a return")
    await refuse_pharmacy_for(cashier, payload.against, [line.productId for line in payload.lines])
    priced = await quote(payload.against, _merge(payload.lines))
    if not priced["lines"]:
        raise ReturnError("Enter how many of something is coming back.")
    sale: SaleRecord = priced["sale"]
    method = (payload.refundMethod or priced["suggestedMethod"]).upper()
    if method not in REFUND_METHODS:
        raise ReturnError("Pick how the money goes back: cash, off the customer's balance, card, bank, Easypaisa or JazzCash.")
    try:
        # Money can't go back through a method the branch has switched off. A credit bill always goes back off the
        # customer's balance, whatever Credit is set to for new sales.
        if method not in ("CASH", "CREDIT"):
            await masters_service.refuse_switched_off_methods([method])
    except masters_service.MastersError as exc:
        raise ReturnError(exc.message) from exc
    reason, remark = await check_reason(payload.reason, payload.remark)

    record = await _record_return(cashier, till, priced, method, payload.refundReference, reason, remark, "refund")
    await _outbox(record, sale, cashier)
    await record.fetch_related("against", "cashier")
    return record


# Replace and exchange:

def _replacement_price(product: Product, paid_per_unit: Decimal) -> Decimal:
    """The shelf price that makes the new bill charge exactly what the customer paid for each unit coming back (GST
    included): the Item's own discount comes off it and GST goes on it, as on any bill. So a straight swap costs
    nothing, whatever the price or the discounts are now."""
    if paid_per_unit <= 0:
        return ZERO
    percent = Decimal(product.disc_percent or 0)
    flat = Decimal(product.disc_flat or 0)
    if percent >= 100:
        return Decimal(product.price)
    before_tax = paid_per_unit / (1 + Decimal(product.tax_rate or 0) / Decimal("100"))
    return ((before_tax + flat) / (1 - percent / Decimal("100"))).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


async def _new_lines(payload, priced: dict) -> list[SaleLineIn]:
    """What goes out. A replace sends the same Items and quantities that came back, each at what was paid for it."""
    if payload.mode == "replace":
        return [SaleLineIn(productId=l["product"].id, qty=l["qty"], unitPrice=_replacement_price(l["product"], l["unitPrice"]))
                for l in priced["lines"]]
    out: list[SaleLineIn] = []
    for product_id, qty in _merge(payload.newLines):
        if qty <= 0:
            continue
        product = await Product.get_or_none(id=product_id)
        if not product:
            raise ReturnError("One of the new Items isn't in the catalog any more. Take it off and add it again.")
        if not product.active:
            raise ReturnError(f"{product.name} is switched off, so it can't be sold.")
        if not product.is_weighed and qty != qty.to_integral_value():
            raise ReturnError(f"{product.name} is sold in whole units. Enter a whole number.")
        out.append(SaleLineIn(productId=product.id, qty=qty, unitPrice=Decimal(product.price)))
    if not out:
        raise ReturnError("Add the Items the customer is taking instead.")
    return out


async def _check_replacement_stock(priced: dict) -> None:
    """A swap needs a good one on the shelf: the Item coming back doesn't count, it's the faulty one."""
    from app.services import stock_guard

    wanted: dict[str, tuple[Product, Decimal]] = {}
    for line in priced["lines"]:
        product = line["product"]
        have = wanted.get(product.id, (product, ZERO))[1]
        wanted[product.id] = (product, have + line["qty"])
    held = await stock_guard.on_hand(list(wanted))
    short = [f"{p.name} ({stock_guard.qty_text(held.get(pid, ZERO))} in stock, {stock_guard.qty_text(q)} needed)"
             for pid, (p, q) in wanted.items() if held.get(pid, ZERO) < q]
    if short:
        raise ReturnError("Not enough in stock to swap: " + "; ".join(short) + ". Refund it instead, or exchange it for something else.")


def _sale_figures(sale: SaleRecord) -> dict:
    return {
        "invoiceNumber": sale.invoice_number, "net": Decimal(sale.net_value), "gst": Decimal(sale.gst),
        "discount": Decimal(sale.disc_total), "gross": Decimal(sale.gross),
        "lines": [{
            "productId": str(l.product_id), "name": l.product.name, "sku": l.product.sku, "qty": Decimal(l.qty),
            "isWeighed": l.product.is_weighed,
            "value": (Decimal(l.qty) * Decimal(l.unit_price) - Decimal(l.disc_amount or 0) + Decimal(l.tax_amount or 0)).quantize(CENT, rounding=ROUND_HALF_UP),
        } for l in sale.lines if not l.is_return],
    }


def _with_unit(lines: list[dict]) -> list[dict]:
    for l in lines:
        l["unitPrice"] = (l["value"] / l["qty"]).quantize(CENT, rounding=ROUND_HALF_UP) if l["qty"] else ZERO
    return lines


async def _trial_sale(cashier: User, request: SaleCreateRequest) -> dict:
    """Rings the new bill and undoes it, to learn exactly what it comes to with every rule that applies to a bill."""
    from app.services import sales_service

    trial = request.model_copy(update={"tenders": {"CASH": _TRIAL_CASH}, "tenderDetails": {}, "member": None, "clientRequestId": None, "voucherCode": None})
    try:
        async with in_transaction():
            try:
                sale = await sales_service.create_sale(cashier, trial)
            except sales_service.SaleError as exc:
                raise ReturnError(exc.message) from exc
            raise _Rollback(_sale_figures(sale))
    except _Rollback as done:
        return done.result


async def _settle(payload, method: str, refund: Decimal, net: Decimal) -> dict[str, Decimal]:
    """The new bill's payments: the return's value in the same way it was refunded, up to what the bill comes to,
    then whatever the customer pays for the rest."""
    covered = min(refund, net)
    tenders: dict[str, Decimal] = {method: covered} if covered > 0 else {}
    due = net - refund
    if due <= 0:
        return tenders
    extra: dict[str, Decimal] = {}
    for code, amount in (payload.tenders or {}).items():
        code = (code or "").strip().upper()
        amount = Decimal(amount or 0)
        if amount <= 0:
            continue
        if code not in DIFFERENCE_TENDERS:
            raise ReturnError("The difference can be paid in cash, by card, Easypaisa, JazzCash, bank transfer or on credit.")
        extra[code] = extra.get(code, ZERO) + amount
    paid = sum(extra.values(), ZERO)
    if paid < due:
        raise ReturnError(f"The new Items come to Rs {money_str(due)} more than what came back. Take the rest: Rs {money_str(due - paid)} still to pay.")
    if paid - extra.get("CASH", ZERO) > due:
        raise ReturnError("Card, wallet, bank and credit payments can't be more than what is due. Only cash can have change.")
    for code, amount in extra.items():
        tenders[code] = tenders.get(code, ZERO) + amount
    return tenders


async def _exchange(cashier: User, payload, trial: bool) -> dict:
    """A replace or an exchange, inside the caller's transaction. On a trial nothing is paid for: the new bill is rung
    to price it and the caller undoes everything."""
    from app.services import sale_rules, sales_service, till_service

    if payload.mode not in ("replace", "exchange"):
        raise ReturnError("Pick Replace or Exchange.")
    if not payload.lines:
        raise ReturnError("Enter how many of something is coming back.")
    till = await till_service.session_for(cashier)
    if not till:
        raise ReturnError("No Till is open. Open one at your counter before taking a return")
    await refuse_pharmacy_for(cashier, payload.against, [line.productId for line in payload.lines])
    priced = await quote(payload.against, _merge(payload.lines))
    if not priced["lines"]:
        raise ReturnError("Enter how many of something is coming back.")
    sale: SaleRecord = priced["sale"]
    method = (payload.refundMethod or priced["suggestedMethod"]).upper()
    if method not in EXCHANGE_METHODS:
        method = "CASH"
    if method == "CREDIT" and sale.party.is_walk_in:
        raise ReturnError("A walk-in customer has no balance. Settle it in cash.")
    reason, remark = await check_reason(payload.reason, payload.remark)
    if payload.mode == "replace":
        await _check_replacement_stock(priced)
    lines = await _new_lines(payload, priced)

    record = await _record_return(cashier, till, priced, method, None, reason, remark, payload.mode)
    member = MemberIn(code=(await sale.member).code) if sale.member_id else payload.member
    request = SaleCreateRequest(
        partyId=str(sale.party_id), member=member, lines=lines, tenders={},
        tenderDetails=payload.tenderDetails or {}, clientRequestId=payload.clientRequestId,
    )
    refund = Decimal(record.refund_total)

    # A replace is a swap at the price paid, not a discount: the no-loss rule takes that price as the Item's own.
    swap = sale_rules.replacing.set(True) if payload.mode == "replace" else None
    try:
        if trial:
            figures = _sale_figures(await _run_sale(cashier, request.model_copy(update={
                "tenders": {"CASH": _TRIAL_CASH}, "tenderDetails": {}, "member": None, "clientRequestId": None})))
            return {"record": record, "priced": priced, "method": method, "new": figures}

        figures = await _trial_sale(cashier, request)
        request.tenders = await _settle(payload, method, refund, figures["net"])
        new_sale = await _run_sale(cashier, request)
    finally:
        if swap is not None:
            sale_rules.replacing.reset(swap)
    record.exchange_sale = new_sale
    await record.save(update_fields=["exchange_sale_id"])
    await _outbox(record, sale, cashier, new_sale)
    return {"record": record, "priced": priced, "method": method, "sale": new_sale}


async def _run_sale(cashier: User, request: SaleCreateRequest) -> SaleRecord:
    from app.services import sales_service

    try:
        return await sales_service.create_sale(cashier, request)
    except sales_service.SaleError as exc:
        raise ReturnError(exc.message) from exc


async def create_exchange(cashier: User, payload) -> tuple[ReturnRecord, SaleRecord]:
    """Takes the Items back and rings the new bill in one go: both happen or neither does."""
    await _lists_ready()
    return await _create_exchange(cashier, payload)


@atomic()
async def _create_exchange(cashier: User, payload) -> tuple[ReturnRecord, SaleRecord]:
    if payload.clientRequestId:
        done = await SaleRecord.get_or_none(client_request_id=payload.clientRequestId)
        if done:
            record = await ReturnRecord.get_or_none(exchange_sale=done)
            if not record:
                raise ReturnError("This exchange was sent twice with a bill's number. Refresh the screen and check the returns list.")
            return record, done
    out = await _exchange(cashier, payload, trial=False)
    return out["record"], out["sale"]


async def quote_exchange(cashier: User, payload) -> dict:
    """What a replace or exchange comes to, worked out by making it and undoing it, so the figures are the ones the
    real one will have."""
    # Lists that fill themselves on first use are filled for good before the trial, not inside it where they'd be undone.
    await _lists_ready()
    try:
        async with in_transaction():
            out = await _exchange(cashier, payload, trial=True)
            priced, new = out["priced"], out["new"]
            refund = Decimal(out["record"].refund_total)
            result = {
                "against": priced["sale"].invoice_number, "partyName": priced["sale"].party.name, "walkIn": priced["sale"].party.is_walk_in,
                "suggestedMethod": priced["suggestedMethod"], "method": out["method"],
                "lines": [{"productId": l["product"].id, "name": l["product"].name, "qty": money_str(l["qty"]),
                           "unitPrice": money_str(l["unitPrice"]), "value": money_str(l["value"])} for l in priced["lines"]],
                "refundTotal": money_str(refund), "taxTotal": money_str(priced["taxTotal"]),
                "newLines": [{**{k: v for k, v in l.items() if k not in ("qty", "value", "unitPrice")}, "qty": money_str(l["qty"]),
                              "unitPrice": money_str(l["unitPrice"]), "value": money_str(l["value"])} for l in _with_unit(new["lines"])],
                "newTotal": money_str(new["net"]), "newGst": money_str(new["gst"]), "newDiscount": money_str(new["discount"]),
                "difference": money_str(new["net"] - refund),
            }
            raise _Rollback(result)
    except _Rollback as done:
        return done.result


# Points:

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


# The receipt and the list:

async def _method_names() -> dict[str, str]:
    from app.models import PaymentMethod

    names = {m.code: m.name for m in await PaymentMethod.all()}
    names.setdefault("CREDIT", "Customer balance")
    names.setdefault("VOUCHER", "Gift voucher")
    return names


def _method_name(code: str, names: dict[str, str]) -> str:
    if code == "CREDIT":
        return "Off the customer's balance"
    if code == "VOUCHER":
        return "Back onto the gift voucher"
    return names.get(code, code.title())


async def _till_label(till_id) -> str | None:
    if not till_id:
        return None
    session = await TillSession.get_or_none(id=till_id).prefetch_related("counter")
    if not session:
        return None
    return f"{session.counter.name if session.counter else 'Till'} · {session.session_number}"


async def receipt(record_id: str, viewer: User | None = None) -> dict:
    """Everything a return receipt prints. A viewer who doesn't sell Pharmacy Items gets them as one line, never by name
    (services/pharmacy_service.py)."""
    from app.services import fbr_service, masters_service, pharmacy_service

    try:
        record = await ReturnRecord.get_or_none(id=record_id).prefetch_related("against__party", "cashier", "lines__product")
    except (ValueError, TypeError):
        record = None
    if not record:
        raise ReturnError("That return doesn't exist at this branch.")
    names = await _method_names()
    reasons = await masters_service.reason_entries("sale-return") if record.reason else {}
    out = {
        "id": str(record.id), "number": shown_number(record), "at": record.at, "against": record.against.invoice_number,
        "againstAt": record.against.at, "partyName": record.against.party.name, "walkIn": record.against.party.is_walk_in,
        "kind": record.kind or "refund",
        "lines": [{"productId": str(l.product_id), "name": l.product.name, "sku": l.product.sku, "qty": l.qty, "unitPrice": l.unit_price,
                   "value": (Decimal(l.qty) * Decimal(l.unit_price)).quantize(CENT, rounding=ROUND_HALF_UP), "isWeighed": l.product.is_weighed}
                  for l in record.lines],
        "refundTotal": record.refund_total, "taxTotal": record.tax_total, "rounding": record.rounding,
        "refundMethod": record.refund_method or "CASH", "refundMethodName": _method_name(record.refund_method or "CASH", names),
        "refundReference": record.refund_reference, "reason": record.reason,
        "reasonLabel": reasons[record.reason].name if record.reason in reasons else record.reason,
        "remark": record.note, "cashierName": record.cashier.name if record.cashier else None,
        "tillLabel": await _till_label(record.till_session_id),
        # The return's FBR invoice (a credit note): a test number, FBR's number, or waiting for FBR. None on older returns.
        "fbr": await fbr_service.stamp_for_return(record),
    }
    if record.exchange_sale_id:
        sale = await SaleRecord.get_or_none(id=record.exchange_sale_id).prefetch_related("lines__product", "tenders")
        if sale:
            figures = _sale_figures(sale)
            refund = Decimal(record.refund_total)
            method = record.refund_method or "CASH"
            covered = min(refund, figures["net"])
            # The part of the new bill the returned goods paid for is not a payment the customer made.
            tenders = []
            for t in sale.tenders:
                amount = Decimal(t.amount)
                if t.code == method and covered > 0:
                    taken = min(amount, covered)
                    amount -= taken
                    covered -= taken
                if amount > 0:
                    tenders.append({"code": t.code, "name": names.get(t.code, t.code.title()), "amount": amount,
                                    "reference": t.reference})
            stamp = await fbr_service.stamp_for_sale(sale)
            out.update({
                "exchangeInvoice": sale.invoice_number, "exchangeFbrInvoice": stamp["number"], "exchangeFbr": stamp,
                "exchangeLines": _with_unit(figures["lines"]), "exchangeGst": figures["gst"], "exchangeDiscount": figures["discount"],
                "exchangeTotal": figures["net"], "difference": figures["net"] - refund, "differenceTenders": tenders,
                "change": sale.cash_back or None,
            })
    if pharmacy_service.hides_pharmacy(viewer):
        seen = [row["productId"] for row in out["lines"]] + [row["productId"] for row in out.get("exchangeLines") or []]
        hidden = await pharmacy_service.pharmacy_ids(seen)
        if hidden:
            out["lines"] = pharmacy_service.fold_rows(out["lines"], hidden, record.against.slips)
            if out.get("exchangeLines"):
                out["exchangeLines"] = pharmacy_service.fold_rows(out["exchangeLines"], hidden, None)
    return out


REPRINT_ROUTE = "/returns/{return_id}/reprint"


async def reprint(record_id: str, user: User) -> dict:
    """A return receipt printed again. The POST that prints it is kept in the activity log (middlewares/activity.py),
    so the copy number counts the reprints before this one."""
    from app.models import ActivityLog

    out = await receipt(record_id, user)
    earlier = await ActivityLog.filter(
        route=REPRINT_ROUTE, method="POST", status_code__lt=400, path__iexact=f"/returns/{out['id']}/reprint",
    ).count()
    out["reprint"] = {"at": datetime.now(timezone.utc), "by": user.name, "copyNumber": earlier + 1}
    return out


async def list_returns(from_at: datetime | None, to_at: datetime | None, limit: int = 200, cashier_id=None) -> list[dict]:
    """Returns taken in a period, newest first: today's by default. `cashier_id` keeps it to the returns one person took
    (a salesperson sees their own, like their own bills: sales_service.sees_every_bill)."""
    from app.core.pk_time import day_start
    from app.services import masters_service

    if from_at is None and to_at is None:
        from_at = day_start(today_pk())
        to_at = from_at + timedelta(days=1)
    qs = ReturnRecord.all() if cashier_id is None else ReturnRecord.filter(cashier_id=cashier_id)
    if from_at:
        qs = qs.filter(at__gte=from_at)
    if to_at:
        qs = qs.filter(at__lt=to_at)
    records = await qs.order_by("-at").limit(max(1, min(limit, 500))).prefetch_related("against__party", "cashier", "lines", "exchange_sale")
    names = await _method_names()
    reasons = await masters_service.reason_entries("sale-return")
    out = []
    for r in records:
        out.append({
            "id": str(r.id), "number": shown_number(r), "at": r.at, "against": r.against.invoice_number,
            "partyName": r.against.party.name, "kind": r.kind or "refund", "refundTotal": r.refund_total,
            "refundMethod": r.refund_method or "CASH", "refundMethodName": _method_name(r.refund_method or "CASH", names),
            "reasonLabel": reasons[r.reason].name if r.reason in reasons else r.reason, "remark": r.note,
            "cashierName": r.cashier.name if r.cashier else None,
            "exchangeInvoice": r.exchange_sale.invoice_number if r.exchange_sale else None,
            "exchangeTotal": r.exchange_sale.net_value if r.exchange_sale else None,
            "items": len(r.lines),
        })
    return out


# Undoing a sale paid by a voucher it had no right to use:

@atomic()
async def reverse_voucher_sale(invoice_number: str, processed_by: User, reason: str) -> ReturnRecord:
    """Undo a whole sale that was paid by a gift voucher it had no right to use: spent on another
    customer's bill before the ownership check existed. Everything goes back where it came from: the
    stock onto the shelf and the amount onto the voucher. No cash changed hands, so the return is
    refunded to the voucher, not the drawer, and the till and cash figures don't move."""
    sale = await SaleRecord.get_or_none(invoice_number=invoice_number.strip().upper()).prefetch_related("lines__product", "tenders")
    if not sale:
        raise ReturnError(f"Invoice {invoice_number} not found")
    if await ReturnRecord.exists(against=sale):
        raise ReturnError(f"{sale.invoice_number} already has a return against it, so sort that out by hand, not with this.")
    tenders = list(sale.tenders)
    if len(tenders) != 1 or tenders[0].code != "VOUCHER":
        raise ReturnError(f"{sale.invoice_number} wasn't paid entirely by one gift voucher.")
    if any(line.is_return for line in sale.lines):
        raise ReturnError(f"{sale.invoice_number} has return lines on it, so reverse it by hand.")
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
        refund_reference=voucher.code, note=reason, number=await _next_number(), kind="refund",
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

    from app.services import fbr_service

    await fbr_service.issue_for_return(record, sale)

    await OutboxEvent.create(
        aggregate_type="ReturnRecord", aggregate_id=str(record.id),
        payload={
            "against": sale.invoice_number, "refundTotal": str(amount), "refundMethod": "VOUCHER",
            "voucherCode": voucher.code, "note": reason, "number": record.number,
        },
        origin_user_id=str(processed_by.id), origin_device_id=get_device_id(),
    )
    await record.fetch_related("against", "cashier")
    return record
