"""The posting run — the branch's records become automatic vouchers.

One voucher per source, regenerated whenever the source changes, never touched once its month is closed:

  sales-day:<day>            SV   the day's bills and returns: payments in, sales by department, GST, delivery,
                                  discounts, rounding, cost of goods sold, loyalty points earned and taken back
  till-open:<id> / till-close:<id>   TV   float from the safe into the counter; counted cash back to the safe, short/over
  cash-move:<id>             CPV / CRV   a till Cash Out (expense, supplier, safe) or Cash In
  customer-payment:<id>      BRV  a credit customer paying by card, bank or wallet (cash ones come through the till)
  grn:<id>                   PV   goods received: stock, GST input, advance tax — owed to the supplier
  purchase-return:<id>       PRV  goods sent back: the supplier owes it back
  stock-corrections:<day>    STV  approved counts and adjustments at cost
  transfer-out / transfer-out-received / transfer-out-settled / transfer-in:<id>   TRV   stock in transit, at cost
  gift-voucher:<id> / gift-voucher-expiry:<id>   GVV  vouchers sold other than for cash, given free, or expired
  cheque-received / cheque-cleared / cheque-bounced:<id>   JV / BRV

Days are the shop's days (Pakistan time). Nothing before the books' start day is posted — that's what opening
balances are for.
"""
import asyncio
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from app.models import (
    GRN,
    Account,
    CashMovement,
    Cheque,
    CustomerPayment,
    GiftVoucher,
    LoyaltyEntry,
    Party,
    Product,
    PurchaseReturn,
    ReturnRecord,
    SaleLine,
    SaleRecord,
    StockMovement,
    Supplier,
    TillSession,
    Transfer,
    Voucher,
)
from app.services import vouchers_service
from app.services.accounts_chart_service import Resolver, money
from app.services.accounts_reports_service import PKT, shop_day
from app.services.till_service import LINKED_FROM

ZERO = Decimal("0")
WINDOW_DAYS = 10
_lock = asyncio.Lock()


def bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """The UTC instants a run of shop days covers."""
    return (datetime.combine(start, time.min, tzinfo=PKT).astimezone(timezone.utc),
            datetime.combine(end + timedelta(days=1), time.min, tzinfo=PKT).astimezone(timezone.utc))


def _cost(qty, unit_cost, product: Product | None) -> Decimal:
    cost = unit_cost if unit_cost is not None else (product.avg_cost if product and product.avg_cost is not None else ZERO)
    return Decimal(qty) * Decimal(cost)


class Run:
    def __init__(self, settings, start: date, end: date) -> None:
        self.settings = settings
        self.start = start
        self.end = end
        self.accounts = Resolver()
        self.counts: Counter = Counter()
        self.problems: list[str] = []
        self.seen: set[str] = set()

    async def put(self, source: str, vtype: str, day: date, rows: list, description: str, reference: str | None = None, header: Account | None = None) -> None:
        self.seen.add(source)
        if day < self.settings.books_start:
            return
        try:
            async with in_transaction():
                outcome = await vouchers_service.upsert_auto(source, vtype, day, rows, description, reference, header, self.settings)
        except vouchers_service.VoucherError as exc:
            self.problems.append(exc.message)
            return
        self.counts[outcome] += 1
        if outcome == "locked":
            self.problems.append(f"{source} changed after its month was closed — the closed books keep the old figures.")

    async def drop(self, source: str) -> None:
        async with in_transaction():
            outcome = await vouchers_service.delete_auto(source, self.settings)
        if outcome != "empty":
            self.counts[outcome] += 1


# ── sales and returns, one voucher a day ─────────────────────────────────────────────────────────

