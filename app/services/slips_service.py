"""Pharmacy slips: a Pharmacist rings Pharmacy Items up and prints a slip, and the customer pays for it at the cash counter.

A Pharmacist never takes money (core/abilities.py PHARMACIST_NEVER_*). In Billing they ring Pharmacy Items up as anyone
does; Print slip stores the bill here as a held bill of kind "slip", with its own number (P-0042, running per branch),
priced again at the Items' own prices. The number is printed on the slip as a barcode.

At the cash counter a slip is only an amount to collect. Its number is typed or scanned into Billing's code box, or the
slip is picked from F5, and its payment is taken on its own (`pay`): the bill on that till is never touched, and the
person taking the money is told what the slip comes to, who made it, when and how many Items, never which (`to_out`
leaves the Items off for anyone who doesn't sell them). The payment is a sale made here from the slip's own lines at the
slip's prices, recorded exactly as any sale is (stock, invoice number, the till's drawer), by the person who took the
money, and it records the slip and the Pharmacist who made it. The slip records who took the payment, when, on which
bill and how. The customer's slip is stamped PAID by hand at the counter and handed back.

An open slip is cancelled, with a reason, by the Pharmacist who made it or by a Branch Manager. Nothing else happens to
slips left open: they wait on the list until they are paid or cancelled.

Slips brought onto a bill the earlier way (status "on-bill", 18 Sep) count as open: they are paid the same way now.
"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from tortoise.transactions import in_transaction

from app.core.abilities import BRANCH_MANAGER
from app.models import HeldBill, PaymentMethod, Product, ProductAlias, SaleRecord, User, next_value
from app.schemas.held_bills import SlipCreateIn, SlipOut, SlipPaidOut, SlipPayIn, SlipTenderOut
from app.schemas.sales import SaleCreateRequest, SaleLineIn, SaleLineOut
from app.services import pharmacy_service, sale_rules, sell_levels

KIND = "slip"
OPEN, ON_BILL, PAID, CANCELLED = "open", "on-bill", "paid", "cancelled"
# Brought onto a bill the earlier way and never paid there: as good as open.
PAYABLE = (OPEN, ON_BILL)
ZERO = Decimal("0")
# Printing the same bill again within this long hands back the slip already made.
_REPRINT_WINDOW = timedelta(hours=12)
# How a slip is paid at the cash counter: no credit, gift voucher or points.
PAY_TENDERS = ("CASH", "CARD", "EASYPAISA", "JAZZCASH", "BANK")
# How far back the list looks for a slip paid or cancelled on the day asked about.
_LIST_LOOKBACK = timedelta(days=31)


class SlipError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def number_of(seq: int) -> str:
    return f"P-{seq:04d}"


def normalise_number(code: str | None) -> str | None:
    """P-0042 however it was typed or scanned (p0042, P 0042, p-0042); None when it isn't a slip number.

    The digits must be the whole number as it is printed on the slip. A short P-42 is not taken as P-0042: at a busy
    counter that turns one missed key into the wrong slip being paid, and the cashier would have no way of knowing."""
    m = re.fullmatch(r"\s*[Pp]\s*-?\s*(\d{4,8})\s*", code or "")
    return number_of(int(m.group(1))) if m else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _clock(value) -> str:
    """3:44 pm, Pakistan time (another day's time names the day)."""
    from app.services.login_session_service import clock

    at = _when(value)
    return clock(at) if at else "an earlier time"


def _money(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a figure missing from an older row reads as nothing
        return ZERO


def maker_name(slip: HeldBill) -> str | None:
    made_by = slip.made_by if slip.made_by_id and isinstance(getattr(slip, "made_by", None), User) else None
    return made_by.name if made_by else (slip.slip or {}).get("madeByName")


def shown_status(slip: HeldBill) -> str:
    return OPEN if slip.status == ON_BILL else slip.status


def _paid_by_name(d: dict) -> str | None:
    # A slip paid on a bill the earlier way names whoever brought it onto that bill.
    return d.get("paidByName") or d.get("takenByName")


def _tenders_of(d: dict) -> list[SlipTenderOut]:
    return [SlipTenderOut(**t) for t in d.get("tenders") or [] if isinstance(t, dict) and t.get("code")]


def to_out(slip: HeldBill, show_lines: bool = True) -> SlipOut:
    """`show_lines` is False for anyone who doesn't sell Pharmacy Items: they are told how many, never which."""
    d = slip.slip or {}
    status = shown_status(slip)
    return SlipOut(
        id=str(slip.id), number=slip.number or "", status=status,
        lines=[SaleLineOut(**line) for line in slip.lines] if show_lines else [], itemCount=len(slip.lines or []),
        gross=_money(d.get("gross")), discTotal=_money(d.get("disc")), gst=_money(d.get("gst")), total=_money(d.get("total")),
        madeById=str(slip.made_by_id) if slip.made_by_id else None, madeByName=maker_name(slip), madeAt=slip.held_at,
        paidInvoice=d.get("paidInvoice") if status == PAID else None, paidAt=_when(d.get("paidAt")) if status == PAID else None,
        paidByName=_paid_by_name(d) if status == PAID else None, paidTenders=_tenders_of(d) if status == PAID else [],
        change=_money(d["change"]) if status == PAID and d.get("change") is not None else None,
        cancelledByName=d.get("cancelledByName") if status == CANCELLED else None,
        cancelledAt=_when(d.get("cancelledAt")) if status == CANCELLED else None,
        reason=d.get("reason") if status == CANCELLED else None,
        refusal=None if status == OPEN else why_not_payable(slip),
    )


def paid_out(slip: HeldBill, sale: SaleRecord) -> SlipPaidOut:
    """What the cash counter is told about a payment: the amount, the change and the bill, never the Items."""
    d = slip.slip or {}
    return SlipPaidOut(
        number=slip.number or "", invoiceNumber=sale.invoice_number, amount=sale.net_value, received=sale.received,
        change=sale.cash_back, paidAt=_when(d.get("paidAt")) or sale.at, paidByName=_paid_by_name(d),
        madeByName=maker_name(slip), itemCount=len(slip.lines or []), tenders=_tenders_of(d),
    )


# ── Making a slip ─────────────────────────────────────────────────────────────────────────────────

async def make(user: User, data: SlipCreateIn) -> HeldBill:
    """The Pharmacist's bill as a slip. Only Pharmacy Items they may sell, nothing coming back, each at the Item's own
    price (its piece, strip, pack or box price when sold that way). The total is what the cash counter will charge for
    it: the Items' own discounts, stopped at their floors, and GST, to the rupee. No bill discount: that is the cash
    counter's to give."""
    from app.services.sales_service import _item_disc, _line_gross

    if not data.lines:
        raise SlipError("Ring up at least one Pharmacy Item for the slip.")
    if data.billId:
        # The same bill printed again (a reply that never arrived, a second press): the slip already made.
        for earlier in await HeldBill.filter(kind=KIND, made_by_id=user.id, held_at__gte=_now() - _REPRINT_WINDOW):
            if (earlier.slip or {}).get("billId") == data.billId:
                return await HeldBill.get(id=earlier.id).prefetch_related("made_by")
    pharmacy_departments = await pharmacy_service.departments()
    products = {p.id: p for p in await Product.filter(id__in=list({line.productId for line in data.lines}))}
    lines: list[dict] = []
    figures: list[sale_rules.LineIn] = []
    for line in data.lines:
        product = products.get(line.productId)
        if not product:
            raise SlipError("An Item on this slip isn't in the Item list any more. Take it off and ring it up again.")
        if line.isReturn:
            raise SlipError("A slip can't take anything back. Returns are taken at the cash counter.")
        if not (pharmacy_service.is_pharmacy(product, pharmacy_departments)
                and pharmacy_service.may_sell_item(user, product, pharmacy_departments)):
            # As at the till: only that it can't, not why.
            raise SlipError(f"{product.name} can't go on a pharmacy slip. Take it off and try again.", 403)
        alias = await ProductAlias.get_or_none(code=line.aliasCode.strip(), product_id=product.id) if line.aliasCode else None
        level, count, level_price = line.level, None, None
        if level:
            if not sell_levels.sells_as(product, level):
                raise SlipError(f"{product.name} isn't sold by the {level}. Take the line off and ring it up again.")
            count = line.levelQty
            if count is None or count <= 0 or count != count.to_integral_value():
                raise SlipError(f"{product.name}: a {level} line needs a whole number of {level}{'es' if level == 'box' else 's'}.")
            level_price = sell_levels.own_price(product, level)
            qty = sell_levels.units_for(product, level, count)
            unit_price = sell_levels.unit_price(count * level_price, qty)
        else:
            qty = Decimal(line.qty)
            if qty <= 0:
                raise SlipError(f"{product.name}: the quantity has to be more than nothing.")
            unit_price = Decimal(product.price)
        priced = SaleLineIn(
            productId=product.id, qty=qty, unitPrice=unit_price, aliasCode=alias.code if alias else None,
            level=level, levelQty=count, levelPrice=level_price,
        )
        gross = _line_gross(priced, unit_price)
        figures.append(sale_rules.LineIn(
            name=product.name, is_return=False, gross=gross, item_disc=_item_disc(priced, product, alias),
            tax_rate=product.tax_rate, qty=qty, avg_cost=product.avg_cost, locked=product.lock_disc, own_price=True,
        ))
        lines.append(SaleLineOut(
            productId=product.id, name=product.name, sku=product.sku, qty=qty, unitPrice=unit_price,
            isWeighed=product.is_weighed, isReturn=False, aliasCode=alias.code if alias else None,
            level=level, levelQty=count, levelPrice=level_price, levelDetail=sell_levels.detail(product, level) if level else None,
        ).model_dump(mode="json"))
    bill = sale_rules.figures(figures, ZERO, ZERO)
    gross = sum((f.gross for f in figures), ZERO)
    disc = sum(bill.item_discs, ZERO)
    gst = sum(((f.gross - d) * Decimal(f.tax_rate or 0) / Decimal("100") for f, d in zip(figures, bill.item_discs)), ZERO)
    total = (gross - disc + gst).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    # Every line is the Pharmacist's to sell. Marked so, as a held bill's lines are.
    lines = await pharmacy_service.mark_held_lines(lines, user, None, None)
    cent = Decimal("0.01")
    async with in_transaction():
        number = number_of(await next_value("slip", 1))
        slip = await HeldBill.create(
            kind=KIND, number=number, label=f"Slip {number}", lines=lines, made_by=user, status=OPEN,
            slip={
                "gross": format(gross.quantize(cent), "f"), "disc": format(disc.quantize(cent), "f"),
                "gst": format(gst.quantize(cent), "f"), "total": format(total, "f"), "billId": data.billId, "madeByName": user.name,
            },
        )
    return await HeldBill.get(id=slip.id).prefetch_related("made_by")


# ── Finding and listing ──────────────────────────────────────────────────────────────────────────

async def _find(code: str | None, slip_id: str | None) -> HeldBill:
    slip = None
    if slip_id:
        try:
            slip = await HeldBill.get_or_none(id=uuid.UUID(str(slip_id)), kind=KIND).prefetch_related("made_by")
        except ValueError:
            slip = None
        if not slip:
            raise SlipError("That slip is no longer there. Refresh the list.", 404)
        return slip
    number = normalise_number(code)
    if number:
        slip = await HeldBill.get_or_none(number=number, kind=KIND).prefetch_related("made_by")
    if not slip:
        raise SlipError(f"No slip {number or (code or '').strip()} at this branch. Check the number on the slip.", 404)
    return slip


def why_not_payable(slip: HeldBill) -> str:
    """Why this slip can't be paid, in the cashier's words: who took its payment and when, or who cancelled it and why."""
    d = slip.slip or {}
    if slip.status == PAID:
        who = _paid_by_name(d)
        return (f"Slip {slip.number} was already paid on {d.get('paidInvoice') or 'another bill'} at {_clock(d.get('paidAt'))}"
                f"{f', taken by {who}' if who else ''}.")
    if slip.status == CANCELLED:
        by = f" by {d['cancelledByName']}" if d.get("cancelledByName") else ""
        reason = f": {d['reason']}" if d.get("reason") else ""
        return f"Slip {slip.number} was cancelled{by} at {_clock(d.get('cancelledAt'))}{reason}."
    return f"Slip {slip.number} is waiting to be paid."


async def lookup(code: str) -> HeldBill:
    """A slip by the number printed on it, for the cash counter's payment dialog."""
    return await _find(code, None)


def _on_day(value, day: date) -> bool:
    from app.core.pk_time import pk_day

    at = _when(value)
    return bool(at) and pk_day(at) == day


async def list_slips(user: User, mine_only: bool, status: str, day: date) -> list[HeldBill]:
    """Newest first. Open ones whatever day they were printed (they are still waiting); paid and cancelled ones by the
    day that happened. "all" is both. A Pharmacist sees their own only."""
    qs = HeldBill.filter(kind=KIND)
    if mine_only:
        qs = qs.filter(made_by_id=user.id)
    out: list[HeldBill] = []
    if status in ("open", "all"):
        out += await qs.filter(status__in=PAYABLE).order_by("-held_at").prefetch_related("made_by")
    done = {"paid": (PAID,), "cancelled": (CANCELLED,), "all": (PAID, CANCELLED)}.get(status, ())
    if done:
        from app.core.pk_time import day_start

        since = day_start(day) - _LIST_LOOKBACK
        until = day_start(day + timedelta(days=1))
        for slip in await qs.filter(status__in=done, held_at__gte=since, held_at__lt=until).order_by("-held_at").prefetch_related("made_by"):
            d = slip.slip or {}
            if _on_day(d.get("paidAt") if slip.status == PAID else d.get("cancelledAt"), day):
                out.append(slip)
    return out


# ── At the cash counter ──────────────────────────────────────────────────────────────────────────

def _sale_lines(slip: HeldBill) -> list[SaleLineIn]:
    """The slip's own lines at the slip's own prices, as the sale takes them."""
    return [
        SaleLineIn(
            productId=str(line["productId"]), qty=Decimal(str(line["qty"])), unitPrice=Decimal(str(line["unitPrice"])),
            aliasCode=line.get("aliasCode"), level=line.get("level"),
            levelQty=Decimal(str(line["levelQty"])) if line.get("levelQty") is not None else None,
            levelPrice=Decimal(str(line["levelPrice"])) if line.get("levelPrice") is not None else None,
        )
        for line in slip.lines
    ]


async def _slip_of(sale: SaleRecord) -> HeldBill | None:
    numbers = pharmacy_service.slip_numbers(sale.slips)
    return await HeldBill.get_or_none(number=numbers[0], kind=KIND).prefetch_related("made_by") if numbers else None


def _for_counter(message: str, slip: HeldBill, names: list[str], user: User) -> str:
    """A sale's refusal as the person paying the slip may hear it: never naming an Item they don't sell. The Branch
    Manager hears it as it is."""
    if not pharmacy_service.hides_pharmacy(user):
        return message
    if message.startswith("Not enough stock"):
        count = message.count("; ") + 1
        return (f"Slip {slip.number} can't be paid yet: the records show too little stock of {count} Item"
                f"{'' if count == 1 else 's'} on it. Ask the Pharmacist to check, or the Branch Manager.")
    if any(name and name in message for name in names):
        return f"Slip {slip.number} can't be paid at this counter. Ask the Branch Manager to take its payment."
    return message


async def _record_scans(user: User, slip: HeldBill, sale: SaleRecord, till) -> None:
    """Scan history: the slip's lines, as paid at this counter by this person, sold on this bill."""
    from app.core.device_context import get_device_id
    from app.models import ScanEvent, ScanLine

    now = _now()
    device = get_device_id()
    for key, line in enumerate(slip.lines, start=1):
        qty = Decimal(str(line.get("levelQty") if line.get("level") and line.get("levelQty") is not None else line.get("qty") or 0))
        scan = await ScanLine.create(
            bill_id=sale.client_request_id or str(sale.id), line_key=key, product_id=str(line["productId"]), user=user,
            till_session=till, counter_id=till.counter_id if till else None, device_id=device, how="slip",
            first_at=now, last_at=now, qty=qty, level=line.get("level"), outcome="sold", ended_at=now,
            invoice_number=sale.invoice_number,
        )
        await ScanEvent.create(id=uuid.uuid4().hex, line=scan, at=now, action="added", qty=qty, level=line.get("level"))


async def pay(user: User, code: str, data: SlipPayIn) -> tuple[HeldBill, SaleRecord]:
    """Takes an open slip's payment, on its own: a sale of the slip's lines at the slip's prices, by this person, into
    their till. Sending the same payment again (its clientRequestId) hands back the one already taken."""
    from app.services import sales_service, till_service

    earlier = await SaleRecord.get_or_none(client_request_id=data.clientRequestId)
    if earlier:
        slip = await _slip_of(earlier)
        if slip and slip.number == normalise_number(code):
            return slip, earlier
        raise SlipError("This payment was already used for another bill. Close the dialog and scan the slip again.", 409)

    slip = await _find(code, None)
    if slip.status not in PAYABLE:
        raise SlipError(why_not_payable(slip), 409)
    tenders = {c.strip().upper(): Decimal(a) for c, a in data.tenders.items() if Decimal(a) > 0}
    if any(c not in PAY_TENDERS for c in tenders):
        raise SlipError("A slip is paid in cash, by card or online. Take the other payment off.")
    if not tenders:
        raise SlipError("Enter what the customer is paying.")
    till = await till_service.session_for(user)
    if not till:
        raise SlipError("No Till is open. Open one at your counter before taking payment.", 409)
    total = _money((slip.slip or {}).get("total"))
    received = sum(tenders.values(), ZERO)
    if received < total:
        raise SlipError(f"Payment not covered. Still to pay: {sale_rules.rs(total - received)}")
    if received - total > tenders.get("CASH", ZERO):
        raise SlipError("Only cash gives change back. Lower the card or online amount to what is due.")

    made = {"number": slip.number, "pharmacistId": str(slip.made_by_id) if slip.made_by_id else None, "pharmacistName": maker_name(slip)}
    request = SaleCreateRequest(
        lines=_sale_lines(slip), tenders=tenders, tenderDetails=data.tenderDetails, member=data.member,
        clientRequestId=data.clientRequestId,
    )
    names = [p.name for p in await Product.filter(id__in=list({str(line["productId"]) for line in slip.lines}))]
    try:
        async with in_transaction():
            # Off the open list first, in this same transaction, so two counters can't both be paid for it.
            if not await HeldBill.filter(id=slip.id, status__in=PAYABLE).update(status=PAID):
                await slip.refresh_from_db()
                raise SlipError(why_not_payable(slip), 409)
            sale = await sales_service.create_sale(user, request, slip=made)
            if Decimal(sale.net_value) != total:
                raise SlipError(
                    f"Slip {slip.number} comes to {sale_rules.rs(sale.net_value)} now, not the {sale_rules.rs(total)} printed on it: "
                    "an Item's discount or GST changed since. Ask the Pharmacist to print it again.", 409,
                )
            methods = {m.code: m.name for m in await PaymentMethod.all()}
            paid_tenders = [
                {"code": t.code, "name": methods.get(t.code, t.code.title()), "amount": format(Decimal(t.amount), "f"),
                 "reference": t.reference or t.transaction_id}
                for t in sale.tenders
            ]
            d = {k: v for k, v in (slip.slip or {}).items() if k not in ("takenBy", "takenByName", "takenAt")}
            slip.status, slip.on_bill = PAID, None
            slip.slip = {
                **d, "paidInvoice": sale.invoice_number, "paidSaleId": str(sale.id), "paidAt": _now().isoformat(),
                "paidBy": str(user.id), "paidByName": user.name, "tenders": paid_tenders,
                "received": format(Decimal(sale.received), "f"), "change": format(Decimal(sale.cash_back), "f"),
            }
            await slip.save(update_fields=["status", "on_bill", "slip"])
            await _record_scans(user, slip, sale, till)
    except sales_service.SaleError as exc:
        raise SlipError(_for_counter(exc.message, slip, names, user), exc.status) from exc
    return await HeldBill.get(id=slip.id).prefetch_related("made_by"), sale


# ── Cancelling ────────────────────────────────────────────────────────────────────────────────────

async def cancel(user: User, slip_id: str, reason: str) -> HeldBill:
    """An open slip the customer won't pay for. The Pharmacist who made it, or a Branch Manager, with a reason."""
    from app.services.rbac_service import has_permission

    slip = await _find(None, slip_id)
    mine = str(slip.made_by_id) == str(user.id)
    if not (user.role_id == BRANCH_MANAGER or (mine and await has_permission(user, "store.slips", "W"))):
        raise SlipError("Only the Pharmacist who made a slip, or a Branch Manager, can cancel it.", 403)
    d = slip.slip or {}
    if slip.status == PAID:
        raise SlipError(f"Slip {slip.number} was paid on {d.get('paidInvoice')} at {_clock(d.get('paidAt'))}, so it can't be cancelled.", 409)
    if slip.status == CANCELLED:
        raise SlipError(f"Slip {slip.number} is already cancelled.", 409)
    reason = " ".join((reason or "").split())
    if len(reason) < 3:
        raise SlipError("Say why the slip is cancelled, in a few words.")
    if not await HeldBill.filter(id=slip.id, status__in=PAYABLE).update(status=CANCELLED, on_bill=None):
        await slip.refresh_from_db()
        raise SlipError(why_not_payable(slip), 409)
    slip.slip = {**d, "cancelledBy": str(user.id), "cancelledByName": user.name, "cancelledAt": _now().isoformat(), "reason": reason[:120]}
    await slip.save(update_fields=["slip"])
    return await HeldBill.get(id=slip.id).prefetch_related("made_by")
