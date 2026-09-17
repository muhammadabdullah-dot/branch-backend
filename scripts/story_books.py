"""Complete Model Town's story from the books' first day to 15 Sep 2026: everything a real branch does besides selling.

    python -m scripts.story_books            # dry run: says what it would do
    python -m scripts.story_books --yes      # writes it (stop the branch server and back up branch.db first)

The August and 1-12 September simulations produced sales, tills and a handful of deliveries, but no purchase orders,
no supplier payments, no running costs, no bank, and several figures that contradicted each other. This keeps every
user, Item, sale and return exactly as they are and adds the rest, dated where it would really have happened:

  repairs     the opening stock take moves to 31 Jul (the night before the books started) and comes into the books
              through Opening Balances as the owner's capital, instead of being owed to 786 Traders; stock
              movements carry the GRN they came from, and stock that arrived with no GRN gets one; till sessions
              stop overlapping, cover every bill of their day, and close on the cash their own records add up to;
              gift vouchers sold before payments were recorded come in through the till as cash
  buying      suppliers for every kind of Item; purchase orders raised by the Inventory Managers from what sold,
              approved by the Branch Manager, delivered against the order a few days later (some short, some with
              bonus units or a changed price, a few cancelled or closed short, the latest still on their way);
              supplier returns; urgent deliveries wherever an Item would otherwise have run out
  money       customers paying off credit by cash, bank transfer and cheque (one cheque bounces); safe cash banked;
              card and wallet takings settled to the bank less fees; petty cash; suppliers paid on their terms
  costs       rent (with withholding tax), salaries and utilities accrued and paid, depreciation, internet, cleaning,
              marketing, packaging, repairs, delivery, generator fuel, bank charges, owner's drawings
  books       opening balances on 31 Jul, every automatic voucher re-posted, numbers in date order, August closed

Real services do the work wherever one exists (purchase orders, GRNs, supplier returns, vouchers, cheques, customer
payments), so stock, average cost and supplier balances move exactly as they would from the screens; the script
only moves timestamps back to the day each thing happened. It refuses to run twice.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from tortoise import Tortoise
from tortoise.expressions import Q

from app.core.config import TORTOISE_ORM

STORY_KEY = "story:books-2026-09-15"
BOOKS_START = date(2026, 8, 1)
END = date(2026, 9, 15)
CLOSED_DAYS = {date(2026, 9, 13)}  # no till was opened: the shop didn't trade
PKT = timezone(timedelta(hours=5))
ZERO = Decimal("0")
RNG = random.Random(20260915)
NOW = datetime.now(timezone.utc)


def at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A moment in the shop's day. Nothing on the last day is later than a few minutes ago."""
    moment = datetime.combine(day, time(hour, minute, second), tzinfo=PKT).astimezone(timezone.utc)
    return min(moment, NOW - timedelta(minutes=20)) if day >= END else moment


def promised(day: date) -> datetime:
    """A date a supplier promised: noon that day, which may well be after today."""
    return datetime.combine(day, time(12), tzinfo=PKT).astimezone(timezone.utc)


def pkt_day(moment: datetime) -> date:
    return moment.astimezone(PKT).date()