async def _sales_days(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    tenders_map = run.settings.tender_accounts or {}
    days: dict[date, list] = defaultdict(list)
    info: dict[date, dict] = defaultdict(lambda: {"bills": 0, "returns": 0, "first": None, "last": None})

    stock = await acc.key("stock.main")
    sales = await SaleRecord.filter(at__gte=lo, at__lt=hi).order_by("at").prefetch_related("lines__product", "tenders", "party")
    for sale in sales:
        day = shop_day(sale.at)
        rows = days[day]
        meta = info[day]
        meta["bills"] += 1
        meta["first"] = meta["first"] or sale.invoice_number
        meta["last"] = sale.invoice_number
        for tender in sale.tenders:
            amount = Decimal(tender.amount)
            if tender.code == "CASH":
                amount -= Decimal(sale.cash_back or 0)
                rows.append((await acc.key("cash.counter"), amount, ZERO, "Cash sales"))
            elif tender.code == "CREDIT":
                rows.append((await acc.customer(sale.party), amount, ZERO, "Credit sales"))
            else:
                label = {"CARD": "Card payments", "VOUCHER": "Gift vouchers taken", "POINTS": "Points spent"}.get(tender.code, f"{tender.code.title()} payments")
                rows.append((await acc.tender(tender.code, tenders_map, tender.account), amount, ZERO, label))
        for line in sale.lines:
            product = line.product
            gross = Decimal(line.qty) * Decimal(line.unit_price)
            cost = _cost(line.qty, line.unit_cost, product)
            sales_acc = await acc.department(product.department)
            cogs_acc = await acc.department(product.department, cost=True)
            if line.is_return:
                rows.append((await acc.key("sales.returns"), gross, ZERO, "Returned on the bill"))
                rows.append((stock, cost, ZERO, "Stock returned"))
                rows.append((cogs_acc, ZERO, cost, "Cost of goods returned"))
            else:
                rows.append((sales_acc, ZERO, gross, "Sales"))
                rows.append((cogs_acc, cost, ZERO, "Cost of goods sold"))
                rows.append((stock, ZERO, cost, "Stock sold"))
        rows.append((await acc.key("sales.discounts"), Decimal(sale.disc_total), ZERO, "Discounts given"))
        rows.append((await acc.key("tax.gst_output"), ZERO, Decimal(sale.gst), "GST on sales"))
        if sale.fare:
            rows.append((await acc.key("income.delivery"), ZERO, Decimal(sale.fare), "Delivery charges"))
        rounding = Decimal(sale.net_value) - Decimal(sale.grand_total)
        if rounding:
            rows.append((await acc.key("income.rounding"), ZERO, rounding, "Bill rounding"))

    returns = await ReturnRecord.filter(at__gte=lo, at__lt=hi).order_by("at").prefetch_related("lines__product", "against__party")
    for ret in returns:
        day = shop_day(ret.at)
        rows = days[day]
        info[day]["returns"] += 1
        sale = ret.against
        sold_cost: dict[str, Decimal | None] = {}
        for sl in await SaleLine.filter(sale_id=sale.id, is_return=False):
            sold_cost.setdefault(str(sl.product_id), sl.unit_cost)
        total_value = ZERO
        for line in ret.lines:
            value = Decimal(line.qty) * Decimal(line.unit_price)
            tax = Decimal(line.tax_amount) if line.tax_amount is not None else ZERO
            total_value += value
            rows.append((await acc.key("sales.returns"), value - tax, ZERO, "Goods returned"))
            if tax:
                rows.append((await acc.key("tax.gst_output"), tax, ZERO, "GST on returns"))
            unit_cost = line.unit_cost if line.unit_cost is not None else sold_cost.get(str(line.product_id))
            cost = _cost(line.qty, unit_cost, line.product)
            rows.append((stock, cost, ZERO, "Stock returned"))
            rows.append((await acc.department(line.product.department, cost=True), ZERO, cost, "Cost of goods returned"))
        refund = Decimal(ret.refund_total)
        rounding = refund - total_value
        if rounding:
            rows.append((await acc.key("income.rounding"), rounding, ZERO, "Refund rounding"))
        method = (ret.refund_method or "CASH").upper()
        if method == "CASH":
            credit_acc, label = await acc.key("cash.counter"), "Cash refunds"
        elif method == "CREDIT":
            credit_acc, label = await acc.customer(sale.party), "Returns off customer balances"
        elif method == "VOUCHER":
            credit_acc, label = await acc.key("liab.gift_vouchers"), "Refunded to gift vouchers"
        else:
            credit_acc, label = await acc.tender(method, tenders_map), f"{method.title()} refunds"
        rows.append((credit_acc, ZERO, refund, label))

    code = await _branch_code()
    loyalty = await _loyalty_settings()
    point_value = Decimal(loyalty.point_value) if loyalty else ZERO
    if point_value:
        for entry in await LoyaltyEntry.filter(at__gte=lo, at__lt=hi, branch_code=code, kind__in=["earn", "reverse", "adjust"]):
            value = Decimal(entry.points) * point_value
            if not value:
                continue
            day = shop_day(entry.at)
            label = "Points earned" if value > 0 else "Points taken back"
            rows = days[day]
            rows.append((await acc.key("expense.loyalty"), value, ZERO, label))
            rows.append((await acc.key("liab.loyalty"), ZERO, value, label))

    for day in sorted(days):
        meta = info[day]
        parts = [f"{meta['bills']} bill{'s' if meta['bills'] != 1 else ''}"] if meta["bills"] else []
        if meta["returns"]:
            parts.append(f"{meta['returns']} return{'s' if meta['returns'] != 1 else ''}")
        reference = f"{meta['first']} to {meta['last']}" if meta["first"] and meta["first"] != meta["last"] else meta["first"]
        await run.put(f"sales-day:{day.isoformat()}", "SV", day, days[day], f"Sales for {day:%d %b %Y}: {', '.join(parts) or 'points only'}", reference)


async def _branch_code() -> str:
    from app.services import members_service

    return await members_service.branch_code()


async def _loyalty_settings():
    from app.services import members_service

    return await members_service.settings()


# ── the till ───────────────────────────────────────────────────────────────────────────────────

async def _tills(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    counter, safe = await acc.key("cash.counter"), await acc.key("cash.main")
    for till in await TillSession.filter(opened_at__gte=lo, opened_at__lt=hi):
        if till.opening_float:
            await run.put(f"till-open:{till.id}", "TV", shop_day(till.opened_at),
                          [(counter, Decimal(till.opening_float), ZERO, "Opening float"), (safe, ZERO, Decimal(till.opening_float), "Opening float")],
                          f"Till {till.session_number} opened with its float", till.session_number, counter)
    for till in await TillSession.filter(status="closed", closed_at__gte=lo, closed_at__lt=hi):
        expected = Decimal(till.opening_float)
        # This drawer's own bills. Several tills can be open at once, so a bill rung at the next counter during the
        # same hours is not this drawer's cash. Drawers opened before bills carried their till also take the bills
        # rung while they were open that belong to no till — the only way to tell back when one till was open.
        mine = Q(till_session_id=till.id)
        if till.opened_at < LINKED_FROM:
            mine |= Q(till_session_id=None, at__gte=till.opened_at, at__lte=till.closed_at)
        for sale in await SaleRecord.filter(mine).prefetch_related("tenders"):
            for tender in sale.tenders:
                if tender.code == "CASH":
                    expected += Decimal(tender.amount) - Decimal(sale.cash_back or 0)
        for ret in await ReturnRecord.filter(mine, refund_method="CASH"):
            expected -= Decimal(ret.refund_total)
        for movement in await CashMovement.filter(till_session=till):
            expected += Decimal(movement.amount) if movement.kind == "in" else -Decimal(movement.amount)
        counted = Decimal(till.counted_cash or 0)
        # Short or over is what the till's own close said (counted against its Net Cash) — the figure on the X/Z and
        # Day Close. The counter account is emptied by what the recorded sales, refunds and cash in/out put in it; if
        # the till's Net Cash disagrees with those records by more than bill rounding, the gap is shown on its own.
        variance = Decimal(till.variance) if till.variance is not None else counted - expected
        rows = [(safe, counted, ZERO, "Counted cash to the safe"), (counter, ZERO, expected, "Till emptied at close")]
        if variance < 0:
            rows.append((await acc.key("expense.cash_short"), -variance, ZERO, "Till short"))
        elif variance > 0:
            rows.append((await acc.key("income.cash_over"), ZERO, variance, "Till over"))
        gap = expected - (counted - variance)
        if gap:
            gap_acc = await acc.key("income.rounding") if abs(gap) <= Decimal("5") else await acc.key("expense.till_reconciliation")
            label = "Bill rounding in the till" if abs(gap) <= Decimal("5") else "Till's Net Cash differs from the recorded sales"
            rows.append((gap_acc, gap, ZERO, label))
        difference = variance
        await run.put(f"till-close:{till.id}", "TV", shop_day(till.closed_at), rows,
                      f"Till {till.session_number} closed: counted Rs {counted:,.0f}"
                      + (f", short Rs {-difference:,.2f}" if difference < 0 else f", over Rs {difference:,.2f}" if difference > 0 else ""),
                      till.session_number, counter)


async def _cash_movements(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    counter = await acc.key("cash.counter")
    for movement in await CashMovement.filter(at__gte=lo, at__lt=hi).prefetch_related("account", "till_session"):
        amount = Decimal(movement.amount)
        what = movement.account
        note = (movement.notes or "").strip()
        if movement.kind == "out":
            what = what or await acc.key("expense.petty")
            label = f"Cash out: {movement.payee or note or what.name}"
            rows = [(what, amount, ZERO, (note or movement.payee or None)), (counter, ZERO, amount, "__header__")]
            vtype = "CPV"
        else:
            what = what or await acc.key("cash.main")
            label = f"Cash in: {note or movement.payee or what.name}"
            rows = [(counter, amount, ZERO, "__header__"), (what, ZERO, amount, (note or movement.payee or None))]
            vtype = "CRV"
        await run.put(f"cash-move:{movement.id}", vtype, shop_day(movement.at), rows, label[:500], movement.till_session.session_number, counter)


async def _customer_payments(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    for payment in await CustomerPayment.filter(at__gte=lo, at__lt=hi, method__not="CASH").prefetch_related("party"):
        money_acc = await acc.tender(payment.method, run.settings.tender_accounts or {}, payment.reference)
        await run.put(f"customer-payment:{payment.id}", "BRV", shop_day(payment.at),
                      [(money_acc, Decimal(payment.amount), ZERO, "__header__"), (await acc.customer(payment.party), ZERO, Decimal(payment.amount), payment.reference)],
                      f"{payment.number}: {payment.party.name} paid by {payment.method.title()}", payment.number, money_acc)


# ── buying ─────────────────────────────────────────────────────────────────────────────────────

def grn_line_net(line) -> Decimal:
    gross = Decimal(line.qty) * Decimal(line.unit_price) * (Decimal("1") - Decimal(line.disc_percent or 0) / Decimal("100"))
    return max(ZERO, gross - Decimal(line.flat_disc or 0) + Decimal(line.misc or 0))


async def _grns(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    for grn in await GRN.filter(at__gte=lo, at__lt=hi).prefetch_related("lines", "supplier"):
        net = sum((grn_line_net(l) for l in grn.lines), ZERO)
        gst = sum((grn_line_net(l) * Decimal(l.tax_rate or 0) / Decimal("100") for l in grn.lines), ZERO)
        advance = Decimal(grn.advance_tax or 0)
        rows = [
            (await acc.key("stock.main"), net, ZERO, "Goods received"),
            (await acc.key("tax.gst_input"), gst, ZERO, "GST on purchases"),
            (await acc.key("tax.advance"), advance, ZERO, "Advance tax"),
            (await acc.supplier(grn.supplier), ZERO, net + gst + advance, f"Bill {grn.party_inv_no}" if grn.party_inv_no else "Goods received"),
        ]
        await run.put(f"grn:{grn.id}", "PV", shop_day(grn.at), rows,
                      f"{grn.grn_number} from {grn.supplier.name}" + (f", their bill {grn.party_inv_no}" if grn.party_inv_no else ""),
                      grn.party_inv_no or grn.grn_number)


async def _purchase_returns(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    for ret in await PurchaseReturn.filter(at__gte=lo, at__lt=hi).prefetch_related("lines__product", "supplier"):
        value = sum((Decimal(l.qty) * Decimal(l.unit_price) for l in ret.lines), ZERO)
        tax = sum((Decimal(l.qty) * Decimal(l.unit_price) * Decimal(l.tax_rate if l.tax_rate is not None else l.product.tax_rate or 0) / Decimal("100") for l in ret.lines), ZERO)
        cost = sum((_cost(l.qty, l.unit_cost, l.product) for l in ret.lines), ZERO)
        rows = [
            (await acc.supplier(ret.supplier), value + tax, ZERO, "Goods sent back"),
            (await acc.key("stock.main"), ZERO, cost, "Goods sent back at cost"),
            (await acc.key("tax.gst_input"), ZERO, tax, "GST on goods sent back"),
        ]
        difference = value - cost
        if difference:
            rows.append((await acc.key("cogs.return_difference"), ZERO, difference, "Return price above cost" if difference > 0 else "Return price below cost"))
        await run.put(f"purchase-return:{ret.id}", "PRV", shop_day(ret.at), rows,
                      f"{ret.return_number} to {ret.supplier.name} ({ret.reason})", ret.return_number)


# ── stock corrections ──────────────────────────────────────────────────────────────────────────

async def _stock_corrections(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    stock = await acc.key("stock.main")
    days: dict[date, list] = defaultdict(list)
    items: Counter = Counter()
    for m in await StockMovement.filter(at__gte=lo, at__lt=hi, kind__in=["count-correction", "adjust"]).prefetch_related("product"):
        value = _cost(m.qty, m.unit_cost, m.product)
        if not value:
            continue
        day = shop_day(m.at)
        items[day] += 1
        if m.kind == "count-correction":
            other = await acc.key("loss.count") if value < 0 else await acc.key("income.stock_gain")
            label = "Count shortage" if value < 0 else "Found in count"
        else:
            reason = (m.reason or "").lower()
            other = await acc.key({"damage": "loss.damage", "expiry": "loss.expiry"}.get(reason, "income.stock_gain" if value > 0 else "loss.count"))
            label = {"damage": "Damaged stock written off", "expiry": "Expired stock written off", "found": "Stock found"}.get(reason, "Stock adjusted")
        if value < 0:
            days[day] += [(other, -value, ZERO, label), (stock, ZERO, -value, label)]
        else:
            days[day] += [(stock, value, ZERO, label), (other, ZERO, value, label)]
    for day, rows in days.items():
        await run.put(f"stock-corrections:{day.isoformat()}", "STV", day, rows, f"Stock corrections on {day:%d %b %Y}: {items[day]} item{'s' if items[day] != 1 else ''} at cost")


# ── transfers ──────────────────────────────────────────────────────────────────────────────────

async def _transfers(run: Run) -> None:
    acc = run.accounts
    stock, transit, head_office = await acc.key("stock.main"), await acc.key("stock.transit"), await acc.key("interoffice.head_office")
    lo, _ = bounds(run.settings.books_start, run.end)
    for transfer in await Transfer.exclude(origin="local").prefetch_related("lines__product"):
        lines = list(transfer.lines)
        received = transfer.status in ("received", "received_short")
        if transfer.direction == "outbound":
            if transfer.dispatched_at and transfer.status != "cancelled" and transfer.dispatched_at >= lo:
                sent = sum((_cost(l.qty_sent, l.unit_cost, l.product) for l in lines), ZERO)
                await run.put(f"transfer-out:{transfer.id}", "TRV", shop_day(transfer.dispatched_at),
                              [(transit, sent, ZERO, "On the road"), (stock, ZERO, sent, "Stock sent")],
                              f"{transfer.number} dispatched to {transfer.from_warehouse}", transfer.number)
            if received and transfer.dispatched_at and transfer.dispatched_at >= lo:
                arrived = sum((_cost(l.qty_received or 0, l.unit_cost, l.product) for l in lines), ZERO)
                when = transfer.received_at or transfer.updated_at
                await run.put(f"transfer-out-received:{transfer.id}", "TRV", shop_day(when),
                              [(head_office, arrived, ZERO, f"Received by {transfer.from_warehouse}"), (transit, ZERO, arrived, "Arrived")],
                              f"{transfer.number} received by {transfer.from_warehouse}", transfer.number)
                missing = sum((_cost(Decimal(l.qty_sent) - Decimal(l.qty_received or 0), l.unit_cost, l.product) for l in lines), ZERO)
                if missing > 0 and not transfer.dispute_open:
                    await run.put(f"transfer-out-settled:{transfer.id}", "TRV", shop_day(transfer.updated_at),
                                  [(await acc.key("loss.transit"), missing, ZERO, "Short on arrival"), (transit, ZERO, missing, "Short on arrival")],
                                  f"{transfer.number}: shortfall written off when the dispute was settled", transfer.number)
        elif received and transfer.received_at and transfer.received_at >= lo:
            arrived = sum((_cost(l.qty_received or 0, l.unit_cost, l.product) for l in lines), ZERO)
            await run.put(f"transfer-in:{transfer.id}", "TRV", shop_day(transfer.received_at),
                          [(stock, arrived, ZERO, "Stock received"), (head_office, ZERO, arrived, f"From {transfer.from_warehouse}")],
                          f"{transfer.number or 'Transfer'} received from {transfer.from_warehouse}", transfer.number)


# ── gift vouchers and cheques ──────────────────────────────────────────────────────────────────

async def _gift_vouchers(run: Run) -> None:
    lo, hi = bounds(run.start, run.end)
    acc = run.accounts
    outstanding = await acc.key("liab.gift_vouchers")
    for voucher in await GiftVoucher.filter(issued_at__gte=lo, issued_at__lt=hi):
        method = (voucher.paid_by or "").upper()
        if method == "CASH":
            continue  # came in through the till as a Cash In
        if method == "COMPLIMENTARY":
            debit, label = await acc.key("expense.complimentary_vouchers"), "Given free"
        elif method:
            debit, label = await acc.tender(method, run.settings.tender_accounts or {}, voucher.payment_reference), f"Paid by {method.title()}"
        else:
            debit, label = await acc.key("suspense"), "Payment not recorded (issued before payments were recorded)"
        amount = Decimal(voucher.face_value)
        await run.put(f"gift-voucher:{voucher.id}", "GVV", shop_day(voucher.issued_at),
                      [(debit, amount, ZERO, label), (outstanding, ZERO, amount, f"Voucher {voucher.code}")],
                      f"Gift voucher {voucher.code} issued" + (f" to {voucher.issued_to_name}" if voucher.issued_to_name else ""), voucher.code)
    now = datetime.now(timezone.utc)
    for voucher in await GiftVoucher.filter(expires_at__gte=lo, expires_at__lt=min(hi, now), balance__gt=0).exclude(status="cancelled"):
        amount = Decimal(voucher.balance)
        await run.put(f"gift-voucher-expiry:{voucher.id}", "GVV", shop_day(voucher.expires_at),
                      [(outstanding, amount, ZERO, f"Voucher {voucher.code} expired"), (await acc.key("income.voucher_expiry"), ZERO, amount, f"Voucher {voucher.code} expired")],
                      f"Gift voucher {voucher.code} expired unused (Rs {amount:,.0f})", voucher.code)


async def _cheques(run: Run) -> None:
    acc = run.accounts
    in_hand = await acc.key("cash.cheques")
    for cheque in await Cheque.filter(received_on__gte=run.settings.books_start).prefetch_related("party_account", "bank_account"):
        amount = Decimal(cheque.amount)
        source = f"cheque-received:{cheque.id}"
        if cheque.status == "cancelled":
            for prefix in ("cheque-received", "cheque-cleared", "cheque-bounced"):
                await run.drop(f"{prefix}:{cheque.id}")
            continue
        await run.put(source, "JV", cheque.received_on,
                      [(in_hand, amount, ZERO, f"Cheque {cheque.cheque_no}"), (cheque.party_account, ZERO, amount, f"Cheque {cheque.cheque_no}")],
                      f"{cheque.number}: cheque {cheque.cheque_no} from {cheque.party_account.name}" + (f" dated {cheque.cheque_date:%d %b %Y}" if cheque.cheque_date else ""), cheque.cheque_no)
        if cheque.cleared_on and cheque.bank_account:
            await run.put(f"cheque-cleared:{cheque.id}", "BRV", cheque.cleared_on,
                          [(cheque.bank_account, amount, ZERO, "__header__"), (in_hand, ZERO, amount, f"Cheque {cheque.cheque_no} cleared")],
                          f"{cheque.number}: cheque {cheque.cheque_no} cleared into {cheque.bank_account.name}", cheque.cheque_no, cheque.bank_account)
        if cheque.bounced_on:
            from_acc = cheque.bank_account if cheque.cleared_on and cheque.bank_account else in_hand
            await run.put(f"cheque-bounced:{cheque.id}", "JV", cheque.bounced_on,
                          [(cheque.party_account, amount, ZERO, f"Cheque {cheque.cheque_no} bounced"), (from_acc, ZERO, amount, f"Cheque {cheque.cheque_no} bounced")],
                          f"{cheque.number}: cheque {cheque.cheque_no} bounced", cheque.cheque_no)


SOURCES = (_sales_days, _tills, _cash_movements, _customer_payments, _grns, _purchase_returns, _stock_corrections, _transfers, _gift_vouchers, _cheques)
DATED_PREFIXES = ("sales-day:", "till-open:", "till-close:", "cash-move:", "customer-payment:", "grn:", "purchase-return:", "stock-corrections:", "gift-voucher:", "gift-voucher-expiry:")


async def first_record_day() -> date | None:
    firsts = []
    for model, field in ((SaleRecord, "at"), (GRN, "at"), (TillSession, "opened_at")):
        row = await model.all().order_by(field).first()
        if row:
            firsts.append(shop_day(getattr(row, field)))
    return min(firsts) if firsts else None


async def run(full: bool = False) -> dict:
    async with _lock:
        settings = await vouchers_service.settings()
        if settings.books_start is None:
            settings.books_start = await first_record_day() or shop_day()
            await settings.save()
            full = True
        today = shop_day()
        start = settings.books_start if full else max(settings.books_start, today - timedelta(days=WINDOW_DAYS))
        job = Run(settings, start, today)
        for source in SOURCES:
            try:
                await source(job)
            except Exception as exc:  # noqa: BLE001 — one broken source mustn't stop the rest posting
                job.problems.append(f"{source.__name__.strip('_').replace('_', ' ')}: {type(exc).__name__}: {exc}")
        if full:
            # Anything automatic that no longer has a source in range — the books' start moved later, say.
            for voucher in await Voucher.filter(auto=True).only("id", "source", "date"):
                if voucher.source and (voucher.date < settings.books_start or (voucher.source.startswith(DATED_PREFIXES) and voucher.source not in job.seen)):
                    await job.drop(voucher.source)
        else:
            lo_day = start
            for voucher in await Voucher.filter(auto=True, date__gte=lo_day).only("id", "source", "date"):
                if voucher.source and voucher.source.startswith(("sales-day:", "stock-corrections:")) and voucher.source not in job.seen:
                    await job.drop(voucher.source)
        settings = await vouchers_service.settings()
        before = (settings.posting_problems or [], settings.books_start)
        settings.last_posting_at = datetime.now(timezone.utc)
        changed = {k: v for k, v in job.counts.items() if k != "unchanged" and v}
        settings.last_posting_note = (
            ("Everything re-posted from the books' start. " if full else "")
            + (", ".join(f"{v} {k}" for k, v in sorted(changed.items())) if changed else "No changes.")
        )[:255]
        settings.posting_problems = job.problems[:50]
        await settings.save()
        if full or changed or before[0] != settings.posting_problems:
            await vouchers_service.emit_settings(settings)
        return {"full": full, "from": start.isoformat(), "to": today.isoformat(), "counts": dict(job.counts), "problems": job.problems[:50],
                "note": settings.last_posting_note}


async def ensure_recent(max_age_seconds: int = 90) -> None:
    """Reports call this so what's on screen includes the last few minutes' sales."""
    settings = await vouchers_service.settings()
    if settings.last_posting_at and (datetime.now(timezone.utc) - settings.last_posting_at).total_seconds() < max_age_seconds:
        return
    if _lock.locked():
        return
    await run(full=False)


# ── opening balances ───────────────────────────────────────────────────────────────────────────

async def opening_suggestion() -> dict:
    """What the opening balances would have to be for the books to agree with the branch's records today: stock at
    cost, what each credit customer owes, and gift vouchers still to be spent — less everything the automatic
    vouchers have already posted to those accounts since the books' start."""
    from app.services import accounts_reports_service, inventory_service

    await run(full=False)
    settings = await vouchers_service.settings()
    start = settings.books_start
    acc = Resolver()
    lines = []

    async def moved_since(account: Account) -> Decimal:
        rows = await accounts_reports_service._query(
            "SELECT SUM(l.debit) AS dr, SUM(l.credit) AS cr FROM acc_voucher_lines l JOIN acc_vouchers v ON v.id = l.voucher_id "
            "WHERE v.status = 'posted' AND v.vtype != 'OB' AND l.account_id = ? AND v.date >= ?", [str(account.id), start.isoformat()],
        )
        return Decimal(str(rows[0]["dr"] or 0)) - Decimal(str(rows[0]["cr"] or 0)) if rows else ZERO

    stock = await acc.key("stock.main")
    value_now = (await inventory_service.stock_value())["valueAtCost"]
    opening_stock = money(Decimal(value_now) - await moved_since(stock))
    if opening_stock:
        lines.append({"accountId": str(stock.id), "accountCode": stock.code, "accountName": stock.name,
                      "debit": format(max(opening_stock, ZERO), "f"), "credit": format(max(-opening_stock, ZERO), "f"),
                      "description": "Stock at cost on the books' start (so the books agree with today's stock report)"})
    for party in await Party.filter(is_walk_in=False).exclude(credit_balance=0):
        account = await acc.customer(party)
        owed = money(Decimal(party.credit_balance) - await moved_since(account))
        if owed:
            lines.append({"accountId": str(account.id), "accountCode": account.code, "accountName": account.name,
                          "debit": format(max(owed, ZERO), "f"), "credit": format(max(-owed, ZERO), "f"), "description": f"Owed by {party.name} ({party.code})"})
    lo, _ = bounds(start, start)
    outstanding = await acc.key("liab.gift_vouchers")
    held = ZERO
    for voucher in await GiftVoucher.filter(issued_at__lt=lo).exclude(status="cancelled"):
        spent_since = sum([r.amount for r in await voucher.redemptions.filter(at__gte=lo)], ZERO)
        held += Decimal(voucher.balance) + Decimal(spent_since)
    held = money(held)
    if held:
        lines.append({"accountId": str(outstanding.id), "accountCode": outstanding.code, "accountName": outstanding.name,
                      "debit": "0.00", "credit": format(held, "f"), "description": "Gift vouchers sold before the books' start, not yet spent"})
    return {"booksStart": start.isoformat(), "date": (start - timedelta(days=1)).isoformat(), "lines": lines,
            "note": "Cash in the safe, bank balances, supplier dues, fixed assets and capital aren't in the branch's records — add them."}