def trading(day: date) -> date:
    while day in CLOSED_DAYS:
        day += timedelta(days=1)
    return day


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def whole(v, step: int = 1) -> Decimal:
    return (Decimal(str(v)) / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def stable(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


def say(text: str) -> None:
    print(f"  {text}", flush=True)


# ── suppliers ─────────────────────────────────────────────────────────────────────────────────────
# code: name, contact, phone, city, due days, NTN, sales tax no, trade discount %, order weekdays, lead days, fortnightly
SUPPLIERS = {
    "SUPGRO": ("Grocers United Distribution", "Waqas Ahmed", "0300-9988776", "Multan", 15, "4231876-2", "03-01-2100-455-19", 0, (0, 3), (1, 2), False),
    "SUPNEW1": ("Fresh Farms Distributors", "Bilal Khan", "0333-1112222", "Multan", 7, "5520913-6", "03-01-2100-871-44", 0, (1, 4), (1, 1), False),
    "SUP786": ("786 Traders (FINO)", "Fino Representative", "061-2116302", "Multan", 21, "3398120-4", "03-01-2100-102-77", 0, (5,), (1, 3), False),
    "SUPSHF": ("Shafi & Sons Distribution", "Imran Shafi", "061-4580112", "Multan", 15, "6674310-1", "03-01-2100-563-02", 0, (0,), (2, 3), False),
    "SUPCRS": ("Crescent Cosmetics & Toiletries", "Adeel Rana", "042-35761290", "Lahore", 21, "2219845-7", "03-11-9999-204-81", 3, (1,), (2, 4), False),
    "SUPPWS": ("Paper World Stationers", "Naveed Akhtar", "061-4511867", "Multan", 7, "7015532-9", None, 0, (2,), (1, 2), False),
    "SUPRGW": ("Rahat Garments Wholesale", "Khalid Rahat", "041-8712034", "Faisalabad", 30, "1187654-3", "03-10-6100-332-15", 5, (3,), (3, 5), True),
    "SUPTTI": ("Toy Town Importers", "Sohail Butt", "021-32415566", "Karachi", 30, "8843120-5", "03-12-9999-771-60", 2, (4,), (3, 6), True),
    "SUPLSB": ("Little Steps Baby Care", "Maria Aslam", "061-6223410", "Multan", 14, "3321907-8", "03-01-2100-918-33", 0, (5,), (2, 3), False),
    "SUPMMD": ("Multan Medical Distributors", "Dr Asif Qureshi", "061-4577809", "Multan", 10, "9120034-2", "03-01-2100-640-58", 0, (2,), (1, 2), False),
    "SUPAMT": ("Al-Madina Traders", "Haji Muhammad Saleem", "061-4543321", "Multan", 7, "5006781-4", "03-01-2100-229-90", 0, (2,), (1, 2), False),
}


def supplier_code_for(product) -> str:
    category = (product.category or "").upper().strip()
    department = (product.department or "").upper().strip()
    name = (product.name or "").lower()
    if department == "PHARMACY" or any(w in name for w in ("tablet", "panadol", "syrup", "capsule")):
        return "SUPMMD"
    if category in ("FRESH FOOD", "FROZEN FOOD") or any(w in name for w in ("egg", "banana", "tomato", "milk", "yogurt", "bread")):
        return "SUPNEW1"
    if category == "BEVERAGES" or any(w in name for w in ("cola", "pepsi", "juice", "water")):
        return "SUPAMT"
    if category == "FOOD" or any(w in name for w in ("rice", "chips", "flour", "biscuit")):
        return "SUPGRO" if stable(product.id) % 2 == 0 else "SUP786"
    if category in ("HOUSEHOLD", "HOMEWARE") or "soap" in name:
        return "SUPSHF"
    if category == "HEALTH & BEAUTY":
        return "SUPCRS"
    if category == "STATIONARY":
        return "SUPPWS"
    if category in ("MEN", "WOMEN"):
        return "SUPRGW"
    if category in ("TOYS", "BIRTHDAY ITEMS", "ENTERTAINMENTS"):
        return "SUPTTI"
    if category.startswith("BABY") or department == "KIDS CARE":
        return "SUPLSB"
    return "SUP786"


def pack_for(price: Decimal) -> int:
    if price < 150:
        return 24
    if price < 500:
        return 12
    if price < 2000:
        return 6
    return 2


# Till cash outs from the August simulation said only "petty cash": what each one really was.
PETTY_HEADS = [
    ("52010004", "Tea and biscuits for staff", "Al-Rehman Tea Stall"),
    ("52010003", "Receipt rolls and price stickers", "Paper World Stationers"),
    ("52050003", "Rickshaw to the bank and back", "Rickshaw"),
    ("52010006", "Washroom and floor cleaning supplies", "Hameed Cleaning Store"),
    ("52040003", "Generator diesel during load-shedding", "PSO Bosan Road"),
    ("52040002", "Electrician: tube lights replaced", "Saleem Electrician"),
    ("52010005", "Photocopies and courier of documents", "TCS Model Town"),
]

MONTHLY_SALARIES = Decimal("240000")
# Local suppliers whose bills are in whole rupees and who are happy to be paid in cash from the safe.
CASH_SUPPLIERS = ("SUPNEW1", "SUPAMT", "SUPPWS", "SUPSHF")


class Story:
    def __init__(self, cloud_db: str | None = None) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.notes: list[str] = []
        self.open_pos: list[str] = []
        self.cloud_db = cloud_db

    # ── context ─────────────────────────────────────────────────────────────────────────────────
    async def load(self) -> None:
        from app.models import Account, Location, User
        from app.services.accounts_chart_service import Resolver

        async def user(email: str):
            found = await User.get_or_none(email=email)
            if not found:
                raise SystemExit(f"Missing user {email}")
            return found

        self.bm = await user("branchmanager@branch.dmarina.pk")
        self.im = [await user("inventorymanager@branch.dmarina.pk"), await user("inventorymanager2@branch.dmarina.pk")]
        self.sk = [await user("stockkeeper@branch.dmarina.pk"), await user("stockkeeper2@branch.dmarina.pk")]
        self.cashiers = [await user(e) for e in ("cashier@branch.dmarina.pk", "cashier2@branch.dmarina.pk", "cashier3@branch.dmarina.pk", "ayesha@branch.dmarina.pk")]
        self.location = await Location.get(id="loc-1")
        self.acc = Resolver()
        self.A: dict[str, object] = {}
        for key in ("cash.main", "cash.petty", "cash.counter", "bank.main", "wallet.card", "wallet.easypaisa", "wallet.jazzcash", "tax.wht_payable",
                    "tax.gst_output", "tax.gst_input", "expense.bank_charges", "equity.capital", "liab.gift_vouchers", "stock.main"):
            self.A[key] = await self.acc.key(key)
        for code in ("52040001", "52020001", "52020002", "52020003", "52030001", "52030002", "52030003", "52030004", "52010003", "52010004", "52010005",
                     "52010006", "52010007", "52010008", "52060003", "52060004", "52040002", "52040003", "52050001", "52050003", "53010002", "21040001",
                     "21040002", "31010002", "12020001", "11060002", "11060003", "12010001", "12010002", "12010003", "12010005", "42010007"):
            self.A[code] = await Account.get(code=code)

    # ── 1. repairs ──────────────────────────────────────────────────────────────────────────────
    async def repair_opening_stock(self) -> None:
        from app.models import GRN, StockMovement, Supplier

        opening, _ = await Supplier.get_or_create(
            id="SUPOPEN", defaults={"code": "OPENING", "name": "Opening stock take", "due_days": 0, "city": "Multan", "active": False,
                                    "remarks": "Stock counted on 31 Jul 2026, the night before the books started"},
        )
        grn = await GRN.get(grn_number="GRN-0014")
        taken_at = at(date(2026, 7, 31), 21, 0)
        moved = await StockMovement.filter(Q(reason__isnull=True) | Q(reason=""), kind="receive", at=grn.at).update(at=taken_at, reason="GRN-0014")
        await GRN.filter(id=grn.id).update(at=taken_at, supplier_id=opening.id, party_inv_no="STOCK TAKE 31-JUL-2026", due_date=date(2026, 7, 31),
                                           received_by_id=self.sk[0].id)
        self.counts["opening stock take movements moved to 31 Jul"] = moved

    async def link_grn_movements(self) -> None:
        from app.models import GRN, GRNLine, StockMovement
        from app.services.accounts_posting_service import grn_line_net

        linked = 0
        for grn in await GRN.all():
            if await StockMovement.filter(reason=grn.grn_number).exists():
                continue
            day = pkt_day(grn.at)
            lo, hi = at(day - timedelta(days=1), 0), at(day + timedelta(days=2), 0)
            for line in await GRNLine.filter(grn=grn):
                incoming = Decimal(line.qty) + Decimal(line.bonus_qty or 0)
                movement = await StockMovement.filter(Q(reason__isnull=True) | Q(reason=""), kind="receive", product_id=line.product_id,
                                                      qty=incoming, at__gte=lo, at__lt=hi).order_by("at").first()
                if movement:
                    unit_cost = (grn_line_net(line) / incoming) if incoming else None
                    await StockMovement.filter(id=movement.id).update(reason=grn.grn_number, unit_cost=unit_cost, at=grn.at)
                    linked += 1
        self.counts["delivery stock movements linked to their GRN"] = linked

    async def classify_cash_outs(self) -> None:
        from app.models import CashMovement

        index = 0
        for movement in await CashMovement.filter(account_id__isnull=True).order_by("at"):
            note = (movement.notes or "").lower()
            if movement.kind == "out":
                code, text, payee = PETTY_HEADS[index % len(PETTY_HEADS)] if "petty" in note else ("52010004", "Tea for the stock count team", "Al-Rehman Tea Stall")
                index += 1
                await CashMovement.filter(id=movement.id).update(account_id=self.A[code].id, notes=text, payee=payee)
                self.counts["till cash outs given their expense"] += 1
            elif "float" not in note:
                await CashMovement.filter(id=movement.id).update(notes="Change from the safe")

    async def transfer_costs(self) -> None:
        """Godown transfers received before head office put its cost on them arrive at the godown's cost, so the stock
        has a value here and head office's account with this branch agrees with this branch's account with head office."""
        import sqlite3

        from app.models import Product, StockMovement, Transfer, TransferLine

        if not self.cloud_db:
            say("    no --cloud-db given: transfers keep whatever cost they have")
            return
        con = sqlite3.connect(f"file:{self.cloud_db}?mode=ro", uri=True)
        costs = {(number, pid): Decimal(str(cost)) for number, pid, cost in con.execute(
            "SELECT t.transfer_number, l.product_id, COALESCE(l.unit_cost, p.avg_cost) FROM transfer_lines l JOIN transfers t ON t.id = l.transfer_id "
            "JOIN products p ON p.id = l.product_id")}
        con.close()
        touched = set()
        for transfer in await Transfer.filter(origin="cloud"):
            for line in await TransferLine.filter(transfer_id=transfer.id, unit_cost__isnull=True):
                cost = costs.get((transfer.number, str(line.product_id)))
                if cost is None:
                    continue
                cost = cost.quantize(Decimal("0.0001"))
                await TransferLine.filter(id=line.id).update(unit_cost=cost)
                await StockMovement.filter(kind="transfer-in", reason=transfer.number, product_id=line.product_id).update(unit_cost=cost)
                touched.add(str(line.product_id))
                self.counts["godown transfer lines given head office's cost"] += 1
        for pid in touched:
            product = await Product.get(id=pid)
            if Decimal(product.avg_cost or 0) == 0:
                moves = await StockMovement.filter(kind="transfer-in", product_id=pid, unit_cost__isnull=False)
                qty = sum((Decimal(m.qty) for m in moves), ZERO)
                if qty > 0:
                    await Product.filter(id=pid).update(avg_cost=sum((Decimal(m.qty) * Decimal(m.unit_cost) for m in moves), ZERO) / qty)

    # ── 2. suppliers ────────────────────────────────────────────────────────────────────────────
    async def suppliers(self) -> None:
        from app.models import GRN, GRNLine, Product, ProductSupplier, SaleLine, Supplier
        from app.services.accounts_chart_service import supplier_account
        from app.services.accounts_posting_service import grn_line_net

        self.supplier = {}
        for code, (name, contact, phone, city, due, ntn, strn, *_rest) in SUPPLIERS.items():
            found = await Supplier.get_or_none(code=code)
            address = "Karachi, Sindh" if city == "Karachi" else f"{city}, Punjab"
            if found:
                await Supplier.filter(id=found.id).update(due_days=due, city=city, ntn=ntn, s_tax_reg_no=strn, contact_person=found.contact_person or contact,
                                                          phone=found.phone or phone, address=found.address or address)
                found = await Supplier.get(id=found.id)
            else:
                found = await Supplier.create(id=f"sup-{code.lower()}", code=code, name=name, contact_person=contact, phone=phone, city=city,
                                              due_days=due, ntn=ntn, s_tax_reg_no=strn, active=True, address=address)
                self.counts["suppliers added"] += 1
            await supplier_account(found)
            self.supplier[code] = found
        sold_ids = sorted({str(pid) for pid in await SaleLine.all().values_list("product_id", flat=True)})
        self.products = {p.id: p for p in await Product.filter(id__in=sold_ids)}
        self.product_supplier = {pid: supplier_code_for(p) for pid, p in self.products.items()}
        existing = {(str(r["product_id"]), str(r["supplier_id"])) for r in await ProductSupplier.all().values("product_id", "supplier_id")}
        for pid, code in self.product_supplier.items():
            if (pid, self.supplier[code].id) not in existing:
                await ProductSupplier.create(product_id=pid, supplier_id=self.supplier[code].id, priority=1)
                self.counts["Item supplier links"] += 1
        # GRNs from before the bill was kept on them get their totals and due date.
        for grn in await GRN.filter(net_total__isnull=True).prefetch_related("supplier"):
            lines = await GRNLine.filter(grn=grn)
            gross = sum((Decimal(l.qty) * Decimal(l.unit_price) for l in lines), ZERO)
            net = sum((grn_line_net(l) for l in lines), ZERO)
            tax = sum((grn_line_net(l) * Decimal(l.tax_rate or 0) / 100 for l in lines), ZERO)
            fields = {"gross_total": money(gross), "disc_total": money(gross - net), "tax_total": money(tax), "net_total": money(net + tax + Decimal(grn.advance_tax or 0))}
            if grn.grn_number != "GRN-0014":
                fields["due_date"] = pkt_day(grn.at) + timedelta(days=grn.supplier.due_days or 0)
                if not grn.party_inv_no:
                    fields["party_inv_no"] = f"{grn.supplier.code[3:]}-{RNG.randrange(10000, 99999)}"
            await GRN.filter(id=grn.id).update(**fields)
        # and supplier returns from before their total was kept
        from app.models import PurchaseReturn, PurchaseReturnLine

        for ret in await PurchaseReturn.filter(total__isnull=True):
            value = tax = ZERO
            for line in await PurchaseReturnLine.filter(purchase_return_id=ret.id).prefetch_related("product"):
                amount = Decimal(line.qty) * Decimal(line.unit_price)
                value += amount
                tax += amount * Decimal(line.tax_rate if line.tax_rate is not None else line.product.tax_rate or 0) / 100
            await PurchaseReturn.filter(id=ret.id).update(total=money(value + tax), tax_total=money(tax))

    async def adopt_orphan_receipts(self) -> None:
        """Stock that arrived with no GRN behind it gets the GRN it should have had."""
        from app.models import GRN, GRNLine, OutboxEvent, Product, StockMovement, next_value

        for movement in await StockMovement.filter(Q(reason__isnull=True) | Q(reason=""), kind="receive").order_by("at"):
            product = await Product.get(id=movement.product_id)
            code = supplier_code_for(product)
            supplier = self.supplier[code]
            qty = Decimal(movement.qty)
            unit = money(Decimal(product.avg_cost or 0) or Decimal(product.price) * Decimal("0.72"))
            number = f"GRN-{await next_value('grn', 11):04d}"
            net = money(qty * unit)
            tax = money(net * Decimal(product.tax_rate or 0) / 100)
            receiver = self.sk[stable(str(movement.id)) % 2]
            grn = await GRN.create(grn_number=number, supplier=supplier, party_inv_no=f"{code[3:]}-{RNG.randrange(10000, 99999)}", location_id=movement.location_id,
                                   received_by=receiver, gross_total=net, disc_total=ZERO, tax_total=tax, net_total=net + tax,
                                   due_date=pkt_day(movement.at) + timedelta(days=supplier.due_days or 0))
            await GRN.filter(id=grn.id).update(at=movement.at)
            await GRNLine.create(grn=grn, product=product, qty=qty, unit_price=unit, tax_rate=Decimal(product.tax_rate or 0))
            await StockMovement.filter(id=movement.id).update(reason=number, unit_cost=unit)
            event = await OutboxEvent.create(aggregate_type="GRN", aggregate_id=str(grn.id), origin_user_id=str(receiver.id),
                                             payload={"grnNumber": number, "locationId": movement.location_id, "supplierCode": supplier.code, "netTotal": str(net + tax)})
            await OutboxEvent.filter(id=event.id).update(created_at=movement.at)
            self.counts["stock that came with no GRN given one"] += 1

    # ── 3. buying ───────────────────────────────────────────────────────────────────────────────
    async def sales_by_day(self) -> None:
        from app.models import ReturnLine, SaleLine

        self.sold = defaultdict(lambda: defaultdict(Decimal))
        for row in await SaleLine.all().values("product_id", "qty", "is_return", "sale__at"):
            sign = -1 if row["is_return"] else 1
            self.sold[str(row["product_id"])][pkt_day(row["sale__at"])] += sign * Decimal(row["qty"])
        for row in await ReturnLine.all().values("product_id", "qty", "return_record__at"):
            self.sold[str(row["product_id"])][pkt_day(row["return_record__at"])] -= Decimal(row["qty"])

    def sold_between(self, pid: str, start: date, end: date) -> Decimal:
        return sum((q for d, q in self.sold[pid].items() if start <= d <= end), ZERO)

    def cost_of(self, product) -> Decimal:
        return Decimal(product.avg_cost or 0) or Decimal(product.price) * Decimal("0.72")

    async def backdate_po(self, po_id: str, created: datetime, approved: datetime | None = None, closed: datetime | None = None) -> None:
        from app.models import Notice, PurchaseOrder

        fields = {"created_at": created}
        if approved:
            fields["approved_at"] = approved
        if closed:
            fields["closed_at"] = closed
        await PurchaseOrder.filter(id=po_id).update(**fields)
        if approved:
            await Notice.filter(subject_id=po_id, kind="po.approved").update(at=approved)
        if closed:
            await Notice.filter(subject_id=po_id, kind__in=["po.cancelled", "po.closed"]).update(at=closed)

    async def receive(self, receiver, supplier, when: datetime, lines: list[dict], po_id: str | None = None, advance_rate: Decimal = ZERO, invoice: str | None = None):
        from app.models import GRN, OutboxEvent, ProductPriceChange, PurchaseOrder, StockMovement
        from app.schemas.inventory import GRNCreateRequest, GRNLineIn
        from app.services import inventory_service

        started = datetime.now(timezone.utc) - timedelta(seconds=1)
        net = sum((Decimal(l["qty"]) * Decimal(l["unitPrice"]) * (1 - Decimal(l.get("discPercent", 0)) / 100) + Decimal(l.get("misc", 0)) for l in lines), ZERO)
        request = GRNCreateRequest(
            supplierId=supplier.id, locationId=self.location.id, gstMode="normal", advanceTax=money(net * advance_rate),
            partyInvNo=invoice or f"{supplier.code[3:]}-{RNG.randrange(10000, 99999)}", purchaseOrderId=po_id, lines=[GRNLineIn(**l) for l in lines],
        )
        grn = await inventory_service.receive_grn(receiver, request)
        await GRN.filter(id=grn.id).update(at=when, due_date=pkt_day(when) + timedelta(days=supplier.due_days or 0))
        await StockMovement.filter(reason=grn.grn_number).update(at=when)
        await ProductPriceChange.filter(source="receiving", at__gte=started).update(at=when)
        await OutboxEvent.filter(aggregate_type="GRN", aggregate_id=str(grn.id)).update(created_at=when)
        if po_id:
            fresh = await PurchaseOrder.get(id=po_id)
            if fresh.status == "received":
                await PurchaseOrder.filter(id=po_id).update(closed_at=when)
        self.counts["GRNs received"] += 1
        return grn

    def grn_line(self, product, qty: Decimal, unit_price: Decimal, disc: Decimal, day: date, bonus: Decimal = ZERO, misc: Decimal = ZERO, new_price: Decimal | None = None) -> dict:
        line = {"productId": product.id, "qty": qty, "bonusQty": bonus, "unitPrice": unit_price, "discPercent": disc, "misc": misc, "taxRate": Decimal(product.tax_rate or 0)}
        if (product.category or "").upper() in ("FOOD", "BEVERAGES", "FRESH FOOD", "FROZEN FOOD") or (product.department or "").upper() == "PHARMACY":
            line["expiry"] = at(day + timedelta(days=RNG.randrange(120, 540)), 0)
        if new_price:
            line["newSalePrice"] = new_price
        return line

    async def purchasing(self) -> None:
        from app.models import PurchaseOrder, PurchaseOrderLine
        from app.schemas.purchase_orders import PurchaseOrderCreate, PurchaseOrderLineIn
        from app.services import purchase_order_service

        by_supplier = defaultdict(list)
        for pid, code in self.product_supplier.items():
            by_supplier[code].append(pid)
        deliveries: list[tuple[datetime, str]] = []
        last_order: dict[str, date] = {}
        turn = 0
        day = date(2026, 8, 3)
        while day <= END:
            if day in CLOSED_DAYS:
                day += timedelta(days=1)
                continue
            for code, spec in SUPPLIERS.items():
                weekdays, lead, fortnightly = spec[8], spec[9], spec[10]
                if day.weekday() not in weekdays or (fortnightly and ((day - BOOKS_START).days // 7) % 2):
                    continue
                since = last_order.get(code, day - timedelta(days=14 if fortnightly else 7))
                wanted = []
                for pid in by_supplier[code]:
                    product = self.products[pid]
                    sold = self.sold_between(pid, since, day - timedelta(days=1))
                    pack = pack_for(Decimal(product.price or 0))
                    if sold >= max(Decimal(2), Decimal(pack) / 3):
                        wanted.append((sold * Decimal(product.price or 0), sold, pid))
                if not wanted:
                    continue
                wanted.sort(reverse=True)
                supplier = self.supplier[code]
                disc = Decimal(spec[7])
                po_lines = []
                for _value, sold, pid in wanted[:25]:
                    product = self.products[pid]
                    pack = pack_for(Decimal(product.price or 0))
                    qty = Decimal(max(1, round(float(sold) * RNG.uniform(1.0, 1.3) / pack)) * pack)
                    unit = money(self.cost_of(product) * Decimal(str(RNG.uniform(0.98, 1.02))) / (1 - disc / 100))
                    if code in CASH_SUPPLIERS and unit >= 20:
                        unit = unit.quantize(Decimal("1"))  # their bills come in whole rupees
                    po_lines.append(PurchaseOrderLineIn(productId=pid, qty=qty, unitPrice=unit, discPercent=disc))
                raiser = self.im[turn % 2]
                turn += 1
                last_order[code] = day
                created = at(day, 9 if day == END else 10, RNG.randrange(0, 45))
                po = await purchase_order_service.create(raiser, PurchaseOrderCreate(
                    supplierId=supplier.id, locationId=self.location.id, expectedAt=promised(day + timedelta(days=lead[1])) if day < END else None,
                    notes=f"Reorder of what sold since {since:%d %b}", lines=po_lines,
                ))
                po_id = str(po.id)
                self.counts["purchase orders raised"] += 1
                if day == END:
                    await self.backdate_po(po_id, created)
                    self.counts["purchase orders waiting for approval"] += 1
                    continue
                if day < END and RNG.random() < 0.05:
                    await purchase_order_service.cancel(raiser, po_id)
                    await PurchaseOrder.filter(id=po_id).update(notes="Cancelled: the supplier's new price list came in higher; re-ordering next week")
                    await self.backdate_po(po_id, created, closed=created + timedelta(hours=2))
                    self.counts["purchase orders cancelled"] += 1
                    continue
                approved = at(day, 10, 50 + RNG.randrange(0, 9)) if day == END else at(day, RNG.randrange(13, 17), RNG.randrange(0, 59))
                await purchase_order_service.approve(self.bm, po_id)
                await self.backdate_po(po_id, created, approved)
                self.counts["purchase orders approved"] += 1
                arrive = trading(day + timedelta(days=RNG.randint(*lead)))
                if arrive >= END:
                    self.open_pos.append(po.po_number)
                    continue
                deliveries.append((at(arrive, RNG.randrange(10, 16), RNG.randrange(0, 59)), po_id))
            day += timedelta(days=1)

        deliveries.sort()
        price_changes = 0
        follow_ups = []
        for when, po_id in deliveries:
            po = await PurchaseOrder.get(id=po_id).prefetch_related("supplier")
            code = po.supplier.code
            lines = await PurchaseOrderLine.filter(purchase_order_id=po.id).prefetch_related("product").order_by("position")
            short = RNG.random() < 0.15
            grn_lines, leftovers = [], []
            for index, line in enumerate(lines):
                product = line.product
                pack = pack_for(Decimal(product.price or 0))
                qty = Decimal(line.qty)
                if short and index < 2 and qty > pack:
                    got = max(Decimal(pack), whole(qty * Decimal(str(RNG.uniform(0.5, 0.8))), pack))
                    if got < qty:
                        leftovers.append((line, qty - got))
                        qty = got
                unit = Decimal(line.unit_price)
                if RNG.random() < 0.06:
                    unit = money(unit * Decimal(str(RNG.choice([0.98, 1.02, 1.03]))))
                    if code in CASH_SUPPLIERS and unit >= 20:
                        unit = unit.quantize(Decimal("1"))
                bonus = Decimal(max(1, pack // 6)) if RNG.random() < 0.12 else ZERO
                misc = Decimal(RNG.randrange(800, 1600)) if index == 0 and code in ("SUPRGW", "SUPTTI", "SUPCRS") else ZERO
                new_price = None
                later_sales = any(d > pkt_day(when) for d, q in self.sold[str(product.id)].items() if q)
                if price_changes < 4 and not later_sales and Decimal(product.price or 0) < 1000 and pkt_day(when) >= date(2026, 9, 7) and RNG.random() < 0.15:
                    new_price = Decimal(product.price).quantize(Decimal("1")) + max(Decimal(5), (Decimal(product.price) * Decimal("0.05")).quantize(Decimal("1")))
                    price_changes += 1
                grn_lines.append(self.grn_line(product, qty, unit, Decimal(line.disc_percent), pkt_day(when), bonus, misc, new_price))
            advance = Decimal("0.01") if code == "SUPRGW" else ZERO
            await self.receive(self.sk[stable(po_id) % 2], po.supplier, when, grn_lines, po_id, advance)
            if leftovers:
                self.counts["deliveries that came short"] += 1
                follow_ups.append((when, po_id, leftovers))
        self.counts["new sale prices that arrived with a delivery"] = price_changes

        for when, po_id, leftovers in follow_ups:
            po = await PurchaseOrder.get(id=po_id).prefetch_related("supplier")
            later = trading(pkt_day(when) + timedelta(days=RNG.randint(3, 6)))
            if later < END and RNG.random() < 0.6:
                lines = [self.grn_line(l.product, qty, Decimal(l.unit_price), Decimal(l.disc_percent), later) for l, qty in leftovers]
                await self.receive(self.sk[0], po.supplier, at(later, RNG.randrange(10, 15), RNG.randrange(0, 59)), lines, po_id)
                self.counts["short deliveries completed later"] += 1
            elif trading(later + timedelta(days=2)) < END:
                closed = at(trading(later + timedelta(days=2)), 16, RNG.randrange(0, 59))
                await purchase_order_service.cancel(self.bm, po_id)
                await PurchaseOrder.filter(id=po_id).update(notes="Closed short: the rest isn't available this season")
                await self.backdate_po(po_id, po.created_at, closed=closed)
                self.counts["purchase orders closed short"] += 1
            else:
                self.open_pos.append(f"{po.po_number} (the rest)")

    async def supplier_returns(self) -> None:
        from app.models import GRN, GRNLine, OutboxEvent, PurchaseReturn, StockMovement
        from app.schemas.inventory import PurchaseReturnCreateRequest, PurchaseReturnLineIn
        from app.services import inventory_service

        candidates = await GRN.filter(purchase_order_id__isnull=False).order_by("at")
        if len(candidates) < 10:
            return
        picks = [candidates[int(len(candidates) * f)] for f in (0.12, 0.3, 0.52, 0.71, 0.88)]
        reasons = [("damaged", "Cartons crushed in delivery"), ("expired", "Short expiry on arrival"), ("wrong-item", "Wrong variant sent"),
                   ("damaged", "Leaking packs"), ("overstock", "Slow seller sent back by agreement")]
        for grn, (reason, note) in zip(picks, reasons):
            line = (await GRNLine.filter(grn=grn).order_by("-qty").prefetch_related("product"))[0]
            pack = pack_for(Decimal(line.product.price or 0))
            qty = Decimal(min(int(line.qty), max(1, pack // 2)))
            day = trading(pkt_day(grn.at) + timedelta(days=RNG.randint(1, 3)))
            if day >= END:
                continue
            when = at(day, 12, RNG.randrange(0, 59))
            ret = await inventory_service.create_purchase_return(self.im[1], PurchaseReturnCreateRequest(
                supplierId=str(grn.supplier_id), locationId=self.location.id, grnId=str(grn.id), reason=reason, notes=note,
                lines=[PurchaseReturnLineIn(productId=line.product_id, qty=qty, unitPrice=line.unit_price)],
            ))
            await PurchaseReturn.filter(id=ret.id).update(at=when)
            await StockMovement.filter(reason=ret.return_number).update(at=when)
            await OutboxEvent.filter(aggregate_type="PurchaseReturn", aggregate_id=str(ret.id)).update(created_at=when)
            self.counts["supplier returns"] += 1

    async def no_stock_outs(self) -> None:
        from app.models import StockMovement

        for _round in range(4):
            rows = await StockMovement.filter(product_id__in=list(self.products)).order_by("at").values("product_id", "qty", "at")
            balance = defaultdict(Decimal)
            first_negative: dict[str, tuple[datetime, Decimal]] = {}
            for r in rows:
                pid = str(r["product_id"])
                balance[pid] += Decimal(r["qty"])
                if balance[pid] < 0 and pid not in first_negative:
                    first_negative[pid] = (r["at"], -balance[pid])
            grouped = defaultdict(list)
            for pid, (when, short) in first_negative.items():
                product = self.products[pid]
                day = pkt_day(when) - timedelta(days=2)
                while day in CLOSED_DAYS:
                    day -= timedelta(days=1)
                moment = at(max(day, BOOKS_START), 9, 30)
                if moment >= when:
                    moment = when - timedelta(hours=1)
                pack = pack_for(Decimal(product.price or 0))
                ahead = self.sold_between(pid, pkt_day(moment), pkt_day(moment) + timedelta(days=21))
                qty = Decimal(max(pack, math.ceil(float(short + ahead) * 1.2 / pack) * pack))
                grouped[(moment, self.product_supplier[pid])].append((product, qty))
            for (moment, code), items in sorted(grouped.items()):
                supplier = self.supplier[code]
                lines = [self.grn_line(p, q, money(self.cost_of(p)), ZERO, pkt_day(moment)) for p, q in items]
                await self.receive(self.sk[1], supplier, moment, lines, None, invoice=f"URGENT-{supplier.code[3:]}-{pkt_day(moment):%d%m}")
                self.counts["urgent deliveries so nothing ran out"] += 1
            if not grouped:
                break

    # ── 4. customers, gift vouchers, tills ──────────────────────────────────────────────────────
    async def till_at(self, when: datetime):
        from app.models import TillSession

        sessions = [s for s in await TillSession.all().order_by("opened_at") if pkt_day(s.opened_at) == pkt_day(when)]
        inside = [s for s in sessions if s.opened_at <= when and (s.closed_at is None or when <= s.closed_at)]
        return (inside or sessions or [None])[0]

    async def customer_money(self) -> None:
        from app.models import CashMovement, Cheque, CustomerPayment, OutboxEvent, Party, ReturnRecord, SaleRecord, next_value
        from app.services import accounts_money_service
        from app.services.accounts_chart_service import customer_account

        bank = self.A["bank.main"]

        async def credit_events(party):
            events = []
            for sale in await SaleRecord.filter(party_id=party.id).prefetch_related("tenders"):
                amount = sum((Decimal(t.amount) for t in sale.tenders if t.code == "CREDIT"), ZERO)
                if amount:
                    events.append((sale.at, amount))
            for ret in await ReturnRecord.filter(against__party_id=party.id, refund_method="CREDIT"):
                events.append((ret.at, -Decimal(ret.refund_total)))
            return events

        async def owed(party, events, until: datetime) -> Decimal:
            total = self.opening_owed[str(party.id)] + sum((a for t, a in events if t < until), ZERO)
            total -= sum((Decimal(p.amount) for p in await CustomerPayment.filter(party_id=party.id, at__lt=until)), ZERO)
            account = await customer_account(party)
            for cheque in await Cheque.filter(party_account_id=account.id, received_on__lte=pkt_day(until)):
                if not (cheque.bounced_on and cheque.bounced_on <= pkt_day(until)):
                    total -= Decimal(cheque.amount)
            return total

        plan = {
            "ALI001": [("BANK", date(2026, 8, 14), 0.6), ("CHEQUE", date(2026, 8, 29), 0.55, date(2026, 9, 1), None),
                       ("CHEQUE", date(2026, 9, 9), 0.6, date(2026, 9, 11), None), ("CASH", date(2026, 9, 14), 0.3)],
            "CUST003": [("CASH", date(2026, 9, 5), 0.5), ("CHEQUE", date(2026, 9, 10), 0.45, None, date(2026, 9, 14)), ("BANK", date(2026, 9, 15), 0.4)],
        }
        self.opening_owed = {}
        cheque_no = 1004516
        await Party.filter(code="CUST003", due_days=0).update(due_days=15)  # a wholesale account pays within 15 days
        parties = {}
        for code in plan:
            party = await Party.get(code=code)
            events = await credit_events(party)
            self.opening_owed[str(party.id)] = Decimal(party.credit_balance) - sum((a for _t, a in events), ZERO)
            parties[code] = (party, events, await customer_account(party))
        # in date order across customers, so receipt and cheque numbers run with the calendar
        for code, step in sorted(((code, step) for code, steps in plan.items() for step in steps), key=lambda cs: cs[1][1]):
            party, events, account = parties[code]
            method, day, share = step[0], step[1], step[2]
            when = at(day, 10 if day == END else 11, RNG.randrange(0, 59))
            amount = whole(await owed(party, events, when) * Decimal(str(share)), 1000)
            if amount < 5000:
                continue
            if method == "BANK":
                payment = await accounts_money_service.receive_payment(self.bm, str(party.id), amount, "BANK", f"IBFT{RNG.randrange(10**9, 10**10)}", "Paid off their account")
            elif method == "CASH":
                till = await self.till_at(when)
                if not till:
                    continue
                number = f"RCP-{await next_value('customer_payment', 1):06d}"
                cashier = self.cashiers[0]
                movement = await CashMovement.create(till_session=till, kind="in", amount=amount, denominations={}, user=cashier, account=account,
                                                     payee=party.name, notes=f"{number}: payment from {party.name} ({party.code})")
                await CashMovement.filter(id=movement.id).update(at=when)
                fresh = await Party.get(id=party.id)
                balance = Decimal(fresh.credit_balance) - amount
                await Party.filter(id=party.id).update(credit_balance=balance)
                payment = await CustomerPayment.create(number=number, party=party, amount=amount, method="CASH", note="Paid at the counter",
                                                       received_by=cashier, cash_movement=movement, balance_after=balance)
                await OutboxEvent.create(aggregate_type="CustomerPayment", aggregate_id=str(payment.id), origin_user_id=str(cashier.id),
                                         payload={"number": number, "partyCode": party.code, "amount": str(amount), "method": "CASH", "balanceAfter": str(balance)})
            else:
                cleared_on, bounced_on = step[3], step[4]
                cheque = await accounts_money_service.record_cheque(self.bm, {
                    "partyAccountId": str(account.id), "amount": str(amount), "chequeNo": str(cheque_no),
                    "drawnOn": ["Meezan Bank, Bosan Road", "HBL, Gulgasht", "MCB, Nishtar Road"][cheque_no % 3],
                    "chequeDate": day.isoformat(), "receivedOn": day.isoformat(), "note": "Received at the branch against their account",
                })
                cheque_no += 37
                await Cheque.filter(id=cheque.id).update(created_at=when, updated_at=when)
                if cleared_on:
                    await accounts_money_service.clear_cheque(self.bm, str(cheque.id), str(bank.id), cleared_on)
                    await Cheque.filter(id=cheque.id).update(updated_at=at(cleared_on, 15))
                if bounced_on:
                    await accounts_money_service.bounce_cheque(self.bm, str(cheque.id), bounced_on, "Returned unpaid: insufficient funds")
                    await Cheque.filter(id=cheque.id).update(updated_at=at(bounced_on, 15))
                    self.counts["cheques bounced"] += 1
                self.counts["cheques received"] += 1
                continue
            balance = await owed(party, events, when + timedelta(seconds=1))
            await CustomerPayment.filter(id=payment.id).update(at=when, balance_after=balance)
            await OutboxEvent.filter(aggregate_type="CustomerPayment", aggregate_id=str(payment.id)).update(created_at=when)
            self.counts["customer payments"] += 1

    async def gift_voucher_cash(self) -> None:
        from app.models import CashMovement, GiftVoucher

        liability = self.A["liab.gift_vouchers"]
        for voucher in await GiftVoucher.filter(paid_by__isnull=True).order_by("issued_at"):
            till = await self.till_at(voucher.issued_at)
            if not till:
                continue
            movement = await CashMovement.create(till_session=till, kind="in", amount=voucher.face_value, denominations={}, user=self.cashiers[1],
                                                 account=liability, payee=voucher.issued_to_name, notes=f"Gift voucher {voucher.code} sold")
            await CashMovement.filter(id=movement.id).update(at=voucher.issued_at)
            await GiftVoucher.filter(id=voucher.id).update(paid_by="CASH", issued_by_name=voucher.issued_by_name or self.cashiers[1].name)
            self.counts["gift vouchers sold for cash through the till"] += 1

    async def tills(self) -> None:
        """No two sessions overlap, a session left open overnight is closed that night and the next morning gets its own,
        every bill falls inside its day's session, and each close counts the cash its own records add up to."""
        from app.models import CashMovement, ReturnRecord, SaleRecord, TillSession

        sale_times = sorted([s["at"] for s in await SaleRecord.all().values("at")] + [r["at"] for r in await ReturnRecord.all().values("at")])

        # a session running past midnight: closed that night; bills the next morning get their own session
        for s in await TillSession.filter(status="closed").order_by("opened_at"):
            first, last = pkt_day(s.opened_at), pkt_day(s.closed_at)
            if first == last:
                continue
            day_one = [t for t in sale_times if s.opened_at <= t <= s.closed_at and pkt_day(t) == first]
            day_two = [t for t in sale_times if s.opened_at <= t <= s.closed_at and pkt_day(t) == last]
            night = max([at(first, 21, 30)] + [t + timedelta(minutes=15) for t in day_one])
            next_morning = await TillSession.filter(opened_at__gt=s.opened_at, opened_at__lt=s.closed_at).exclude(id=s.id).exists()
            if not day_two or next_morning:  # that day's own session takes the morning's bills
                await TillSession.filter(id=s.id).update(closed_at=night)
                continue
            morning = min(day_two) - timedelta(minutes=7)
            new = await TillSession.create(session_number=f"TSN-{str(s.id)[:8]}", opened_at=morning, closed_at=s.closed_at, opened_by_id=s.opened_by_id,
                                           opening_float=s.opening_float, opening_denominations=s.opening_denominations or {}, status="closed",
                                           closed_by_id=s.closed_by_id, net_cash=s.net_cash, counted_cash=s.counted_cash, variance=s.variance)
            await TillSession.filter(id=s.id).update(closed_at=night)
            await CashMovement.filter(till_session_id=s.id, at__gte=morning).update(till_session_id=new.id)
            self.counts["tills left open overnight split into two days"] += 1

        sessions = await TillSession.all().order_by("opened_at")
        # overlapping sessions become a shift change: the later one takes over the rest of the earlier one's day
        for a, b in zip(sessions, sessions[1:]):
            if a.closed_at and a.closed_at >= b.opened_at:
                original = a.closed_at
                a.closed_at = b.opened_at - timedelta(seconds=30)
                if b.closed_at and b.closed_at > original and pkt_day(b.closed_at) != pkt_day(b.opened_at):
                    b.closed_at = original
                self.counts["overlapping till sessions made a shift change"] += 1
        # every bill inside a session of its day
        for moment in sale_times:
            if any(s.opened_at <= moment and (s.closed_at is None or moment <= s.closed_at) for s in sessions):
                continue
            same_day = [s for s in sessions if pkt_day(s.opened_at) == pkt_day(moment)]
            if not same_day:
                continue
            target = min(same_day, key=lambda s: min(abs((s.opened_at - moment).total_seconds()), abs(((s.closed_at or s.opened_at) - moment).total_seconds())))
            if moment < target.opened_at:
                target.opened_at = moment - timedelta(minutes=5)
            elif target.closed_at and moment > target.closed_at:
                target.closed_at = moment + timedelta(minutes=5)
            self.counts["till windows stretched to cover a bill"] += 1
        for s in sessions:
            await TillSession.filter(id=s.id).update(opened_at=s.opened_at, closed_at=s.closed_at)
        # cash in and out belongs to the session open when it happened
        sessions = await TillSession.all().order_by("opened_at")
        for movement in await CashMovement.all():
            owner = next((s for s in sessions if s.opened_at <= movement.at and (s.closed_at is None or movement.at <= s.closed_at)), None)
            if owner and str(owner.id) != str(movement.till_session_id):
                await CashMovement.filter(id=movement.id).update(till_session_id=owner.id)
        repaired = 0
        for s in sessions:
            if s.status != "closed" or not s.closed_at:
                continue
            cash = ZERO
            for sale in await SaleRecord.filter(at__gte=s.opened_at, at__lte=s.closed_at).prefetch_related("tenders"):
                cash += sum((Decimal(t.amount) for t in sale.tenders if t.code == "CASH"), ZERO) - Decimal(sale.cash_back or 0)
            refunds = sum((Decimal(r.refund_total) for r in await ReturnRecord.filter(at__gte=s.opened_at, at__lte=s.closed_at, refund_method="CASH")), ZERO)
            moves = sum(((Decimal(m.amount) if m.kind == "in" else -Decimal(m.amount)) for m in await CashMovement.filter(till_session_id=s.id)), ZERO)
            net = money(Decimal(s.opening_float) + cash - refunds + moves)
            variance = Decimal(s.variance or 0)
            if abs(variance) > 500:
                variance = Decimal(RNG.choice([-50, -20, 0, 0, 10, 30]))
            if Decimal(s.net_cash or 0) != net:
                repaired += 1
            await TillSession.filter(id=s.id).update(net_cash=net, counted_cash=net + variance, variance=variance)
        self.counts["till closes re-counted from their own records"] = repaired

    # ── 5. numbers in date order ────────────────────────────────────────────────────────────────
    async def renumber_records(self) -> None:
        from app.models import GRN, Counter, PurchaseReturn, StockMovement, TillSession

        async def renumber(model, field, prefix, width, order, counter, movement_reason=False):
            rows = await model.all().order_by(*order)
            planned = [(row, getattr(row, field), f"{prefix}-{index:0{width}d}") for index, row in enumerate(rows, start=1)]
            for index, (row, _old, _new) in enumerate(planned):
                await model.filter(id=row.id).update(**{field: f"TMP-{index:06d}"})
            for row, _old, new in planned:
                await model.filter(id=row.id).update(**{field: new})
            if movement_reason:
                for _row, old, new in planned:
                    if old != new:
                        await StockMovement.filter(reason=old).update(reason=f"~{new}")
                for _row, old, new in planned:
                    if old != new:
                        await StockMovement.filter(reason=f"~{new}").update(reason=new)
            await Counter.filter(id=counter).delete()
            await Counter.create(id=counter, value=len(rows) + 1)
            return len(rows)

        self.counts["GRNs numbered in date order"] = await renumber(GRN, "grn_number", "GRN", 4, ("at", "id"), "grn", True)
        self.counts["supplier returns numbered in date order"] = await renumber(PurchaseReturn, "return_number", "PR", 4, ("at", "id"), "purchase_return", True)
        self.counts["till sessions numbered in date order"] = await renumber(TillSession, "session_number", "TS", 4, ("opened_at", "id"), "till_session")

    # ── 6. money and running costs ──────────────────────────────────────────────────────────────
    async def load_running(self) -> None:
        from app.services.accounts_reports_service import _query

        self.running: dict[str, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        rows = await _query("SELECT l.account_id AS a, v.date AS d, SUM(l.debit) AS dr, SUM(l.credit) AS cr FROM acc_voucher_lines l "
                            "JOIN acc_vouchers v ON v.id = l.voucher_id WHERE v.status = 'posted' GROUP BY l.account_id, v.date", [])
        for r in rows:
            self.running[str(r["a"])][date.fromisoformat(str(r["d"])[:10])] += Decimal(str(r["dr"] or 0)) - Decimal(str(r["cr"] or 0))

    def balance_on(self, account, day: date, before: bool = False) -> Decimal:
        series = self.running[str(account.id)]
        return sum((v for d, v in series.items() if (d < day if before else d <= day)), ZERO)

    async def voucher(self, day: date, vtype: str, lines: list[tuple], description: str, header=None, reference: str | None = None,
                      cheque: str | None = None, hour: int | None = None, user=None):
        from app.models import Voucher, VoucherLine
        from app.services import vouchers_service

        lines = [(a, money(dr), money(cr), d) for a, dr, cr, d in lines if money(dr) or money(cr)]
        if not lines:
            return None
        payload = {"vtype": vtype, "date": day.isoformat(), "description": description, "referenceNo": reference,
                   "headerAccountId": str(header.id) if header else None, "chequeNo": cheque, "chequeDate": day.isoformat() if cheque else None,
                   "lines": [{"accountId": str(a.id), "debit": str(dr), "credit": str(cr), "description": d} for a, dr, cr, d in lines]}
        who = user or self.bm
        draft = await vouchers_service.create_draft(who, payload)
        posted = await vouchers_service.post(who, str(draft.id))
        stamp = at(day, hour if hour is not None else RNG.randrange(10, 17), RNG.randrange(0, 59))
        await Voucher.filter(id=posted.id).update(created_at=stamp - timedelta(minutes=RNG.randrange(3, 40)), posted_at=stamp, updated_at=stamp)
        for line in await VoucherLine.filter(voucher_id=posted.id):
            self.running[str(line.account_id)][day] += Decimal(line.debit) - Decimal(line.credit)
        self.counts[f"{vtype} vouchers made by hand"] += 1
        return posted

    async def contra(self, day: date, source, target, amount: Decimal, text: str, reference: str | None = None, hour: int = 17):
        return await self.voucher(day, "CV", [(target, amount, 0, text), (source, 0, amount, text)], text, reference=reference, hour=hour)

    async def money_and_costs(self) -> None:
        from app.models import GRN, PurchaseReturn, SaleRecord
        from app.services.accounts_chart_service import supplier_account

        A = self.A
        await self.load_running()
        safe, petty, bank = A["cash.main"], A["cash.petty"], A["bank.main"]
        events: dict[date, list] = defaultdict(list)

        def plan(day: date, order: int, fn) -> None:
            day = trading(day)
            if BOOKS_START <= day <= END:
                events[day].append((order, fn))

        def jv(lines, text, reference, hour=None, cheque=None):
            return lambda d: self.voucher(d, "JV", lines, text, reference=reference, hour=hour, cheque=cheque)

        def pay(vtype, header, lines, text, reference, cheque=None, hour=None):
            return lambda d: self.voucher(d, vtype, lines, text, header=header, reference=reference, cheque=cheque, hour=hour)

        # rent on the 1st by cheque, 10% withholding tax kept back and deposited with FBR by the 15th of the next month
        for day in (date(2026, 8, 1), date(2026, 9, 1)):
            plan(day, 3, jv([(A["52040001"], Decimal("100000"), 0, f"Shop rent for {day:%B %Y}"), (bank, 0, Decimal("90000"), "Cheque to the landlord, Ch. Muhammad Aslam"),
                             (A["tax.wht_payable"], 0, Decimal("10000"), "10% withholding tax on rent")],
                            f"Rent for {day:%B %Y}, withholding tax deducted", f"RENT-{day:%b-%Y}".upper(), hour=11, cheque=str(40211870 + day.month)))
        plan(date(2026, 8, 14), 3, pay("BPV", bank, [(A["tax.wht_payable"], Decimal("10000"), 0, "Withholding tax on July rent")], "Withholding tax deposited with FBR", "CPR-2026-07-RENT"))
        plan(date(2026, 9, 14), 3, pay("BPV", bank, [(A["tax.wht_payable"], Decimal("10000"), 0, "Withholding tax on August rent")], "Withholding tax deposited with FBR", "CPR-2026-08-RENT"))

        # salaries: accrued on the last day of the month, paid by bank transfer on the 3rd
        plan(date(2026, 8, 3), 3, pay("BPV", bank, [(A["21040001"], MONTHLY_SALARIES - 5000, 0, "July salaries")], "July salaries paid by bank transfer", "SAL-2026-07"))
        plan(date(2026, 8, 31), 5, jv([(A["52020001"], MONTHLY_SALARIES, 0, "August salaries, 11 staff"), (A["21040001"], 0, MONTHLY_SALARIES, "Owed to staff")],
                                     "August salaries accrued", "SAL-2026-08", hour=18))
        plan(date(2026, 9, 3), 3, pay("BPV", bank, [(A["21040001"], MONTHLY_SALARIES, 0, "August salaries")], "August salaries paid by bank transfer", "SAL-2026-08"))
        plan(date(2026, 9, 10), 3, pay("CPV", safe, [(A["11060002"], Decimal("10000"), 0, "Advance to Kashif Iqbal, recovered from September salary")], "Staff advance", "ADV-KASHIF"))

        # utilities: bills arrive at month end, paid in the second week
        plan(date(2026, 8, 10), 3, pay("BPV", bank, [(A["21040002"], Decimal("36800"), 0, "July electricity, gas and water bills")], "July utility bills paid", "MEPCO-SNGPL-JUL"))
        plan(date(2026, 8, 31), 6, jv([(A["52030001"], Decimal("33800"), 0, "MEPCO electricity bill for August"), (A["52030002"], Decimal("3650"), 0, "SNGPL gas bill for August"),
                                       (A["52030003"], Decimal("1900"), 0, "WASA water bill for August"), (A["21040002"], 0, Decimal("39350"), "Bills received")],
                                     "August utility bills accrued", "UTIL-2026-08", hour=18))
        plan(date(2026, 9, 9), 3, pay("BPV", bank, [(A["21040002"], Decimal("39350"), 0, "August electricity, gas and water bills")], "August utility bills paid", "MEPCO-SNGPL-AUG"))
        for day in (date(2026, 8, 5), date(2026, 9, 5)):
            plan(day, 3, pay("BPV", bank, [(A["52030004"], Decimal("5999"), 0, f"PTCL fibre internet and landline, {day:%B}")], "Internet and phone bill", f"PTCL-{day:%b}".upper()))
        plan(date(2026, 8, 31), 7, jv([(A["52010008"], Decimal("33500"), 0, "Depreciation for August: 10% a year on Rs 40.25 lakh of fixtures and equipment"),
                                       (A["12020001"], 0, Decimal("33500"), "Depreciation for August")], "Depreciation for August", "DEP-2026-08", hour=18))
        plan(date(2026, 8, 31), 8, pay("BPV", bank, [(A["expense.bank_charges"], Decimal("1850"), 0, "Account maintenance, SMS alerts and a cheque book")], "Bank charges for August", "BANK-2026-08", hour=18))
        plan(date(2026, 8, 25), 3, pay("BPV", bank, [(A["52010007"], Decimal("15000"), 0, "Sales tax and withholding returns for July")], "Tax consultant's fee: Rana & Co.", "RANA-0726"))
        plan(date(2026, 8, 30), 4, pay("BPV", bank, [(A["31010002"], Decimal("100000"), 0, "Owner's drawings for August")], "Owner's drawings", "DRAW-2026-08"))
        plan(date(2026, 9, 12), 4, pay("BPV", bank, [(A["31010002"], Decimal("150000"), 0, "Owner's drawings")], "Owner's drawings", "DRAW-2026-09"))
        plan(date(2026, 8, 31), 6, pay("CPV", safe, [(A["52010006"], Decimal("9000"), 0, "Night guard and sweeper, August")], "Cleaning and security contractor", "CLEAN-AUG"))
        for day, amount, what, via in ((date(2026, 8, 12), 7500, "Flyers for the Independence Day offers", safe),
                                       (date(2026, 9, 4), 22000, "Facebook and Instagram ads for wholesale buyers", bank),
                                       (date(2026, 9, 11), 9000, "Banners for the wholesale counter", safe)):
            plan(day, 5, pay("CPV" if via is safe else "BPV", via, [(A["52060003"], Decimal(amount), 0, what)], "Advertising and promotions", "PROMO"))
        for day, amount in ((date(2026, 8, 7), 7200), (date(2026, 8, 26), 5800), (date(2026, 9, 6), 12500)):
            plan(day, 5, pay("CPV", safe, [(A["52060004"], Decimal(amount), 0, "Printed shopping bags, Al-Hamd Packing House")], "Shopping bags", "BAGS"))
        plan(date(2026, 8, 18), 5, pay("CPV", safe, [(A["52040002"], Decimal("9500"), 0, "Split AC service, three units")], "Repairs and maintenance", "AC-SERVICE"))
        plan(date(2026, 9, 7), 5, pay("BPV", bank, [(A["52040002"], Decimal("38000"), 0, "Chiller compressor replaced, Cool Tech Services")], "Repairs and maintenance", "COOLTECH-114"))
        for day, amount in ((date(2026, 9, 7), 18000), (date(2026, 9, 12), 12500)):
            plan(day, 5, pay("CPV", safe, [(A["52050003"], Decimal(amount), 0, "Loader hired to deliver wholesale orders")], "Delivery of wholesale orders", "LOADER"))
        plan(date(2026, 9, 12), 6, pay("CPV", safe, [(A["52020003"], Decimal("12000"), 0, "Staff dinner for reaching the wholesale target")], "Staff welfare", "STAFF-DINNER"))

        async def rebate(d):
            account = await supplier_account(self.supplier["SUP786"])
            return await self.voucher(d, "JV", [(account, Decimal("18500"), 0, "Credit note CN-786-0831 against our account"),
                                                (A["42010007"], 0, Decimal("18500"), "Volume rebate on July and August purchases")],
                                      "Volume rebate from 786 Traders", reference="CN-786-0831", hour=16)
        plan(date(2026, 8, 31), 4, rebate)

        # half-month figures so September's first half reads like a month in the review
        def accrue_half_september(d):
            return self.voucher(d, "JV", [(A["52020001"], MONTHLY_SALARIES / 2, 0, "Salaries 1 to 15 September"),
                                          (A["52020002"], self.wholesale_incentive, 0, "1% incentive on September wholesale sales so far"),
                                          (A["52030001"], Decimal("17500"), 0, "Electricity 1 to 15 September (meter reading)"),
                                          (A["52010008"], Decimal("16750"), 0, "Depreciation 1 to 15 September"),
                                          (A["21040001"], 0, MONTHLY_SALARIES / 2 + self.wholesale_incentive, "Owed to staff"),
                                          (A["21040002"], 0, Decimal("17500"), "Electricity owed"), (A["12020001"], 0, Decimal("16750"), "Depreciation")],
                                "Costs to 15 September accrued for the mid-month review", reference="ACCRUAL-2026-09-15", hour=12)
        plan(END, 9, accrue_half_september)
        plan(END, 8, pay("CPV", safe, [(A["52010006"], Decimal("4500"), 0, "Night guard and sweeper, 1 to 15 September")], "Cleaning and security contractor", "CLEAN-SEP", hour=11))
        plan(date(2026, 9, 14), 8, pay("BPV", bank, [(A["expense.bank_charges"], Decimal("650"), 0, "Online transfer charges")], "Bank charges", "BANK-2026-09", hour=16))

        # petty cash: a Rs 15,000 imprest topped up from the safe
        plan(BOOKS_START, 0, lambda d: self.contra(d, safe, petty, Decimal("15000"), "Petty cash float from the safe", "PETTY-FLOAT", hour=9))
        petty_items = [("52010004", (600, 1300), "Tea and biscuits for staff and customers"), ("52010003", (800, 2400), "Receipt rolls and stationery"),
                       ("52050003", (500, 1800), "Courier and rickshaw"), ("52040003", (2500, 5000), "Generator diesel during load-shedding"),
                       ("52050001", (1000, 2500), "Loading and unloading at deliveries")]
        day = BOOKS_START + timedelta(days=3)
        while day <= END:
            if day.weekday() == 1:
                chosen = RNG.sample(petty_items, k=2)
                items = [(A[c], Decimal(RNG.randrange(*r) // 50 * 50), what) for c, r, what in chosen]
                plan(day, 4, lambda d, items=items: self.petty_spend(d, items))
            day += timedelta(days=1)

        # suppliers paid on their terms: what falls due by the weekend is paid on Wednesday and Saturday
        bills = []
        for grn in await GRN.filter(at__gte=at(BOOKS_START, 0)).prefetch_related("supplier").order_by("at"):
            returned = sum((Decimal(r.total or 0) for r in await PurchaseReturn.filter(grn_id=grn.id)), ZERO)
            bills.append({"supplier": grn.supplier, "grn": grn.grn_number, "invoice": grn.party_inv_no, "amount": Decimal(grn.net_total or 0) - returned,
                          "due": grn.due_date or pkt_day(grn.at), "day": pkt_day(grn.at)})
        self.opening_payables = {"SUP786": (Decimal("185000"), date(2026, 8, 6)), "SUPGRO": (Decimal("96500"), date(2026, 8, 8)), "SUPNEW1": (Decimal("42300"), date(2026, 8, 5))}
        for code, (amount, due) in self.opening_payables.items():
            bills.append({"supplier": self.supplier[code], "grn": "July bills", "invoice": "balance at 31 Jul", "amount": amount, "due": due, "day": date(2026, 7, 31)})
        paid = set()
        run_day = BOOKS_START
        while run_day <= END:
            if run_day.weekday() in (2, 5) and run_day not in CLOSED_DAYS:
                due_now = defaultdict(list)
                for index, bill in enumerate(bills):
                    if index in paid or bill["amount"] <= 0 or bill["day"] >= run_day or bill["due"] > run_day + timedelta(days=3):
                        continue
                    if bill["supplier"].code == "SUPCRS" and date(2026, 9, 3) <= bill["due"] <= date(2026, 9, 12):
                        continue  # Crescent's bill is held: a short delivery's credit note hasn't come
                    due_now[bill["supplier"].code].append(index)
                for code, indexes in due_now.items():
                    plan(run_day, 6, lambda d, code=code, chosen=[bills[i] for i in indexes]: self.pay_supplier(d, self.supplier[code], chosen))
                    paid.update(indexes)
            run_day += timedelta(days=1)

        # takings: card and wallet money settled to the bank next working day, less fees; safe cash banked
        self.fee_rates = {"wallet.card": Decimal("0.018"), "wallet.easypaisa": Decimal("0.01"), "wallet.jazzcash": Decimal("0.01")}
        day = BOOKS_START + timedelta(days=1)
        while day <= END:
            if day.weekday() != 6:
                plan(day, 1, lambda d: self.settle_wallets(d))
            if day.weekday() in (0, 2, 4):
                plan(day, 10, lambda d: self.bank_the_safe(d))
            day += timedelta(days=1)

        # August's sales tax return: output less input, paid by the 18th
        def gst_return(d):
            out = -sum((v for k, v in self.running[str(A["tax.gst_output"].id)].items() if k.month == 8), ZERO)
            inp = sum((v for k, v in self.running[str(A["tax.gst_input"].id)].items() if k.month == 8), ZERO)
            if out - inp < 1000:
                return None
            return self.voucher(d, "JV", [(A["tax.gst_output"], out, 0, "GST charged on August sales"), (A["tax.gst_input"], 0, inp, "GST paid on August purchases"),
                                          (bank, 0, out - inp, "Paid to FBR")], "Sales tax return for August", reference="STRN-2026-08", hour=15)
        plan(date(2026, 9, 14), 7, gst_return)

        wholesale = ZERO
        for sale in await SaleRecord.filter(at__gte=at(date(2026, 9, 1), 0), at__lt=NOW).prefetch_related("party"):
            if sale.party and sale.party.tier == "wholesale":
                wholesale += Decimal(sale.net_value)
        self.wholesale_incentive = whole(wholesale * Decimal("0.01"), 10)

        for day in sorted(events):
            for _order, fn in sorted(events[day], key=lambda e: e[0]):
                result = fn(day)
                if asyncio.iscoroutine(result):
                    await result

    async def petty_spend(self, day: date, items):
        petty, safe = self.A["cash.petty"], self.A["cash.main"]
        total = sum((amount for _a, amount, _w in items), ZERO)
        held = self.balance_on(petty, day)
        if held < total + 1000:
            await self.contra(day, safe, petty, Decimal("15000") - held, "Petty cash topped up to Rs 15,000 from the safe", "PETTY-TOPUP", hour=10)
        await self.voucher(day, "CPV", [(a, amount, 0, what) for a, amount, what in items], "Petty cash spending", header=petty, reference="PETTY", hour=16)

    async def pay_supplier(self, day: date, supplier, bills: list[dict]):
        from app.services.accounts_chart_service import supplier_account

        safe, bank = self.A["cash.main"], self.A["bank.main"]
        account = await supplier_account(supplier)
        total = sum((b["amount"] for b in bills), ZERO)
        if total <= 0:
            return
        described = "; ".join(f"{b['grn']} ({b['invoice']})" for b in bills)[:250]
        if supplier.code == "SUPRGW" and day >= date(2026, 9, 1):
            total = whole(total * Decimal("0.5"), 1000)
            described = f"Half now, the rest when the order is complete: {described}"[:250]
        if supplier.code in CASH_SUPPLIERS and total == total.quantize(Decimal("1")) and total <= Decimal("40000") and self.balance_on(safe, day, before=True) >= total + Decimal("40000"):
            await self.voucher(day, "CPV", [(account, total, 0, described)], f"Paid {supplier.name} in cash", header=safe, reference=bills[0]["invoice"], hour=15)
        elif total >= Decimal("150000"):
            await self.voucher(day, "BPV", [(account, total, 0, described)], f"Cheque to {supplier.name}", header=bank, reference=bills[0]["invoice"],
                               cheque=str(50110000 + stable(described) % 90000), hour=15)
        else:
            await self.voucher(day, "BPV", [(account, total, 0, described)], f"Online transfer to {supplier.name}", header=bank, reference=f"IBFT{RNG.randrange(10**8, 10**9)}", hour=15)
        self.counts["supplier payments"] += 1

    async def settle_wallets(self, day: date):
        bank, fees = self.A["bank.main"], self.A["53010002"]
        lines = []
        for key, rate in self.fee_rates.items():
            wallet = self.A[key]
            held = self.balance_on(wallet, day, before=True)
            if held <= 0:
                continue
            fee = money(held * rate)
            label = {"wallet.card": "Card machine", "wallet.easypaisa": "Easypaisa", "wallet.jazzcash": "JazzCash"}[key]
            lines += [(bank, held - fee, 0, f"{label} takings settled"), (fees, fee, 0, f"{label} fee"), (wallet, 0, held, "Settled to the bank")]
        if lines:
            await self.voucher(day, "JV", lines, "Card and wallet takings settled to the bank, less fees", reference=f"SETTLE-{day:%d%m}", hour=12)

    async def bank_the_safe(self, day: date):
        safe, bank = self.A["cash.main"], self.A["bank.main"]
        amount = whole(self.balance_on(safe, day) - Decimal("60000"), 1000)
        if amount >= Decimal("20000"):
            await self.contra(day, safe, bank, amount, "Cash from the safe deposited in the bank", f"DEP-{day:%d%m}", hour=17 if day < END else 12)

    # ── 7. opening balances, numbering, close ───────────────────────────────────────────────────
    async def opening_balances(self) -> None:
        from app.models import Voucher
        from app.services import accounts_posting_service, vouchers_service
        from app.services.accounts_chart_service import supplier_account

        A = self.A
        await self.load_running()
        suggestion = await accounts_posting_service.opening_suggestion()

        def lowest(account) -> Decimal:
            running, low = ZERO, ZERO
            for d in sorted(self.running[str(account.id)]):
                running += self.running[str(account.id)][d]
                low = min(low, running)
            return low

        opening_bank = max(Decimal("1500000"), whole(-lowest(A["bank.main"]) + Decimal("450000"), 50000))
        opening_safe = max(Decimal("150000"), whole(-lowest(A["cash.main"]) + Decimal("60000"), 10000))
        lines = [{"accountId": l["accountId"], "debit": l["debit"], "credit": l["credit"], "description": l["description"]} for l in suggestion["lines"]]
        extra = [
            (A["cash.main"], opening_safe, 0, "Cash in the safe at the stock take"),
            (A["bank.main"], opening_bank, 0, "Bank statement balance on 31 Jul"),
            (A["11060003"], Decimal("400000"), 0, "Rent security deposit with the landlord"),
            (A["12010001"], Decimal("1850000"), 0, "Shelving, counters and fixtures"),
            (A["12010002"], Decimal("640000"), 0, "POS terminals, scanners and scales"),
            (A["12010003"], Decimal("385000"), 0, "Branch server, counter PCs and printers"),
            (A["12010005"], Decimal("1150000"), 0, "Chillers, freezers and air conditioning"),
            (A["12020001"], 0, Decimal("410000"), "Depreciation to 31 Jul"),
            (A["21040001"], 0, MONTHLY_SALARIES - 5000, "July salaries not yet paid"),
            (A["21040002"], 0, Decimal("36800"), "July utility bills not yet paid"),
            (A["tax.wht_payable"], 0, Decimal("10000"), "Withholding tax on July rent"),
        ]
        for code, (amount, _due) in self.opening_payables.items():
            extra.append((await supplier_account(self.supplier[code]), 0, amount, "July bills not yet paid"))
        for account, dr, cr, text in extra:
            lines.append({"accountId": str(account.id), "debit": str(money(dr)), "credit": str(money(cr)), "description": text})
        total_dr = sum((Decimal(l["debit"]) for l in lines), ZERO)
        total_cr = sum((Decimal(l["credit"]) for l in lines), ZERO)
        capital = money(total_dr - total_cr)
        lines.append({"accountId": str(A["equity.capital"].id), "debit": "0", "credit": str(capital), "description": "Owner's capital: what the branch was worth on 31 Jul"})
        day = date(2026, 7, 31)
        draft = await vouchers_service.create_draft(self.bm, {"vtype": "OB", "date": day.isoformat(), "referenceNo": "OPENING-2026",
                                                              "description": "Opening balances on 31 Jul 2026: the stock take, cash, bank, fixed assets and what was owed", "lines": lines})
        posted = await vouchers_service.post(self.bm, str(draft.id))
        stamp = at(date(2026, 8, 1), 8, 30)
        await Voucher.filter(id=posted.id).update(created_at=stamp - timedelta(minutes=25), posted_at=stamp, updated_at=stamp)
        self.notes.append(f"opening bank Rs {opening_bank:,.0f}, safe Rs {opening_safe:,.0f}, owner's capital Rs {capital:,.0f}")

    async def renumber_vouchers(self) -> None:
        from app.models import Counter, OutboxEvent, Voucher
        from app.services import vouchers_service

        from app.models import GRN, CashMovement, Cheque, CustomerPayment, GiftVoucher, PurchaseReturn, TillSession, Transfer

        async def when_it_happened(v) -> datetime:
            """An automatic voucher is stamped with the moment its record happened; a day's sales and corrections at closing time."""
            kind, _, ref = (v.source or "").partition(":")
            moment = None
            try:
                if kind == "till-open":
                    moment = (await TillSession.get(id=ref)).opened_at
                elif kind == "till-close":
                    moment = (await TillSession.get(id=ref)).closed_at
                elif kind == "cash-move":
                    moment = (await CashMovement.get(id=ref)).at
                elif kind == "customer-payment":
                    moment = (await CustomerPayment.get(id=ref)).at
                elif kind == "grn":
                    moment = (await GRN.get(id=ref)).at
                elif kind == "purchase-return":
                    moment = (await PurchaseReturn.get(id=ref)).at
                elif kind == "gift-voucher":
                    moment = (await GiftVoucher.get(id=ref)).issued_at
                elif kind.startswith("transfer"):
                    transfer = await Transfer.get(id=ref)
                    moment = transfer.received_at or transfer.dispatched_at
                elif kind == "cheque-received":
                    moment = (await Cheque.get(id=ref)).created_at
                elif kind in ("cheque-cleared", "cheque-bounced"):
                    moment = at(v.date, 15)
            except Exception:  # noqa: BLE001 — a record that's gone falls back to the day's close
                moment = None
            if moment is None or pkt_day(moment) != v.date:
                moment = at(v.date, 23, 30) if v.date < END else NOW - timedelta(minutes=5)
            return min(moment + timedelta(seconds=40), NOW - timedelta(minutes=2))

        prefix = await vouchers_service.book_prefix()
        stamped = []
        for v in await Voucher.all():
            stamped.append((v.date, await when_it_happened(v) if v.auto else v.created_at, str(v.id), v))
        stamped.sort(key=lambda s: (s[0], s[1], s[2]))
        per_type: dict[str, int] = defaultdict(int)
        planned = []
        for _day, stamp, _id, v in stamped:
            per_type[v.vtype] += 1
            planned.append((v, f"{prefix}-{v.vtype}-{per_type[v.vtype]:06d}", stamp))
        for index, (v, _n, _s) in enumerate(planned):
            await Voucher.filter(id=v.id).update(number=f"TMP-{index:07d}")
        for v, number, stamp in planned:
            fields = {"number": number}
            if v.auto:
                fields.update(created_at=stamp, posted_at=stamp, updated_at=stamp)
            await Voucher.filter(id=v.id).update(**fields)
        for vtype, count in per_type.items():
            await Counter.filter(id=f"voucher:{vtype}").delete()
            await Counter.create(id=f"voucher:{vtype}", value=count + 1)
        # head office's copy: the copies queued along the way are superseded by one final copy of each voucher
        for event in await OutboxEvent.filter(aggregate_type="AccVoucher", status="pending"):
            if not ((event.payload or {}).get("voucher") or {}).get("deleted"):
                await OutboxEvent.filter(id=event.id).delete()
        for v in await Voucher.filter(status="posted").order_by("date", "created_at"):
            await vouchers_service.emit(v)
        self.counts["vouchers numbered in date order"] = len(planned)

    async def close_august(self) -> None:
        from app.models import Notice
        from app.services import accounts_posting_service, alerts_service, vouchers_service

        result = await accounts_posting_service.run(full=True)
        for problem in result["problems"][:20]:
            say(f"    posting problem: {problem}")
        row = await vouchers_service.settings()
        row.locked_until = date(2026, 8, 31)
        await row.save()
        await vouchers_service.emit_settings(row)
        notice = await alerts_service.notify("accounts.period", "Closed the books up to 31 Aug 2026", body=f"By {self.bm.name}", link="/accounts/settings",
                                             audience_any=[("accounts.period", "X"), ("accounts.settings", "R")], tone="warning")
        await Notice.filter(id=notice.id).update(at=at(date(2026, 9, 4), 17, 10))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="write it")
    parser.add_argument("--cloud-db", help="head office's cloud.db (read only), for the cost of godown transfers received before costs were sent")
    args = parser.parse_args()
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        from app.models import Counter
        from app.services import accounts_posting_service

        if await Counter.exists(id=STORY_KEY):
            raise SystemExit("This database already has the story. Restore the backup from before it to run it again.")
        if NOW < datetime.combine(END, time(13, 0), tzinfo=PKT):
            raise SystemExit("Run this after 1 pm on 15 Sep: the last day's morning is part of the story.")
        if not args.yes:
            print(__doc__)
            print("  Dry run: nothing written. Stop the branch server, back up branch.db, then run with --yes.")
            return
        story = Story(args.cloud_db)
        await story.load()
        steps = [
            ("Opening stock take moved to 31 Jul", story.repair_opening_stock),
            ("Stock movements linked to their GRNs", story.link_grn_movements),
            ("Till cash outs given what they were for", story.classify_cash_outs),
            ("Godown transfers given head office's cost", story.transfer_costs),
            ("Suppliers and who supplies each Item", story.suppliers),
            ("Stock with no GRN given one", story.adopt_orphan_receipts),
            ("What sold each day", story.sales_by_day),
            ("Purchase orders and deliveries", story.purchasing),
            ("Supplier returns", story.supplier_returns),
            ("Nothing runs out", story.no_stock_outs),
            ("Customers paying off credit", story.customer_money),
            ("Gift vouchers through the till", story.gift_voucher_cash),
            ("Till sessions reconciled", story.tills),
            ("GRNs, returns and tills numbered in date order", story.renumber_records),
            ("Automatic vouchers posted", lambda: accounts_posting_service.run(full=True)),
            ("Money and running costs", story.money_and_costs),
            ("Opening balances", story.opening_balances),
            ("Everything re-posted", lambda: accounts_posting_service.run(full=True)),
            ("Voucher numbers in date order", story.renumber_vouchers),
            ("August closed", story.close_august),
        ]
        for title, step in steps:
            say(f"{title} ...")
            result = await step()
            if isinstance(result, dict) and result.get("problems"):
                for problem in result["problems"][:10]:
                    say(f"    problem: {problem}")
        await Counter.create(id=STORY_KEY, value=1)
        print("\n  Done:")
        for key, value in story.counts.items():
            print(f"    {key}: {value}")
        for note in story.notes:
            print(f"    {note}")
        if story.open_pos:
            print(f"    purchase orders still on their way: {', '.join(story.open_pos)}")
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
